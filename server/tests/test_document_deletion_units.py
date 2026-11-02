"""Deletion races and shared graph projections without HTTP or real models."""
import json
from pathlib import Path

import pytest

from server.app import create_app
from server.document_deletion import DocumentDeletionService
from server.image_retrieval import ImageRetrievalService
from server.store import dumps, ident, now
from server.tests.test_image_library import publish
from server.wiki_canonical import _hash


@pytest.fixture
def app(tmp_path):
    instance = create_app(data_dir=tmp_path / 'isolated-deletion-unit')
    ImageRetrievalService(instance.state.store, instance.state.models)
    return instance


def remove(store, source):
    service = DocumentDeletionService(store)
    reviewed = service.impact(source)
    return service.delete(source, reviewed['revisionId'], reviewed['impactToken'], True)


def seed_page(store, document, entry):
    timestamp, job, run = now(), ident('job'), ident('wiki-run')
    page = {'id': entry, 'version': 1, 'sourceId': document['source_id'],
        'revisionId': document['revision_id'], 'label': '同一定义', 'type': 'concept',
        'subtype': '', 'summary': '来自不同文章的相同定义', 'aliases': [], 'sections': [],
        'evidence': [], 'issues': [], 'relations': []}
    with store.connect() as db:
        db.execute("INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at) VALUES(?,?,?,'wiki_build','{}','succeeded','ready',?,?,?,?)",
            (job, 'batch', '图谱', document['source_id'], document['revision_id'], timestamp, timestamp))
        db.execute('INSERT INTO wiki_runs(id,job_id,source_id,revision_id,model_id,model_version,model_json,prompt_version,fingerprint,created_at) VALUES(?,?,?,?,?,1,?,?,?,?)',
            (run, job, document['source_id'], document['revision_id'], 'fake', '{}', 'test', 'test', timestamp))
        db.execute('INSERT INTO wiki_entries VALUES(?,?,?,?)', (entry, document['source_id'], 'definition', timestamp))
        db.execute('INSERT INTO wiki_pages VALUES(?,1,?,?,?,?,?)',
            (entry, run, document['source_id'], document['revision_id'], dumps(page), timestamp))
        db.execute('INSERT INTO wiki_publications VALUES(?,?,?)', (document['source_id'], run, timestamp))
    return page


def test_deleting_non_anchor_member_invalidates_shared_group_and_rederives_survivor(app):
    store = app.state.store
    first = publish(store, title='保留的文章', url='https://example.org/keep')
    second = publish(store, title='删除的文章', url='https://example.org/remove')
    survivor, deleted = seed_page(store, first, 'entry-a'), seed_page(store, second, 'entry-z')
    canonical = app.state.wiki_knowledge.canonical
    snapshot = canonical.snapshot([survivor, deleted])
    assert len(snapshot['groups']) == 1
    old_membership = _hash(['entry-a', 'entry-z'])
    remove(store, second['source_id'])
    with store.connect() as db:
        assert db.execute('SELECT 1 FROM wiki_canonical_memberships WHERE member_key=?', (old_membership,)).fetchone() is None
        assert db.execute('SELECT count(*) FROM wiki_canonical_search_index').fetchone()[0] == 0
    fresh = canonical.snapshot([survivor])
    assert len(fresh['groups']) == 1
    group = next(iter(fresh['groups'].values()))
    assert group['entryIds'] == ['entry-a']
    assert group['sourceIds'] == [first['source_id']]
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_entries').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM wiki_canonical_memberships').fetchone()[0] == 1
        assert all(second['source_id'] not in row['content_json'] for row in db.execute('SELECT content_json FROM wiki_canonical_pages'))


