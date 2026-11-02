import base64
import gzip
from pathlib import Path

import httpx
import pytest

from server.parser import ParseError, parse_html
from server.fetcher import FetchError, fetch_html


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD2sAAAAASUVORK5CYII=")


def page(body, head="<title>测试文档</title>"):
    return f"<!doctype html><html><head><meta charset='utf-8'>{head}</head><body>{body}</body></html>".encode()


def public_dns(host, port):
    return ["93.184.216.34"]


def test_clean_body_metadata_original_paths_and_order():
    result = parse_html(page("""<nav><p>导航不能入库</p></nav>
        <article><h1>正文标题</h1><p>保留<strong>重要</strong>中文与 English。</p>
        <script>stealSecret()</script><iframe src='http://127.0.0.1'>内部页面</iframe>
        <p hidden>隐藏内容</p><div class='related'>推荐内容</div>
        <p>末尾段落<br>换行的内容。</p></article><footer>页脚条款</footer>""",
        "<title>测试文档</title><meta property='article:published_time' content='2026-10-22'><meta name='author' content='作者甲'>"))
    text = "\n".join(result["body"])
    for forbidden in ["导航", "stealSecret", "内部页面", "隐藏内容", "推荐内容", "页脚"]:
        assert forbidden not in text
    assert result["body"] == ["正文标题", "保留重要中文与 English。", "末尾段落", "换行的内容。"]
    assert result["published_at"] == "2026-10-22"
    assert result["author"] == "作者甲"
    assert result["blocks"][1]["locator"]["xpath"] == "/html/body/article/p[1]"
    assert [b["ordinal"] for b in result["blocks"]] == list(range(4))
    assert result["language"] == "zh"
    assert parse_html(page("<p>中文文档 with substantial English text.</p>"))["language"] == "foreign"


def test_legacy_encoding_gbk_and_utf8_snapshot_override():
    legacy = "<html><head><meta charset='gb2312'><title>中文标题</title></head><body><p>知识检索与数据治理。</p></body></html>"
    assert parse_html(legacy.encode("gbk"))["body"] == ["知识检索与数据治理。"]
    assert parse_html(legacy.encode("utf-8"))["title"] == "中文标题"


def test_table_not_truncated_and_nested_inline_text_is_preserved():
    rows = "".join(f"<tr><td>记录{i}</td><td>完整<strong>数值</strong>{i}</td></tr>" for i in range(200))
    result = parse_html(page(f"<article><p>表格说明。</p><table><thead><tr><th>名称</th><th>值</th></tr></thead><tbody>{rows}</tbody></table><p>表格末尾。</p></article>"))
    assert len(result["tables"][0]) == 201
    assert result["tables"][0][-1] == ["记录199", "完整数值199"]
    assert result["blocks"][1]["kind"] == "table"
    assert result["blocks"][1]["table"] == result["tables"][0]
    assert result["body"][-1] == "表格末尾。"


def test_inline_images_separate_from_index_and_only_in_body(monkeypatch):
    encoded = base64.b64encode(PNG).decode()
    result = parse_html(page(f"<aside><img src='data:image/png;base64,{encoded}'></aside><article><p>文档图片说明。</p><img alt='架构图' src='data:image/png;base64,{encoded}'><img src='https://example.org/private.png'><img src='data:image/svg+xml;base64,PHN2Zy8+'><img src='data:image/png;base64,YWJj'></article>"))
    assert len(result["images"]) == 1
    assert result["images"][0]["data"] == PNG
    assert result["images"][0]["mime"] == "image/png"
    assert result["images"][0]["alt"] == "架构图"
    assert encoded not in str(result["body"])
    assert len(result["warnings"]) == 2


def test_image_count_and_size_limit_warn(monkeypatch):
    import server.parser as parser
    encoded = base64.b64encode(PNG).decode()
    monkeypatch.setattr(parser, "MAX_IMAGES", 1)
    result = parse_html(page(f"<article><p>测试图片限制。</p><img src='data:image/png;base64,{encoded}'><img src='data:image/png;base64,{encoded}'></article>"))
    assert len(result["images"]) == 1
    assert any("上限" in message for message in result["warnings"])
    monkeypatch.setattr(parser, "MAX_IMAGE_BYTES", 8)
    result = parse_html(page(f"<article><p>测试图片限制。</p><img src='data:image/png;base64,{encoded}'></article>"))
    assert not result["images"]
    assert any("大小限制" in message for message in result["warnings"])


