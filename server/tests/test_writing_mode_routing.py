"""Mode-specific writing instructions are routed, budgeted and frozen per job.

All calls use the controlled document gateway and isolated stores. The checks
exercise requests sent by queued jobs, including mixed-mode whole drafts and
the already-frozen request format used before the catalogue was split.
"""
import copy
import json

import pytest

from server.prompts import PromptLibrary, writing_prompt_id
from server.store import Store, dumps
from server.tests.test_writing_document import document, ready, queue_and_claim, composition_context_reserve
from server.tests.test_writing_integrity import controlled_outline_result
from server.writing import WritingService


def edit(store, prompt_id, marker):
    library = PromptLibrary(store)
    item = next(item for item in library.list()['items'] if item['id'] == prompt_id)
    return library.update(prompt_id, item['content'] + '\n' + marker, item['version'])


def capture(gateway):
    observed = []
    gateway.captured_matching_requests = []
    complete = gateway.complete

    def recording(config, messages):
        payload = json.loads(messages[1]['content'])
        if payload.get('task') != 'writing_citation_match':
            observed.append(copy.deepcopy(messages))
        else:
            gateway.captured_matching_requests.append(copy.deepcopy(messages))
        if payload['task'] == 'writing_outline':
            result = {'sections': [{'title': '资料治理', 'goal': '说明来源与版本',
                                   'mode': payload['mode'], 'targetWords': 200}], 'issues': []}
            return {'text': dumps(controlled_outline_result(payload, result))}
        return complete(config, messages)

    gateway.complete = recording
    return observed


def project_with_section(document, project_mode, section_mode):
    _, gateway, service, source = document
    project = service.create({'title': '章节实际模式路由', 'brief': {'topic': '保存原文依据', 'targetWords': 200},
                              'mode': project_mode, 'sourceIds': [source],
                              'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': '资料治理', 'goal': '说明来源与版本', 'mode': section_mode, 'targetWords': 200}])
    return service.confirm(project['id'], project['version'])


def snapshot_ids(store, job_id):
    with store.connect() as db:
        return set(json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                         (job_id,)).fetchone()[0]))


def auxiliary_snapshot_ids(store, run_id, job_id):
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])
        saved = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (job_id,)).fetchone()[0])
    preference = frozen.get('preferenceSnapshot') or {}
    composition = frozen.get('compositionSnapshot') or {}
    auxiliary = {**preference.get('stylePromptSnapshot', {}), **preference.get('structurePromptSnapshot', {}),
                 **composition.get('compositionPromptSnapshot', {})}
    assert all(saved[prompt_id] == item and item['version'] == 1 for prompt_id, item in auxiliary.items())
    return set(auxiliary)


@pytest.mark.parametrize(('project_mode', 'section_mode', 'actual_mode'), [
    ('strict', 'inherit', 'strict'), ('extended', 'inherit', 'extended'),
    ('strict', 'extended', 'extended'), ('extended', 'strict', 'strict')])
