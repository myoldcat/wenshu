"""New-import publication-day extraction with isolated catalogs and fake URLs.

Only synthetic HTML and temporary stores are used. No provider, real snapshot,
historical backfill, network request or real library is exercised.
"""
import hashlib
import json
import pytest

from server.model_gateway import ModelService
from server.parser import PARSER_VERSION, parse_html
from server.store import Store
from server.worker import process_job


IMPORT_TIME = '2026-10-28T12:34:56+00:00'
TITLE = '隔离发表日期资料'
BODY = '这是一篇完全合成的隔离资料，正文与原文快照需要完整保留，日期应当独立提取并保留明确依据，不调用任何模型。'


def page(extra='', *, head='', title=TITLE, body=None):
    content = body if body is not None else f'<article><h1>{title}</h1>{extra}<p>{BODY}</p></article>'
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>{head}'
            f'</head><body>{content}</body></html>').encode('utf-8')


@pytest.fixture
def store(tmp_path, monkeypatch):
    def forbidden_model(*args, **kwargs):
        pytest.fail('Publication-day import checks must never call a model')

    monkeypatch.setattr(ModelService, 'complete', forbidden_model)
    monkeypatch.setattr('server.store.now', lambda: IMPORT_TIME)
    return Store(tmp_path / 'isolated-publication-import')


def import_snapshot(store, raw, *, filename='隔离资料.html', url=None, fetched_at=None):
    if url is None:
        _, relative = store.put_blob(raw)
        payload = {'path': str(store.root / relative), 'filename': filename}
        kind = 'file'
    else:
        payload = {'url': url}
        kind = 'url'
    if fetched_at is not None:
        payload['fetched_at'] = fetched_at
    queued = store.queue(kind, payload, filename)
    job = store.claim()
    assert job['id'] == queued['id']
    process_job(store, job, enrichment=object())
    completed = store.job(job['id'])
    assert completed['status'] == 'succeeded', completed
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE kind GLOB 'ai_*'").fetchone()[0] == 0
    return completed, store.detail(completed['source_id'])


