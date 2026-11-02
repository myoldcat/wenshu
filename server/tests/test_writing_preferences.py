"""Structure/voice contracts use isolated stores and simulated providers."""
import copy
import json

import pytest

from server.store import dumps
from server.writing_preferences import (POLICY, INTRODUCTION_POLICY, STYLES, snapshot, apply,
                                       TEMPLATE_TITLE_POLICY, LEGACY_RESEARCH_OUTLINE_REQUIREMENT)
from server.writing_output import parse_output, OUTPUT_POLICY
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.tests.test_writing_composition_integration import _frozen, _queue_parent, _parent_context, _steps


def test_new_defaults_and_legacy_creation_are_independent(document):
    _, _, service, _ = document
    new = service.create({'brief': {'topic': '测试'}})
    assert new['brief']['structureTemplate'] == 'research-report-decision'
    assert new['brief']['writingStyle'] == 'official'
    legacy = service.create({'brief': {'topic': '旧模板', 'template': '原模板', 'style': '原有自由文风'}})
    assert 'structureTemplate' not in legacy['brief'] and 'writingStyle' not in legacy['brief']
    assert snapshot(legacy['brief'])['style']['instructions'] == '原有自由文风'


@pytest.mark.parametrize('key,value', [('structureTemplate', 'missing'), ('writingStyle', True), ('writingStyle', None)])
def test_unknown_selections_are_rejected(document, key, value):
    service = document[2]
    with pytest.raises(ValueError, match='选项无效'):
        service.create({'brief': {'topic': '测试', key: value}})


def test_browsing_historical_missing_values_does_not_migrate_or_save(document):
    store, _, service, _ = document
    project = ready(document)
    with store.connect() as db:
        saved = service._load(db, project['id'])
        saved['brief'].pop('structureTemplate')
        saved['brief'].pop('writingStyle')
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(saved), project['id']))
    before = service.get(project['id'])
    after = service.patch(project['id'], before['version'], {'brief': copy.deepcopy(before['brief'])})
    assert after['version'] == before['version']
    assert 'structureTemplate' not in after['brief'] and 'writingStyle' not in after['brief']


