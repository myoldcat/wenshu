"""Reader output rules reach frozen requests without upgrading saved jobs.

Only isolated stores and controlled gateways are used. These tests establish
instruction delivery, author-note separation and request durability, rather
than claiming that a real model always follows the requested prose style.
"""
import copy
import json

import pytest

from server import prompt_defaults as defaults, prompts, writing_composition
from server.prompts import PromptLibrary
from server.store import Store, dumps
from server.tests.test_writing_automatic import automatic
from server.tests.test_writing_document import document, ready
from server.tests.test_writing_edit_integrity import (
    cited_section, edit_environment, queued_edit, request_for, write,
)
from server.writing import WritingService


AUTHOR_NOTE = '现有材料主要集中于版本保存，写作时合并同义信息。'
LATER_RULE = '后来更新的正文规则不能进入已经冻结的任务。'


def _frozen(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?',
                                     (run_id,)).fetchone()[0])


def _saved_prompts(store, job_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                     (job_id,)).fetchone()[0])


def _steps(store, run_id):
    with store.connect() as db:
        return [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run_id,))]


def _record_calls(gateway):
    recorded = []
    complete = gateway.complete

    def recording(config, messages):
        recorded.append(copy.deepcopy(messages))
        return complete(config, messages)

    gateway.complete = recording
    return recorded


def _assert_reader_rules(text):
    assert defaults.WRITING_READER_CONTENT_BOUNDARY in text
    assert defaults.WRITING_NATURAL_CONTINUITY in text


def _later_defaults(monkeypatch):
    monkeypatch.setattr(defaults, 'WRITING_READER_CONTENT_BOUNDARY', LATER_RULE)
    monkeypatch.setattr(defaults, 'WRITING_NATURAL_CONTINUITY', LATER_RULE)
    monkeypatch.setattr(writing_composition, 'PROSE_INSTRUCTIONS', LATER_RULE)
    monkeypatch.setattr(writing_composition, '_ROLES',
                        {role: LATER_RULE for role in ('whole', 'opening', 'development', 'closing')})
    for prompt_id in prompts._WRITING_COMPOSITION_DEFAULTS:
        monkeypatch.setitem(prompts._WRITING_COMPOSITION_DEFAULTS, prompt_id, LATER_RULE)


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_new_chapter_delivers_reader_rules_and_keeps_author_notes_outside_body(document, mode):
    store, gateway, service, source = document
    project = ready(document, modes=[mode, mode, mode])
    previous = '原始版本保留可回查的历史。'
    project = service.save_section(project['id'], project['sections'][0]['id'], project['version'],
                                   {'content': previous, 'locked': True})
    source_before = store.detail(source)
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][1]['id'])
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    saved = _saved_prompts(store, queued['job']['id'])['writing_section_' + mode]
    _assert_reader_rules(saved['content'])
    assert saved['content'] in frozen['messages'][0]['content']
    assert frozen['compositionSnapshot']['proseInstructions'] in frozen['messages'][0]['content']
    assert '不要求每章开头回顾前章、结尾预告下章' in frozen['compositionSnapshot']['proseInstructions']
    context = packet['chapterContext']
    assert context['evidenceRole'] == 'none' and context['previousChapter']['written'] is True
    assert previous in context['previousChapter']['tail']
    assert '确有需要' in context['roleInstruction'] and '不固定' in context['roleInstruction']
    assert previous not in dumps(packet['blocks'])
    gateway.transform = lambda result, payload: {**result, 'issues': [AUTHOR_NOTE]}
    calls = _record_calls(gateway)
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert calls == [frozen['messages']] and not gateway.matching_calls
    current = service.get(project['id'])
    section = current['sections'][1]
    assert AUTHOR_NOTE not in section['content'] and AUTHOR_NOTE in section['generationNotices']
    assert current['sections'][0] == project['sections'][0]
    assert store.detail(source) == source_before


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_new_outline_keeps_material_commentary_out_of_chapter_tasks(document, mode):
    store, gateway, service, _ = document
    project = ready(document)
    project = service.patch(project['id'], project['version'], {'mode': mode})
    queued = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, queued['runId'])
    actual = frozen['messages'][0]['content']
    saved = _saved_prompts(store, queued['job']['id'])['writing_outline_' + mode]
    assert saved['content'] in actual
    assert defaults.WRITING_NATURAL_CONTINUITY in actual
    assert '不设评价本次材料总体情况或解释如何运用材料的章节' in actual
    assert '作者备注只写入 issues，不安排成正文任务' in actual
    assert '不把固定回顾或预告当作章节要点' in actual
    packet = json.loads(frozen['messages'][1]['content'])
    assert packet['outlinePolicy'] == 'abstract-outline-v1'
    assert 'planningEvidence' not in dumps(packet['outputSchema'])
    assert not gateway.calls and not gateway.matching_calls


