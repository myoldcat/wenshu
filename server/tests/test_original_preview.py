"""Immutable HTML previews stay offline, inert and separate from downloads."""
import base64

import pytest
from fastapi.testclient import TestClient
from lxml import html

from server.app import create_app
from server.worker import run_one


PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ4sAAAAASUVORK5CYII='
GIF = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
PROSE = '原始资料保留静态排版、表格和已保存的插图，阅读时可以与正文相互对照，版本之间的内容和资源必须独立。'


@pytest.fixture
def workbench(tmp_path):
    app = create_app(data_dir=tmp_path/'data')
    with TestClient(app) as client:
        yield client, app.state.store


def page(title='原始文件', extra='', head=''):
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>{head}'
            f'</head><body><article><h1>{title}</h1><p>{PROSE}</p>{extra}</article></body></html>').encode()


def publish(store, raw, url='https://example.org/article'):
    _, blob = store.put_blob(raw)
    job = store.queue('corpus', {'path':str(store.root/blob),'url':url}, '预览验收')
    assert run_one(store)
    result = store.job(job['id'])
    assert result['status'] == 'succeeded', result
    return result


def test_preview_keeps_static_layout_but_removes_active_markup_and_navigation(workbench):
    client, store = workbench
    head = '''<meta http-equiv="refresh" content="0;url=https://evil.example/redirect">
        <base href="https://evil.example/"><link rel="stylesheet" href="https://evil.example/style.css">
        <style>article{max-width:46em;margin:auto;color:#124534} @import url(https://evil.example/import.css);</style>
        <script>window.top.location='https://evil.example'</script>'''
    extra = '''<section class="original" style="padding:12px;background:url(https://evil.example/a.png)">
        <a id="saved-anchor" href="https://evil.example" ping="/api/health" target="_top">保留链接文字</a>
        <form action="/api/jobs" method="post"><p>表单中的说明文字</p><input name="secret"><button>提交</button></form>
        <iframe src="/api/health" srcdoc="<script>evil()</script>"></iframe>
        <object data="https://evil.example/a"><p>嵌入对象</p></object>
        <svg onload="evil()"><a href="https://evil.example">SVG</a></svg>
        <math><mtext><script>evil()</script></mtext></math>
        <img src="https://evil.example/pixel" srcset="/api/health 2x" onerror="evil()">
        <x-unknown onclick="evil()"><p>自定义元素中的正文</p></x-unknown>
        <table class="original-table"><tr><th>栏目</th><td style="text-align:right">内容</td></tr></table></section>'''
    job = publish(store, page(extra=extra, head=head))
    document = store.detail(job['source_id'])
    response = client.get(document['previewUrl'])
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['content-disposition'] == 'inline'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-dns-prefetch-control'] == 'off'
    csp = response.headers['content-security-policy']
    assert csp.startswith("sandbox;") and 'allow-scripts' not in csp and 'allow-same-origin' not in csp
    for directive in ("default-src 'none'", "script-src 'none'", "style-src 'unsafe-inline'",
                      "img-src data:", "font-src 'none'", "connect-src 'none'", "frame-src 'none'",
                      "base-uri 'none'", "form-action 'none'", "frame-ancestors 'self'"):
        assert directive in csp
    tree = html.fromstring(response.text)
    assert not tree.xpath('//script|//iframe|//form|//input|//button|//object|//svg|//math|//base|//link|//x-unknown')
    assert len(tree.xpath('//meta')) == 2 and tree.xpath('//meta/@charset') == ['utf-8']
    policy = tree.xpath('/html/head/*')[0]
    assert policy.tag == 'meta' and policy.get('http-equiv') == 'Content-Security-Policy'
    assert "default-src 'none'" in policy.get('content') and "img-src data:" in policy.get('content')
    assert "script-src 'none'" in policy.get('content') and "form-action 'none'" in policy.get('content')
    assert 'http-equiv="refresh"' not in response.text
    assert not tree.xpath('//@href|//@srcset|//@srcdoc|//@action|//@ping|//@target|//@onerror|//@onclick|//@onload')
    assert not tree.xpath('//img/@src')
    assert '保留链接文字' in tree.text_content()
    assert '表单中的说明文字' in tree.text_content()
    assert '自定义元素中的正文' in tree.text_content()
    assert tree.xpath('//section/@class') == ['original']
    assert tree.xpath('//td/@style') == ['text-align:right']
    assert any('max-width:46em' in css for css in tree.xpath('//style/text()'))
    # Original CSS survives for layout; CSP denies its external imports, images
    # and fonts before a request is made. No preview code resolves remote URLs.
    assert 'background:url(https://evil.example/a.png)' in tree.xpath('//section/@style')[0]


