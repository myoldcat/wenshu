"""Bounded, public-only HTML fetching with DNS-pinned connections.

Each redirect is validated and resolved independently. The TCP destination is
the checked public IP, while Host and TLS SNI use the original hostname. Ambient
HTTP_PROXY/HTTPS_PROXY variables are intentionally ignored: an implicit proxy
would invalidate the address-pinning guarantee.
"""
from __future__ import annotations

import ipaddress
import queue
import socket
import threading
import time
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpx

MAX_HTML_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 5
TOTAL_TIMEOUT_SECONDS = 60
USER_AGENT = "KooHub/1.0 (local document importer)"


class FetchError(ValueError):
    """The URL or response failed the import policy."""


def _resolve(host: str, port: int) -> list[str]:
    result = queue.Queue(maxsize=1)
    def lookup():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.put(exc)
    # getaddrinfo has no cross-platform timeout option. A daemon bounds the
    # importer wait without keeping application shutdown alive on stalled DNS.
    threading.Thread(target=lookup, daemon=True).start()
    try:
        records = result.get(timeout=10)
    except queue.Empty as exc:
        raise FetchError("网址 DNS 解析超时。") from exc
    if isinstance(records, OSError):
        exc = records
        raise FetchError("无法解析网址的主机名。") from exc
    return list(dict.fromkeys(record[4][0] for record in records))


def _public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not ip.is_unspecified


def _validated_target(url: str, resolver: Callable[[str, int], Iterable[str]]) -> tuple[httpx.URL, str, str]:
    if len(url) > 8192 or any(ord(c) < 32 for c in url) or "\\" in url:
        raise FetchError("网址包含无效字符或过长。")
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise FetchError("仅支持 http/https 公开网页。")
        if parsed.username is not None or parsed.password is not None:
            raise FetchError("网址不能包含账户凭据。")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        normalized = httpx.URL(url).copy_with(fragment=None)
        host = normalized.raw_host.decode("ascii")
    except (ValueError, httpx.InvalidURL) as exc:
        if isinstance(exc, FetchError):
            raise
        raise FetchError("网址格式无效。") from exc
    if "%" in host or host.rstrip(".").lower() in {"localhost", "localhost.localdomain"}:
        raise FetchError("不允许访问本机或内网地址。")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = list(resolver(host, port))
    else:
        addresses = [str(literal)]
    # Reject mixed public/private answers instead of selecting the convenient one.
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise FetchError("仅允许公网地址；已阻止本机、内网或保留地址。")
    host_header = f"[{host}]" if ":" in host else host
    if port != (443 if normalized.scheme == "https" else 80):
        host_header += f":{port}"
    return normalized.copy_with(host=addresses[0]), host, host_header


def fetch_html(url: str, *, transport: httpx.BaseTransport | None = None, resolver: Callable[[str, int], Iterable[str]] | None = None, max_bytes: int = MAX_HTML_BYTES) -> dict:
    """Fetch HTML synchronously; injectable transport/resolver support offline tests.

    max_bytes may lower, but cannot raise, the production 64 MiB ceiling.
    """
    if not 0 < max_bytes <= MAX_HTML_BYTES:
        raise ValueError("max_bytes must be between 1 and 64 MiB")
    resolver = resolver or _resolve
    current = url.strip()
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    seen = set()
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=httpx.Timeout(15, connect=10), headers={"User-Agent": USER_AGENT, "Accept": "text/html, application/xhtml+xml", "Accept-Encoding": "identity"}) as client:
        for hop in range(MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                raise FetchError("网页抓取超过总时间限制。")
            pinned, hostname, host_header = _validated_target(current, resolver)
            if current in seen:
                raise FetchError("网页存在循环重定向。")
            seen.add(current)
            try:
                with client.stream("GET", pinned, headers={"Host": host_header}, extensions={"sni_hostname": hostname}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("网页重定向缺少目标地址。")
                        if hop >= MAX_REDIRECTS:
                            raise FetchError("网页重定向次数超过上限。")
                        current = urljoin(current, location)
                        continue
                    if not 200 <= response.status_code < 300:
                        raise FetchError(f"网页返回 HTTP {response.status_code}，无法入库。")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise FetchError("网址未返回 HTML 页面。")
                    if response.headers.get("content-encoding", "identity").lower().strip() not in {"", "identity"}:
                        raise FetchError("网页未遵守未压缩响应请求，请下载 HTML 后导入。")
                    declared = response.headers.get("content-length")
                    if declared:
                        try:
                            size = int(declared)
                        except ValueError as exc:
                            raise FetchError("网页返回无效的 Content-Length。") from exc
                        if size < 0 or size > max_bytes:
                            raise FetchError("网页大小超过允许上限。")
                    data = bytearray()
                    # Compressed bodies are rejected above, avoiding unbounded
                    # decompressor allocations before a chunk can be measured.
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        if time.monotonic() > deadline:
                            raise FetchError("网页抓取超过总时间限制。")
                        if len(data) + len(chunk) > max_bytes:
                            raise FetchError("网页大小超过允许上限。")
                        data.extend(chunk)
                    if not data:
                        raise FetchError("网页返回空内容。")
                    return {"data": bytes(data), "final_url": str(httpx.URL(current).copy_with(fragment=None)), "content_type": content_type}
            except httpx.HTTPError as exc:
                raise FetchError("网页连接失败或超时；请检查网络，或下载 HTML 后导入。") from exc
    raise FetchError("网页抓取失败。")
