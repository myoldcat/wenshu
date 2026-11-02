"""Whole-draft catalogue prompts retain chapter-local modes and queue-time text.

Controlled providers and isolated stores prove routing, persisted snapshots,
budgeting and structural validation, without contacting a configured model.
"""
import copy
import json

import pytest

from server import prompt_defaults, writing_document
from server.prompts import (PromptLibrary, legacy_prompt_text, writing_prompt_id,
                            STYLE_PROMPT_IDS, OFFICIAL_BODY_PROMPT_ID)
from server.store import Store, dumps
from server.tests.test_writing_citation_contract import add_source
from server.tests.test_writing_document import document, queue_and_claim, composition_context_reserve
from server.tests.test_writing_integrity import freeze_legacy_generation_output
from server.writing import WritingService


def project_for_modes(document, default, modes, *, words=200, source=None):
    _, gateway, service, default_source = document
    project = service.create({'title': '一次成文模式验收', 'brief': {'topic': '原文版本与人工修订', 'targetWords': words * len(modes)},
                              'mode': default, 'sourceIds': [source or default_source],
                              'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': '资料治理' + str(index + 1), 'goal': '保存原始资料版本并保留人工修订',
         'mode': mode, 'targetWords': words} for index, mode in enumerate(modes)])
    return service.confirm(project['id'], project['version'])


def edit(store, prompt_id, marker):
    library = PromptLibrary(store)
    item = next(item for item in library.list()['items'] if item['id'] == prompt_id)
    return library.update(prompt_id, item['content'] + '\n' + marker, item['version'])


def record(gateway):
    observed = []
    complete = gateway.complete

    def capture(config, messages):
        if json.loads(messages[1]['content']).get('task') != 'writing_citation_match':
            observed.append(copy.deepcopy(messages))
        return complete(config, messages)

    gateway.complete = capture
    return observed


def frozen(store, run_id, job_id):
    with store.connect() as db:
        project = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])
        steps = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run_id,))]
        prompts = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (job_id,)).fetchone()[0])
    return project, steps, prompts


def chapter_instructions(packet, section, kind):
    reference = section.get('writingInstructionRef' if kind == 'section' else 'documentInstructionRef')
    if reference:
        item = packet['instructionCatalog'][reference]
        assert item['mode'] == section['mode'] and item['kind'] == kind
        assert item['promptId'] == section['promptId' if kind == 'section' else 'documentPromptId']
        return item['content']
    return section['writingInstructions' if kind == 'section' else 'documentInstructions']