@pytest.mark.parametrize('style', list(STYLES))
def test_outline_includes_selected_structure_and_specific_voice(document, style):
    store, _, service, _ = document
    project = ready(document, brief={'topic': '测试结构与文风', 'structureTemplate': 'research-report',
                                     'writingStyle': style, 'targetWords': 600})
    run = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, run['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert frozen['preferenceSnapshot']['policy'] == POLICY
    assert packet['writingPreferences']['style']['id'] == style
    assert STYLES[style][1] in frozen['messages'][0]['content']
    assert '按实际内容拟定章标题，模板提示不作标题' in packet['requirements']
    assert '仅规划当前态势' not in packet['requirements']
    assert packet['writingPreferences']['structure']['sectionHints'] == ['当前态势', '存在问题隐患', '对策建议']
    assert 'sections' not in packet['writingPreferences']['structure']
    assert frozen['preferenceSnapshot']['templateTitlePolicy'] == TEMPLATE_TITLE_POLICY
    assert '正式章标题须结合主题及本章内容补充完善，不照抄模板短提示' in frozen['messages'][0]['content']
    assert '背景介绍不算独立章节' in frozen['messages'][0]['content']
    assert '事实边界' in frozen['messages'][0]['content']


@pytest.mark.parametrize('structure', ['opinion', 'review', 'report', 'book', 'custom'])
def test_all_new_template_choices_are_hints_not_formal_titles(structure):
    frozen = snapshot({'structureTemplate': structure, 'writingStyle': 'official'})
    packet = {'requirements': '按主题规划纲要。'}
    system = apply('', packet, frozen)
    assert '正式章标题须结合主题及本章内容补充完善，不照抄模板短提示' in system
    assert '保留作者明确指定、已确认或人工调整的章节标题' in system
    assert 'sectionHints' in packet['writingPreferences']['structure']
    assert 'sections' not in packet['writingPreferences']['structure']


def test_topic_specific_generated_titles_are_saved_without_template_renaming(document):
    store, gateway, service, _ = document
    project = ready(document)
    titles = ['固定版本资料的管理现状与应用范围', '版本漂移与人工修订衔接中的风险隐患', '完善快照追溯和修订管理的措施']
    calls = []

    def generate_outline(_config, messages):
        packet = json.loads(messages[1]['content'])
        calls.append(packet)
        return {'text': dumps({'sections': [
            {'title': title, 'goal': '按本章实际内容展开。', 'mode': packet['mode'], 'targetWords': 200}
            for title in titles], 'issues': []}), 'usage': {'total_tokens': 1}}

    gateway.complete = generate_outline
    queued = service.queue(project['id'], project['version'], 'outline')
    # Queueing or reading the advisory request cannot rename saved chapters.
    assert service.get(project['id'])['sections'] == project['sections']
    job = store.claim()
    assert job['id'] == queued['job']['id']
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    saved = service.get(project['id'])
    assert [section['title'] for section in saved['sections']] == titles
    assert service.get(project['id'])['version'] == saved['version']
    assert len(calls) == 1 and not gateway.matching_calls


@pytest.mark.parametrize('with_suffix', [False, True])
def test_legacy_frozen_template_request_keeps_its_original_title_contract(with_suffix):
    frozen = snapshot({'structureTemplate': 'research-report', 'writingStyle': 'official'})
    frozen.pop('templateTitlePolicy')
    frozen.pop('outlineTemplateRequirement')
    frozen.pop('outlineTitleRequirement')
    for key in ('authorStructurePolicy', 'authorStructureInstructions', 'authorOutlineRequirement',
                'templateDefaultPrefix', 'authorIntroductionRequirement',
                'authorIntroductionPolicy', 'authorIntroductionDefaultRequirement'):
        frozen.pop(key)
    frozen['boundary'] = '旧任务已经冻结的边界。'
    frozen['structure']['instructions'] = '旧任务已经冻结的结构要求。'
    if not with_suffix:
        frozen.pop('sectionSystemSuffix')
    packet = {'requirements': ''}
    before = copy.deepcopy(frozen)
    system = apply('', packet, frozen)
    assert packet['requirements'] == LEGACY_RESEARCH_OUTLINE_REQUIREMENT
    assert 'sections' in packet['writingPreferences']['structure']
    assert 'sectionHints' not in packet['writingPreferences']['structure']
    assert '不照抄模板短提示' not in system
    assert '旧任务已经冻结的结构要求。' in system
    assert frozen == before


def test_new_outline_title_requirement_is_frozen_not_recompiled(monkeypatch):
    from server import writing_preferences

    frozen = snapshot({'structureTemplate': 'research-report', 'writingStyle': 'official'})
    expected = frozen['outlineTemplateRequirement']
    monkeypatch.setattr(writing_preferences, 'RESEARCH_OUTLINE_REQUIREMENT', '后续改变不能进入已冻结请求。')
    packet = {'requirements': ''}
    apply('', packet, frozen)
    assert packet['requirements'] == frozen.get('templateDefaultPrefix', '') + expected


def test_first_chapter_intro_is_persisted_and_noop_preserved(document):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '旧调研报告开篇', 'targetWords': 600, 'structureTemplate': 'research-report'})
    gateway.transform = lambda result, packet: dict(result, introductionParagraphCount=1)
    run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    packet = json.loads(_frozen(store, run['runId'])['messages'][1]['content'])
    assert packet['introductionPolicy'] == INTRODUCTION_POLICY
    assert 'introductionParagraphCount' in packet['outputSchema']
    service.process(store.claim())
    current = service.get(project['id'])
    section = current['sections'][0]
    assert section['introductionParagraphCount'] == 1
    saved = service.save_section(current['id'], section['id'], current['version'], {'content': section['content']})
    assert saved['version'] == current['version']
    changed = service.save_section(saved['id'], section['id'], saved['version'], {'content': section['content'] + '\n\n新增正文段。'})
    assert changed['sections'][0]['introductionParagraphCount'] == 1
    changed = service.save_section(changed['id'], section['id'], changed['version'], {'content': '新背景。\n\n新正文。'})
    assert 'introductionParagraphCount' not in changed['sections'][0]


