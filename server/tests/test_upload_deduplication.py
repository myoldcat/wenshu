"""Cross-entry upload deduplication uses temporary catalogs and synthetic HTML.

No application singleton, HTTP fetch, provider request or real library is used.
"""
import pytest

from server.document_deletion import DocumentDeletionService
from server.model_gateway import ModelService
from server.parser import PARSER_VERSION, parse_html
from server.store import Store
from server.worker import process_job


URL = 'https://example.org/isolated-upload-source'


def page(title='隔离去重文档'):
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f'<title>{title}</title></head><body><article><h1>{title}</h1>'
            '<p>这是一份完全合成的隔离验收资料。文档入库必须保留不可变原文、'
            '证据定位和人工设置，重复上传不得增加资料或触发自动模型加工。</p>'
            '</article></body></html>').encode('utf-8')


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'isolated-upload-deduplication')


def claimed_import(store, raw, *, url=None, filename='上传资料.html'):
    _, relative = store.put_blob(raw)
    payload = {'path': str(store.root / relative), 'filename': filename}
    if url:
        payload['url'] = url
    queued = store.queue('corpus' if url else 'file', payload, filename)
    claimed = store.claim()
    assert claimed['id'] == queued['id']
    return claimed, payload


def imported(store, raw, *, url=None, filename='上传资料.html'):
    job, _ = claimed_import(store, raw, url=url, filename=filename)
    # Imports do not need an enrichment adapter; a truthy sentinel prevents
    # process_job from constructing one and keeps every provider unavailable.
    process_job(store, job, enrichment=object())
    result = store.job(job['id'])
    assert result['status'] == 'succeeded', result
    return result


def catalog_rows(store):
    with store.connect() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in ('sources', 'revisions', 'blocks', 'search_index',
                              'document_chinese', 'translated_blocks', 'document_article_types')}


def test_upload_reuses_identical_url_snapshot_before_parsing(store, monkeypatch):
    raw = page()
    first = imported(store, raw, url=URL)
    before = catalog_rows(store)

    def forbidden_parse(*args, **kwargs):
        pytest.fail('An identical URL snapshot uploaded as a file must bypass parsing')

    monkeypatch.setattr('server.parser.parse_html', forbidden_parse)
    repeated = imported(store, raw, filename='另一个文件名.html')
    assert repeated['duplicate'] is True
    assert repeated['source_id'] == first['source_id']
    assert repeated['revision_id'] == first['revision_id']
    assert catalog_rows(store) == before
    assert store.documents()['total'] == 1


def test_upload_of_historical_url_snapshot_does_not_roll_current_back(store):
    old_raw, new_raw = page('旧版隔离资料'), page('新版隔离资料')
    old = imported(store, old_raw, url=URL)
    latest = imported(store, new_raw, url=URL)
    before = catalog_rows(store)

    repeated = imported(store, old_raw)
    assert repeated['duplicate'] is True
    assert repeated['source_id'] == old['source_id'] == latest['source_id']
    assert repeated['revision_id'] == old['revision_id']
    assert store.detail(old['source_id'])['revisionId'] == latest['revision_id']
    assert store.detail(old['source_id'])['revisionCount'] == 2
    assert catalog_rows(store) == before


def test_cross_entry_duplicate_preserves_manual_state_and_never_schedules_ai(store, monkeypatch):
    raw = page()
    first = imported(store, raw, url=URL)
    store.patch(first['source_id'], {'favorite': True, 'read': True, 'selected': True,
                                   'tags': ['人工分类'], 'articleType': 'technical',
                                   'revisionId': first['revision_id']})
    with store.connect() as db:
        block = db.execute('SELECT id FROM blocks WHERE revision_id=? ORDER BY ordinal LIMIT 1',
                           (first['revision_id'],)).fetchone()[0]
        db.execute("INSERT INTO document_chinese VALUES(?,?,?,'manual','[]')",
                   (first['revision_id'], '保留人工标题', '保留人工摘要'))
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)',
                   (first['revision_id'], block, '保留人工译文'))
    models = ModelService(store, cipher=object())
    config = models.save_config({'name': '仅配置不调用', 'baseUrl': 'https://model.invalid/v1',
                                 'model': 'isolated-model'})
    models.save_preferences({'defaultModelId': config['id'], 'autoProcess': True})

    def forbidden_model(*args, **kwargs):
        pytest.fail('Duplicate imports must not call a model')

    monkeypatch.setattr(ModelService, 'complete', forbidden_model)
    before = catalog_rows(store)
    repeated = imported(store, raw)
    assert repeated['duplicate'] is True
    assert catalog_rows(store) == before
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE kind GLOB 'ai_*'").fetchone()[0] == 0
    # The preference is effective: genuinely new content creates its outbox.
    imported(store, page('不同内容的隔离资料'))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='ai_schedule'").fetchone()[0] == 1


def test_publish_rechecks_cross_entry_duplicate_after_two_early_misses(store):
    raw = page()
    url_job, url_payload = claimed_import(store, raw, url=URL)
    file_job, file_payload = claimed_import(store, raw)
    assert store.reuse_revision(url_job, url_payload, raw, PARSER_VERSION) is False
    assert store.reuse_revision(file_job, file_payload, raw, PARSER_VERSION) is False
    parsed = parse_html(raw)
    store.publish(url_job, url_payload, raw, parsed, URL)
    first = store.job(url_job['id'])
    before = catalog_rows(store)
    # This upload finished parsing before the competing URL publication.
    store.publish(file_job, file_payload, raw, parsed)
    repeated = store.job(file_job['id'])
    assert repeated['status'] == 'succeeded'
    assert repeated['duplicate'] is True
    assert repeated['source_id'] == first['source_id']
    assert repeated['revision_id'] == first['revision_id']
    assert catalog_rows(store) == before