@pytest.mark.parametrize(('default', 'modes', 'actual'), [
    ('strict', ('inherit', 'inherit'), ('strict', 'strict')),
    ('extended', ('inherit', 'inherit'), ('extended', 'extended')),
    ('strict', ('inherit', 'extended'), ('strict', 'extended')),
    ('extended', ('strict', 'inherit'), ('strict', 'extended')),
    ('strict', ('extended', 'strict'), ('extended', 'strict')),
    ('extended', ('strict', 'strict'), ('strict', 'strict')),
])
def test_single_draft_routes_only_actual_chapter_modes_and_freezes_versions(document, default, modes, actual):
    store, gateway, service, _ = document
    # Mode routing needs room for the expanded official-style body contract;
    # small-capacity rejection is covered by test_writing_official_prose.
    gateway.config['contextChars'] = 26000
    calls = record(gateway)
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('document', mode), mode + '整稿队列甲')
        edit(store, writing_prompt_id('section', mode), mode + '章队列甲')
    project = project_for_modes(document, default, modes)
    run, job = queue_and_claim(document, project)
    envelope, steps, prompts = frozen(store, run['runId'], job['id'])
    expected_ids = {writing_prompt_id(kind, mode) for kind in ('section', 'document') for mode in actual}
    style_ids = {STYLE_PROMPT_IDS['official'], OFFICIAL_BODY_PROMPT_ID}
    preference = envelope['preferenceSnapshot']
    auxiliary = {**preference['stylePromptSnapshot'], **preference['structurePromptSnapshot'],
                 **envelope['compositionSnapshot']['compositionPromptSnapshot']}
    assert set(prompts) == expected_ids | set(auxiliary)
    assert all(prompts[prompt_id]['version'] == 2 for prompt_id in expected_ids)
    assert all(prompts[prompt_id]['version'] == 1 for prompt_id in auxiliary)
    assert {prompt_id: prompts[prompt_id] for prompt_id in auxiliary} == auxiliary
    assert {prompt_id: prompts[prompt_id] for prompt_id in style_ids} == envelope['preferenceSnapshot']['stylePromptSnapshot']
    assert envelope['citationReviewPolicy'] == 'deferred-to-check-v1'
    assert all(step.get('citationReviewPolicy') == 'deferred-to-check-v1'
               and 'citationMatchInstructions' not in step for step in steps)
    assert 'writing_document' not in prompts
    assert envelope['documentPromptProtocol'] == writing_document.DOCUMENT_MODE_PROMPT_PROTOCOL
    assert all(step['documentPrompt']['version'] == 2 for step in steps)
    assert [step['documentPrompt']['mode'] for step in steps] == list(actual)

    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('document', mode), mode + '整稿未来乙')
        edit(store, writing_prompt_id('section', mode), mode + '章未来乙')
    WritingService(Store(store.root), gateway).process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert len(calls) == 1
    system, packet = calls[0][0]['content'], json.loads(calls[0][1]['content'])
    assert system == envelope['documentRequirements']
    assert '整稿队列甲' not in system and '章队列甲' not in system
    assert '整稿未来乙' not in dumps(calls) and '章未来乙' not in dumps(calls)
    for section, mode, step in zip(packet['sections'], actual, steps):
        other = 'extended' if mode == 'strict' else 'strict'
        assert section['mode'] == mode
        assert section['documentPromptId'] == writing_prompt_id('document', mode)
        assert section['documentPromptVersion'] == 2
        document_instructions = chapter_instructions(packet, section, 'document')
        writing_instructions = chapter_instructions(packet, section, 'section')
        assert document_instructions == step['documentPrompt']['content']
        assert mode + '整稿队列甲' in document_instructions
        assert other + '整稿队列甲' not in document_instructions
        assert mode + '章队列甲' in writing_instructions
        assert other + '章队列甲' not in writing_instructions


def test_sequential_execution_uses_corresponding_frozen_document_and_section_prompts(document, monkeypatch):
    store, gateway, service, _ = document
    calls = record(gateway)
    gateway.config['maxOutputTokens'] = 30000
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('document', mode), mode + '顺序整稿甲')
        edit(store, writing_prompt_id('section', mode), mode + '顺序章甲')
    project = project_for_modes(document, 'extended', ('strict', 'inherit'), words=5000)
    run, job = queue_and_claim(document, project)
    envelope, steps, _ = frozen(store, run['runId'], job['id'])
    assert envelope['plan']['strategy'] == 'sequential'
    monkeypatch.setattr(prompt_defaults, 'WRITING_DOCUMENT_ORCHESTRATION', '未来编排不得替换冻结规则')

    def forbidden(*args, **kwargs):
        raise AssertionError('The queued generation must not rebuild document prompts')

    monkeypatch.setattr(writing_document, 'prompt_text', forbidden)
    WritingService(Store(store.root), gateway).process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    _, dispatched, _ = frozen(store, run['runId'], job['id'])
    assert calls == [step['messages'] for step in dispatched]
    for queued_step, sent_step in zip(steps, dispatched):
        assert sent_step['messages'][0] == queued_step['messages'][0]
        queued_packet = json.loads(queued_step['messages'][1]['content'])
        sent_packet = json.loads(sent_step['messages'][1]['content'])
        queued_packet.pop('chapterContext')
        sent_packet.pop('chapterContext')
        assert sent_packet == queued_packet
    for messages, mode in zip(calls, ('strict', 'extended')):
        assert mode + '顺序整稿甲' in messages[0]['content']
        assert mode + '顺序章甲' in messages[0]['content']
        assert ('extended' if mode == 'strict' else 'strict') + '顺序整稿甲' not in messages[0]['content']
        assert '未来编排' not in messages[0]['content']
        packet = json.loads(messages[1]['content'])
        assert packet['mode'] == mode and packet['documentPromptId'] == writing_prompt_id('document', mode)


