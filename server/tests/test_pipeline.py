"""Local import API -> worker -> immutable storage -> Chinese search integration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store
from server.worker import run_one


LOCAL = {"X-Wenshu-Client": "local"}
BODY_ONLY = "基层档案治理"


def html_page(title="地方工作记录", text=None, extra=""):
    text = text or (
        "基层档案治理需要保留材料来源与证据位置。"
        "每次调整都保留原始版本，并用完整正文检索帮助工作人员找到依据。"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body><nav>导航测试噪声</nav>"
        f"<article><h1>{title}</h1><p>{text}</p>{extra}</article>"
        "<footer>页脚测试噪声</footer></body></html>"
    ).encode("utf-8")


@pytest.fixture
def workbench(tmp_path):
    data = tmp_path / "data"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    app = create_app(data_dir=data)
    with TestClient(app) as client:
        yield client, app.state.store, corpus


def upload(client, raw=None, name="资料.html", headers=None):
    response = client.post(
        "/api/imports/files",
        files=[("files", (name, raw or html_page(), "text/html"))],
        headers=LOCAL | (headers or {}),
    )
    assert response.status_code == 202, response.text
    return response.json()["jobs"][0]


def process(store, job):
    assert run_one(store) is True
    result = store.job(job["id"])
    assert result["status"] == "succeeded", result
    return result


def publish_at_url(store, raw, url="https://example.org/source", **metadata):
    """Use the actual parser and worker while keeping all network traffic stubbed out."""
    _, blob = store.put_blob(raw)
    job = store.queue(
        "corpus", {"path": str(store.root / blob), "url": url, **metadata}, "样本文档"
    )
    return process(store, job)


def test_multipart_to_worker_body_search_locator_and_reopened_store(workbench):
    client, store, _ = workbench
    job = upload(client)
    assert job["status"] == "queued"
    assert client.get("/api/documents").json()["total"] == 0
    completed = process(store, job)
    assert run_one(store) is False

    response = client.get("/api/search", params={"q": BODY_ONLY})
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    hit = result["results"][0]
    assert BODY_ONLY not in hit["title"]
    assert BODY_ONLY in hit["snippet"]
    assert hit["id"] == completed["source_id"]

    detail = client.get(f"/api/documents/{hit['id']}").json()["document"]
    block = next(b for b in detail["blocks"] if b["id"] == hit["matchBlockId"])
    assert BODY_ONLY in block["text"]
    assert block["locator"]["xpath"] == "/html/body/article/p"
    assert "导航测试噪声" not in "\n".join(detail["body"])
    assert "页脚测试噪声" not in "\n".join(detail["body"])
    assert len(detail["blobHash"]) == 64
    assert detail["byteSize"] == len(html_page())

    reopened = Store(store.root)
    assert reopened.detail(hit["id"])["blocks"] == detail["blocks"]
    assert reopened.search(BODY_ONLY)["results"][0]["matchBlockId"] == block["id"]
    assert reopened.job(job["id"])["status"] == "succeeded"


def test_same_url_revisions_keep_old_blocks_and_deduplicate(workbench):
    _, store, _ = workbench
    old_raw = html_page("第一版", "旧版专属红豆杉档案使用原始证据编号，工作人员保留完整正文与来源位置，方便随后核实事实。")
    new_raw = html_page("第二版", "新版专属银杏树档案增加审核说明，工作人员使用最新资料完成分类，同时保留历史证据和完整出处。")
    first = publish_at_url(store, old_raw, "HTTPS://Example.ORG:443/source#old")
    old_detail = store.detail(first["source_id"])
    second = publish_at_url(store, new_raw, "https://example.org/source#new")
    assert first["source_id"] == second["source_id"]
    assert first["revision_id"] != second["revision_id"]
    current = store.detail(first["source_id"])
    assert current["revisionCount"] == 2
    assert current["revisionId"] == second["revision_id"]
    historical = store.detail(first["source_id"], first["revision_id"])
    assert historical["blocks"] == old_detail["blocks"]
    assert store.search("红豆杉")["total"] == 0
    assert store.search("银杏树")["total"] == 1

    repeated = publish_at_url(store, new_raw)
    assert repeated["duplicate"] is True
    assert repeated["revision_id"] == second["revision_id"]
    # Re-importing an old snapshot must not silently roll the active document back.
    older = publish_at_url(store, old_raw)
    assert older["duplicate"] is True
    assert older["revision_id"] == first["revision_id"]
    assert store.detail(first["source_id"])["revisionId"] == second["revision_id"]
    assert store.documents()["total"] == 1


def test_same_filename_different_contents_are_separate_and_identical_uploads_dedupe(workbench):
    client, store, _ = workbench
    first = process(store, upload(client, html_page("档案甲"), "同名.html"))
    second = process(store, upload(client, html_page("档案乙"), "同名.html"))
    assert first["source_id"] != second["source_id"]
    duplicate = process(store, upload(client, html_page("档案甲"), "另一个文件名.html"))
    assert duplicate["duplicate"] is True
    assert duplicate["source_id"] == first["source_id"]
    assert store.documents()["total"] == 2


def test_upload_idempotency_reuses_job_before_processing(workbench):
    client, store, _ = workbench
    header = {"Idempotency-Key": "single-submit"}
    first = upload(client, headers=header)
    repeated = upload(client, headers=header)
    assert first["id"] == repeated["id"]
    assert store.jobs()["counts"]["total"] == 1
    process(store, first)


def test_annotations_persist_across_store_reopen_and_new_revision(workbench):
    client, store, _ = workbench
    first = publish_at_url(store, html_page(), tags=["旧分类"])
    changes = {"favorite": True, "read": True, "selected": True, "tags": [" 治理研究 ", "治理研究", "本地资料"]}
    response = client.patch(f"/api/documents/{first['source_id']}", json=changes, headers=LOCAL)
    assert response.status_code == 200, response.text
    updated = response.json()["document"]
    assert updated["tags"] == ["治理研究", "本地资料"]
    reopened = Store(store.root)
    record = reopened.detail(first["source_id"])
    for flag in ("favorite", "read", "selected"):
        assert record[flag] is True
    assert record["tags"] == updated["tags"]
    publish_at_url(reopened, html_page("新版本记录"), tags=["自动分类"])
    record = reopened.detail(first["source_id"])
    assert record["tags"] == updated["tags"]
    assert all(record[flag] for flag in ("favorite", "read", "selected"))


def test_search_filters_pagination_and_literal_fts_queries(workbench):
    client, store, _ = workbench
    a = publish_at_url(store, html_page("A 文档"), "https://alpha.example/a", tags=["治理"])
    b = publish_at_url(store, html_page("B 文档"), "https://beta.example/b", tags=["技术"])
    c = process(store, upload(client, html_page("C 文档")))
    store.patch(a["source_id"], {"favorite": True, "read": True})

    def search(**params):
        response = client.get("/api/search", params={"q": BODY_ONLY, **params})
        assert response.status_code == 200, response.text
        return response.json()

    assert search()["total"] == 3
    assert [r["id"] for r in search(category="治理")["results"]] == [a["source_id"]]
    assert [r["id"] for r in search(source="beta.example")["results"]] == [b["source_id"]]
    assert [r["id"] for r in search(source="本地导入")["results"]] == [c["source_id"]]
    assert search(filter="favorite")["total"] == 1
    assert search(filter="unread")["total"] == 2
    assert search(since="9999-01-01")["total"] == 0
    assert search(category="治理", source="beta.example")["total"] == 0
    pages = [search(limit=1, offset=i, sort="title") for i in range(3)]
    assert all(page["total"] == 3 for page in pages)
    assert [page["results"][0]["title"] for page in pages] == ["A 文档", "B 文档", "C 文档"]
    assert search(limit=1, offset=3)["results"] == []
    for query in ('" OR nonexistent', '"; DROP TABLE sources; --', "NEAR(档案,10)", "title_tokens:secret"):
        assert search(q=query)["total"] == 0
    assert search(category="治理' OR 1=1 --")["total"] == 0
    assert search(source="alpha.example' OR 1=1 --")["total"] == 0
    assert store.documents()["total"] == 3


def test_api_query_and_patch_validation(workbench):
    client, store, _ = workbench
    doc = process(store, upload(client))
    for params in ({"limit": 0}, {"offset": -1}, {"limit": 501}, {"q": "x" * 501}):
        assert client.get("/api/search", params=params).status_code == 422
    assert client.patch(f"/api/documents/{doc['source_id']}", headers=LOCAL, json={"title": "越权改写"}).status_code == 422
    assert client.patch(f"/api/documents/{doc['source_id']}", headers=LOCAL, json={"tags": ["x" * 81]}).status_code == 422
    assert client.get("/api/documents/missing").status_code == 404


def test_failed_job_can_retry_and_does_not_publish_partial_document(workbench):
    client, store, _ = workbench
    job = upload(client, b"<html><body><p>too short</p></body></html>")
    assert run_one(store)
    failed = store.job(job["id"])
    assert failed["status"] == "failed"
    assert failed["error"]
    assert store.documents()["total"] == 0
    assert store.search("")["total"] == 0

    # A recoverable missing local file becomes available before retry.
    path = store.root / "staging" / "later.html"
    missing = store.queue("file", {"path": str(path)}, "稍后出现的文件")
    assert run_one(store)
    assert store.job(missing["id"])["status"] == "failed"
    path.write_bytes(html_page())
    response = client.post(f"/api/jobs/{missing['id']}/retry", headers=LOCAL)
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "queued"
    completed = process(store, missing)
    assert completed["attempts"] == 2
    assert completed["error"] is None
    assert client.post(f"/api/jobs/{missing['id']}/retry", headers=LOCAL).status_code == 422


def test_queued_cancel_and_retry(workbench):
    client, store, _ = workbench
    job = upload(client)
    response = client.post(f"/api/jobs/{job['id']}/cancel", headers=LOCAL)
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "cancelled"
    assert run_one(store) is False
    assert store.documents()["total"] == 0
    assert client.post(f"/api/jobs/{job['id']}/retry", headers=LOCAL).status_code == 200
    process(store, job)


def test_uncategorized_search_includes_manually_cleared_tags(workbench):
    client, store, _ = workbench
    completed = process(store, upload(client))
    client.patch('/api/documents/'+completed['source_id'], json={'tags': []}, headers=LOCAL).raise_for_status()
    result = client.get('/api/search', params={'q': BODY_ONLY, 'category': '未分类'}).json()
    assert result['total'] == 1
    assert result['results'][0]['id'] == completed['source_id']


def test_cancel_during_parse_prevents_publication(workbench, monkeypatch):
    import server.parser as parser

    client, store, _ = workbench
    job = upload(client)
    actual_parse = parser.parse_html

    def cancel_while_parsing(*args, **kwargs):
        parsed = actual_parse(*args, **kwargs)
        store.cancel(job["id"])
        return parsed

    monkeypatch.setattr(parser, "parse_html", cancel_while_parsing)
    assert run_one(store)
    assert store.job(job["id"])["status"] == "cancelled"
    assert store.documents()["total"] == 0
    assert store.search(BODY_ONLY)["total"] == 0


def test_recover_running_job_after_reopen(workbench):
    client, store, _ = workbench
    job = upload(client)
    assert store.claim()["id"] == job["id"]
    assert store.job(job["id"])["status"] == "running"
    restarted = Store(store.root)
    restarted.recover()
    recovered = restarted.job(job["id"])
    assert recovered["status"] == "queued"
    assert "恢复" in recovered["error"]
    done = process(restarted, job)
    assert done["attempts"] == 2
    assert done["error"] is None
    assert restarted.documents()["total"] == 1






def test_url_import_uses_normalized_url_and_actual_worker_without_network(workbench, monkeypatch):
    import server.fetcher as fetcher

    client, store, _ = workbench
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return {"data": html_page(), "final_url": "https://example.org/resolved"}

    monkeypatch.setattr(fetcher, "fetch_html", fake_fetch)
    response = client.post("/api/imports/urls", headers=LOCAL, json={"urls": ["HTTPS://Example.ORG:443/path#one", "https://example.org/path#two"]})
    assert response.status_code == 202
    assert len(response.json()["jobs"]) == 1
    done = process(store, response.json()["jobs"][0])
    assert calls == ["https://example.org/path"]
    assert store.detail(done["source_id"])["url"] == "https://example.org/path"


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost.evil.example:4173", "null"])
def test_untrusted_origins_cannot_read_or_mutate(workbench, origin):
    client, store, _ = workbench
    headers = LOCAL | {"Origin": origin}
    assert client.get("/api/documents", headers=headers).status_code == 403
    assert client.post("/api/imports/urls", headers=headers, json={"urls": ["https://example.org/a"]}).status_code == 403
    assert store.jobs()["counts"]["total"] == 0


def test_untrusted_host_and_missing_client_header_are_rejected(workbench):
    client, store, _ = workbench
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/imports/urls", json={"urls": ["https://example.org/a"]}).status_code == 403
    assert client.post("/api/imports/urls", headers=LOCAL | {"Host": "evil.example"}, json={"urls": ["https://example.org/a"]}).status_code == 400
    assert store.jobs()["counts"]["total"] == 0
    assert client.get("/api/health", headers={"Origin": "http://127.0.0.1:4173"}).status_code == 200


def test_original_snapshot_is_exact_download_with_execution_blocked(workbench):
    client, store, _ = workbench
    raw = html_page(extra="<script>window.evilMarker = true</script><iframe src='https://evil.example'></iframe>")
    done = process(store, upload(client, raw))
    detail = store.detail(done["source_id"])
    assert "evilMarker" not in "\n".join(detail["body"])
    assert store.search("evilMarker")["total"] == 0
    response = client.get(detail["originalUrl"])
    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_source_filter_matches_literal_hostname_including_nondefault_port(workbench):
    client, store, _ = workbench
    expected = publish_at_url(store, html_page("端口文档"), "https://alpha.example:9443/source")
    publish_at_url(store, html_page("其他域名文档"), "https://alpha.example.evil/source")
    result = client.get("/api/search", params={"source": "ALPHA.EXAMPLE"}).json()
    assert [r["id"] for r in result["results"]] == [expected["source_id"]]
    for source in ("%", "alpha._xample", "alpha.example%", "alpha.example:9443"):
        response = client.get("/api/search", params={"source": source})
        assert response.status_code == 200
        assert response.json()["total"] == 0


def test_invalid_upload_is_not_retryable_and_valid_batch_item_survives(workbench):
    client, store, _ = workbench
    response = client.post(
        "/api/imports/files",
        headers=LOCAL,
        files=[("files", ("wrong.pdf", b"%PDF-1.7 invalid input", "application/pdf")),
               ("files", ("valid.html", html_page(), "text/html"))],
    )
    assert response.status_code == 202
    rejected, valid = response.json()["jobs"]
    assert rejected["status"] == "failed"
    assert rejected["retryable"] is False
    assert "重新上传" in rejected["error"]
    assert valid["retryable"] is True
    retry = client.post(f"/api/jobs/{rejected['id']}/retry", headers=LOCAL)
    assert retry.status_code == 422
    assert "重新上传" in retry.json()["detail"]
    reopened = Store(store.root)
    assert reopened.job(rejected["id"])["retryable"] is False
    assert reopened.job(rejected["id"])["status"] == "failed"
    assert reopened.job(rejected["id"])["attempts"] == 0
    assert next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == rejected["id"])["retryable"] is False
    process(store, valid)
    assert store.documents()["total"] == 1


def test_oversized_upload_rejection_cannot_retry_deleted_staging_file(workbench, monkeypatch):
    import importlib

    app_module = importlib.import_module("server.app")
    monkeypatch.setattr(app_module, "MAX_FILE", 64)
    client, store, _ = workbench
    rejected = upload(client, html_page())
    assert rejected["status"] == "failed"
    assert rejected["retryable"] is False
    assert client.post(f"/api/jobs/{rejected['id']}/retry", headers=LOCAL).status_code == 422
    assert list((store.root / "staging").iterdir()) == []
    assert run_one(store) is False


def test_duplicate_skips_parser_and_preserves_current_revision(workbench, monkeypatch):
    import server.parser as parser

    _, store, _ = workbench
    old_raw, new_raw = html_page("旧文档"), html_page("新文档")
    old = publish_at_url(store, old_raw)
    latest = publish_at_url(store, new_raw)

    def must_not_parse(*args, **kwargs):
        pytest.fail("An identical source/hash/parser revision must bypass parsing")

    monkeypatch.setattr(parser, "parse_html", must_not_parse)
    repeated = publish_at_url(store, old_raw)
    assert repeated["duplicate"] is True
    assert repeated["revision_id"] == old["revision_id"]
    assert store.detail(old["source_id"])["revisionId"] == latest["revision_id"]
    assert store.detail(old["source_id"])["revisionCount"] == 2
    assert publish_at_url(store, new_raw)["duplicate"] is True


def test_new_parser_version_reprocesses_same_source_bytes(workbench, monkeypatch):
    import server.parser as parser

    _, store, _ = workbench
    first = publish_at_url(store, html_page())
    actual_parse = parser.parse_html
    calls = []

    def record_parse(*args, **kwargs):
        calls.append(True)
        return actual_parse(*args, **kwargs)

    monkeypatch.setattr(parser, "PARSER_VERSION", parser.PARSER_VERSION + "-integration-test")
    monkeypatch.setattr(parser, "parse_html", record_parse)
    second = publish_at_url(store, html_page())
    assert calls == [True]
    assert second["duplicate"] is False
    assert second["source_id"] == first["source_id"]
    assert second["revision_id"] != first["revision_id"]
    assert store.detail(first["source_id"])["revisionCount"] == 2


def test_cancel_at_duplicate_check_is_not_overwritten(workbench, monkeypatch):
    import server.parser as parser

    client, store, _ = workbench
    first = process(store, upload(client))
    duplicate = upload(client)
    actual_reuse = store.reuse_revision

    def cancel_before_reuse(job, *args, **kwargs):
        store.cancel(job["id"])
        return actual_reuse(job, *args, **kwargs)

    def must_not_parse(*args, **kwargs):
        pytest.fail("A cancelled duplicate must not be parsed")

    monkeypatch.setattr(store, "reuse_revision", cancel_before_reuse)
    monkeypatch.setattr(parser, "parse_html", must_not_parse)
    assert run_one(store) is True
    assert store.job(duplicate["id"])["status"] == "cancelled"
    assert store.detail(first["source_id"])["revisionId"] == first["revision_id"]
    assert store.documents()["total"] == 1


def test_title_only_and_empty_search_do_not_claim_a_body_match(workbench):
    client, store, _ = workbench
    title = "NeedleTitleExclusive"
    raw = html_page(title).replace(f"<h1>{title}</h1>".encode(), b"")
    done = process(store, upload(client, raw))
    result = client.get("/api/search", params={"q": title}).json()
    assert result["total"] == 1
    assert result["results"][0]["id"] == done["source_id"]
    assert result["results"][0]["matchBlockId"] is None
    assert client.get("/api/search").json()["results"][0]["matchBlockId"] is None
    assert client.get("/api/search", params={"q": BODY_ONLY}).json()["results"][0]["matchBlockId"] is not None


def test_health_identifies_application_and_exact_storage_root(workbench):
    client, store, _ = workbench
    health = client.get("/api/health").json()
    assert health["application"] == "wenshu"
    assert Path(health["data_dir"]) == store.root


def test_large_table_list_and_search_keep_reader_detail_complete(workbench):
    client, store, _ = workbench
    table = "<table><tr><th>名称</th><th>说明</th></tr>" + "".join(
        f"<tr><td>记录{i}</td><td>完整证据说明{i}</td></tr>" for i in range(300)
    ) + "</table>"
    done = process(store, upload(client, html_page(extra=table)))
    listing = client.get("/api/documents").json()["documents"][0]
    result = client.get("/api/search", params={"q": BODY_ONLY}).json()["results"][0]
    assert listing["table"] is True
    assert result["table"] is True
    assert "tables" not in listing and "body" not in listing
    assert "tables" not in result and "body" not in result
    detail = client.get(f"/api/documents/{done['source_id']}").json()["document"]
    assert len(detail["tables"][0]) == 301
    assert detail["tables"][0][-1] == ["记录299", "完整证据说明299"]
    with store.connect() as db:
        assert db.execute("PRAGMA temp_store").fetchone()[0] == 2
