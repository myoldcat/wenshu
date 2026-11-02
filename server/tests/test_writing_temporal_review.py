"""Frozen semantic time review; controlled models do not prove real accuracy."""
import copy
import json

import pytest

from server import writing_citation_matching as matching
from server.store import dumps
from server.tests.test_writing_integrity import writing, enqueue as queue_current, freeze_legacy_inline_review
from server.tests.test_writing_document import document, ready, queue_and_claim as queue_current_document
from server.tests.test_writing_citation_tasks import environment, queued


DATE = '2021-04-09T00:00:00+08:00'


def enqueue(environment):
    """Temporal snapshots of historical inline-review generation stay frozen."""
    result, job = queue_current(environment)
    freeze_legacy_inline_review(environment[0], result['runId'])
    return result, job


def queue_and_claim(environment, project):
    result, job = queue_current_document(environment, project)
    freeze_legacy_inline_review(environment[0], result['runId'], whole_document=True)
    return result, job


def frozen_run(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


def test_new_strict_section_uses_frozen_source_date_in_generation_and_independent_review(writing, monkeypatch):
    store, gateway, service, source, project = writing
    with store.connect() as db:
        db.execute('UPDATE revisions SET published_at=? WHERE source_id=?', (DATE, source))
        draft = service._load(db, project['id'])
        draft['brief']['asOf'] = '2026-10-23'
        service._save(db, draft, 'controlled_observation_time')
    run, job = enqueue(writing)
    frozen = frozen_run(store, run['runId'])
    generation = json.loads(frozen['messages'][1]['content'])
    review = frozen['citationTemporalReview']
    assert review == generation['citationTemporalReview']
    assert review['sourceContext'] == generation['sourceContext']
    assert review['sourceContext'][0]['publishedAt'] == DATE
    assert review['asOf'] == '2026-10-23'
    assert '逐字照抄' in review['instruction'] and '不能独立支持事件事实' in review['instruction']
    original_policy = copy.deepcopy(matching.matching_policy('strict'))
    monkeypatch.setattr(matching, 'TEMPORAL_REVIEW_INSTRUCTION', '未来任务新指令。')
    with store.connect() as db:
        db.execute('UPDATE revisions SET published_at=? WHERE source_id=?', ('2099-01-01', source))
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    messages = gateway.matching_calls[0][1]
    packet = json.loads(messages[1]['content'])
    assert packet['temporalReview'] == review
    assert review['instruction'] in messages[0]['content'] and '未来任务新指令' not in messages[0]['content']
    assert packet['policy'] == original_policy == matching.matching_policy('strict')


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_new_document_steps_freeze_time_review_only_for_actual_strict_mode(document, strategy):
    store, gateway, service, source = document
    gateway.config['maxOutputTokens'] = 8000 if strategy == 'single' else 1800
    with store.connect() as db:
        db.execute('UPDATE revisions SET published_at=? WHERE source_id=?', (DATE, source))
    project = ready(document, words=(200, 200), modes=('strict', 'extended'))
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        steps = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],))]
    assert frozen_run(store, run['runId'])['plan']['strategy'] == strategy
    assert steps[0]['citationTemporalReview']['sourceContext'][0]['publishedAt'] == DATE
    assert 'asOf' not in steps[0]['citationTemporalReview']
    assert 'citationTemporalReview' not in steps[1]
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    sent = (gateway.calls[0]['payload']['sections'] if strategy == 'single'
            else [call['payload'] for call in gateway.calls])
    assert sent[0]['citationTemporalReview'] == steps[0]['citationTemporalReview']
    assert 'citationTemporalReview' not in sent[1]
    packet = gateway.matching_calls[0]['payload']
    assert packet['temporalReview'] == steps[0]['citationTemporalReview']