def test_archived_raster_mime_list_is_checked_against_signature():
    encoded = base64.b64encode(PNG).decode()
    result = parse_html(page(f"<article><p>Archive image text.</p><img src='data:image/png,image/jpg,image/jpeg,image/wbmp,image/gif;base64,{encoded}'></article>"))
    assert len(result["images"]) == 1
    assert result["images"][0]["mime"] == "image/png"


@pytest.mark.parametrize("raw", [b"", b"%PDF-1.7", b"not html", b"<html><body><script>only_js()</script></body></html>"])
def test_invalid_or_script_only_pages_fail_honestly(raw):
    with pytest.raises(ParseError):
        parse_html(raw)


def test_large_embedded_font_does_not_pollute_body():
    result = parse_html(page("<article><p>仍保留完整正文。</p><style>@font-face{src:url(data:font/woff2;base64," + "A" * 2_000_000 + ")}</style><p>最终段落。</p></article>"))
    assert result["body"] == ["仍保留完整正文。", "最终段落。"]


def test_article_body_beats_long_boilerplate_and_aria_hidden_is_not_discarded():
    raw = page("<div>" + "Large site announcement. " * 100 + "</div><article><h1>Title</h1><div id='article-body'><p>First real paragraph contains substantial useful information.</p><p aria-hidden='true'>Second real paragraph is already present in the archived document.</p><div class='newsletter'>Subscribe to the newsletter.</div></div></article>")
    result = parse_html(raw)
    assert len(result["body"]) == 2
    assert result["body"][1].startswith("Second real paragraph")
    assert result["extraction_method"] == "semantic-dom"


@pytest.mark.parametrize("container_id", ["ContentBody", "content-body", "content_body"])
def test_news_content_body_excludes_navigation_and_recommendations(container_id):
    # Structure from a failed archived-news summary: the article is shorter
    # than the surrounding non-semantic navigation/recommendation containers.
    # Selecting the whole page yields a first AI chunk consisting only of menus.
    paragraphs = [
        "市场担心美国将对铜征收关税，引发库存格局变化。如果关税最终没有落地，铜价可能下跌。",
        "随着选举临近，投资者仍在评估贸易政策的变化，原文没有给出确定的关税实施日期。",
        "分析师给出了不同情形下的价格预测，这些预测包含条件，不能改写为已经发生的结果。",
    ]
    menus = "".join(f"<li>网站导航{i}</li>" for i in range(23))
    recommendations = "".join(f"<li>相关推荐{i}：与本文无关的其他市场消息和网站推广内容。</li>" for i in range(40))
    article = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    raw = page(f"<div class='main'><div class='headwrap'><div class='globalNav'><ul>{menus}</ul></div></div>"
               f"<div class='contentwrap'><div class='contentbox'><div class='mainleft'><div class='zwinfos'>"
               f"<div id='{container_id}' class='txtinfos'>{article}</div></div></div>"
               f"<div class='mainright'><ul>{recommendations}</ul></div></div></div></div>")
    result = parse_html(raw)
    assert result["body"] == paragraphs
    assert result["extraction_method"] == "semantic-dom"
    assert result["blocks"][0]["locator"]["xpath"] == "/html/body/div/div[2]/div/div[1]/div/div/p[1]"
    assert [block["ordinal"] for block in result["blocks"]] == [0, 1, 2]


@pytest.mark.parametrize("layout", ["no-sidebar", "layout-sidebar", "layout-one-sidebar layout-sidebar-second"])
def test_layout_class_on_body_does_not_erase_article(layout):
    raw = f"<html><body class='{layout}'><nav>Navigation only</nav><article><p>This is the full article content and it must survive layout class cleanup.</p></article></body></html>".encode()
    result = parse_html(raw)
    assert result["body"] == ["This is the full article content and it must survive layout class cleanup."]


