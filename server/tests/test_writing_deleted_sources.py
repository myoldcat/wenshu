"""Deleted-library rendering remains a read-only view of saved writing."""
import copy
import json
import sqlite3

import pytest

from server.writing_library_availability import library_availability
from server.writing import WritingExportConflict
from server.writing_images import content_hash, scope_hash
from server.store import dumps
from server.tests.test_writing_integrity import writing


def test_availability_is_separate_from_saved_source_scope():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('CREATE TABLE sources(id TEXT); CREATE TABLE revisions(id TEXT,source_id TEXT); '
                     'CREATE TABLE assets(id TEXT,revision_id TEXT); '
                     "INSERT INTO sources VALUES('live'); INSERT INTO revisions VALUES('v1','live'); "
                     "INSERT INTO assets VALUES('image','v1');")
    project = {'sources': [{'sourceId': 'live', 'revisionId': 'v1'}, {'sourceId': 'gone', 'revisionId': 'old'}],
               'illustrations': [{'sourceId': 'gone', 'revisionId': 'old', 'assetId': 'gone-image'},
                                 {'sourceId': 'live', 'revisionId': 'v1', 'assetId': 'image'}],
               'sections': [{'evidence': [{'sourceId': 'live', 'revisionId': 'missing-version'}]}]}
    before = copy.deepcopy(project)
    availability = library_availability(db, project)
    assert [item['status'] for item in availability['sources']] == ['available', 'deleted', 'unavailable']
    assert [item['status'] for item in availability['assets']] == ['deleted', 'available']
    assert project == before


def test_get_and_history_derive_missing_status_without_persisting_or_changing_approval(writing):
    store, gateway, service, source_id, project = writing
    with store.connect() as db:
        before = db.execute('SELECT content_json FROM writing_projects WHERE id=?', (project['id'],)).fetchone()[0]
        history_before = [tuple(row) for row in db.execute('SELECT * FROM writing_history ORDER BY version')]
    # This isolated fixture simulates old imported manuscripts that retained a
    # source which is no longer in the library, without touching real files.
    saved = json.loads(before)
    saved['sources'][0]['sourceId'] = 'deleted-source'
    saved['sourceIds'] = ['deleted-source']
    section = saved['sections'][0]
    section['content'] = '保存的正文。[E1]'
    section['evidence'] = [{**saved['sources'][0], 'id': 'E1', 'blockId': 'deleted-block',
                            'quote': '原有摘录', 'start': 0, 'end': 4}]
    frozen_scope = scope_hash(saved, section)
    encoded = dumps(saved)
    with store.connect() as db:
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (encoded, project['id']))
    result = service.get(project['id'])
    assert result['libraryAvailability']['sources'][0]['status'] == 'deleted'
    assert scope_hash(result, result['sections'][0]) == frozen_scope
    assert result['sections'][0]['content'] == section['content']
    assert result['sections'][0]['evidence'] == section['evidence']
    historical = service.historical(project['id'], 1)
    assert historical['libraryAvailability']['sources'][0]['status'] == 'available'
    with store.connect() as db:
        assert db.execute('SELECT content_json FROM writing_projects WHERE id=?', (project['id'],)).fetchone()[0] == encoded
        assert [tuple(row) for row in db.execute('SELECT * FROM writing_history ORDER BY version')] == history_before
    assert not gateway.calls and not gateway.matching_calls


def test_missing_image_export_fails_with_business_conflict_without_losing_prose(writing):
    store, gateway, service, source_id, project = writing
    with store.connect() as db:
        saved = json.loads(db.execute('SELECT content_json FROM writing_projects WHERE id=?', (project['id'],)).fetchone()[0])
        section = saved['sections'][0]
        section['content'] = '保留正文。'
        saved['illustrations'] = [{'id': 'figure', 'sectionId': section['id'], 'sourceId': source_id,
            'revisionId': saved['sources'][0]['revisionId'], 'assetId': 'missing-asset', 'caption': '原图注',
            'status': 'accepted', 'reviewed': True, 'anchor': {'paragraphIndex': 0,
                'paragraphExact': section['content'], 'contentHash': content_hash(section['content'])}}]
        encoded = dumps(saved)
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (encoded, project['id']))
    with pytest.raises(WritingExportConflict) as rejected:
        service.export_snapshot(project['id'], project['version'], format='html', allow_issues=True)
    assert rejected.value.detail['code'] == 'illustrations_invalid'
    assert '不可用' in str(rejected.value)
    with store.connect() as db:
        assert db.execute('SELECT content_json FROM writing_projects WHERE id=?', (project['id'],)).fetchone()[0] == encoded
    assert not gateway.calls and not gateway.matching_calls