def test_same_bytes_from_distinct_declared_urls_remain_independent(store):
    raw = page()
    first = imported(store, raw, url=URL)
    second = imported(store, raw, url=URL + '/other')
    assert first['duplicate'] is second['duplicate'] is False
    assert first['source_id'] != second['source_id']
    assert store.documents()['total'] == 2


def test_existing_file_identity_takes_precedence_over_cross_entry_match(store):
    raw = page()
    local = imported(store, raw)
    remote = imported(store, raw, url=URL)
    assert local['source_id'] != remote['source_id']
    repeated = imported(store, raw, filename='重新命名.html')
    assert repeated['duplicate'] is True
    assert repeated['source_id'] == local['source_id']
    assert repeated['revision_id'] == local['revision_id']


def test_cancelled_cross_entry_upload_is_not_published_or_marked_success(store):
    raw = page()
    imported(store, raw, url=URL)
    before = catalog_rows(store)
    job, payload = claimed_import(store, raw)
    store.cancel(job['id'])
    assert store.reuse_revision(job, payload, raw, PARSER_VERSION) is True
    store.publish(job, payload, raw, parse_html(raw))
    result = store.job(job['id'])
    assert result['status'] == 'cancelled'
    assert result['duplicate'] is False
    assert result['source_id'] is None
    assert catalog_rows(store) == before


def test_deleted_url_source_cannot_be_reused_by_a_later_upload(store):
    raw = page()
    original = imported(store, raw, url=URL)
    deletion = DocumentDeletionService(store)
    impact = deletion.impact(original['source_id'])
    deletion.delete(original['source_id'], impact['revisionId'], impact['impactToken'])
    assert store.documents()['total'] == 0

    uploaded = imported(store, raw)
    assert uploaded['duplicate'] is False
    assert uploaded['source_id'] != original['source_id']
    assert uploaded['revision_id'] != original['revision_id']
    assert store.documents()['total'] == 1
    with pytest.raises(KeyError):
        store.detail(original['source_id'])


def test_url_reimport_under_new_parser_still_creates_a_revision(store, monkeypatch):
    import server.parser as parser

    raw = page()
    first = imported(store, raw, url=URL)
    monkeypatch.setattr(parser, 'PARSER_VERSION', parser.PARSER_VERSION + '-isolated-upgrade')
    upgraded = imported(store, raw, url=URL)
    assert upgraded['duplicate'] is False
    assert upgraded['source_id'] == first['source_id']
    assert upgraded['revision_id'] != first['revision_id']
    assert store.detail(first['source_id'])['revisionCount'] == 2
    uploaded = imported(store, raw)
    assert uploaded['duplicate'] is True
    assert uploaded['source_id'] == first['source_id']
    assert uploaded['revision_id'] == upgraded['revision_id']


@pytest.mark.parametrize('existing_url', [None, URL])
def test_http_repeated_uploads_and_batch_copies_publish_one_document(tmp_path, existing_url, monkeypatch):
    from fastapi.testclient import TestClient
    from server.app import create_app

    def forbidden_model(*args, **kwargs):
        pytest.fail('Upload acceptance must not call a model')

    monkeypatch.setattr(ModelService, 'complete', forbidden_model)
    app = create_app(data_dir=tmp_path / 'isolated-http-uploads')
    store, raw = app.state.store, page()
    original = imported(store, raw, url=existing_url) if existing_url else None
    with TestClient(app) as client:
        response = client.post('/api/imports/files', headers={'X-Wenshu-Client': 'local'}, files=[
            ('files', ('同一文件.html', raw, 'text/html')),
            ('files', ('同一文件.html', raw, 'text/html')),
            ('files', ('同一文件改名.html', raw, 'text/html')),
        ])
        assert response.status_code == 202, response.text
        completed = []
        for queued in response.json()['jobs']:
            job = store.claim()
            assert job['id'] == queued['id']
            process_job(store, job, enrichment=object())
            completed.append(store.job(job['id']))
        assert all(job['status'] == 'succeeded' for job in completed)
        original = original or completed[0]
        assert {job['source_id'] for job in completed} == {original['source_id']}
        assert {job['revision_id'] for job in completed} == {original['revision_id']}
        assert all(job['duplicate'] for job in completed[1:])
        assert client.get('/api/documents').json()['total'] == 1
        assert store.detail(original['source_id'])['revisionCount'] == 1
        before = catalog_rows(store)
        repeated = client.post('/api/imports/files', headers={'X-Wenshu-Client': 'local'},
            files={'files': ('同一文件.html', raw, 'text/html')})
        assert repeated.status_code == 202
        job = store.claim()
        assert job['id'] == repeated.json()['jobs'][0]['id']
        process_job(store, job, enrichment=object())
        assert store.job(job['id'])['duplicate'] is True
        assert catalog_rows(store) == before
        assert client.get('/api/documents').json()['total'] == 1
