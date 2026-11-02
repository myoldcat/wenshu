"""Static, offline view of an immutable HTML snapshot.

This is deliberately separate from article extraction: original layout and inline
styles survive, but active HTML and navigation do not. The response CSP sandbox
is mandatory even when this function has removed dangerous markup.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from lxml import etree, html

from .parser import _decode, _image_type, MAX_IMAGE_BYTES, MAX_IMAGE_TOTAL_BYTES


PREVIEW_CONTENT_CSP = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; font-src 'none'; frame-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)
PREVIEW_CSP = "sandbox; " + PREVIEW_CONTENT_CSP + "; frame-ancestors 'self'"
PREVIEW_HEADERS = {
    'Content-Security-Policy': PREVIEW_CSP,
    'Referrer-Policy': 'no-referrer',
    'X-DNS-Prefetch-Control': 'off',
    'Content-Disposition': 'inline',
}

_DROP = {
    'script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet',
    'portal', 'fencedframe', 'base', 'link', 'meta', 'template', 'svg', 'math',
    'canvas', 'input', 'button', 'select', 'textarea', 'option', 'datalist',
    'audio', 'video', 'source', 'track', 'bgsound',
}
_TAGS = {
    'html', 'head', 'body', 'title', 'style', 'article', 'main', 'section',
    'header', 'footer', 'nav', 'aside', 'div', 'span', 'p', 'br', 'hr', 'wbr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'abbr', 'address', 'b', 'bdi',
    'bdo', 'blockquote', 'cite', 'code', 'data', 'dd', 'del', 'dfn', 'dl', 'dt',
    'em', 'figcaption', 'figure', 'font', 'i', 'ins', 'kbd', 'li', 'mark',
    'ol', 'pre', 'q', 'rp', 'rt', 'ruby', 's', 'samp', 'small', 'strong',
    'sub', 'sup', 'time', 'u', 'ul', 'var', 'table', 'caption', 'col',
    'colgroup', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'img', 'center',
}
_ATTRS = {
    'id', 'class', 'style', 'title', 'lang', 'dir', 'role', 'alt', 'width',
    'height', 'align', 'valign', 'border', 'cellpadding', 'cellspacing',
    'colspan', 'rowspan', 'scope', 'headers', 'start', 'reversed', 'type',
    'color', 'face', 'size', 'datetime', 'hidden',
}
_RASTER = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


def _saved_images(assets: list[dict]) -> dict[str, str]:
    """Only caller-selected revision assets can become image sources."""
    images = {}
    total = 0
    for asset in assets:
        if asset['mime'] not in _RASTER:
            continue
        path = Path(asset['path'])
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            continue
        data = path.read_bytes()
        mime = _image_type(data)
        if mime != asset['mime'] or total + len(data) > MAX_IMAGE_TOTAL_BYTES:
            continue
        try:
            locator = json.loads(asset['locator_json'])
        except (TypeError, ValueError):
            continue
        xpath = locator.get('xpath') if isinstance(locator, dict) else None
        if not isinstance(xpath, str) or not xpath.startswith('/html/'):
            continue
        total += len(data)
        images[xpath] = f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')
    return images


def render_preview(raw: bytes, assets: list[dict]) -> str:
    """Return inert HTML; do not serve it without PREVIEW_HEADERS."""
    decoded = _decode(raw, [])
    try:
        tree = html.document_fromstring(decoded, parser=html.HTMLParser(
            no_network=True, remove_comments=True, remove_pis=True, huge_tree=True,
        ))
    except (etree.ParserError, ValueError) as exc:
        raise ValueError('原始 HTML 文件无法预览，请下载后查看') from exc
    original_tree = tree.getroottree()
    paths = {node: original_tree.getpath(node) for node in tree.iter()
             if isinstance(node.tag, str)}
    images = _saved_images(assets)
    for node in list(tree.iter()):
        if not isinstance(node.tag, str):
            continue
        tag = node.tag.lower()
        if tag in _DROP:
            if node.getparent() is not None:
                node.drop_tree()
            continue
        # Remove unknown/custom elements and form wrappers, preserving useful
        # prose. Do not retain their attributes or browser-specific behavior.
        if tag not in _TAGS:
            if node.getparent() is not None:
                node.drop_tag()
            continue
        for attribute in list(node.attrib):
            if attribute not in _ATTRS and not attribute.startswith('aria-'):
                del node.attrib[attribute]
        if tag == 'img':
            saved = images.get(paths[node])
            if saved:
                node.set('src', saved)
            else:
                # Do not fetch original remote, local-file or unsaved data URLs.
                node.set('data-preview-unavailable', 'true')
    head = next(iter(tree.xpath('/html/head')), None)
    if head is None:
        head = html.Element('head')
        tree.insert(0, head)
    # The API response is sandboxed by its header. A first-child meta policy
    # also protects callers that verify the response and mount it as iframe
    # srcdoc; those frames must separately retain sandbox="".
    policy = html.Element('meta', {'http-equiv':'Content-Security-Policy',
                                  'content':PREVIEW_CONTENT_CSP})
    head.insert(0, policy)
    charset = html.Element('meta', charset='utf-8')
    head.insert(1, charset)
    # A fallback canvas only; subsequent original CSS retains precedence.
    fallback = html.Element('style')
    fallback.text = ':root{color-scheme:light}html{background:#fff;color:#222}'
    head.insert(2, fallback)
    return '<!doctype html>\n' + html.tostring(tree, encoding='unicode', method='html')