def test_section_uses_its_actual_mode_and_queue_time_edit_after_restart(document, project_mode, section_mode, actual_mode):
    store, gateway, service, _ = document
    observed = capture(gateway)
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('section', mode), mode + '章节队列甲')
    project = project_with_section(document, project_mode, section_mode)
    section_id = project['sections'][0]['id']
    run = service.queue(project['id'], project['version'], 'section', section_id)
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('section', mode), mode + '章节未来乙')
    job = store.claim()
    WritingService(Store(store.root), gateway).process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert snapshot_ids(store, job['id']) == {writing_prompt_id('section', actual_mode), 'writing_citation_match'} | auxiliary_snapshot_ids(store, run['runId'], job['id'])
    system, payload = observed[0][0]['content'], json.loads(observed[0][1]['content'])
    other_mode = 'extended' if actual_mode == 'strict' else 'strict'
    assert actual_mode + '章节队列甲' in system
    assert other_mode + '章节队列甲' not in system and '章节未来乙' not in system
    assert payload['mode'] == actual_mode and payload['section']['mode'] == actual_mode
    assert payload['promptId'] == writing_prompt_id('section', actual_mode)


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_outline_uses_project_mode_prompt_and_frozen_edit(document, mode):
    store, gateway, service, source = document
    observed = capture(gateway)
    for value in ('strict', 'extended'):
        edit(store, writing_prompt_id('outline', value), value + '纲要队列甲')
    edit(store, 'writing_citation_match', '范围复核队列甲')
    project = service.create({'title': '纲要模式路由', 'brief': {}, 'mode': mode,
                              'sourceIds': [source], 'modelId': gateway.config['id']})
    run = service.queue(project['id'], project['version'], 'outline')
    edit(store, writing_prompt_id('outline', mode), mode + '纲要未来乙')
    edit(store, 'writing_citation_match', '范围复核未来乙')
    service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    expected_ids = {writing_prompt_id('outline', mode)}
    assert snapshot_ids(store, run['job']['id']) == expected_ids | auxiliary_snapshot_ids(store, run['runId'], run['job']['id'])
    assert mode + '纲要队列甲' in observed[0][0]['content']
    assert '纲要未来乙' not in observed[0][0]['content']
    other = 'extended' if mode == 'strict' else 'strict'
    assert other + '纲要队列甲' not in observed[0][0]['content']
    assert json.loads(observed[0][1]['content'])['promptId'] == writing_prompt_id('outline', mode)
    scope_requests = [messages for messages in gateway.captured_matching_requests
                      if json.loads(messages[1]['content']).get('reviewPurpose') == 'outline_scope']
    assert scope_requests == []
    assert json.loads(observed[0][1]['content'])['outlinePolicy'] == 'abstract-outline-v1'


@pytest.mark.parametrize(('modes', 'maximum', 'strategy'), [
    (('strict', 'extended'), 8000, 'single'),
    (('extended', 'strict'), 8000, 'single'),
    (('strict', 'extended'), 1800, 'sequential')])
def test_mixed_document_uses_each_chapter_frozen_instructions(document, modes, maximum, strategy):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = maximum
    # This fixture tests mode routing. Capacity rejection has its own tests;
    # admit the full current frozen instructions on this simulated connection.
    gateway.config['contextChars'] = 64000
    observed = capture(gateway)
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('section', mode), mode + '成文章节甲')
        edit(store, writing_prompt_id('document', mode), mode + '成文模式队列甲')
    project = ready(document, words=(200, 200), modes=modes,
                    brief={'topic': '混合模式路由', 'targetWords': 400,
                           'interactiveGeneration': 'chapter' if strategy == 'sequential' else 'single'})
    run, job = queue_and_claim(document, project)
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('section', mode), mode + '成文章节乙')
        edit(store, writing_prompt_id('document', mode), mode + '成文模式未来乙')
    WritingService(Store(store.root), gateway).process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert snapshot_ids(store, job['id']) == {
        'writing_section_strict', 'writing_section_extended',
        'writing_document_strict', 'writing_document_extended'} | auxiliary_snapshot_ids(store, run['runId'], job['id'])
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        prepared = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],))]
    assert frozen['citationReviewPolicy'] == 'deferred-to-check-v1'
    assert all(item['citationReviewPolicy'] == 'deferred-to-check-v1' and 'citationMatchInstructions' not in item
               for item in prepared)
    assert not gateway.captured_matching_requests
    assert service.get(project['id'])['runs'][0]['plan']['strategy'] == strategy
    if strategy == 'single':
        assert len(observed) == 1
        system = observed[0][0]['content']
        payload = json.loads(observed[0][1]['content'])
        assert '成文模式队列甲' not in system and '成文模式未来乙' not in system
        assert '成文章节甲' not in system
        for section, mode in zip(payload['sections'], modes):
            instructions = (payload['instructionCatalog'][section['writingInstructionRef']]['content']
                            if 'writingInstructionRef' in section else section['writingInstructions'])
            document_instructions = (payload['instructionCatalog'][section['documentInstructionRef']]['content']
                                     if 'documentInstructionRef' in section else section['documentInstructions'])
            other = 'extended' if mode == 'strict' else 'strict'
            assert section['mode'] == mode
            assert mode + '成文章节甲' in instructions
            assert other + '成文章节甲' not in instructions and '成文章节乙' not in instructions
            assert section['documentPromptId'] == writing_prompt_id('document', mode)
            assert section['documentPromptVersion'] == 2
            assert mode + '成文模式队列甲' in document_instructions
            assert other + '成文模式队列甲' not in document_instructions
            assert '成文模式未来乙' not in document_instructions
    else:
        assert len(observed) == 2
        for messages, mode in zip(observed, modes):
            assert mode + '成文章节甲' in messages[0]['content']
            assert '成文章节乙' not in messages[0]['content']
            assert mode + '成文模式队列甲' in messages[0]['content']
            other = 'extended' if mode == 'strict' else 'strict'
            assert other + '成文模式队列甲' not in messages[0]['content']
            assert '成文模式未来乙' not in messages[0]['content']
            assert json.loads(messages[1]['content'])['mode'] == mode