def test_saved_images_are_rewritten_by_xpath_and_do_not_cross_revisions(workbench):
    client, store = workbench
    first = publish(store, page('第一版', f'<img alt="旧版图" src="data:image/png;base64,{PNG}">'))
    second = publish(store, page('第二版', f'<img alt="新版图" src="data:image/gif;base64,{GIF}">'))
    third = publish(store, page('其他文档', f'<img alt="别篇图" src="data:image/png;base64,{PNG}">'), 'https://example.org/other')
    assert first['source_id'] == second['source_id']
    first_url = store.detail(first['source_id'], first['revision_id'])['previewUrl']
    second_url = store.detail(second['source_id'])['previewUrl']
    old = html.fromstring(client.get(first_url).text)
    new = html.fromstring(client.get(second_url).text)
    assert old.xpath('//img/@src') == ['data:image/png;base64,'+PNG]
    assert new.xpath('//img/@src') == ['data:image/gif;base64,'+GIF]
    assert '第一版' in old.text_content() and '第二版' not in old.text_content()
    assert '第二版' in new.text_content() and '第一版' not in new.text_content()
    assert base64.b64decode(old.xpath('//img/@src')[0].split(',',1)[1]).startswith(b'\x89PNG')
    for method in (client.get, client.head):
        denied = method(f'/api/documents/{third["source_id"]}/preview',params={'revision_id':first['revision_id']})
        assert denied.status_code == 404


def test_head_checks_existence_and_version_without_rendering(workbench,monkeypatch):
    import server.preview as preview_module
    client, store = workbench
    job = publish(store, page())
    document = store.detail(job['source_id'])

    def no_render(*args, **kwargs):
        raise AssertionError('HEAD must not parse HTML or encode image resources')

    monkeypatch.setattr(preview_module,'render_preview',no_render)
    response = client.head(document['previewUrl'])
    assert response.status_code == 200 and response.content == b''
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['content-security-policy'] == preview_module.PREVIEW_CSP
    assert client.head('/api/documents/missing/preview').status_code == 404
    assert client.head(document['previewUrl'].split('?')[0]+'?revision_id=missing').status_code == 404
    path,_ = store.original(job['source_id'])
    path.unlink()
    assert client.head(document['previewUrl']).status_code == 404


def test_preview_does_not_change_snapshot_download_or_original_bytes(workbench):
    client, store = workbench
    raw = page(head='<script>notTrusted()</script><style>h1{letter-spacing:.2em}</style>')
    job = publish(store, raw)
    document = store.detail(job['source_id'])
    original_url = f'/api/documents/{job["source_id"]}/original?revision_id={job["revision_id"]}'
    assert document['originalUrl'] == original_url
    preview = client.get(document['previewUrl'])
    assert preview.status_code == 200 and 'notTrusted' not in preview.text
    download = client.get(original_url)
    assert download.status_code == 200 and download.content == raw
    assert download.headers['content-type'] == 'application/octet-stream'
    assert download.headers['content-disposition'].startswith('attachment;')
    path,_ = store.original(job['source_id'])
    assert path.read_bytes() == raw


def test_preview_decodes_legacy_chinese_html_and_missing_saved_asset_is_offline(workbench):
    client, store = workbench
    raw = (f'<html><head><meta charset="gb2312"><title>编码验收</title></head>'
           f'<body><article><p>{PROSE}</p><img alt="已丢失的图片" src="data:image/png;base64,{PNG}">'
           '</article></body></html>').encode('gb18030')
    job = publish(store, raw)
    path, assets = store.preview_source(job['source_id'],include_assets=True)
    assert path.is_file() and len(assets) == 1
    assets[0]['path'].unlink()
    response = client.get(store.detail(job['source_id'])['previewUrl'])
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    assert PROSE in tree.text_content()
    assert tree.xpath('//img/@alt') == ['已丢失的图片']
    assert not tree.xpath('//img/@src')