@pytest.mark.parametrize("wrapper", ["<div class='flex-wrap-footer'><main>", "<div id='left-menu'><main>"])
def test_layout_wrapper_with_content_landmark_is_preserved(wrapper):
    result = parse_html(page(wrapper + "<div class='press-content'><p>Legislative response contains the complete answer with enough substantive text.</p></div></main></div>"))
    assert result["body"] == ["Legislative response contains the complete answer with enough substantive text."]


def test_malformed_nav_parent_preserves_nested_article_and_original_locator():
    raw = page("<header><nav class='main-nav'><ul><li class='menu-item'>Navigation label<article><div class='c-article__body'><div class='c-article__content'><p>Actual document text survives an unclosed navigation list in the source archive.</p><p>The last substantive paragraph is also retained.</p></div><aside><article><h3>A recommended article</h3></article></aside></div></article></li></ul></nav></header>")
    result = parse_html(raw)
    assert len(result["body"]) == 2
    assert "recommended" not in " ".join(result["body"])
    assert result["blocks"][0]["locator"]["xpath"].startswith("/html/body/header/nav/ul/li/article/")


def test_scholarly_abstract_is_complete_and_labelled_without_download_widgets():
    abstract = "The abstract describes the entire scientific question, methodology, assumptions, and findings. " * 8
    raw = page(f"<div class='flex-wrap-footer'><main><div id='content'><div id='abs'><h1>Research title</h1><div class='authors'>Authors: Example Scientist</div><div id='download-button-info'>Download PDF</div><blockquote class='abstract mathjax'>{abstract}</blockquote><div class='metatable'><table><tr><td>Subjects</td><td>Machine Learning</td></tr></table></div></div><div>arXivLabs recommenders and account controls {'widgets ' * 300}</div></div></main></div>")
    result = parse_html(raw)
    text = " ".join(result["body"])
    assert abstract.strip() in text
    assert "Download PDF" not in text
    assert "arXivLabs" not in text
    assert result["extraction_method"] == "scholarly-abstract"
    assert any("不代表论文全文" in warning for warning in result["warnings"])


def test_archived_hidden_report_intro_retained_with_source_gap_warning():
    paragraph = "The archived report introduction explains the study methodology and coverage, while the PDF itself is absent."
    result = parse_html(page(f"<main><h1>Report</h1><div id='opening-paragraph' style='display: none'><p>{paragraph}</p></div><div class='kb-offline-placeholder'>[object 媒体内容未随离线快照保存]</div></main>"))
    assert paragraph in result["body"]
    assert any("隐藏样式" in warning for warning in result["warnings"])
    assert any("未保存的嵌入媒体" in warning for warning in result["warnings"])


def test_mixed_div_press_release_keeps_prose_before_nested_gallery_and_table():
    raw = (Path(__file__).parent / "fixtures" / "mixed-div-press-release.html").read_bytes()
    result = parse_html(raw)
    text = "\n".join(result["body"])
    assert "外洩事件合共影響9,045名資料當事人" in text
    assert "黑客利用該漏洞成功竊取服務供應商的帳戶憑證" in text
    assert "伺服器的遠端存取欠缺用戶身分認證措施；" in result["body"]
    assert "－完－" in result["body"]
    assert result["tables"] == [[["資料類別", "狀態"], ["會員資料", "已通知受影響人士"]]]
    assert sum(block["kind"] == "table" for block in result["blocks"]) == 1
    assert len(result["blocks"]) >= 12
    assert result["extraction_method"] == "trafilatura-dom"
    assert result["blocks"][1]["locator"]["xpath"] == "/html/body/div/div/div/div/div/div/div/div"
    assert [block["ordinal"] for block in result["blocks"]] == list(range(len(result["blocks"])))