def test_mixed_whole_draft_delivers_rules_to_both_mode_catalogs(document):
    store, gateway, service, _ = document
    gateway.config['contextChars'] = 48000
    project = ready(document, modes=['strict', 'extended', 'strict'])
    queued = service.queue_document(project['id'], project['version'])
    frozen = _frozen(store, queued['runId'])
    prepared = _steps(store, queued['runId'])
    assert frozen['plan']['strategy'] == 'single'
    calls = _record_calls(gateway)
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(calls) == 1 and not gateway.matching_calls
    packet = json.loads(calls[0][1]['content'])
    assert packet['compositionInstructionFormat'] == 'shared-catalog-v1'
    assert len(packet['instructionCatalog']) == 4
    assert frozen['compositionSnapshot']['proseInstructions'] in calls[0][0]['content']
    for section, step in zip(packet['sections'], prepared):
        for key in ('writingInstructionRef', 'documentInstructionRef'):
            item = packet['instructionCatalog'][section[key]]
            assert item['mode'] == section['mode'] == step['effectiveMode']
            _assert_reader_rules(item['content'])
        assert step['compositionSnapshot']['roleInstructions'][section['chapterContext']['position']['role']] == section['chapterContext']['roleInstruction']


@pytest.mark.parametrize('kind,mode', [('section', 'strict'), ('document', 'extended')])
def test_custom_style_cannot_remove_fixed_reader_boundary_or_force_chapter_navigation(document, kind, mode):
    store, gateway, service, _ = document
    prompt_id = 'writing_' + kind + '_' + mode
    authored = '使用短句。每章先回顾前章、末尾预告下章，并解释如何综合所选材料。'
    saved = PromptLibrary(store).update(prompt_id, authored, 1)
    project = ready(document, modes=[mode, mode, mode])
    if kind == 'section':
        queued = service.queue(project['id'], project['version'], kind, project['sections'][1]['id'])
        compiled = _saved_prompts(store, queued['job']['id'])[prompt_id]
        actual = _frozen(store, queued['runId'])['messages'][0]['content']
    else:
        queued = service.queue_document(project['id'], project['version'])
        step = _steps(store, queued['runId'])[0]
        compiled = step['documentPrompt']
        actual = compiled['content']
    assert compiled['content'].startswith(authored + '\n程序固定约束：')
    _assert_reader_rules(compiled['content'][len(authored):])
    assert compiled['content'] in actual and compiled['version'] == saved['version']
    with store.connect() as db:
        row = db.execute('SELECT content,version FROM prompt_overrides WHERE id=?', (prompt_id,)).fetchone()
        assert row['content'] == authored and row['version'] == saved['version']
    assert not gateway.calls and not gateway.matching_calls


