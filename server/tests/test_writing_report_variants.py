"""Report variants freeze their own advisory structure without upgrading history."""
import copy
import json

import pytest

from server.store import dumps
from server import writing_preferences
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.tests.test_writing_composition_integration import _frozen, _queue_parent, _parent_context


VARIANTS = [
    ('research-report-problem', ['调研背景与方法', '基本情况', '存在问题', '原因分析', '对策建议']),
    ('research-report-experience', ['主要做法', '取得成效', '经验启示', '推广建议']),
    ('research-report-decision', ['现状与趋势', '风险研判', '意见建议（分近期、中期）']),
]


@pytest.mark.parametrize('variant,hints', VARIANTS)
def test_variant_outline_has_its_own_advisory_hints_and_unnumbered_lead(document, variant, hints):
    store, _, service, _ = document
    project = ready(document, brief={'topic': '固定版本资料治理', 'targetWords': 600,
                                     'structureTemplate': variant, 'writingStyle': 'official'})
    run = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, run['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert packet['writingPreferences']['structure']['id'] == variant
    assert packet['writingPreferences']['structure']['sectionHints'] == hints
    assert '正式章标题须结合主题及本章内容补充完善，不照抄模板短提示' in frozen['messages'][0]['content']
    assert '引言不单列章，写入首章goal' in packet['requirements']
    assert all('引言' not in hint for hint in hints)
    assert 'introductionPolicy' not in packet
    assert frozen['preferenceSnapshot']['introductionPolicy'] == writing_preferences.INTRODUCTION_POLICY
    assert packet['requirements'].endswith(frozen['preferenceSnapshot']['outlineTemplateRequirement'])
    if variant == 'research-report-problem':
        assert '按五部分组织纲要：首章为调研背景与方法' in packet['requirements']
    assert service.get(project['id'])['version'] == project['version']


@pytest.mark.parametrize('variant,hints', VARIANTS)
def test_variant_first_chapter_and_whole_draft_keep_matching_rules(document, variant, hints):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '固定版本资料治理', 'targetWords': 600,
                                     'structureTemplate': variant, 'writingStyle': 'official'})
    with store.connect() as db:
        plan, steps = service._document_plan(db, project, gateway.config)
        assert plan['canGenerate'], plan['blockingReasons']
        messages = service._document_messages(project, steps, db=db)
    whole = json.loads(messages[1]['content'])
    assert whole['writingPreferences']['structureTemplate'] == variant
    for index, item in enumerate(steps):
        packet = json.loads(item['messages'][1]['content'])
        assert packet['writingPreferences']['structure']['sectionHints'] == hints
        if index == 0:
            assert packet['introductionPolicy'] == writing_preferences.INTRODUCTION_POLICY
            assert '不编号、不单列章的简短引言' in packet['introductionRequirement']
            assert 'introductionParagraphCount' in packet['outputSchema']
            if variant == 'research-report-problem':
                assert '引言不替代或重复第一章调研背景与方法' in packet['introductionRequirement']
        else:
            assert 'introductionPolicy' not in packet
            assert 'introductionParagraphCount' not in packet['outputSchema']
        assert item['preferenceSnapshot']['structure']['id'] == variant
    specific = steps[0]['preferenceSnapshot']['structure']['instructions']
    assert specific in messages[0]['content']
    if variant == 'research-report-decision':
        assert '近期、中期' in specific
        assert '不将可能性写成确定事实' in specific
    elif variant == 'research-report-problem':
        assert '实际提供的调研方法、样本与过程' in specific
        assert '不虚构调研或因果' in specific
    else:
        assert '推广条件与局限' in specific
        assert '不夸大成果' in specific
    assert whole['sections'][0]['introductionPolicy'] == writing_preferences.INTRODUCTION_POLICY
    assert all('introductionPolicy' not in item for item in whole['sections'][1:])
    assert 'introductionParagraphCount' in whole['outputSchema']['sections'][0]
    section_ids = [section['id'] for section in project['sections']]

    def generated_with_lead(result, _packet):
        result['sections'][0]['introductionParagraphCount'] = 1
        return result

    gateway.transform = generated_with_lead
    run, job = queue_and_claim(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    saved = service.get(project['id'])
    assert [section['id'] for section in saved['sections']] == section_ids
    assert saved['sections'][0]['introductionParagraphCount'] == 1
    assert all('introductionParagraphCount' not in section for section in saved['sections'][1:])


@pytest.mark.parametrize('variant,hints', VARIANTS)
def test_variant_specific_formal_titles_survive_generation_and_save(document, variant, hints):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '固定版本资料治理', 'targetWords': 600,
                                     'structureTemplate': variant, 'writingStyle': 'official'})
    titles = [f'固定版本资料治理的第{index + 1}项分析与具体安排' for index in range(len(hints))]

    def generated_outline(_config, messages):
        packet = json.loads(messages[1]['content'])
        return {'text': dumps({'sections': [
            {'title': title, 'goal': '结合固定资料展开本章内容。', 'mode': packet['mode'], 'targetWords': 100}
            for title in titles], 'issues': []}), 'usage': {'total_tokens': 1}}

    gateway.complete = generated_outline
    queued = service.queue(project['id'], project['version'], 'outline')
    job = store.claim()
    assert job['id'] == queued['job']['id']
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    saved = service.get(project['id'])
    assert [section['title'] for section in saved['sections']] == titles
    assert saved['brief']['structureTemplate'] == variant
    again = service.patch(saved['id'], saved['version'], {'brief': copy.deepcopy(saved['brief'])})
    assert again['version'] == saved['version'] and again['sections'] == saved['sections']