def test_intro_boundary_is_not_trusted_outside_first_chapter_or_if_invalid(document):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '旧调研报告开篇', 'targetWords': 600, 'structureTemplate': 'research-report'})
    gateway.transform = lambda result, packet: dict(result, introductionParagraphCount=1)
    run = service.queue(project['id'], project['version'], 'section', project['sections'][1]['id'])
    assert 'introductionPolicy' not in json.loads(_frozen(store, run['runId'])['messages'][1]['content'])
    service.process(store.claim())
    assert 'introductionParagraphCount' not in service.get(project['id'])['sections'][1]


def test_whole_document_keeps_first_intro_and_frozen_voice(document):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '旧调研报告开篇', 'targetWords': 600, 'structureTemplate': 'research-report'})
    def response(result, packet):
        result['sections'][0]['introductionParagraphCount'] = 1
        return result
    gateway.transform = response
    run, job = queue_and_claim(document, project)
    steps = _steps(store, run['runId'])
    assert steps[0]['preferenceSnapshot']['style']['id'] == 'official'
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    packet = gateway.calls[0]['payload']
    assert packet['sections'][0]['introductionPolicy'] == INTRODUCTION_POLICY
    assert 'introductionPolicy' not in packet['sections'][1]
    assert 'introductionParagraphCount' in packet['outputSchema']['sections'][0]
    assert packet['writingPreferences'] == {'policy': POLICY, 'structureTemplate': 'research-report', 'writingStyle': 'official'}
    assert all('writingPreferences' not in section for section in packet['sections'])
    suffix = steps[0]['preferenceSnapshot']['sectionSystemSuffix']
    assert _frozen(store, run['runId'])['documentRequirements'].count(suffix) == 1
    assert all(suffix not in item['content'] for item in packet['instructionCatalog'].values())
    assert service.get(project['id'])['sections'][0]['introductionParagraphCount'] == 1


def test_automatic_children_inherit_actual_preference_snapshot_and_requeue(document, monkeypatch):
    store, gateway, service, _ = document
    project = ready(document)
    parent = _queue_parent(document, project)
    frozen = _frozen(store, parent['runId'])
    original = copy.deepcopy(frozen['preferenceSnapshot'])
    frozen['preferenceSnapshot']['style']['instructions'] = '父任务冻结的专用文风。'
    frozen['preferenceSnapshot']['sectionSystemSuffix'] = frozen['preferenceSnapshot']['sectionSystemSuffix'].replace(
        original['style']['instructions'], '父任务冻结的专用文风。')
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), parent['runId']))
    monkeypatch.setitem(STYLES, 'official', ('政府公文', '升级后的文风不能渗入旧任务。'))
    _parent_context(service, gateway, parent, store.claim())
    child = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    child_frozen = _frozen(store, child['runId'])
    assert '父任务冻结的专用文风。' in child_frozen['messages'][0]['content']
    assert original['style']['instructions'] not in child_frozen['messages'][0]['content']
    assert '升级后的文风不能渗入旧任务。' not in child_frozen['messages'][0]['content']
    repeated = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    assert repeated['runId'] == child['runId']
    assert _frozen(store, child['runId']) == child_frozen