def test_mixed_div_mapping_does_not_promote_layout_navigation_or_sidebar(monkeypatch):
    # The independent extractor identifies the story; mixed-flow matching must
    # not match the enclosing layout merely through descendant story text.
    first = "The investigation found an outdated remote access application with a known security vulnerability."
    second = "The incident affected 9,045 data subjects, and the organisation notified the affected individuals."
    caption = "The privacy commissioner presents the investigation report and explains the protection measures."
    monkeypatch.setattr("server.parser.trafilatura.extract", lambda *args, **kwargs: "\n".join([first, second, caption]))
    navigation = "".join(f"<li><a href='/other-{i}'>Unrelated navigation headline number {i} about another news story.</a></li>" for i in range(40))
    raw = page(f"<div><div><ul>{navigation}</ul></div><div><strong>{first}</strong><br>{second}<br>"
               f"<div><div>{caption}</div></div></div><div class='sidebar'><p>Sidebar promotion must not be selected.</p></div></div>")
    result = parse_html(raw)
    assert result["body"] == [first, second, caption]
    assert result["extraction_method"] == "trafilatura-dom"
    assert result["blocks"][0]["locator"]["xpath"] == "/html/body/div/div[2]"


def test_fetch_pins_ip_and_preserves_host_sni_without_environment_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=page("<p>正文</p>"))
    result = fetch_html("https://example.org/document#position", resolver=public_dns, transport=httpx.MockTransport(handler))
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "example.org"
    assert requests[0].extensions["sni_hostname"] == "example.org"
    assert result["final_url"] == "https://example.org/document"
    assert result["data"].startswith(b"<!doctype")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1", "http://[::1]", "http://169.254.169.254", "http://10.0.0.1", "http://localhost", "http://[::ffff:127.0.0.1]", "http://user:secret@example.org", "http://example.org\\@127.0.0.1", "http://192.0.2.1", "http://100.64.0.1"])
def test_private_and_invalid_targets_blocked_before_connect(url):
    with pytest.raises(FetchError):
        fetch_html(url, resolver=public_dns, transport=httpx.MockTransport(lambda req: pytest.fail("must not connect")))


def test_mixed_dns_answer_rejected():
    with pytest.raises(FetchError, match="公网"):
        fetch_html("https://example.org", resolver=lambda h, p: ["93.184.216.34", "127.0.0.1"], transport=httpx.MockTransport(lambda req: pytest.fail("must not connect")))


def test_redirect_validated_and_relative_final_url_retained():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=page("<p>Final</p>"))
    result = fetch_html("https://example.org/start", resolver=public_dns, transport=httpx.MockTransport(handler))
    assert result["final_url"] == "https://example.org/final"
    assert len(calls) == 2


def test_redirect_to_private_host_blocked():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    with pytest.raises(FetchError, match="公网"):
        fetch_html("https://example.org/start", resolver=public_dns, transport=httpx.MockTransport(handler))
    assert len(calls) == 1


def test_dns_is_rechecked_on_same_hostname_redirect():
    resolutions = []
    requests = []
    def changing_dns(host, port):
        resolutions.append(host)
        return ["93.184.216.34"] if len(resolutions) == 1 else ["10.0.0.7"]
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "/next"})
    with pytest.raises(FetchError, match="公网"):
        fetch_html("https://example.org/start", resolver=changing_dns, transport=httpx.MockTransport(handler))
    assert resolutions == ["example.org", "example.org"]
    assert len(requests) == 1


def test_redirect_loop_rejected():
    with pytest.raises(FetchError, match="循环"):
        fetch_html("https://example.org/start", resolver=public_dns, transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "/start"})))


@pytest.mark.parametrize("headers,content", [({"content-type": "application/pdf"}, b"pdf"), ({"content-type": "text/html", "content-length": "2048"}, b"x"), ({"content-type": "text/html"}, b"x" * 1025), ({"content-type": "text/html", "content-encoding": "gzip"}, gzip.compress(b"x" * 2048)), ({"content-type": "text/html"}, b"")])
def test_type_and_declared_streamed_decompressed_size_limits(headers, content):
    with pytest.raises(FetchError):
        fetch_html("https://example.org", resolver=public_dns, transport=httpx.MockTransport(lambda r: httpx.Response(200, headers=headers, content=content)), max_bytes=1024)


def test_timeout_is_user_readable():
    def fail(request):
        raise httpx.ReadTimeout("offline test")
    with pytest.raises(FetchError, match="连接失败或超时"):
        fetch_html("https://example.org", resolver=public_dns, transport=httpx.MockTransport(fail))
