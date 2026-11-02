"""Destructive behavior is exercised exclusively against temporary libraries."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.document_deletion import DocumentDeletionConflict, DocumentDeletionService
from server.store import dumps, ident, now
from server.tests.test_image_library import publish


LOCAL = {'X-Wenshu-Client': 'local'}


@pytest.fixture
def workbench(tmp_path):
    app = create_app(data_dir=tmp_path / 'isolated-deletion-library')
    with TestClient(app) as client:
        yield client, app.state.store, app.state.writing


def impact(client, source):
    result = client.get(f'/api/documents/{source}/deletion-impact')
    assert result.status_code == 200, result.text
    return result.json()['impact']


def delete(client, source, reviewed=None, confirm=True, headers=LOCAL):
    reviewed = reviewed or impact(client, source)
    return client.request('DELETE', f'/api/documents/{source}', headers=headers, json={
        'expectedRevisionId': reviewed['revisionId'], 'impactToken': reviewed['impactToken'],
        'confirmReferences': confirm})


def stored_manuscript(store, writing, source, *, image=None):
    project = writing.create({'title': '保留正文与引用的文章', 'sourceIds': [source]})
    project['sections'] = [{'id': 'chapter', 'title': '正文', 'content': '正文保留原样。[E1]',
        'evidence': [{'id': 'E1', 'sourceId': source, 'revisionId': project['sources'][0]['revisionId'],
                      'quote': '保存的摘录', 'title': '固定来源标题'}]}]
    if image:
        project['illustrations'] = [{'id': 'figure', 'sourceId': source, 'assetId': image,
                                    'revisionId': project['sources'][0]['revisionId'], 'caption': '保存的图注'}]
    with store.connect() as db:
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(project), project['id']))
    return project


def test_delete_cleans_library_originals_images_search_and_preserves_manuscripts(workbench):
    client, store, writing = workbench
    document = publish(store, title='待删除的图片文章', alts=['独有图片'])
    source = document['source_id']
    with store.connect() as db:
        image = db.execute('SELECT id FROM assets WHERE revision_id=?', (document['revision_id'],)).fetchone()[0]
        paths = [store.root / row['blob_path'] for row in db.execute('SELECT blob_path FROM assets')]
        paths += [store.root / row['blob_path'] for row in db.execute('SELECT blob_path FROM revisions')]
    stored_manuscript(store, writing, source, image=image)
    with store.connect() as db:
        writing_before = [tuple(row) for row in db.execute('SELECT * FROM writing_projects')]
        history_before = [tuple(row) for row in db.execute('SELECT * FROM writing_history')]
    reviewed = impact(client, source)
    assert reviewed['references'][0]['textReferences'] == 1
    assert reviewed['references'][0]['imageReferences'] == 1
    assert reviewed['referenceCount'] == 1
    assert reviewed['imageCount'] == 1
    assert delete(client, source, reviewed, confirm=False).status_code == 409
    assert client.get('/api/documents/' + source).status_code == 200
    result = delete(client, source, reviewed)
    assert result.status_code == 200, result.text
    assert result.json()['removed']['files'] == 2
    assert not any(path.exists() for path in paths)
    assert client.get('/api/documents/' + source).status_code == 404
    assert client.get('/api/assets/' + image).status_code == 404
    assert client.get('/api/documents').json()['total'] == 0
    assert client.get('/api/images').json()['total'] == 0
    assert client.get('/api/search', params={'q': '图片'}).json()['total'] == 0
    assert delete(client, source, reviewed).status_code == 404
    with store.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM writing_projects')] == writing_before
        assert [tuple(row) for row in db.execute('SELECT * FROM writing_history')] == history_before
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_shared_picture_and_original_blob_survive_other_source_deletion(workbench):
    client, store, _ = workbench
    first = publish(store, title='相同的原始内容', alts=['共享图片'], url='https://example.org/first')
    second = publish(store, title='相同的原始内容', alts=['共享图片'], url='https://example.org/second')
    with store.connect() as db:
        assert db.execute('SELECT count(DISTINCT blob_path) FROM revisions').fetchone()[0] == 1
        assert db.execute('SELECT count(DISTINCT blob_path) FROM assets').fetchone()[0] == 1
        second_image = db.execute('SELECT id FROM assets WHERE revision_id=?', (second['revision_id'],)).fetchone()[0]
    assert delete(client, first['source_id']).status_code == 200
    assert client.get('/api/assets/' + second_image).status_code == 200
    assert client.get('/api/documents/' + second['source_id'] + '/original').status_code == 200
    assert client.get('/api/images').json()['total'] == 1
    assert delete(client, second['source_id']).json()['removed']['files'] == 2


def test_confirmed_impact_is_invalidated_by_new_citation_or_revision(workbench):
    client, store, writing = workbench
    document = publish(store)
    source = document['source_id']
    reviewed = impact(client, source)
    stored_manuscript(store, writing, source)
    conflict = delete(client, source, reviewed)
    assert conflict.status_code == 409
    assert conflict.json()['detail']['impact']['hasReferences']
    reviewed = impact(client, source)
    replacement = publish(store, title='来源更新的第二版')
    assert replacement['source_id'] == source
    conflict = delete(client, source, reviewed)
    assert conflict.status_code == 409
    assert conflict.json()['detail']['impact']['revisionId'] == replacement['revision_id']
    assert delete(client, source).status_code == 200


@pytest.mark.parametrize('kind,payload', [
    ('ai_summary', lambda store, doc: {'sourceId': doc['source_id']}),
    ('writing_document', lambda store, doc: {'sources': [{'sourceId': doc['source_id']}]}),
    ('url', lambda store, doc: {'url': 'https://example.org/images'}),
    ('file', lambda store, doc: {'path': str(store.original(doc['source_id'])[0])}),
])
def test_active_related_work_blocks_delete_and_cannot_resurrect_source(workbench, kind, payload):
    client, store, _ = workbench
    document = publish(store)
    queued = store.queue(kind, payload(store, document), '正在使用文档')
    reviewed = impact(client, document['source_id'])
    assert reviewed['activeJobs'][0]['id'] == queued['id']
    assert reviewed['canDelete'] is False
    assert delete(client, document['source_id'], reviewed).status_code == 409
    assert client.get('/api/documents/' + document['source_id']).status_code == 200
    store.cancel(queued['id'])
    assert delete(client, document['source_id']).status_code == 200


def test_history_and_soft_deleted_manuscripts_warn_without_altering_records(workbench):
    client, store, writing = workbench
    document = publish(store)
    source = document['source_id']
    project = stored_manuscript(store, writing, source)
    with store.connect() as db:
        db.execute('UPDATE writing_projects SET content_json=?,deleted_at=? WHERE id=?',
                   (dumps({**project, 'sourceIds': [], 'sources': [], 'sections': []}), now(), project['id']))
    reviewed = impact(client, source)
    assert reviewed['hasReferences']
    assert reviewed['references'][0]['deleted']
    assert reviewed['references'][0]['historicalReferences'] == 1
    assert delete(client, source, reviewed, confirm=False).status_code == 409


def seed_derived_records(store, document):
    source, revision = document['source_id'], document['revision_id']
    timestamp = now()
    with store.connect() as db:
        job = ident('job')
        db.execute("INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at) VALUES(?,?,?,'wiki_build','{}','succeeded','ready',?,?,?,?)",
                   (job, 'batch', '图谱', source, revision, timestamp, timestamp))
        db.execute('INSERT INTO wiki_runs(id,job_id,source_id,revision_id,model_id,model_version,model_json,prompt_version,fingerprint,created_at) VALUES(?,?,?,?,?,1,?,?,?,?)',
                   ('wiki-run', job, source, revision, 'fake', '{}', 'test', 'test', timestamp))
        db.execute('INSERT INTO wiki_entries VALUES(?,?,?,?)', ('entry-deleted', source, 'test', timestamp))
        page = {'id': 'entry-deleted', 'sourceId': source, 'revisionId': revision, 'label': '被删知识', 'sections': [], 'evidence': []}
        db.execute('INSERT INTO wiki_pages VALUES(?,1,?,?,?,?,?)', ('entry-deleted', 'wiki-run', source, revision, dumps(page), timestamp))
        db.execute('INSERT INTO wiki_publications VALUES(?,?,?)', (source, 'wiki-run', timestamp))
        db.execute('INSERT INTO wiki_search_index VALUES(?,?,?)', ('entry-deleted', '知识', '删除'))
        db.execute('INSERT INTO wiki_unified_outbox VALUES(?,?,?,?,?)', (source, 'wiki-run', 'complete', None, timestamp))
        db.execute('INSERT INTO wiki_canonical_entities VALUES(?,?,?)', ('canonical-deleted', 'entry-deleted', timestamp))
        db.execute('INSERT INTO wiki_canonical_pages VALUES(?,1,?,?,?,?)', ('canonical-deleted', 'snapshot', 'hash', dumps({'members': [page]}), timestamp))
        db.execute('INSERT INTO wiki_canonical_search_index VALUES(?,?,?)', ('canonical-deleted', '知识', '删除'))
        db.execute('INSERT INTO retrieval_embeddings VALUES(?,?,1,?,?,?,?)', ('embedding', 'fake', source, revision, '[1,0]', timestamp))
        db.execute('INSERT INTO retrieval_vector_units VALUES(?,?,1,?,?,?,?,?,?,?,?)', ('vector', 'fake', source, revision, 'unit', 'passage', 'hash', 1, b'\0\0\0\0', timestamp))
        db.execute('INSERT INTO retrieval_snapshots VALUES(?,?,?)', ('recommendation-related', dumps({'selectionVersions': {source: revision}}), timestamp))
        db.execute('INSERT INTO retrieval_snapshots VALUES(?,?,?)', ('recommendation-unrelated', '{}', timestamp))
        db.execute('INSERT INTO job_prompt_snapshots VALUES(?,?)', (job, '{}'))


def test_graph_vector_caches_and_source_jobs_removed_but_unrelated_cache_retained(workbench):
    client, store, _ = workbench
    document = publish(store)
    seed_derived_records(store, document)
    response = delete(client, document['source_id'])
    assert response.status_code == 200, response.text
    with store.connect() as db:
        for table in ('wiki_entries', 'wiki_pages', 'wiki_publications', 'wiki_runs', 'wiki_search_index',
                      'wiki_unified_outbox', 'wiki_canonical_entities', 'wiki_canonical_pages',
                      'wiki_canonical_search_index', 'retrieval_embeddings', 'retrieval_vector_units', 'job_prompt_snapshots'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0, table
        assert db.execute('SELECT id FROM retrieval_snapshots').fetchone()[0] == 'recommendation-unrelated'
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_delete_requires_local_mutation_header_and_review_contract(workbench):
    client, store, _ = workbench
    source = publish(store)['source_id']
    assert delete(client, source, headers={}).status_code == 403
    assert client.request('DELETE', '/api/documents/' + source, headers=LOCAL, json={}).status_code == 422
    assert client.request('DELETE', '/api/documents/' + source, headers=LOCAL,
        json={'expectedRevisionId': 'old', 'impactToken': 'a' * 64, 'confirmReferences': 'true'}).status_code == 422
    assert client.get('/api/documents/' + source).status_code == 200


def test_new_publication_recreates_a_blob_collected_between_parse_and_commit(workbench, monkeypatch):
    _, store, _ = workbench
    original = store.put_blob
    count = 0

    def interleaved(data, kind='blobs', suffix='.html'):
        nonlocal count
        result = original(data, kind, suffix)
        if kind == 'blobs':
            count += 1
            if count == 1:
                (store.root / result[1]).unlink()
        return result

    monkeypatch.setattr(store, 'put_blob', interleaved)
    document = publish(store)
    assert store.original(document['source_id'])[0].is_file()
    assert count == 2
