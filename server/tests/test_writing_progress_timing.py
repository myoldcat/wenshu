"""Elapsed display is derived from records, never guessed or persisted."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from server.writing_progress_timing import workflow_timings
from server.tests.test_writing_document import document


def stamp(seconds):
    return (datetime(2026, 10, 27, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def timing_db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,updated_at TEXT);
        CREATE TABLE writing_runs(id TEXT PRIMARY KEY,job_id TEXT,project_id TEXT,kind TEXT,
            section_id TEXT,created_at TEXT,applied INTEGER,result_json TEXT);
        CREATE TABLE writing_history(project_id TEXT,version INTEGER,content_json TEXT,reason TEXT,created_at TEXT);''')
    yield db
    db.close()


def project(*ids):
    return {'id': 'p', 'version': 7, 'sections': [{'id': sid, 'content': '已保存正文。'} for sid in ids],
            'workflowPendingSteps': []}


def run(db, name, kind, *, sid=None, start=0, finish=2, status='succeeded', applied=True,
        result=None, owner='p'):
    start = stamp(start) if isinstance(start, (int, float)) else start
    finish = stamp(finish) if isinstance(finish, (int, float)) else finish
    db.execute('INSERT INTO jobs VALUES(?,?,?)', (name + '-job', status, finish))
    db.execute('INSERT INTO writing_runs VALUES(?,?,?,?,?,?,?,?)',
               (name, name + '-job', owner, kind, sid, start, int(applied), json.dumps(result) if result is not None else None))
    return name


def document_result(*ids, status='applied'):
    return {'document': {'steps': [{'sectionId': sid, 'status': status} for sid in ids]}}


def parent(db, *, children, stages=None, version=7, status='succeeded', expected=7):
    state = {'startedAt': stamp(0), 'expectedVersion': expected, 'children': children,
             'stages': stages or [], 'report': {'projectVersion': version, 'stages': stages or []}}
    return run(db, 'parent', 'automatic', start=0, finish=40, status=status,
               result={'automatic': state})


def test_missing_records_stay_unknown_in_all_six_steps(timing_db):
    result = workflow_timings(timing_db, project('s1'))
    assert list(result) == ['brief', 'materials', 'outline', 'body', 'images', 'review']
    assert all(value['durationMs'] is None for value in result.values())
    assert set(result['body']) == {'durationMs', 'kind'}


def test_image_batch_uses_parent_elapsed_once_without_adding_child_searches(timing_db):
    run(timing_db, 'batch', 'image_batch', start=0, finish=20, result={'imageBatch': {
        'sectionIds': ['s1', 's2'], 'completedSectionIds': ['s1', 's2'],
        'children': {'s1': {'runId': 'first'}, 's2': {'runId': 'second'}}}})
    run(timing_db, 'first', 'images', sid='s1', start=2, finish=5)
    run(timing_db, 'second', 'images', sid='s2', start=8, finish=13)
    assert workflow_timings(timing_db, project('s1', 's2'))['images']['durationMs'] == 20000


def test_failed_image_batch_never_borrows_child_or_old_success_time(timing_db):
    run(timing_db, 'old', 'images', sid='s1', start=-2, finish=-1)
    run(timing_db, 'batch', 'image_batch', status='failed', start=0, finish=20, result={'imageBatch': {
        'sectionIds': ['s1'], 'completedSectionIds': [], 'children': {'s1': {'runId': 'first'}}}})
    run(timing_db, 'first', 'images', sid='s1', start=2, finish=5)
    assert workflow_timings(timing_db, project('s1'))['images']['durationMs'] is None


@pytest.mark.parametrize('start,finish', [
    (None, stamp(3)), (stamp(0), None), ('bad timestamp', stamp(3)),
    ('2026-10-27T00:00:00', stamp(3)), (stamp(5), stamp(2)),
])
def test_missing_invalid_or_reversed_timestamps_are_never_zero(timing_db, start, finish):
    run(timing_db, 'outline', 'outline', start=start, finish=finish)
    assert workflow_timings(timing_db, project())['outline']['durationMs'] is None


def test_real_subsecond_and_zero_intervals_are_preserved(timing_db):
    run(timing_db, 'first', 'outline', start=1, finish=1.125)
    assert workflow_timings(timing_db, project())['outline']['durationMs'] == 125
    run(timing_db, 'next', 'outline', start=2, finish=2)
    assert workflow_timings(timing_db, project())['outline']['durationMs'] == 0


def test_whole_document_is_counted_once_for_all_saved_chapters(timing_db):
    ids = tuple('s' + str(index) for index in range(6))
    run(timing_db, 'whole', 'document', start=61.601, finish=217.477, result=document_result(*ids))
    result = workflow_timings(timing_db, project(*ids))
    assert result['body'] == {'durationMs': 155876, 'kind': 'generation'}


def test_perchapter_uses_latest_applied_job_and_not_unadopted_candidates(timing_db):
    run(timing_db, 'old-first', 'section', sid='s1', start=0, finish=1)
    run(timing_db, 'second', 'section', sid='s2', start=2, finish=4)
    run(timing_db, 'new-first', 'section', sid='s1', start=5, finish=10)
    run(timing_db, 'candidate', 'section', sid='s1', start=11, finish=80, applied=False)
    assert workflow_timings(timing_db, project('s1', 's2'))['body']['durationMs'] == 7000
    assert workflow_timings(timing_db, project('s1', 's2', 'manual'))['body']['durationMs'] is None


def test_document_and_later_chapter_regeneration_do_not_duplicate_document_steps(timing_db):
    run(timing_db, 'whole', 'document', start=0, finish=10, result=document_result('s1', 's2', 'removed'))
    run(timing_db, 'new-first', 'section', sid='s1', start=11, finish=13)
    assert workflow_timings(timing_db, project('s1', 's2'))['body']['durationMs'] == 12000


def test_incomplete_or_failed_applied_records_cannot_borrow_old_completion_time(timing_db):
    run(timing_db, 'old', 'section', sid='s1', start=0, finish=2)
    run(timing_db, 'partial', 'document', start=3, finish=8, status='failed', result=document_result('s1'))
    assert workflow_timings(timing_db, project('s1'))['body']['durationMs'] is None


def test_active_jobs_and_empty_or_candidate_chapters_have_no_completed_time(timing_db):
    run(timing_db, 'running', 'document', status='running', applied=False, result=document_result('s1', status='generating'))
    assert workflow_timings(timing_db, project('s1'))['body']['durationMs'] is None
    run(timing_db, 'candidate', 'document', start=3, finish=4, applied=False, result=document_result('s1', status='candidate'))
    assert workflow_timings(timing_db, project('s1'))['body']['durationMs'] is None


def test_job_elapsed_includes_recorded_retry_wait_and_save_time(timing_db):
    run(timing_db, 'retry-job', 'section', sid='s1', start=0, finish=12.345)
    assert workflow_timings(timing_db, project('s1'))['body']['durationMs'] == 12345


def test_current_automatic_uses_own_stage_bounds_and_skips_do_not_inherit_old_work(timing_db):
    run(timing_db, 'old-image', 'images', sid='s1', start=-20, finish=-10)
    run(timing_db, 'old-review', 'citation_match', start=-9, finish=-1)
    run(timing_db, 'outline', 'outline', start=5, finish=15)
    run(timing_db, 'whole', 'document', start=16, finish=30, result=document_result('s1'))
    parent(timing_db, children={'outline': {'runId': 'outline'}, 'body:document': {'runId': 'whole'}},
           stages=[{'stage': 'materials', 'status': 'complete'}, {'stage': 'images', 'status': 'skipped'},
                   {'stage': 'review', 'status': 'skipped'}])
    result = workflow_timings(timing_db, project('s1'))
    assert result['materials'] == {'durationMs': 5000, 'kind': 'automatic-stage'}
    assert result['outline']['durationMs'] == 10000 and result['body']['durationMs'] == 14000
    assert result['images']['durationMs'] is None and result['review']['durationMs'] is None


def test_old_automatic_report_cannot_supply_current_stage_bounds(timing_db):
    run(timing_db, 'outline', 'outline', start=5, finish=15)
    parent(timing_db, children={'outline': {'runId': 'outline'}}, version=6,
           stages=[{'stage': 'materials', 'status': 'complete'}])
    result = workflow_timings(timing_db, project())
    assert result['materials']['durationMs'] is None
    assert result['outline']['durationMs'] == 10000  # Genuine latest generation remains known.


def test_active_automatic_exposes_only_finished_children_at_matching_checkpoint(timing_db):
    run(timing_db, 'outline', 'outline', start=5, finish=15)
    run(timing_db, 'whole', 'document', start=16, finish=17, status='running', applied=False)
    parent(timing_db, children={'outline': {'runId': 'outline'}, 'body:document': {'runId': 'whole'}},
           status='running', stages=[{'stage': 'materials', 'status': 'complete'}])
    result = workflow_timings(timing_db, project('s1'))
    assert result['materials']['durationMs'] == 5000 and result['outline']['durationMs'] == 10000
    assert result['body']['durationMs'] is None


def test_current_automatic_preserved_prose_does_not_inherit_previous_task_time(timing_db):
    run(timing_db, 'old', 'section', sid='s1', start=-10, finish=-1)
    parent(timing_db, children={}, stages=[{'stage': 'chapters', 'status': 'complete'}])
    assert workflow_timings(timing_db, project('s1'))['body']['durationMs'] is None


def test_automatic_child_identity_cannot_cross_project_boundaries(timing_db):
    run(timing_db, 'foreign', 'outline', start=5, finish=15, owner='other')
    parent(timing_db, children={'outline': {'runId': 'foreign'}}, stages=[{'stage': 'materials', 'status': 'complete'}])
    assert workflow_timings(timing_db, project())['materials']['durationMs'] is None


def test_confirmation_requires_explicit_pending_to_confirmed_history(timing_db):
    for version, pending, reason, at in [(1, ['brief'], 'created', 0), (2, ['brief'], 'edited', 3),
                                         (3, [], 'step_confirmed_brief', 6)]:
        timing_db.execute('INSERT INTO writing_history VALUES(?,?,?,?,?)',
                          ('p', version, json.dumps({'workflowPendingSteps': pending}), reason, stamp(at)))
    result = workflow_timings(timing_db, project())
    assert result['brief'] == {'durationMs': 6000, 'kind': 'confirmation'}
    changed = project(); changed['workflowPendingSteps'] = ['brief']
    assert workflow_timings(timing_db, changed)['brief']['durationMs'] is None


def test_derivation_issues_only_selects_and_is_deterministic(timing_db):
    run(timing_db, 'one', 'section', sid='s1')
    statements = []; timing_db.set_trace_callback(statements.append)
    first = workflow_timings(timing_db, project('s1'))
    assert workflow_timings(timing_db, project('s1')) == first
    assert statements and all(statement.lstrip().upper().startswith('SELECT') for statement in statements)


def test_malformed_or_legacy_history_cannot_fabricate_confirmation_interval(timing_db):
    timing_db.execute('INSERT INTO writing_history VALUES(?,?,?,?,?)',
                      ('p', 1, json.dumps({'workflowPendingSteps': ['brief']}), 'created', stamp(0)))
    timing_db.execute('INSERT INTO writing_history VALUES(?,?,?,?,?)',
                      ('p', 2, '{broken', 'step_confirmed_brief', stamp(5)))
    assert workflow_timings(timing_db, project())['brief']['durationMs'] is None


def test_public_get_exposes_timings_without_mutating_manuscript_history_or_jobs(document):
    store, gateway, service, _ = document
    created = service.create({'title': '只读耗时展示', 'brief': {'topic': '只读耗时展示'}, 'mode': 'extended'})
    def saved():
        with store.connect() as db:
            return ([tuple(row) for row in db.execute('SELECT * FROM writing_projects ORDER BY id')],
                    [tuple(row) for row in db.execute('SELECT * FROM writing_history ORDER BY project_id,version')],
                    [tuple(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')])
    before = saved()
    result = service.get(created['id'])
    assert result['workflowTimings'] == service.get(created['id'])['workflowTimings']
    assert all(item['durationMs'] is None for item in result['workflowTimings'].values())
    assert saved() == before and not gateway.calls and not gateway.matching_calls