def metadata_rows(store):
    with store.connect() as db:
        return {name: [tuple(row) for row in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                for name in ('sources', 'revisions', 'blocks', 'assets', 'search_index',
                             'document_chinese', 'translated_blocks', 'document_article_types')}


def test_file_import_normalizes_explicit_publication_day_and_keeps_original_bytes(store):
    raw = page(head='<meta property="article:published_time" content="2024-02-29T23:59:59-12:00">')
    job, detail = import_snapshot(store, raw)
    assert detail['publishedAt'] == '2024-02-29'
    assert detail['date'] == '2026-10-28'
    assert detail['createdAt'] == IMPORT_TIME
    assert detail['blobHash'] == hashlib.sha256(raw).hexdigest()
    with store.connect() as db:
        blob = db.execute('SELECT blob_path FROM revisions WHERE id=?', (job['revision_id'],)).fetchone()[0]
    assert (store.root / blob).read_bytes() == raw
    assert detail['parserVersion'] == PARSER_VERSION
    assert BODY in '\n'.join(detail['body'])


def test_mock_url_import_reads_fetched_snapshot_not_url_or_fetch_date(store, monkeypatch):
    raw = page('<div>發佈日期: 2024年2月29日</div>')
    calls = []

    def fetch(url):
        calls.append(url)
        return {'data': raw, 'final_url': 'https://example.org/redirected/2021/01/01/article'}

    monkeypatch.setattr('server.fetcher.fetch_html', fetch)
    _, detail = import_snapshot(store, raw, url='https://example.org/2020/01/01/article',
                                fetched_at='2025-12-31T23:59:59Z')
    assert calls == ['https://example.org/2020/01/01/article']
    assert detail['publishedAt'] == '2024-02-29'
    assert detail['fetchedAt'] == '2025-12-31T23:59:59Z'
    assert detail['date'] == '2026-10-28'


def test_uploaded_snapshot_can_identify_original_site_but_never_uses_collection_date(store):
    body = (f'<div id="dsh-page"><h1>{TITLE}</h1><div class="dsh-meta">'
            '日期：2030-01-01 ｜ 原文：<a href="https://www.wenweipo.com/a/202501/01/story.html">原文链接</a></div>'
            f'<main><div class="title-bar"><h1>{TITLE}</h1><div class="info-bar">'
            '<span class="time">2024-02-29 08:15:00</span></div></div>'
            f'<div class="article-content"><p>{BODY}</p></div></main></div>')
    _, detail = import_snapshot(store, page(body=body))
    assert detail['publishedAt'] == '2024-02-29'
    assert detail['date'] == '2026-10-28'


@pytest.mark.parametrize('extra,head', [
    ('<div>发布日期：2026-02-29</div>', ''),
    ('<div>发布日期：2024年2月</div>', ''),
    ('<div>发布日期：昨天</div>', ''),
    ('<div>更新时间：2024-02-29</div>', '<meta property="article:modified_time" content="2024-02-29">'),
    ('<time datetime="2024-02-29T12:00:00Z">2024-02-29</time>', '<meta name="date" content="2024-02-29">'),
    ('<p>2024年2月29日某项措施正式生效，此处是事件日期而非文章发表日期。</p>', ''),
    ('<p>本篇正文先说明事件背景，其后引用的资料另有自身发布时间。</p>'
     '<blockquote><div>发布日期：2024-02-29</div></blockquote>', ''),
    ('<aside><h2>相关文章</h2><time itemprop="datePublished" datetime="2024-02-29">2024-02-29</time></aside>', ''),
    ('<div>发布日期：2024-03-01</div>', '<meta property="article:published_time" content="2024-02-29">'),
])
def test_unclear_dates_remain_null_and_dto_keeps_import_fallback(store, extra, head):
    _, detail = import_snapshot(store, page(extra, head=head))
    assert detail['publishedAt'] is None
    assert detail['date'] == '2026-10-28'
    assert detail['createdAt'] == IMPORT_TIME


def test_collection_wrapper_and_url_date_alone_are_not_publication_evidence(store):
    body = (f'<div id="dsh-page"><h1>{TITLE}</h1><div class="dsh-meta">'
            '日期：2024-02-29 ｜ 原文：<a href="https://www.wenweipo.com/a/202402/29/story.html">原文</a></div>'
            f'<article><h1>{TITLE}</h1><p>{BODY}</p></article></div>')
    _, detail = import_snapshot(store, page(body=body))
    assert detail['publishedAt'] is None
    assert detail['date'] == '2026-10-28'


def test_new_original_revision_gets_its_own_date_without_rewriting_prior_revision(store, monkeypatch):
    versions = [page(head='<meta property="article:published_time" content="2024-02-29">'),
                page(head='<meta property="article:published_time" content="2025-01-20">',
                     title='隔离资料的新原文版本')]
    monkeypatch.setattr('server.fetcher.fetch_html', lambda url: {'data': versions.pop(0), 'final_url': url})
    first_job, old_detail = import_snapshot(store, b'', url='https://example.org/current-article')
    second_job, new_detail = import_snapshot(store, b'', url='https://example.org/current-article')
    assert first_job['source_id'] == second_job['source_id']
    assert first_job['revision_id'] != second_job['revision_id']
    assert old_detail['publishedAt'] == '2024-02-29'
    assert new_detail['publishedAt'] == '2025-01-20'
    assert store.detail(first_job['source_id'], first_job['revision_id'])['publishedAt'] == '2024-02-29'
    assert new_detail['date'] == old_detail['date'] == '2026-10-28'


def test_same_bytes_renamed_upload_reuses_old_missing_date_and_preserves_manual_and_frozen_data(store, monkeypatch):
    raw = page('<div>发布日期：2024-02-29</div>')
    first, _ = import_snapshot(store, raw)
    store.patch(first['source_id'], {'favorite': True, 'read': True, 'selected': True,
                                   'tags': ['人工分类'], 'articleType': 'technical',
                                   'revisionId': first['revision_id']})
    with store.connect() as db:
        # Represent a stored version created before the publication extractor.
        db.execute('UPDATE revisions SET published_at=NULL WHERE id=?', (first['revision_id'],))
        block = db.execute('SELECT id FROM blocks WHERE revision_id=? ORDER BY ordinal LIMIT 1',
                           (first['revision_id'],)).fetchone()[0]
        db.execute("INSERT INTO document_chinese VALUES(?,?,?,'manual','[]')",
                   (first['revision_id'], '已修订标题', '已修订摘要'))
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)', (first['revision_id'], block, '人工译文保持不变'))
    frozen = {'sourceContext': [{'sourceId': first['source_id'], 'revisionId': first['revision_id'], 'publishedAt': None}],
              'protocol': 'old-frozen-policy'}
    old_job = store.queue('writing_document', frozen, '隔离旧冻结任务')
    store.cancel(old_job['id'])
    with store.connect() as db:
        old_input = db.execute('SELECT input_json FROM jobs WHERE id=?', (old_job['id'],)).fetchone()[0]
    before = metadata_rows(store)

    def forbidden_parse(*args, **kwargs):
        pytest.fail('An identical byte snapshot must reuse its existing revision before parsing')

    monkeypatch.setattr('server.parser.parse_html', forbidden_parse)
    repeated, detail = import_snapshot(store, raw, filename='重命名不会绕过去重.html')
    assert repeated['duplicate'] is True
    assert repeated['source_id'] == first['source_id']
    assert repeated['revision_id'] == first['revision_id']
    assert detail['revisionCount'] == 1
    assert detail['publishedAt'] is None
    assert all(detail[name] is True for name in ('favorite', 'read', 'selected'))
    assert metadata_rows(store) == before
    with store.connect() as db:
        assert db.execute('SELECT input_json FROM jobs WHERE id=?', (old_job['id'],)).fetchone()[0] == old_input
    assert json.loads(old_input) == frozen
    # Mere browsing and reopening the catalog cannot backfill this old result.
    reopened = Store(store.root)
    reopened.documents()
    assert reopened.detail(first['source_id'])['publishedAt'] is None
    assert metadata_rows(reopened) == before


def test_duplicate_upload_reuses_existing_url_snapshot_date_without_reparsing(store, monkeypatch):
    raw = page(head='<meta property="article:published_time" content="2024-02-29">')
    monkeypatch.setattr('server.fetcher.fetch_html', lambda url: {'data': raw, 'final_url': url})
    original, _ = import_snapshot(store, raw, url='https://example.org/url-import')
    before = metadata_rows(store)
    monkeypatch.setattr('server.parser.parse_html', lambda *args, **kwargs: pytest.fail('Duplicate snapshot was reparsed'))
    repeated, detail = import_snapshot(store, raw, filename='网页同快照.html')
    assert repeated['duplicate'] is True
    assert repeated['source_id'] == original['source_id']
    assert repeated['revision_id'] == original['revision_id']
    assert detail['publishedAt'] == '2024-02-29'
    assert metadata_rows(store) == before