def test_whole_input_budget_includes_both_mode_instructions(document):
    store, gateway, service, _ = document
    gateway.config['contextChars'] = 64000
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('section', mode), mode + '预算' + '章' * 3900)
    project = ready(document, words=(100, 100), modes=('strict', 'extended'),
                    brief={'topic': '混合模式输入预算', 'targetWords': 200,
                           'interactiveGeneration': 'chapter'})
    # Choose a limit that admits either chapter but excludes the combined
    # request, including the reserved preceding-chapter context as well as
    # the current fixed writing contracts.
    with store.connect() as db:
        _, initial = service._document_plan(db, project, gateway.config)
    limit = max(sum(len(m['content']) for m in item['messages']) + composition_context_reserve(item)
                for item in initial) + 1
    gateway.config['contextChars'] = limit
    with store.connect() as db:
        plan, prepared = service._document_plan(db, project, gateway.config)
        messages = service._document_messages(project, prepared, db=db)
    assert plan['canGenerate'], plan
    assert all(sum(len(m['content']) for m in item['messages']) < limit for item in prepared)
    assert plan['estimatedInputChars'] == sum(len(message['content']) for message in messages)
    assert plan['estimatedInputChars'] > limit
    assert plan['strategy'] == 'sequential'
    assert gateway.calls == []


def test_legacy_whole_draft_keeps_first_system_and_no_new_per_chapter_instructions(document):
    store, gateway, service, _ = document
    observed = capture(gateway)
    project = ready(document, words=(100, 100), modes=('strict', 'extended'))
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        frozen.pop('documentPromptProtocol')
        frozen.pop('documentRequirements')
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
        snapshot = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (job['id'],)).fetchone()[0])
        for mode in ('strict', 'extended'):
            snapshot.pop(writing_prompt_id('document', mode))
        snapshot['writing_document'] = {'content': '历史成文编排冻结甲', 'version': 2}
        db.execute('UPDATE job_prompt_snapshots SET snapshot_json=? WHERE job_id=?', (dumps(snapshot), job['id']))
        rows = db.execute('SELECT * FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],)).fetchall()
        for index, row in enumerate(rows):
            item = json.loads(row['input_json'])
            for field in ('documentPromptProtocol', 'documentPrompt', 'sectionInstructions', 'documentOrchestration'):
                item.pop(field)
            item.pop('promptId', None)
            payload = json.loads(item['messages'][1]['content'])
            payload.pop('promptId', None)
            for field in ('documentPromptProtocol', 'documentPromptId', 'documentPromptVersion'):
                payload.pop(field)
            item['messages'][1]['content'] = dumps(payload)
            item['messages'][0]['content'] = f'历史章节{index}冻结系统原文'
            db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                       (dumps(item), run['runId'], row['section_id']))
    for mode in ('strict', 'extended'):
        edit(store, writing_prompt_id('document', mode), '未来编排乙不能改历史任务')
    WritingService(Store(store.root), gateway).process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert len(observed) == 1
    messages = observed[0]
    assert messages[0]['content'] == '历史章节0冻结系统原文'
    payload = json.loads(messages[1]['content'])
    assert '历史成文编排冻结甲' in payload['requirements']
    assert '未来编排乙不能改历史任务' not in payload['requirements']
    assert all('writingInstructions' not in section for section in payload['sections'])
    assert all('documentInstructions' not in section and 'documentPromptId' not in section
               for section in payload['sections'])