def test_single_execution_does_not_reassemble_common_or_mode_prompts_after_queue(document, monkeypatch):
    store, gateway, service, _ = document
    calls = record(gateway)
    project = project_for_modes(document, 'strict', ('strict', 'extended'))
    run, job = queue_and_claim(document, project)
    envelope, steps, _ = frozen(store, run['runId'], job['id'])
    expected = service._document_messages(envelope, steps)
    monkeypatch.setattr(prompt_defaults, 'WRITING_DOCUMENT_ORCHESTRATION', '未来规则')

    def forbidden(*args, **kwargs):
        raise AssertionError('Frozen mode prompts must never consult current prompt text')

    monkeypatch.setattr(writing_document, 'prompt_text', forbidden)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert calls == [expected]


def test_mode_prompt_overhead_is_counted_for_single_and_sequential_input(document):
    store, gateway, service, _ = document
    gateway.config['contextChars'] = 200000
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('document', mode), mode + '整稿预算' + '章' * 3900)
    gateway.config['maxOutputTokens'] = 65536
    project = project_for_modes(document, 'strict', ('strict', 'extended'), words=5000)
    with store.connect() as db:
        first_plan, first_steps = service._document_plan(db, project, gateway.config)
    limit = max(sum(len(message['content']) for message in item['messages']) + composition_context_reserve(item)
                for item in first_steps) + 1
    assert first_plan['estimatedInputChars'] > limit
    gateway.config['contextChars'] = limit
    with store.connect() as db:
        plan, steps = service._document_plan(db, project, gateway.config)
        messages = service._document_messages(project, steps, db=db)
    assert plan['canGenerate'], plan
    assert plan['strategy'] == 'sequential'
    assert plan['estimatedInputChars'] == sum(len(message['content']) for message in messages)
    assert all(sum(len(message['content']) for message in item['messages']) < limit for item in steps)
    assert gateway.calls == []


def test_oversized_applicable_document_prompt_blocks_before_creating_job(document):
    store, gateway, service, _ = document
    edit(store, 'writing_document_strict', '整稿输入预算' + '章' * 12000)
    gateway.config['contextChars'] = 8000
    project = project_for_modes(document, 'strict', ('inherit',), words=100)
    before = store.jobs()['counts']
    plan = service.generation_plan(project['id'])
    assert not plan['canGenerate']
    assert any('一次成文指令' in reason for reason in plan['blockingReasons'])
    with pytest.raises(ValueError, match='一次成文指令'):
        service.queue_document(project['id'], project['version'])
    assert store.jobs()['counts'] == before and gateway.calls == []


def test_saturated_material_budget_reserves_mode_prompt_before_selecting_original_blocks(document):
    store, gateway, service, _ = document
    # Fill the remaining source allowance after reserving all actual official
    # instructions. This affects only the simulated model, not saved settings.
    gateway.config['contextChars'] = 16000
    source, original = add_source(store, paragraphs=[
        f'第{index}段原文必须保留。' + '保存原文版本与人工修订，明确资料范围。' * 28
        for index in range(30)])
    project = project_for_modes(document, 'strict', ('inherit',), source=source)
    # Exercise chapter-budget selection in the >=10000 path; short interactive
    # requests now correctly block when the final whole packet cannot fit.
    gateway.config['maxOutputTokens'] = 65536
    project = service.patch(project['id'], project['version'],
                            {'brief': {**project['brief'], 'targetWords': 10000}})
    with store.connect() as db:
        _, chapter_only_blocks = service._messages(project, 'section', project['sections'][0], gateway.config, db)
        plan, steps = service._document_plan(db, project, gateway.config)
    assert plan['canGenerate'], plan
    assert 0 < len(steps[0]['blocks']) < len(chapter_only_blocks)
    originals = {block['id']: block for block in original['blocks']}
    for block in steps[0]['blocks']:
        assert block['blockId'] in originals
        assert block['text'] == originals[block['blockId']]['text']
        assert block['revisionId'] == original['revisionId']
    assert sum(len(message['content']) for message in steps[0]['messages']) <= gateway.config['contextChars']
    assert set(steps[0]['citationScope']['aliases'].values()) == {block['blockId'] for block in steps[0]['blocks']}
    assert gateway.calls == []