@pytest.mark.parametrize('operation', ['polish', 'rewrite', 'shorten', 'expand'])
def test_each_new_selection_edit_freezes_reader_rules_with_selection_and_citation_protection(edit_environment, operation):
    store, gateway, service, project_id, _ = edit_environment
    project = cited_section(edit_environment)
    original = project['sections'][0]['content']
    original_evidence = copy.deepcopy(project['sections'][0]['evidence'])
    result, job = queued_edit(edit_environment, project, 0, len(original), operation)
    frozen = _frozen(store, result['runId'])
    _assert_reader_rules(frozen['messages'][0]['content'])
    assert '仅在 selectedText 范围内调整' in frozen['messages'][0]['content']
    assert '选区外问题写入 issues' in frozen['messages'][0]['content']
    gateway.response.update(replacementText=original.replace('证据保留原始版本', '原始版本保留证据'),
                            changeSummary='调整选区内表达。', issues=[AUTHOR_NOTE])
    calls = _record_calls(gateway)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert calls == [frozen['messages']]
    current = service.get(project_id)
    assert current['sections'][0]['content'] == original
    assert current['sections'][0]['evidence'] == original_evidence
    candidate = next(run for run in current['runs'] if run['id'] == result['runId'])['candidate']['edit']
    assert AUTHOR_NOTE in candidate['issues'] and AUTHOR_NOTE not in candidate['replacementText']
    assert candidate['replacementText'].count('[E1]') == original.count('[E1]')


def test_legacy_chapter_messages_and_roles_survive_failure_restart_and_retry(document, monkeypatch):
    store, gateway, service, _ = document
    project = ready(document)
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][1]['id'])
    frozen = _frozen(store, queued['runId'])
    frozen['messages'][0]['content'] = '旧章节已经冻结的正文规则，保持原样。'
    frozen['compositionSnapshot']['proseInstructions'] = '旧任务冻结的衔接规则。'
    legacy_role = '旧任务冻结的角色：开头回顾前章，结尾引向下一章。'
    frozen['compositionSnapshot']['roleInstructions']['development'] = legacy_role
    packet = json.loads(frozen['messages'][1]['content'])
    packet['chapterContext']['roleInstruction'] = legacy_role
    frozen['messages'][1]['content'] = dumps(packet)
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    calls = _record_calls(gateway)
    gateway.fail_calls = {1}
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'failed'
    _later_defaults(monkeypatch)
    restarted = WritingService(Store(store.root), gateway)
    restarted.store.retry(queued['job']['id'])
    restarted.process(restarted.store.claim())
    assert restarted.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert calls == [frozen['messages'], frozen['messages']]
    assert _frozen(store, queued['runId']) == frozen


