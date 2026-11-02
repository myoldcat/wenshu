"""Synthetic service coverage for durable, bounded Wiki-all orchestration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json

import pytest

from server.document_deletion import DocumentDeletionService
from server.prompts import PromptLibrary
from server.store import Store
from server.tests.test_wiki import environment, publish, execute, compile_page
from server.wiki import WikiService


def sources(environment, count):
    store, _, _, first = environment
    return [first] + [publish(store, url=f'https://example.org/all/{index}', title=f'隔离资料 {index}')
                      for index in range(1, count)]


def items(store, job_id):
    with store.connect() as db:
        return [dict(row) for row in db.execute('''SELECT i.* FROM wiki_all_items i
            JOIN wiki_all_runs r ON r.id=i.run_id WHERE r.job_id=? ORDER BY i.ordinal''', (job_id,))]


def snapshot(store):
    with store.connect() as db:
        return list(db.iterdump())


def remove(store, source):
    deletion = DocumentDeletionService(store)
    reviewed = deletion.impact(source)
    assert reviewed['canDelete']
    return deletion.delete(source, reviewed['revisionId'], reviewed['impactToken'], True)


def test_45_sources_advance_in_20_20_5_without_claiming_parent_or_read_side_effects(environment):
    store, models, service, _ = environment
    selected = sources(environment, 45)
    parent = service.queue_all(selected)['run']
    assert parent['scope'] == 'selected' and parent['total'] == 45
    assert parent['counts']['pending'] == 45 and parent['batches'] == 0
    assert store.claim() is None, 'A parent must never occupy the source executor lane'
    before = snapshot(store)
    assert service.all_run(parent['id']) == parent
    assert service.all_runs()['runs'] == [parent]
    assert snapshot(store) == before and models.calls == []
    for batch, size in enumerate((20, 20, 5), 1):
        assert service.advance_all()
        current = service.all_run(parent['id'])
        assert current['batchSize'] == 20 and current['batches'] == batch
        assert current['counts']['queued'] == size
        assert not service.advance_all(), 'An active batch must prevent scheduling its successor'
        for _ in range(size):
            assert execute(store, service)['status'] == 'succeeded'
        assert store.claim() is None
    assert service.advance_all()
    final = service.all_run(parent['id'])
    assert final['job']['status'] == 'succeeded' and final['counts']['succeeded'] == 45
    assert final['counts']['pending'] == final['counts']['queued'] == final['counts']['running'] == 0
    assert len(models.calls) == 45 and not service.advance_all()
    assert [len([item for item in items(store, parent['id']) if item['batch'] == number])
            for number in (1, 2, 3)] == [20, 20, 5]


def test_library_scope_filters_built_active_empty_and_does_not_expand_after_creation(environment):
    store, models, service, first = environment
    compile_page(environment)
    selected = sources(environment, 5)
    active, empty, pending = selected[1], selected[2], selected[3:]
    with store.connect() as db:
        db.execute("UPDATE blocks SET text=' \t\n' WHERE revision_id=(SELECT current_revision FROM sources WHERE id=?)", (empty,))
    external = service.queue([active])['jobs'][0]
    result = service.queue_all()
    parent = result['run']
    assert parent['scope'] == 'library' and parent['total'] == 2
    assert parent['excluded'] == {'built': 1, 'active': 1, 'empty': 1}
    assert {item['source_id'] for item in items(store, parent['id'])} == set(pending)
    # Removing an unstarted external queue item makes the synthetic import helper
    # usable; the parent still retains its original exclusion and frozen set.
    store.cancel(external['id'])
    added = publish(store, url='https://example.org/added-after-all')
    assert added not in {item['source_id'] for item in items(store, parent['id'])}
    assert service.advance_all()
    for _ in pending:
        assert execute(store, service)['status'] == 'succeeded'
    service.advance_all()
    assert service.all_run(parent['id'])['job']['status'] == 'succeeded'
    assert len(models.calls) == 3, 'Only the original built page and two frozen candidates run'


def test_selected_scope_deduplicates_and_never_falls_back_when_exhausted(environment):
    store, models, service, first = environment
    selected = sources(environment, 3)
    assert service.queue_all([]) == {'run': None, 'reused': False}
    parent = service.queue_all([selected[1], first, selected[1]])['run']
    assert [item['source_id'] for item in items(store, parent['id'])] == [selected[1], first]
    assert selected[2] not in {item['source_id'] for item in items(store, parent['id'])}
    service.advance_all()
    for _ in range(2):
        assert execute(store, service)['status'] == 'succeeded'
    service.advance_all()
    assert service.queue_all([first, selected[1]]) == {'run': None, 'reused': False}
    assert len(models.calls) == 2


def test_concurrent_duplicate_requests_reuse_one_frozen_parent(environment):
    store, models, service, _ = environment
    selected = sources(environment, 3)
    barrier = Barrier(2)
    def create(_):
        barrier.wait(timeout=10)
        return service.queue_all(selected)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))
    assert results[0]['run']['id'] == results[1]['run']['id']
    assert sorted(result['reused'] for result in results) == [False, True]
    assert len(service.all_runs()['runs']) == 1 and models.calls == []
    service.advance_all()
    assert service.queue_all(selected)['reused']
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='wiki_build_all'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='wiki_build'").fetchone()[0] == 3


def test_cancel_stops_pending_batches_and_keeps_external_jobs(environment):
    store, models, service, _ = environment
    selected = sources(environment, 3)
    parent = service.queue_all(selected)['run']
    external = service.queue([selected[0]])['jobs'][0]
    service.advance_all()
    assert items(store, parent['id'])[0]['reason'] == 'already_active'
    owned = [item['child_job_id'] for item in items(store, parent['id']) if item['owned']]
    assert len(owned) == 2
    assert store.cancel(parent['id'])['status'] == 'cancelled'
    assert store.job(external['id'])['status'] == 'queued'
    assert all(store.job(job)['status'] == 'cancelled' for job in owned)
    assert not service.advance_all()
    assert execute(store, service)['id'] == external['id']
    assert len(models.calls) == 1
    with pytest.raises(ValueError, match='单篇'):
        store.retry(parent['id'])


def test_parent_cancel_during_model_reply_fences_child_publication(environment):
    store, models, service, _ = environment
    selected = sources(environment, 2)
    parent = service.queue_all(selected)['run']
    service.advance_all()
    models.callback = lambda: store.cancel(parent['id'])
    assert execute(store, service)['status'] == 'cancelled'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_publications').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM wiki_pages').fetchone()[0] == 0
    assert service.all_run(parent['id'])['counts']['cancelled'] == 2
    assert not service.advance_all() and store.claim() is None
    assert len(models.calls) == 1


def test_model_and_prompts_remain_frozen_across_later_batches(environment):
    store, models, service, _ = environment
    selected = sources(environment, 21)
    parent = service.queue_all(selected)['run']
    with store.connect() as db:
        frozen = db.execute('SELECT frozen_json FROM wiki_all_runs WHERE job_id=?', (parent['id'],)).fetchone()[0]
    assert 'never-persist-secret' not in frozen
    service.advance_all()
    for _ in range(20):
        assert execute(store, service)['status'] == 'succeeded'
    models.config['version'] = 9
    prompts = PromptLibrary(store)
    prompt = next(item for item in prompts.list()['items'] if item['id'] == 'wiki_compile')
    prompts.update('wiki_compile', prompt['content'] + '\n后改提示词不得进入冻结批次。', prompt['version'])
    service.advance_all()
    assert execute(store, service)['status'] == 'succeeded'
    service.advance_all()
    assert all(config['version'] == 1 for config, _ in models.calls)
    assert all('后改提示词不得进入冻结批次' not in messages[0]['content'] for _, messages in models.calls)
    with store.connect() as db:
        assert db.execute('SELECT frozen_json FROM wiki_all_runs WHERE job_id=?', (parent['id'],)).fetchone()[0] == frozen
        assert {row[0] for row in db.execute('SELECT model_version FROM wiki_runs')} == {1}


def test_recovery_resumes_existing_batch_and_checkpoints_without_duplicate_children(environment):
    store, models, service, _ = environment
    selected = sources(environment, 24)
    parent = service.queue_all(selected)['run']
    service.advance_all()
    for _ in range(3):
        assert execute(store, service)['status'] == 'succeeded'
    interrupted = store.claim()
    assert interrupted['kind'] == 'wiki_build'
    before_ids = [item['child_job_id'] for item in items(store, parent['id'])]
    store.recover()
    restarted = WikiService(Store(store.root), models)
    restarted.advance_all()
    assert [item['child_job_id'] for item in items(store, parent['id'])] == before_ids
    for _ in range(17):
        assert execute(store, restarted)['status'] == 'succeeded'
    restarted.advance_all()
    for _ in range(4):
        assert execute(store, restarted)['status'] == 'succeeded'
    restarted.advance_all()
    final = restarted.all_run(parent['id'])
    assert final['job']['status'] == 'succeeded' and final['counts']['succeeded'] == 24
    assert final['batches'] == 2 and len(models.calls) == 24
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_runs').fetchone()[0] == 24


def test_failed_child_does_not_loop_or_stop_later_batches(environment):
    store, models, service, _ = environment
    parent = service.queue_all(sources(environment, 21))['run']
    service.advance_all()
    models.fail_at = 1
    assert execute(store, service)['status'] == 'failed'
    for _ in range(19):
        assert execute(store, service)['status'] == 'succeeded'
    service.advance_all()
    assert execute(store, service)['status'] == 'succeeded'
    service.advance_all()
    final = service.all_run(parent['id'])
    assert final['job']['status'] == 'failed'
    assert final['counts']['succeeded'] == 20 and final['counts']['failed'] == 1
    assert len(models.calls) == 21 and not service.advance_all() and store.claim() is None


def test_explicit_single_child_retry_detaches_ownership_and_survives_parent_cancel(environment):
    store, models, service, _ = environment
    parent = service.queue_all(sources(environment, 2))['run']
    service.advance_all()
    models.fail_at = 1
    failed = execute(store, service)
    assert failed['status'] == 'failed'
    with store.connect() as db:
        prompt = db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (failed['id'],)).fetchone()[0]
        run_id = db.execute('SELECT id FROM wiki_runs WHERE job_id=?', (failed['id'],)).fetchone()[0]
    store.retry(failed['id'])
    store.cancel(parent['id'])
    assert store.job(failed['id'])['status'] == 'queued'
    models.fail_at = None
    assert execute(store, service)['id'] == failed['id']
    assert store.job(failed['id'])['status'] == 'succeeded'
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (failed['id'],)).fetchone()[0])
        assert 'wikiAllRunId' not in payload
        assert db.execute('SELECT id FROM wiki_runs WHERE job_id=?', (failed['id'],)).fetchone()[0] == run_id
        assert db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (failed['id'],)).fetchone()[0] == prompt


def test_changed_deleted_and_new_sources_do_not_replace_or_expand_frozen_scope(environment):
    store, models, service, first = environment
    selected = sources(environment, 3)
    parent = service.queue_all(selected)['run']
    original_revision = items(store, parent['id'])[0]['revision_id']
    assert publish(store, ['更新原文版本仍保留具体证据，但不得替代当前批次的固定来源版本。']) == first
    assert remove(store, selected[1])['deleted']
    added = publish(store, url='https://example.org/new-outside-frozen')
    assert added not in {item['source_id'] for item in items(store, parent['id'])}
    service.advance_all()
    assert execute(store, service)['status'] == 'succeeded'
    service.advance_all()
    final = service.all_run(parent['id'])
    assert final['job']['status'] == 'succeeded'
    assert final['counts']['succeeded'] == 1 and final['counts']['skipped'] == 2
    records = items(store, parent['id'])
    assert records[0]['revision_id'] == original_revision and records[0]['reason'] == 'source_changed'
    assert records[1]['reason'] == 'source_deleted' and records[1]['child_job_id'] is None
    assert len(models.calls) == 1


def test_deleting_one_terminal_source_keeps_parent_audit_and_unrelated_sources(environment):
    store, _, service, _ = environment
    selected = sources(environment, 2)
    parent = service.queue_all(selected)['run']
    service.advance_all()
    for _ in selected:
        assert execute(store, service)['status'] == 'succeeded'
    service.advance_all()
    assert remove(store, selected[0])['deleted']
    assert service.all_run(parent['id'])['job']['status'] == 'succeeded'
    assert service.all_run(parent['id'])['total'] == 2
    assert store.detail(selected[1])['id'] == selected[1]
    assert items(store, parent['id'])[0]['reason'] == 'source_deleted'
