"""Whole-draft requests keep their assembled instructions across upgrades."""
import copy
import json
import math

import pytest

from server import prompt_defaults
from server import writing_document
from server.store import dumps
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.writing import CITATION_PLACEMENT


def record(gateway):
    calls = []
    complete = gateway.complete
    def capture(config, messages):
        if json.loads(messages[1]['content']).get('task') != 'writing_citation_match':
            calls.append(copy.deepcopy(messages))
        return complete(config, messages)
    gateway.complete = capture
    return calls


def restore_previous_document_protocol(db, run_id, frozen):
    """Build the actual old prepared shape rather than a hybrid test packet."""
    frozen.pop('documentPromptProtocol')
    for row in db.execute('SELECT section_id,input_json FROM writing_document_steps WHERE run_id=?', (run_id,)).fetchall():
        chapter = json.loads(row['input_json'])
        chapter['messages'][0]['content'] = chapter.pop('sectionInstructions')
        for field in ('documentPromptProtocol', 'documentPrompt', 'documentOrchestration'):
            chapter.pop(field)
        packet = json.loads(chapter['messages'][1]['content'])
        for field in ('documentPromptProtocol', 'documentPromptId', 'documentPromptVersion'):
            packet.pop(field)
        chapter['messages'][1]['content'] = dumps(packet)
        db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                   (dumps(chapter), run_id, row['section_id']))


def test_new_document_freezes_complete_requirements_without_runtime_reassembly(document, monkeypatch):
    store, gateway, service, _ = document
    calls = record(gateway)
    project = ready(document, words=(100, 100), modes=('strict', 'extended'))
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
    expected = frozen['documentRequirements']
    assert frozen['documentPromptProtocol'] == 'document-mode-prompts-v1'
    assert 'writingInstructions' in expected
    def forbidden(*args, **kwargs):
        raise AssertionError('Queued whole-draft requirements must not be reassembled')
    monkeypatch.setattr(writing_document, 'prompt_text', forbidden)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert len(calls) == 1
    assert calls[0][0]['content'] == expected
    packet = json.loads(calls[0][1]['content'])
    assert packet['requirements'] == expected
    assert [section['documentPromptId'] for section in packet['sections']] == [
        'writing_document_strict', 'writing_document_extended']


def test_previous_mode_split_document_keeps_its_exact_old_default(document):
    store, gateway, service, _ = document
    calls = record(gateway)
    project = ready(document, words=(100, 100), modes=('strict', 'extended'))
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        frozen.pop('documentRequirements')
        restore_previous_document_protocol(db, run['runId'], frozen)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
        snapshot = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (job['id'],)).fetchone()[0])
        snapshot.pop('writing_document_strict')
        snapshot.pop('writing_document_extended')
        snapshot['writing_document'] = {'content': prompt_defaults.LEGACY_MODE_WRITING_DOCUMENT, 'version': 1}
        db.execute('UPDATE job_prompt_snapshots SET snapshot_json=? WHERE job_id=?', (dumps(snapshot), job['id']))
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert len(calls) == 1
    assert calls[0][0]['content'] == prompt_defaults.LEGACY_MODE_WRITING_DOCUMENT
    packet = json.loads(calls[0][1]['content'])
    assert packet['requirements'] == prompt_defaults.LEGACY_MODE_WRITING_DOCUMENT
    assert all('documentInstructions' not in section for section in packet['sections'])


def test_previous_complete_requirements_and_prepared_instructions_are_not_replaced(document, monkeypatch):
    store, gateway, service, _ = document
    calls = record(gateway)
    project = ready(document, words=(100, 100), modes=('strict', 'extended'))
    run, job = queue_and_claim(document, project)
    previous_requirements = prompt_defaults.WRITING_DOCUMENT + '\n历史完整编排冻结甲'
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        restore_previous_document_protocol(db, run['runId'], frozen)
        frozen['documentRequirements'] = previous_requirements
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))

    def forbidden(*args, **kwargs):
        raise AssertionError('Historical complete requirements must remain frozen')

    monkeypatch.setattr(writing_document, 'prompt_text', forbidden)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert len(calls) == 1 and calls[0][0]['content'] == previous_requirements
    packet = json.loads(calls[0][1]['content'])
    assert packet['requirements'] == previous_requirements
    assert all(section['writingInstructions'] and 'documentInstructions' not in section
               for section in packet['sections'])


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_whole_draft_and_sequential_calls_share_frozen_sentence_anchor_protocol(document, strategy):
    store, gateway, service, _ = document
    calls = record(gateway)
    gateway.config['maxOutputTokens'] = 8000 if strategy == 'single' else 30000
    project = ready(document, words=(600, 600) if strategy == 'single' else (5000, 5000))
    suffix = '这一版本也保留原始段落。'

    def sentence_anchors(result, payload):
        sections = result['sections'] if 'sections' in result else [result]
        for section in sections:
            paragraph = section['paragraphs'][0]
            paragraph['text'] += suffix
            paragraph['evidence'].append({**paragraph['evidence'][0], 'claim': suffix})
        return result

    gateway.transform = sentence_anchors
    run, job = queue_and_claim(document, project)
    assert run['project']['runs'][0]['candidate']['document']['strategy'] == strategy
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert len(calls) == (1 if strategy == 'single' else 2)
    for messages in calls:
        packet = json.loads(messages[1]['content'])
        if strategy == 'single':
            assert 'claim' in packet['outputSchema']['sections'][0]['paragraphs'][0]['evidence'][0]
            assert all(section['citationPlacement'] == CITATION_PLACEMENT for section in packet['sections'])
        else:
            assert packet['citationPlacement'] == CITATION_PLACEMENT
    for section in service.get(project['id'])['sections']:
        assert '[E1]' + suffix + '[E1]' in section['content']
        assert len(section['evidence']) == 1