def test_legacy_selection_edit_reuses_saved_messages_after_restart(edit_environment, monkeypatch):
    store, gateway, service, project_id, section_id = edit_environment
    original = '前句保留。选中内容。后句保留。'
    project = write(edit_environment, original)
    start = original.index('选中内容')
    queued = service.queue_edit(project_id, project['version'], section_id,
                                request_for(project, start, start + len('选中内容')))
    frozen = _frozen(store, queued['runId'])
    frozen['messages'][0]['content'] = '旧选区编辑已经冻结的原始指令，不升级。'
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    _later_defaults(monkeypatch)
    gateway.response['replacementText'] = '已调整的内容'
    calls = _record_calls(gateway)
    restarted = WritingService(Store(store.root), gateway)
    restarted.process(restarted.store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert calls == [frozen['messages']] and _frozen(store, queued['runId']) == frozen
    assert restarted.get(project_id)['sections'][0]['content'] == original


def _automatic_without_sources(automatic):
    store, gateway, service, project = automatic
    project = service.patch(project['id'], project['version'], {'brief': {
        **project['brief'], 'automaticOptions': {'useSelectedSources': False,
            'useRecommendedSources': False, 'mode': 'strict', 'includeImages': False}}})
    queued = service.queue_automatic(project['id'], project['version'])
    return store, gateway, service, project, queued


def _assert_automatic_rules_unchanged(store, run_id, before):
    after = _frozen(store, run_id)
    # Material preparation legitimately commits its chosen sources and mode;
    # this must not recompile the frozen instructions or any other settings.
    before = copy.deepcopy(before)
    for value in (before, after):
        for key in ('sources', 'materialSelection', 'mode'):
            value.pop(key, None)
    assert after == before


def test_new_automatic_parent_freezes_rules_for_children_before_restart_and_later_changes(automatic, monkeypatch):
    store, gateway, _, project, queued = _automatic_without_sources(automatic)
    frozen = _frozen(store, queued['runId'])
    boundary, continuity = defaults.WRITING_READER_CONTENT_BOUNDARY, defaults.WRITING_NATURAL_CONTINUITY
    for mode in ('strict', 'extended'):
        for kind in ('section', 'document'):
            _assert_reader_rules(frozen['compositionPrompts']['writing_' + kind + '_' + mode]['content'])
    original_prompts = copy.deepcopy(frozen['compositionPrompts'])
    _later_defaults(monkeypatch)
    calls = _record_calls(gateway)
    restarted = WritingService(Store(store.root), gateway)
    restarted.process(restarted.store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    _assert_automatic_rules_unchanged(store, queued['runId'], frozen)
    chapters = [messages for messages in calls if json.loads(messages[1]['content'])['task'] == 'writing_section']
    assert len(chapters) == 2
    for messages in chapters:
        packet = json.loads(messages[1]['content'])
        assert packet['blocks'] == [] and packet['mode'] == 'extended'
        assert boundary in messages[0]['content'] and continuity in messages[0]['content']
        assert original_prompts['writing_section_extended']['content'] in messages[0]['content']
        assert frozen['compositionSnapshot']['proseInstructions'] in messages[0]['content']
        assert LATER_RULE not in messages[0]['content']
    current = restarted.get(project['id'])
    assert all(section['content'] for section in current['sections'])
    with store.connect() as db:
        children = db.execute('SELECT kind,job_id FROM writing_runs WHERE project_id=? AND kind IN (\'outline\',\'section\')',
                              (project['id'],)).fetchall()
    for child in children:
        prompt_id = 'writing_' + child['kind'] + '_extended'
        assert _saved_prompts(store, child['job_id'])[prompt_id] == original_prompts[prompt_id]


def test_legacy_automatic_parent_keeps_original_prompt_and_role_text_for_new_children(automatic, monkeypatch):
    store, gateway, _, project, queued = _automatic_without_sources(automatic)
    frozen = _frozen(store, queued['runId'])
    boundary, continuity = defaults.WRITING_READER_CONTENT_BOUNDARY, defaults.WRITING_NATURAL_CONTINUITY
    for prompt_id, item in frozen['compositionPrompts'].items():
        item['content'] = '旧父任务冻结写作指令：' + prompt_id
    frozen['compositionSnapshot']['proseInstructions'] = '旧父任务冻结的衔接要求：每章回顾并预告。'
    frozen['compositionSnapshot']['roleInstructions'] = {
        role: '旧父任务冻结的角色：' + role for role in ('whole', 'opening', 'development', 'closing')}
    frozen['compositionDocumentOrchestration'] = '旧父任务冻结的整稿编排指令。'
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
        db.execute('UPDATE job_prompt_snapshots SET snapshot_json=? WHERE job_id=?',
                   (dumps(frozen['compositionPrompts']), queued['job']['id']))
    _later_defaults(monkeypatch)
    calls = _record_calls(gateway)
    restarted = WritingService(Store(store.root), gateway)
    restarted.process(restarted.store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    _assert_automatic_rules_unchanged(store, queued['runId'], frozen)
    chapters = [messages for messages in calls if json.loads(messages[1]['content'])['task'] == 'writing_section']
    assert len(chapters) == 2
    for messages in chapters:
        packet = json.loads(messages[1]['content'])
        assert boundary not in messages[0]['content'] and continuity not in messages[0]['content']
        assert LATER_RULE not in messages[0]['content']
        assert frozen['compositionPrompts']['writing_section_extended']['content'] in messages[0]['content']
        assert frozen['compositionSnapshot']['proseInstructions'] in messages[0]['content']
        role = packet['chapterContext']['position']['role']
        assert packet['chapterContext']['roleInstruction'] == frozen['compositionSnapshot']['roleInstructions'][role]
    assert all(section['content'] for section in restarted.get(project['id'])['sections'])