def test_snapshot_without_exact_suffix_retains_original_document_assembly(document):
    store, _, service, _ = document
    project = ready(document)
    with store.connect() as db:
        _, steps = service._document_plan(db, project, service._document_config(project))
    suffix = steps[0]['preferenceSnapshot']['sectionSystemSuffix']
    for step in steps:
        step['preferenceSnapshot'].pop('sectionSystemSuffix')
    before = copy.deepcopy(steps)
    messages = service._document_messages(project, steps)
    packet = json.loads(messages[1]['content'])
    assert suffix not in messages[0]['content']
    assert any(suffix in item['content'] for item in packet['instructionCatalog'].values())
    assert 'writingPreferences' not in packet
    assert all(section['writingPreferences'] for section in packet['sections'])
    assert steps == before


@pytest.mark.parametrize('kind', ['outline', 'section', 'document'])
def test_old_automatic_parent_does_not_acquire_new_preferences(document, kind):
    store, gateway, service, _ = document
    project = ready(document)
    parent = _queue_parent(document, project)
    frozen = _frozen(store, parent['runId'])
    frozen.pop('preferenceSnapshot')
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), parent['runId']))
    _parent_context(service, gateway, parent, store.claim())
    child = (service.queue_document(project['id'], project['version']) if kind == 'document' else
             service.queue(project['id'], project['version'], kind,
                           project['sections'][0]['id'] if kind == 'section' else None))
    child_frozen = _frozen(store, child['runId'])
    assert 'preferenceSnapshot' not in child_frozen
    packets = _steps(store, child['runId']) if kind == 'document' else [child_frozen]
    for item in packets:
        packet = json.loads(item['messages'][1]['content'])
        assert 'writingPreferences' not in packet and 'introductionPolicy' not in packet
        assert STYLES['official'][1] not in item['messages'][0]['content']


@pytest.mark.parametrize('count', [None, True, 2, 99])
def test_invalid_intro_boundary_preserves_prose_without_false_metadata(document, count):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '旧调研报告开篇', 'targetWords': 600, 'structureTemplate': 'research-report'})
    run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, run['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    candidate = service._candidate({'kind': 'section'}, frozen, {'text': dumps({
        'paragraphs': gateway.paragraphs(packet), 'introductionParagraphCount': count})})
    assert candidate['section']['content']
    assert 'introductionParagraphCount' not in candidate['section']
    assert any('背景介绍' in notice for notice in candidate['section']['generationNotices'])


def test_intro_count_tracks_transport_split_and_rejects_skipped_lead():
    frozen = {'messages': [{'role': 'system', 'content': ''},
                          {'role': 'user', 'content': dumps({'generationOutputPolicy': OUTPUT_POLICY})}]}
    result, _ = parse_output(frozen, {'text': dumps({'paragraphs': [
        {'text': '背景第一段。\n\n背景第二段。'}, {'text': '第一章正文。'}],
        'introductionParagraphCount': 1})}, 'section')
    assert len(result['paragraphs']) == 3 and result['introductionParagraphCount'] == 2
    result, _ = parse_output(frozen, {'text': dumps({'paragraphs': [
        {'text': ''}, {'text': '第一章正文。'}], 'introductionParagraphCount': 1})}, 'section')
    assert result['introductionParagraphCount'] is None


def test_selection_edit_keeps_specific_style_and_scope(document):
    _, gateway, service, _ = document
    project = ready(document, brief={'topic': '测试', 'writingStyle': 'technical', 'targetWords': 600})
    first = project['sections'][0]
    project = service.save_section(project['id'], first['id'], project['version'], {'content': '技术内容。'})
    first = project['sections'][0]
    target = {'startCodepoint': 0, 'endCodepoint': len(first['content']), 'selectedExact': first['content'],
              'unit': 'utf16', 'start': 0, 'end': len(first['content'])}
    with service.store.connect() as db:
        messages, _ = service._edit_messages(project, first, target, 'polish', '', gateway.config, db)
    assert STYLES['technical'][1] in messages[0]['content']
    assert '仅修改选区' in messages[0]['content']