def test_legacy_whole_draft_reassembly_does_not_add_sentence_anchor_schema(document):
    store, _, service, _ = document
    project = ready(document, words=(100, 100))
    run, _ = queue_and_claim(document, project)
    with store.connect() as db:
        prepared = [json.loads(row['input_json']) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],))]
    for item in prepared:
        packet = json.loads(item['messages'][1]['content'])
        packet.pop('citationPlacement')
        packet['outputSchema']['paragraphs'][0]['evidence'][0].pop('claim')
        item['messages'][1]['content'] = dumps(packet)
    assembled = service._document_messages(project, prepared)
    packet = json.loads(assembled[1]['content'])
    assert all('citationPlacement' not in section for section in packet['sections'])
    assert 'claim' not in packet['outputSchema']['sections'][0]['paragraphs'][0]['evidence'][0]


@pytest.mark.parametrize('legacy', [False, True])
def test_plan_counts_sentence_anchor_output_and_keeps_legacy_packet_allowance(document, monkeypatch, legacy):
    _, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 45000
    project = ready(document, words=(5000, 5000))
    if legacy:
        current_messages = service._messages

        def historical_messages(*args, **kwargs):
            messages, blocks = current_messages(*args, **kwargs)
            packet = json.loads(messages[1]['content'])
            packet.pop('citationPlacement')
            packet['outputSchema']['paragraphs'][0]['evidence'][0].pop('claim')
            messages[0]['content'] = '旧版写作要求，仅返回 blockId 和 quote。'
            messages[1]['content'] = dumps(packet)
            return messages, blocks

        monkeypatch.setattr(service, '_messages', historical_messages)
    plan = service.generation_plan(project['id'])
    factor = 2.8 if legacy else 4.2
    assert plan['canGenerate']
    assert plan['estimatedOutputTokens'] == math.ceil(10000 * factor + 256 + 160 * 2)
    assert all(section['estimatedOutputTokens'] == math.ceil(5000 * factor + 416)
               for section in plan['eligibleSections'])
    assert plan['strategy'] == ('single' if legacy else 'sequential')
    assert not gateway.calls


def test_sentence_anchor_output_can_block_a_chapter_that_old_allowance_would_fit(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 4096
    project = ready(document, words=(800,))
    before = store.jobs()['counts']
    plan = service.generation_plan(project['id'])
    assert math.ceil(800 * 2.8 + 416) <= plan['safeOutputTokens']
    assert plan['eligibleSections'][0]['estimatedOutputTokens'] == math.ceil(800 * 4.2 + 416)
    assert not plan['canGenerate']
    assert any('单章输出预算' in reason for reason in plan['blockingReasons'])
    with pytest.raises(ValueError, match='单章输出预算'):
        service.queue_document(project['id'], project['version'])
    assert not gateway.calls and store.jobs()['counts'] == before


def test_legacy_persisted_plan_is_not_reestimated_when_sentence_protocol_changes(document, monkeypatch):
    store, _, service, _ = document
    project = ready(document, words=(100, 100))
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        for row in db.execute('SELECT section_id,input_json FROM writing_document_steps WHERE run_id=?', (run['runId'],)).fetchall():
            chapter = json.loads(row['input_json'])
            packet = json.loads(chapter['messages'][1]['content'])
            packet.pop('citationPlacement')
            packet['outputSchema']['paragraphs'][0]['evidence'][0].pop('claim')
            chapter['messages'][1]['content'] = dumps(packet)
            db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                       (dumps(chapter), run['runId'], row['section_id']))
        frozen['plan']['estimatedOutputTokens'] = math.ceil(200 * 2.8 + 256 + 160 * 2)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))

    def forbidden(*args, **kwargs):
        raise AssertionError('A queued document must use its persisted output plan')

    monkeypatch.setattr(writing_document, '_output_tokens_per_word', forbidden)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert service.get(project['id'])['runs'][0]['plan'] == frozen['plan']