def test_late_vector_response_cannot_restore_deleted_source_cache(app, monkeypatch):
    store, retrieval = app.state.store, app.state.retrieval
    document = publish(store)
    unit = {'sourceId': document['source_id'], 'revisionId': document['revision_id'],
        'unitId': 'unit', 'kind': 'passage', 'dependencyHash': 'hash', 'text': '原文内容', 'matchText': '原文内容'}

    def response(config, texts):
        remove(store, document['source_id'])
        return [[1., 0.] for _ in texts]

    monkeypatch.setattr(retrieval, '_embed', response)
    retrieval._vector_warm({'id': 'fake', 'version': 1}, [unit])
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM retrieval_vector_units').fetchone()[0] == 0


def test_late_image_vector_response_cannot_restore_deleted_asset_cache(app):
    store = app.state.store
    document = publish(store, alts=['测试图片'])
    with store.connect() as db:
        asset = db.execute('SELECT id FROM assets WHERE revision_id=?', (document['revision_id'],)).fetchone()[0]
    calls = 0

    def response(config, texts):
        nonlocal calls
        calls += 1
        if calls == 2:
            remove(store, document['source_id'])
        return [[1., 0.] for _ in texts]

    item = {'assetId': asset, 'sourceId': document['source_id'], 'revisionId': document['revision_id'],
        'scopeEligible': True, 'status': 'candidate', '_searchText': '测试图片语境', '_rank': 0}
    ImageRetrievalService(store, app.state.models)._semantic([item], '测试图片',
        {'id': 'fake', 'version': 1, 'model': 'fake'}, {}, [], embed_call=response)
    assert calls == 2
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_image_vectors').fetchone()[0] == 0