@pytest.mark.parametrize('damage', ['packet_missing', 'frozen_missing', 'mismatched_date'])
def test_mixed_generation_time_contract_is_rejected_before_matching(writing, damage):
    store, gateway, service, _, _ = writing
    run, job = enqueue(writing)
    frozen = frozen_run(store, run['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    if damage == 'packet_missing':
        packet.pop('citationTemporalReview')
    elif damage == 'frozen_missing':
        frozen.pop('citationTemporalReview')
    else:
        packet['citationTemporalReview']['sourceContext'][0]['publishedAt'] = '2099-01-01'
    frozen['messages'][1]['content'] = dumps(packet)
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert not gateway.matching_calls
    assert all(not section['content'] for section in service.get(frozen['projectId'])['sections'])


def test_older_strict_generation_without_time_fields_keeps_exact_saved_matching_instruction(writing, monkeypatch):
    store, gateway, service, _, _ = writing
    run, job = enqueue(writing)
    frozen = frozen_run(store, run['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    packet.pop('citationTemporalReview')
    frozen.pop('citationTemporalReview')
    frozen['messages'][1]['content'] = dumps(packet)
    frozen['citationMatchInstructions'] = '历史冻结的独立引用审阅。'
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
    monkeypatch.setattr(matching, 'TEMPORAL_REVIEW_INSTRUCTION', '新任务专用的时点指令。')
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    messages = gateway.matching_calls[0][1]
    assert messages[0]['content'] == frozen['citationMatchInstructions']
    assert 'temporalReview' not in json.loads(messages[1]['content'])


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_older_whole_document_steps_without_time_fields_keep_saved_matching_messages(document, strategy, monkeypatch):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 8000 if strategy == 'single' else 1800
    project = ready(document, words=(200, 200), modes=('strict', 'extended'))
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        rows = db.execute('SELECT section_id,input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],)).fetchall()
        for row in rows:
            prepared = json.loads(row['input_json'])
            prepared.pop('citationTemporalReview', None)
            packet = json.loads(prepared['messages'][1]['content'])
            packet.pop('citationTemporalReview', None)
            prepared['messages'][1]['content'] = dumps(packet)
            prepared['citationMatchInstructions'] = '旧一次成文冻结引用指令。'
            db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                       (dumps(prepared), run['runId'], row['section_id']))
    monkeypatch.setattr(matching, 'TEMPORAL_REVIEW_INSTRUCTION', '旧全文不能回填的新时点规则。')
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    calls = [service.operations.call(item['id'])['call'] for item in service.operations.calls(run['runId'])['items']]
    reviewed = [call for call in calls if json.loads(call['messages'][1]['content'])['task'] == 'writing_citation_match']
    assert len(reviewed) == 1
    assert reviewed[0]['messages'][0]['content'] == '旧一次成文冻结引用指令。'
    assert 'temporalReview' not in json.loads(reviewed[0]['messages'][1]['content'])


def test_new_manual_rematch_freezes_date_and_messages_and_keeps_plain_body(environment, monkeypatch):
    store, gateway, service, project = environment
    with store.connect() as db:
        db.execute('UPDATE revisions SET published_at=?', (DATE,))
    run, job = queued(environment)
    frozen = frozen_run(store, run['runId'])
    reviews = frozen['citationTemporalReviews']
    with store.connect() as db:
        steps = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_citation_match_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],))]
        db.execute('UPDATE revisions SET published_at=?', ('2099-01-01',))
    assert all(step['packet']['temporalReview'] == reviews[step['packet']['sectionId']] for step in steps)
    assert all(step['citationTemporalReview']['sourceContext'][0]['publishedAt'] == DATE for step in steps)
    monkeypatch.setattr(matching, 'TEMPORAL_REVIEW_INSTRUCTION', '运行时不能替换冻结指令。')
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert all(call[1][0]['content'] == step['messages'][0]['content'] for call, step in zip(gateway.calls, steps))
    assert all('运行时不能替换冻结指令' not in call[1][0]['content'] for call in gateway.calls)
    assert all('本次冻结' not in section['content'] for section in service.get(project['id'])['sections'])