@pytest.mark.parametrize('variant,hints', VARIANTS)
def test_variant_instructions_are_in_selection_edits_and_title_assistance(document, variant, hints):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '固定版本资料治理', 'targetWords': 600,
                                     'structureTemplate': variant, 'writingStyle': 'official'})
    first = project['sections'][0]
    project = service.save_section(project['id'], first['id'], project['version'], {'content': '固定版本资料治理。'})
    first = project['sections'][0]
    target = {'startCodepoint': 0, 'endCodepoint': len(first['content']), 'selectedExact': first['content'],
              'unit': 'utf16', 'start': 0, 'end': len(first['content'])}
    with store.connect() as db:
        messages, _ = service._edit_messages(project, first, target, 'polish', '', gateway.config, db)
    instructions = writing_preferences.snapshot(project['brief'])['structure']['instructions']
    assert instructions in messages[0]['content']
    assert '仅修改选区' in messages[0]['content']
    calls = []

    def generated_title(_config, title_messages):
        calls.append(copy.deepcopy(title_messages))
        return {'text': dumps({'title': '固定版本资料治理的实践与改进'}), 'usage': {'total_tokens': 1}}

    gateway.complete = generated_title
    service.assist('title', {'topic': project['brief']['topic'], 'modelId': gateway.config['id'],
                             'structureTemplate': variant, 'writingStyle': 'official'})
    assert len(calls) == 1
    assert instructions in calls[0][0]['content']
    assert json.loads(calls[0][1]['content'])['writingPreferences']['structure']['sectionHints'] == hints