@pytest.mark.parametrize('legacy_output', [False, True], ids=['current-preserve', 'legacy-hard-failure'])
def test_mixed_draft_cannot_apply_extended_supplement_to_strict_chapter(document, legacy_output):
    store, gateway, service, _ = document
    project = project_for_modes(document, 'extended', ('strict', 'inherit'))
    run, job = queue_and_claim(document, project)
    assert run['project']['chapterConfirmationPolicy'] == 'chapter-confirmation-v1'
    for section in project['sections']:
        section['chapterConfirmed'] = False
    if legacy_output:
        freeze_legacy_generation_output(store, run['runId'], whole_document=True)

    def corrupt(result, payload):
        result['sections'][0]['paragraphs'] = [
            {'text': '模型扩展内容不能进入严格章。', 'kind': 'model_supplement', 'evidence': []}]
        return result

    gateway.transform = corrupt
    service.process(job)
    current = service.get(project['id'])
    if legacy_output:
        assert store.job(job['id'])['status'] == 'failed'
        assert current['sections'] == project['sections']
        with store.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM writing_document_steps WHERE run_id=? AND candidate_json IS NOT NULL',
                              (run['runId'],)).fetchone()[0] == 0
    else:
        assert store.job(job['id'])['status'] == 'succeeded' and store.job(job['id'])['stage'] == 'candidate'
        assert current['sections'][0] == project['sections'][0]
        assert current['sections'][1]['content'] == '这是模型提出的进一步讨论，尚需补充原文材料核实。'
        with store.connect() as db:
            row = db.execute('SELECT status,candidate_json FROM writing_document_steps WHERE run_id=? AND section_id=?',
                (run['runId'], project['sections'][0]['id'])).fetchone()
        assert row['status'] == 'candidate'
        chapter = json.loads(row['candidate_json'])['section']
        assert chapter['content'] == '模型扩展内容不能进入严格章。'
        assert chapter['evidence'] == [] and chapter['citationBindingState'] == 'needs_review'
        assert 'citationBindings' not in chapter
        assert chapter['citationIssues'] and all(item['status'] == 'unreviewed' for item in chapter['citationIssues'])
        assert gateway.matching_calls == []


@pytest.mark.parametrize('generation', ['pre_mode', 'mode_without_requirements', 'frozen_requirements'])
def test_historical_document_generations_keep_exact_original_assembly(generation, monkeypatch):
    section = {'id': 'old-section', 'title': '旧章', 'goal': '旧目标', 'mode': 'strict', 'targetWords': 100}
    packet = {'task': 'writing_section', 'section': section, 'mode': 'strict', 'blocks': [],
              'outputSchema': {'paragraphs': [{'text': '正文段落', 'kind': 'supported 或 model_supplement',
                                             'evidence': [{'blockId': '从 blocks 复制', 'quote': '逐字原文'}]}]}}
    item = {'section': section, 'effectiveMode': 'strict',
            'messages': [{'role': 'system', 'content': '历史章节原始系统'},
                         {'role': 'user', 'content': dumps(packet)}]}
    if generation != 'pre_mode':
        item['promptId'] = 'writing_section_strict'
    project = {'title': '旧文稿', 'brief': {}}
    if generation == 'frozen_requirements':
        project['documentRequirements'] = '历史冻结的完整整稿指令，包括旧版固定契约'

    def forbidden(*args, **kwargs):
        raise AssertionError('Historical requests cannot consult a current prompt')

    monkeypatch.setattr(writing_document, 'prompt_text', forbidden)
    monkeypatch.setattr(prompt_defaults, 'WRITING_DOCUMENT_ORCHESTRATION', '未来内部编排不得进入历史')
    messages = WritingService._document_messages(project, [item])
    payload = json.loads(messages[1]['content'])
    expected = (legacy_prompt_text('writing_document') if generation == 'pre_mode' else
                legacy_prompt_text('writing_document_mode') if generation == 'mode_without_requirements' else
                project['documentRequirements'])
    assert messages[0]['content'] == ('历史章节原始系统' if generation == 'pre_mode' else expected)
    assert payload['requirements'] == expected
    sent = payload['sections'][0]
    assert ('writingInstructions' in sent) == (generation != 'pre_mode')
    assert 'documentPromptProtocol' not in payload
    assert not any(key in sent for key in ('documentInstructions', 'documentPromptId', 'documentPromptVersion'))
    assert 'claim' not in payload['outputSchema']['sections'][0]['paragraphs'][0]['evidence'][0]