@pytest.mark.parametrize('damage', ['snapshot', 'messages'])
def test_manual_rematch_rejects_changed_frozen_time_snapshot_or_system_message(environment, damage):
    store, gateway, service, _ = environment
    run, job = queued(environment)
    with store.connect() as db:
        row = db.execute('SELECT section_id,batch_index,input_json FROM writing_citation_match_steps WHERE run_id=? ORDER BY ordinal LIMIT 1', (run['runId'],)).fetchone()
        prepared = json.loads(row['input_json'])
        if damage == 'snapshot':
            prepared['citationTemporalReview']['sourceContext'][0]['publishedAt'] = '2099-01-01'
        else:
            prepared['messages'][0]['content'] += '额外运行时规则。'
        db.execute('UPDATE writing_citation_match_steps SET input_json=? WHERE run_id=? AND section_id=? AND batch_index=?',
                   (dumps(prepared), run['runId'], row['section_id'], row['batch_index']))
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert not gateway.calls


def test_old_manual_rematch_without_time_fields_does_not_acquire_them(environment, monkeypatch):
    store, gateway, service, _ = environment
    run, job = queued(environment)
    frozen = frozen_run(store, run['runId'])
    frozen.pop('citationTemporalReviews')
    frozen.pop('citationReviewPolicy', None)
    frozen['instructions'] = '旧引用任务冻结指令。'
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
        for row in db.execute('SELECT section_id,batch_index,input_json FROM writing_citation_match_steps WHERE run_id=?', (run['runId'],)).fetchall():
            prepared = json.loads(row['input_json'])
            prepared.pop('citationTemporalReview')
            prepared['packet'].pop('temporalReview'); prepared['packet'].pop('temporalReviewHash')
            prepared['packet'].pop('reviewScope', None)
            prepared['messages'] = matching.matching_messages(prepared['packet'], instruction=frozen['instructions'])
            db.execute('UPDATE writing_citation_match_steps SET input_json=? WHERE run_id=? AND section_id=? AND batch_index=?',
                       (dumps(prepared), run['runId'], row['section_id'], row['batch_index']))
    monkeypatch.setattr(matching, 'TEMPORAL_REVIEW_INSTRUCTION', '不能回填的新时点指令。')
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert all(call[1][0]['content'] == frozen['instructions'] for call in gateway.calls)
    assert all('temporalReview' not in json.loads(call[1][1]['content']) for call in gateway.calls)


def test_completed_rematch_checkpoint_cannot_be_reused_under_a_different_time_contract(environment):
    store, gateway, service, project = environment
    run, job = queued(environment)
    gateway.fail_calls = {2}
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    with store.connect() as db:
        row = db.execute('SELECT section_id,batch_index,result_json FROM writing_citation_match_steps WHERE run_id=? AND result_json IS NOT NULL LIMIT 1', (run['runId'],)).fetchone()
        checked = json.loads(row['result_json'])
        checked.pop('temporalReviewHash')
        db.execute('UPDATE writing_citation_match_steps SET result_json=? WHERE run_id=? AND section_id=? AND batch_index=?',
                   (dumps(checked), run['runId'], row['section_id'], row['batch_index']))
    store.retry(job['id'])
    service.process(store.claim())
    assert store.job(job['id'])['status'] == 'failed'
    assert service.get(project['id'])['version'] == project['version']


def test_time_review_metadata_cannot_be_changed_after_hashing():
    packet = matching.build_matching_packet('机构当时表示调查仍在进行。', [{'id': 'E1', 'quote': '调查仍在进行。'}], mode='strict')
    snapshot = matching.temporal_review_snapshot('strict', [{'sourceId': 'source', 'revisionId': 'revision', 'publishedAt': None}])
    packet = matching.with_frozen_temporal_review(packet, {'citationTemporalReview': snapshot})
    checked = matching.validate_matching_result(packet, {'assignments': [{
        'sentenceId': 'S1', 'evidenceIds': ['E1'], 'support': 'partial', 'supportBasis': 'direct',
        'reason': '受控模型指出正文缺少通报当时的历史限定。'}]})
    assert checked['issues'] and checked['matches'][0]['status'] == 'partial'
    packet['temporalReview']['sourceContext'][0]['publishedAt'] = '2099-01-01'
    with pytest.raises(ValueError, match='冻结时点规则'):
        matching.matching_messages(packet, instruction='已冻结的基准指令。')