@pytest.mark.parametrize('variant,hints', VARIANTS)
def test_automatic_children_reuse_actual_variant_even_after_catalog_change(document, monkeypatch, variant, hints):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '固定版本资料治理', 'targetWords': 600,
                                     'structureTemplate': variant, 'writingStyle': 'official'})
    parent = _queue_parent(document, project)
    original = _frozen(store, parent['runId'])['preferenceSnapshot']
    monkeypatch.setitem(writing_preferences.STRUCTURES, variant, ('后来变更的结构', ['后来变更的提示'], '后来指令不得进入旧任务。'))
    monkeypatch.setitem(writing_preferences.VARIANT_OUTLINE_REQUIREMENTS, variant, '后来要求不得进入旧任务。')
    _parent_context(service, gateway, parent, store.claim())
    child = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, child['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert frozen['preferenceSnapshot'] == original
    assert packet['writingPreferences']['structure']['sectionHints'] == hints
    assert packet['requirements'].endswith(original['outlineTemplateRequirement'])
    assert '后来' not in dumps(frozen['messages'])


def test_explicit_old_report_remains_available_and_does_not_migrate(document):
    _, _, service, _ = document
    project = ready(document, brief={'topic': '旧稿继续', 'targetWords': 600, 'structureTemplate': 'research-report'})
    before = copy.deepcopy(project)
    after = service.patch(project['id'], project['version'], {'brief': copy.deepcopy(project['brief'])})
    assert after['version'] == before['version']
    assert after['brief']['structureTemplate'] == 'research-report'
    assert after['sections'] == before['sections']
    legacy = writing_preferences.snapshot(after['brief'])
    assert legacy['structure']['sections'] == ['当前态势', '存在问题隐患', '对策建议']
    assert '严格生成3个一级章节' in legacy['outlineTemplateRequirement']
    assert legacy['outlineTemplateRequirement'].endswith(writing_preferences.RESEARCH_OUTLINE_REQUIREMENT)


@pytest.mark.parametrize('variant,hints', VARIANTS)
def test_pre_lead_frozen_variant_parent_keeps_its_original_messages(document, variant, hints):
    store, gateway, service, _ = document
    project = ready(document, brief={'topic': '旧版三型模板任务', 'targetWords': 600,
                                     'structureTemplate': variant, 'writingStyle': 'official'})
    parent = _queue_parent(document, project)
    parent_frozen = _frozen(store, parent['runId'])
    historical = parent_frozen['preferenceSnapshot']
    historical['sectionSystemSuffix'] = historical['sectionSystemSuffix'].replace(
        '\n' + historical['authorStructureInstructions'], '').replace(historical['templateDefaultPrefix'], '')
    for key in ('authorStructurePolicy', 'authorStructureInstructions', 'authorOutlineRequirement',
                'templateDefaultPrefix', 'authorIntroductionRequirement',
                'authorIntroductionPolicy', 'authorIntroductionDefaultRequirement'):
        historical.pop(key)
    historical['sectionSystemSuffix'] = historical['sectionSystemSuffix'].replace(
        '\n' + historical['introductionInstructions'], '')
    historical.pop('introductionPolicy')
    historical.pop('introductionRequirement')
    historical.pop('introductionInstructions')
    historical['outlineTemplateRequirement'] = '这一任务冻结时尚未要求单独开篇引言。'
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(parent_frozen), parent['runId']))
    _parent_context(service, gateway, parent, store.claim())
    child = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, child['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert frozen['preferenceSnapshot'] == historical
    assert 'introductionPolicy' not in packet
    assert 'introductionParagraphCount' not in packet['outputSchema']
    assert '开篇引言' not in frozen['messages'][0]['content']
    outline_packet = {'requirements': ''}
    writing_preferences.apply('', outline_packet, historical)
    assert outline_packet['requirements'] == historical.get('templateDefaultPrefix', '') + historical['outlineTemplateRequirement']
    assert outline_packet['writingPreferences']['structure']['sectionHints'] == hints


def test_variant_lead_rules_are_frozen_before_live_instructions_change(monkeypatch):
    frozen = writing_preferences.snapshot({'structureTemplate': 'research-report-decision', 'writingStyle': 'official'})
    before = copy.deepcopy(frozen)
    monkeypatch.setattr(writing_preferences, 'VARIANT_INTRODUCTION_REQUIREMENT', '后续引言规则不得进入已冻结请求。')
    monkeypatch.setattr(writing_preferences, 'VARIANT_INTRODUCTION_INSTRUCTIONS', '后续正文规则不得进入已冻结请求。')
    section = {'id': 'first'}
    packet = {'outputSchema': {}}
    system = writing_preferences.apply('', packet, frozen, section=section, sections=[section])
    assert packet['introductionRequirement'] == (before['authorIntroductionRequirement'] +
        before['templateDefaultPrefix'] + before['introductionRequirement'])
    assert before['introductionInstructions'] in system
    assert '后续' not in system
    assert frozen == before
