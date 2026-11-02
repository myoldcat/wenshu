"""Offline HTML extraction. Never evaluates scripts or fetches linked resources.

The returned XPath locators refer to the original parsed document, before cleanup.
Binary images stay separate from searchable body text. Source bytes are preserved
by the caller; this module deliberately returns plain text, never trusted HTML.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import re
from copy import deepcopy
from urllib.parse import urlsplit

from lxml import etree, html
import trafilatura
from .language import detect_language
from .publication_date_extraction import extract_publication_date

PARSER_VERSION = "koohub-html-1.5"
MAX_HTML_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 80
MAX_IMAGE_TOTAL_BYTES = 32 * 1024 * 1024
_SPACE = re.compile(r"\s+")
_DATA = re.compile(r"data:[^\s<>\"']{80,}", re.I)
_CONTENT = re.compile(r"(?:^|[-_\s])(?:article[-_]*body|article[-_]*content|post[-_]*content|entry[-_]*content|news[-_]*content|story[-_]*body|rich[-_]*media[-_]*content|content[-_]*body|pressrelease|press[-_]*release|content|article|story|正文)(?:$|[-_\s])", re.I)
_STRONG_CONTENT = re.compile(r"(?:^|[-_\s])(?:article[-_]*body|article[-_]*content|post[-_]*content|entry[-_]*content|story[-_]*body|rich[-_]*media[-_]*content|content[-_]*body|press[-_]+content|pressrelease)(?:$|[-_\s])", re.I)
_NOISE = re.compile(r"(?:^|[-_\s])(?:nav|navbar|menu|breadcrumb|sidebar|related|recommend|advert|advertisement|ads|cookie|share|social|comment|comments|pagination|toolbar|footer|newsletter|utility[-_]?bar)(?:$|[-_\s])", re.I)
_BLOCK = {"p", "div", "section", "article", "main", "li", "ul", "ol", "blockquote", "pre", "figure", "figcaption", "address", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "table"}


class ParseError(ValueError):
    """Input cannot be represented as a searchable HTML document."""


def _text(value: str | None) -> str:
    return _SPACE.sub(" ", _DATA.sub("[内嵌资源已分离]", value or "").replace("\u200b", "")).strip()


def _decode(raw: bytes, warnings: list[str]) -> str:
    # Offline snapshots frequently normalize to UTF-8 while retaining old meta
    # declarations. Strict UTF-8 wins when valid; legacy encodings remain supported.
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    declaration = re.search(br"charset\s*=\s*[\"']?\s*([a-zA-Z0-9_-]+)", raw[:8192], re.I)
    if declaration:
        charset = declaration.group(1).decode("ascii").lower()
        if charset in {"gb2312", "gbk"}:
            charset = "gb18030"
        try:
            return raw.decode(charset)
        except (UnicodeDecodeError, LookupError):
            warnings.append("页面声明的字符编码无效，已尝试自动识别。")
    from charset_normalizer import from_bytes
    detected = from_bytes(raw[:256 * 1024]).best()
    if detected is not None:
        try:
            return raw.decode(detected.encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    warnings.append("部分字符无法解码，已使用替代符；请对照原始文件检查。")
    return raw.decode("utf-8", errors="replace")


def _metadata(tree: html.HtmlElement) -> dict:
    values = {}
    for node in tree.xpath("//head/meta[@content]"):
        key = (node.get("property") or node.get("name") or node.get("itemprop") or "").lower()
        values.setdefault(key, _text(node.get("content")))
    title = values.get("og:title") or values.get("twitter:title") or _text(" ".join(tree.xpath("//title/text()")))
    author = next((values[k] for k in ("author", "article:author", "dc.creator", "byl") if values.get(k)), None)
    return {"title": title, "published_at": None, "author": author}


def _publication_source_url(tree, source_url):
    """Identify the original page, never derive a date from its URL.

    A local snapshot may retain an explicit canonical/og:url or a single saved
    "原文" link. Collection-wrapper dates are not publication evidence.
    """
    def valid(value):
        try:
            parts = urlsplit(value or '')
            return parts.scheme.lower() in ('http', 'https') and bool(parts.hostname) and not parts.username and not parts.password
        except (TypeError, ValueError):
            return False

    if source_url:
        return source_url if valid(source_url) else ''
    urls = tree.xpath('//head/link[contains(concat(" ",translate(normalize-space(@rel),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")," ")," canonical ")]/@href')
    urls += tree.xpath('//head/meta[translate(@property,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="og:url"]/@content')
    for wrapper in tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," dsh-meta ")]'):
        if '原文' in wrapper.text_content():
            urls.extend(wrapper.xpath('./a/@href'))
    urls = {value.strip() for value in urls if valid(value)}
    return next(iter(urls)) if len(urls) == 1 else ''


def _own_prose_runs(node: html.HtmlElement):
    """Yield the node's paragraph-like flow without nested block content.

    Legacy HTML often uses div + br + inline markup instead of p elements.
    Matching only leaf divs then maps an otherwise complete extraction to a
    small gallery of captions. Block and br boundaries keep matches local and
    prevent a layout wrapper from absorbing unrelated descendant prose.
    """
    buffer = [node.text or ""]

    def flush():
        value = _text("".join(buffer))
        buffer.clear()
        return value

    def inline(child):
        if child.tag == "br" or child.tag in _BLOCK:
            yield flush()
        elif child.tag != "img":
            buffer.append(child.text or "")
            for descendant in child:
                yield from inline(descendant)
        buffer.append(child.tail or "")

    for child in node:
        yield from inline(child)
    yield flush()


def _choose_content(tree: html.HtmlElement) -> tuple[html.HtmlElement, str]:
    body = next(iter(tree.xpath("//body")), tree)
    # Scholarly abstract pages contain large download/recommendation interfaces
    # outside the actual title/author/abstract container. Keep the bibliographic
    # record, and explicitly identify this as an abstract rather than full text.
    for abstract in body.xpath(".//blockquote[contains(concat(' ',normalize-space(@class),' '),' abstract ')]"):
        parent = abstract.getparent()
        if parent is not None and parent.xpath("./h1|./h2") and len(_text(abstract.text_content())) >= 40:
            return parent, "scholarly-abstract"
    candidates = [body]
    for node in body.iterdescendants():
        if not isinstance(node.tag, str):
            continue
        identity = " ".join([node.get("id", ""), node.get("class", ""), node.get("itemprop", "")])
        if node.tag in {"article", "main"} or node.get("role") == "main" or _CONTENT.search(identity) or node.get("itemprop") == "articleBody":
            candidates.append(node)

    def score(node):
        txt = _text(node.text_content())
        size = len(txt)
        linked = sum(len(_text(a.text_content())) for a in node.iter("a"))
        density = min(linked / max(size, 1), 1)
        boost = 1.12 if node.tag == "article" or node.get("itemprop") == "articleBody" else 1
        return size * (1 - density * .85) * boost

    explicit = [n for n in candidates if (n.get("itemprop") == "articleBody" or _STRONG_CONTENT.search(" ".join([n.get("id", ""), n.get("class", "")]))) and len(_text(n.text_content())) >= 40]
    if explicit:
        strongest = max(score(n) for n in explicit)
        tight = [n for n in explicit if score(n) >= strongest * .82]
        return min(tight, key=lambda n: (len(_text(n.text_content())), -len(list(n.iterancestors())))), "semantic-dom"
    best = max(candidates, key=score)
    best_score = score(best)
    # Prefer a tight content container with nearly all of the useful text.
    adequate = [n for n in candidates if score(n) >= best_score * .82]
    if adequate:
        best = min(adequate, key=lambda n: (len(_text(n.text_content())), -len(list(n.iterancestors()))))
    if best is not body:
        return best, "semantic-dom"

    # For pages without semantic containers, use trafilatura as an independent
    #正文 signal, and map its paragraphs back to original DOM ancestors.
    extracted = trafilatura.extract(deepcopy(tree), output_format="txt", include_comments=False, include_tables=True, favor_recall=True, deduplicate=False)
    if extracted:
        reference = _text(extracted)
        matched = [n for n in body.xpath(".//p|.//div")
                   if any(len(run) >= 40 and run in reference for run in _own_prose_runs(n))]
        if matched:
            ancestors = [matched[0], *matched[0].iterancestors()]
            for candidate in ancestors:
                if all(candidate is n or candidate in n.iterancestors() for n in matched):
                    if candidate is not body and candidate.tag not in {"html", "p"}:
                        return candidate, "trafilatura-dom"
                    break
    return body, "clean-dom-fallback"


def _image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def parse_html(raw: bytes, source_url: str = "", title_hint: str = "") -> dict:
    if not raw or len(raw) > MAX_HTML_BYTES:
        raise ParseError("HTML 文件为空或超过 64 MiB 上限。")
    warnings: list[str] = []
    decoded = _decode(raw, warnings)
    if not re.search(r"<(?:!doctype\s+html|html|head|body|title|article|main|div|p|h[1-6]|table)\b", decoded[:65536], re.I):
        raise ParseError("文件不是可识别的 HTML 文档。")
    try:
        tree = html.document_fromstring(decoded, parser=html.HTMLParser(no_network=True, remove_comments=True, huge_tree=True))
    except (etree.ParserError, ValueError) as exc:
        raise ParseError("HTML 解析失败。") from exc
    metadata = _metadata(tree)
    publication_source_url = _publication_source_url(tree, source_url)
    language_hint = tree.get('lang') or tree.get('{http://www.w3.org/XML/1998/namespace}lang') or ''
    if not language_hint:
        language_hint = next(iter(tree.xpath("//meta[translate(@http-equiv,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='content-language']/@content")), '')
    root_tree = tree.getroottree()
    paths = {node: root_tree.getpath(node) for node in tree.iter() if isinstance(node.tag, str)}
    # Retain only safe inline raster images; no URI or source attribute is ever
    # passed through to the reader. Strip binary attributes before extraction.
    image_sources = {}
    for node in tree.iter("img"):
        image_sources[node] = (node.get("src") or "", _text(node.get("alt")))
    # Layout class names such as no-sidebar/flex-wrap-footer describe the whole
    # page, not a removable sidebar/footer. Malformed offline HTML may also make
    # the parser nest the real article inside an unclosed navigation element.
    # Never discard semantic content or its ancestors on a noise-name heuristic.
    protected = {tree}
    for node in tree.iter():
        if not isinstance(node.tag, str):
            continue
        identity = " ".join([node.get("id", ""), node.get("class", "")])
        if node.tag in {"html", "body", "article", "main"} or node.get("role") == "main" or node.get("itemprop") == "articleBody" or _STRONG_CONTENT.search(identity):
            protected.add(node)
            protected.update(node.iterancestors())
    for node in list(tree.iter()):
        if not isinstance(node.tag, str):
            continue
        identity = " ".join([node.get("id", ""), node.get("class", "")])
        style = node.get("style", "").lower()
        # aria-hidden only affects accessibility, not whether content exists or is
        # visible. Archived article paragraphs using it must not silently vanish.
        unsafe = node.tag in {"script", "style", "head", "iframe", "object", "embed", "svg", "canvas", "form", "button", "input", "select", "textarea", "template", "noscript"}
        noise = node.tag in {"nav", "footer", "aside"} or _NOISE.search(identity)
        hidden_style = re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style)
        archived_intro = bool(hidden_style and re.search(r"(?:^|[-_\s])opening[-_]paragraph(?:$|[-_\s])", identity) and len(_text(node.text_content())) >= 80 and any(a.tag in {"main", "article"} for a in node.iterancestors()))
        if archived_intro:
            warnings.append("已保留原始 HTML 中以隐藏样式保存的正文介绍；请对照原文件检查。")
        if unsafe or node.get("hidden") is not None or (hidden_style and not archived_intro) or (noise and node not in protected):
            if node.getparent() is not None:
                node.drop_tree()
            continue
        for key in list(node.attrib):
            if key not in {"id", "class", "itemprop", "role", "rowspan", "colspan", "alt"}:
                del node.attrib[key]
    content, method = _choose_content(tree)
    if method == "clean-dom-fallback":
        warnings.append("页面缺少明确正文结构，使用清理后的正文回退提取；建议检查导航或推荐内容。")
    if method == "scholarly-abstract":
        warnings.append("该文件为论文摘要页，已保存摘要和书目信息；不代表论文全文。")
        for node in list(content.iterdescendants()):
            identity = " ".join([node.get("id", ""), node.get("class", "")])
            if re.search(r"(?:^|[-_\s])download(?:$|[-_\s])", identity):
                node.drop_tree()
    if content.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' kb-offline-placeholder ')]"):
        warnings.append("原始离线快照包含未保存的嵌入媒体；本次仅解析现有 HTML 内容。")

    blocks: list[dict] = []
    tables: list[list[list[str]]] = []

    def emit(value: str, node, kind="paragraph", table=None):
        value = _text(value)
        if not value:
            return
        locator = {"type": "html", "xpath": paths.get(node, "/html/body"), "ordinal": len(blocks)}
        block = {"ordinal": len(blocks), "kind": kind, "text": value, "locator": locator}
        if table is not None:
            block["table"] = table
        blocks.append(block)

    def walk(node):
        if node.tag == "table":
            rows = [[_text(cell.text_content()) for cell in row if cell.tag in {"td", "th"}] for row in node.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")]
            rows = [row for row in rows if row]
            if rows:
                tables.append(rows)
                emit("\n".join(" | ".join(row) for row in rows), node, "table", rows)
            return
        kind = "heading" if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"} else "list_item" if node.tag in {"li", "dt", "dd"} else "code" if node.tag == "pre" else "paragraph"
        buffer = [node.text or ""]

        def flush():
            emit("".join(buffer), node, kind)
            buffer.clear()

        def inline(child):
            if child.tag == "br":
                flush()
            elif child.tag in _BLOCK:
                flush()
                walk(child)
            elif child.tag != "img":
                buffer.append(child.text or "")
                for descendant in child:
                    inline(descendant)
            buffer.append(child.tail or "")

        for child in node:
            inline(child)
        flush()

    walk(content)
    if not blocks:
        raise ParseError("未提取到可检索正文；文件可能仅有图片、脚本或需要登录。")
    images = []
    image_bytes = 0
    for node in content.iter("img"):
        src, alt = image_sources.get(node, ("", ""))
        if not src.lower().startswith("data:"):
            continue
        # Some archived sites serialize a comma-separated raster MIME allowlist
        # into a data URL. Accept it only when every entry is a supported raster
        # type; the stored MIME still comes from the binary signature below.
        match = re.match(r"data:image/(?:png|jpe?g|gif|webp)(?:,image/(?:png|jpe?g|gif|webp|wbmp))*;base64,", src, re.I)
        if not match:
            warnings.append("已忽略不支持的内嵌图片格式。")
            continue
        if len(images) >= MAX_IMAGES or image_bytes >= MAX_IMAGE_TOTAL_BYTES:
            warnings.append("内嵌图片达到数量或总大小上限，剩余图片保留在原始文件中。")
            break
        payload = src[match.end():]
        if len(payload) > (MAX_IMAGE_BYTES * 4 // 3 + 1024):
            warnings.append("已跳过超过 8 MiB 的内嵌图片；原始文件保留完整资源。")
            continue
        try:
            data = base64.b64decode(re.sub(r"\s", "", payload), validate=True)
        except (binascii.Error, ValueError):
            warnings.append("已跳过无法解码的内嵌图片。")
            continue
        mime = _image_type(data)
        if mime is None:
            warnings.append("已跳过图片声明与文件内容不匹配的内嵌资源。")
            continue
        if len(data) > MAX_IMAGE_BYTES or image_bytes + len(data) > MAX_IMAGE_TOTAL_BYTES:
            warnings.append("已跳过超出大小限制的内嵌图片；原始文件保留完整资源。")
            continue
        images.append({"mime": mime, "data": data, "alt": alt, "locator": {"type": "html", "xpath": paths[node], "ordinal": len(images)}})
        image_bytes += len(data)
    body = [b["text"] for b in blocks]
    all_text = "\n".join(b['text'] for b in blocks if b['kind'] not in ('code', 'heading', 'title'))
    language = detect_language(all_text, language_hint)
    title = metadata["title"] or _text(title_hint) or next((b["text"] for b in blocks if b["kind"] == "heading"), "未命名文档")
    # Only new parsing uses the conservative snapshot rule. Keep the original
    # body parser version so identical-byte imports still reuse saved revisions.
    metadata['published_at'] = extract_publication_date(decoded, title=title, source_url=publication_source_url)['date']
    if language == 'und' and not all_text.strip():
        language = detect_language(title, language_hint)
    return {**metadata, "title": title, "language": language, "body": body, "blocks": blocks, "tables": tables, "images": images, "warnings": list(dict.fromkeys(warnings)), "parser_version": PARSER_VERSION, "extraction_method": method}