def test_deleted_source_ai_and_image_contexts_do_not_leave_orphans(app):
    store = app.state.store
    document = publish(store, alts=['配图'])
    source, revision, timestamp = document['source_id'], document['revision_id'], now()
    with store.connect() as db:
        asset = db.execute('SELECT id FROM assets WHERE revision_id=?', (revision,)).fetchone()[0]
        block = db.execute('SELECT id FROM blocks WHERE revision_id=? LIMIT 1', (revision,)).fetchone()[0]
        job = ident('job')
        db.execute("INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at) VALUES(?,?,?,'ai_summary','{}','succeeded','ready',?,?,?,?)",
            (job, 'batch', '摘要', source, revision, timestamp, timestamp))
        db.execute('INSERT INTO ai_runs(id,job_id,source_id,revision_id,kind,model_id,model_version,model_json,prompt_version,target_language,fingerprint,created_at) VALUES(?,?,?,?,?,?,1,?,?,?,?,?)',
            ('run', job, source, revision, 'summary', 'fake', '{}', 'test', 'zh-CN', 'hash', timestamp))
        db.execute('INSERT INTO ai_outputs VALUES(?,?,?,1,?)', ('output', 'run', '{}', timestamp))
        db.execute('INSERT INTO ai_checkpoints VALUES(?,?,?,?,?)', ('run', 'checkpoint', 'hash', '{}', timestamp))
        db.execute('INSERT INTO ai_calls VALUES(?,?,1,1,?,?,?,?)', ('run', 'checkpoint', '{}', 'fake', 'accepted', timestamp))
        db.execute('INSERT INTO ai_translation_edits(revision_id,block_id,text,locked,base_run_seq,created_at) VALUES(?,?,?,1,1,?)', (revision, block, '人工译文', timestamp))
        db.execute('INSERT INTO writing_image_contexts VALUES(?,?,?,?,?,?)', (asset, source, revision, 'hash', '{}', timestamp))
        db.execute('INSERT INTO writing_image_vectors VALUES(?,?,?,?,?,?,?,?)', ('vector', asset, revision, 'fake', 1, 'hash', '[1,0]', timestamp))
    remove(store, source)
    with store.connect() as db:
        for table in ('ai_runs', 'ai_outputs', 'ai_calls', 'ai_checkpoints', 'ai_translation_edits',
                      'writing_image_contexts', 'writing_image_vectors'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0, table
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_locked_picture_cleanup_is_durable_and_retried_without_model_jobs(app, monkeypatch):
    store = app.state.store
    document = publish(store, alts=['被阅读窗口占用的图片'])
    with store.connect() as db:
        relative = db.execute('SELECT blob_path FROM assets').fetchone()[0]
    path = (store.root / relative).resolve()
    unlink = Path.unlink

    def occupied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError('Windows reader still holds this file')
        return unlink(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', occupied)
        response = remove(store, document['source_id'])
    assert response['pendingCleanup'] == 1
    assert path.exists()
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 0
        assert db.execute('SELECT blob_path FROM document_blob_cleanup').fetchone()[0] == relative
        jobs_before = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
    # A newly opened service sees the durable intent after a process restart.
    from server.store import Store
    reopened = Store(store.root)
    assert DocumentDeletionService(reopened).collect_pending(force=True) == (1, 0)
    assert not path.exists()
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == jobs_before
        assert db.execute('SELECT 1 FROM image_dimensions WHERE blob_path=?', (relative,)).fetchone() is None


def test_active_library_image_task_is_detected_in_frozen_run_input(app):
    store = app.state.store
    document = publish(store, alts=['尚未采纳的图片'])
    project = app.state.writing.create({'title': '尚未引用此文档的文稿', 'sourceIds': []})
    job = store.queue('writing_images', {}, '图片检索中')
    with store.connect() as db:
        db.execute('INSERT INTO writing_runs(id,job_id,project_id,kind,base_version,model_id,model_version,model_json,prompt_version,input_json,created_at) VALUES(?,?,?,?,1,?,1,?,?,?,?)',
            ('image-run', job['id'], project['id'], 'images', 'fake', '{}', 'test',
             dumps({'libraryScope': [{'sourceId': document['source_id'], 'revisionId': document['revision_id']}]}), now()))
    reviewed = DocumentDeletionService(store).impact(document['source_id'])
    assert reviewed['canDelete'] is False
    assert reviewed['activeJobs'][0]['id'] == job['id']
    assert reviewed['hasReferences'] is False


def test_missing_file_also_removes_dimension_cache(app):
    store = app.state.store
    document = publish(store, alts=['已经缺失的图片'])
    with store.connect() as db:
        relative = db.execute('SELECT blob_path FROM assets').fetchone()[0]
        db.execute('INSERT OR REPLACE INTO image_dimensions VALUES(?,200,200)', (relative,))
    (store.root / relative).unlink()
    remove(store, document['source_id'])
    with store.connect() as db:
        assert db.execute('SELECT 1 FROM image_dimensions WHERE blob_path=?', (relative,)).fetchone() is None
        assert db.execute('SELECT count(*) FROM document_blob_cleanup').fetchone()[0] == 0


def test_cancelled_import_intent_cannot_be_retried_after_source_deletion(app):
    store = app.state.store
    document = publish(store)
    queued = store.queue('url', {'url': 'https://example.org/images'}, '已经取消的重复导入')
    store.cancel(queued['id'])
    remove(store, document['source_id'])
    with store.connect() as db:
        assert db.execute('SELECT 1 FROM jobs WHERE id=?', (queued['id'],)).fetchone() is None


def test_active_graph_resolution_is_detected_in_frozen_run_input(app):
    store = app.state.store
    document = publish(store)
    job = store.queue('wiki_resolve', {'runId': 'resolution-run'}, '图谱辨析中')
    with store.connect() as db:
        db.execute('INSERT INTO wiki_resolution_runs(id,job_id,kind,target_id,model_id,model_version,model_json,prompt_version,fingerprint,input_json,created_at) VALUES(?,?,?,?,?,1,?,?,?,?,?)',
            ('resolution-run', job['id'], 'wiki_resolve', 'target', 'fake', '{}', 'test', 'hash',
             dumps({'sourceState': [{'sourceId': document['source_id'], 'revisionId': document['revision_id']}]}), now()))
    reviewed = DocumentDeletionService(store).impact(document['source_id'])
    assert reviewed['canDelete'] is False
    assert reviewed['activeJobs'][0]['id'] == job['id']
