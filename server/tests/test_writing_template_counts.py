"""Selected-template counts are frozen prompt defaults, never local output repairs."""
import copy
import json

import pytest

from server import writing_composition as composition
from server import writing_preferences as preferences
from server.store import dumps
from server.tests.test_writing_document import document, ready
from server.tests.test_writing_composition_integration import _frozen, _queue_parent, _parent_context


COUNTS = [('research-report-problem', 5), ('research-report-experience', 4),
          ('research-report-decision', 3), ('research-report', 3),
          ('opinion', 5), ('review', 5), ('report', 5), ('book', 5)]


def project_for(document, template, purpose=''):
    return ready(document, brief={'topic': '固定版本资料治理', 'purpose': purpose,
        'targetWords': 600, 'structureTemplate': template, 'writingStyle': 'official'})


def generate(document, project, titles, goals=None):
    store, gateway, service, _ = document

    def complete(_config, messages):
        packet = json.loads(messages[1]['content'])
        return {'text': dumps({'sections': [
            {'title': title, 'goal': goals[index] if goals else '按固定资料说明本部分内容。',
             'mode': packet['mode'], 'targetWords': 100}
            for index, title in enumerate(titles)], 'issues': []}), 'usage': {'total_tokens': 1}}

    gateway.complete = complete
    queued = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, queued['runId'])
    assert service.get(project['id'])['sections'] == project['sections']
    job = store.claim()
    assert job['id'] == queued['job']['id']
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    saved = service.get(project['id'])
    assert [section['title'] for section in saved['sections']] == titles
    if goals:
        assert [section['goal'] for section in saved['sections']] == goals
    return frozen, json.loads(frozen['messages'][1]['content'])


@pytest.mark.parametrize('template,count', COUNTS)
def test_actual_outline_request_requires_each_selected_template_default_count(document, template, count):
    project = project_for(document, template)
    hints = preferences.STRUCTURES[template][1]
    titles = ['固定版本治理中' + hint + '的具体分析' for hint in hints]
    frozen, packet = generate(document, project, titles)
    requirements = packet['requirements']
    assert f'严格生成{count}个一级章节' in requirements
    assert '→'.join(hints) in requirements
    assert '只调整标题或顺序不改变默认章数' in requirements
    assert '完整章节列表或层级' in requirements
    assert '章外引言不计章' in requirements
    assert '仅模板无有效参考部分时参考2至8章' in requirements
    assert requirements.endswith(frozen['preferenceSnapshot']['outlineTemplateRequirement'])
    assert packet['writingPreferences']['structure']['sectionHints'] == hints
    actual_messages = '\n'.join(message['content'] for message in frozen['messages'])
    assert '生成 2 至 8 章' not in actual_messages
    assert '未指定时参考2至8章' not in actual_messages
    assert '不强制套用固定章节数' not in actual_messages
    assert '其次服从所选模板有效参考部分的默认章数' in frozen['messages'][0]['content']
    assert '不照抄模板短提示' in frozen['messages'][0]['content']
    assert all(title not in hints for title in titles)
    if template == 'research-report-decision':
        assert '近期、中期建议均写入意见建议所在章的goal内，不另拆章' in requirements
        assert '默认顺序下该章为第三章' in requirements
    # Count planning must not add another constraint to confirmed body requests.
    assert '严格生成' not in frozen['preferenceSnapshot']['sectionSystemSuffix']


@pytest.mark.parametrize('count', [1, 2, 6])
def test_complete_author_chapter_list_overrides_template_without_numeric_count(document, count):
    titles = ['作者指定的资料治理议题' + str(index + 1) for index in range(count)]
    purpose = '一级章节完整列表如下，保持标题与顺序：' + '；'.join(titles) + '。不要章外引言。'
    frozen, packet = generate(document, project_for(document, 'research-report-problem', purpose), titles)
    assert packet['brief']['purpose'] == purpose
    assert '完整章节列表、层级可确定一级章数时据此确定章数' in packet['requirements']
    assert '未明确一级章数且未给出可确定一级章数的完整章节列表或层级时' in packet['requirements']
    assert '明确标题原样保留' in frozen['messages'][0]['content']


def test_author_order_only_keeps_default_count_and_recommendation_grouping(document):
    purpose = '将意见建议放在首章，其他部分随后展开；只调整顺序，不指定新的章数。'
    titles = ['完善资料追溯的具体安排', '固定版本资料的现状与趋势', '人工修订衔接中的主要风险']
    goals = ['本章内分近期、中期安排，不另拆章。', '交代固定资料反映的现状。', '按依据分析风险。']
    _, packet = generate(document, project_for(document, 'research-report-decision', purpose), titles, goals)
    assert packet['brief']['purpose'] == purpose
    assert '严格生成3个一级章节' in packet['requirements']
    assert '只调整标题或顺序不改变默认章数' in packet['requirements']
    assert '采用默认章数且作者未另定分工或顺序时' in packet['requirements']
    assert '意见建议所在章的goal内，不另拆章' in packet['requirements']


def test_only_chapter_subsection_count_does_not_override_default_top_level_count(document):
    purpose = '每章设置两个二级小节，分别讨论固定版本和人工修订。'
    titles = ['固定资料的现状与变化', '人工修订面临的主要风险', '完善版本追溯的具体建议']
    goals = ['（一）固定版本；（二）人工修订。'] * 3
    _, packet = generate(document, project_for(document, 'research-report-decision', purpose), titles, goals)
    assert packet['brief']['purpose'] == purpose
    assert '严格生成3个一级章节' in packet['requirements']
    assert '可确定一级章数的完整章节列表或层级' in packet['requirements']
    assert '仅规定章内小节数量也不改变一级章数' in packet['requirements']
    assert '下级标题、层级及顺序写入goal' in packet['requirements']


@pytest.mark.parametrize('brief,count', [
    ({'structureTemplate': 'custom', 'templateSections': ['问题', '依据', '安排']}, 3),
    ({'template': '已保存的专用结构', 'templateSections': ['情况', '建议']}, 2),
    ({'structureTemplate': 'custom', 'templateSections': ['唯一部分']}, 1),
    ({'template': '完整结构', 'templateSections': ['部分' + str(index) for index in range(20)]}, 20),
])
def test_valid_saved_reference_parts_supply_custom_and_legacy_default_count(brief, count):
    before = copy.deepcopy(brief)
    frozen = preferences.snapshot(brief)
    packet = {'requirements': frozen['authorOutlineRequirement']}
    preferences.apply('', packet, frozen)
    assert f'严格生成{count}个一级章节' in packet['requirements']
    assert '→'.join(brief['templateSections']) in packet['requirements']
    assert brief == before


@pytest.mark.parametrize('brief', [
    {}, {'template': '旧自由模板'}, {'structureTemplate': 'custom'}, {'structureTemplate': 'explainer'},
    {'structureTemplate': 'custom', 'templateSections': []},
    {'structureTemplate': 'custom', 'templateSections': ['']},
    {'structureTemplate': 'custom', 'templateSections': ['一', False]},
    {'template': '旧自由模板', 'templateSections': ['部分'] * 21},
])
def test_templates_without_valid_reference_parts_keep_conditional_two_to_eight_fallback(brief):
    frozen = preferences.snapshot(brief)
    packet = {'requirements': frozen['authorOutlineRequirement']}
    preferences.apply('', packet, frozen)
    assert '仅模板无有效参考部分时参考2至8章' in packet['requirements']
    assert '严格生成' not in packet['requirements']


def test_new_default_count_is_frozen_before_catalog_and_live_rules_change(document, monkeypatch):
    store, gateway, service, _ = document
    project = project_for(document, 'research-report-experience')
    parent = _queue_parent(document, project)
    original = _frozen(store, parent['runId'])
    monkeypatch.setitem(preferences.STRUCTURES, 'research-report-experience', ('后来模板', ['后来唯一部分'], '后来规则'))
    monkeypatch.setattr(preferences, 'AUTHOR_OUTLINE_REQUIREMENT', '后来章数规则')
    monkeypatch.setattr(composition, 'OUTLINE_INSTRUCTIONS', '后来行文规则')
    _parent_context(service, gateway, parent, store.claim())
    child = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, child['runId'])
    assert frozen['preferenceSnapshot'] == original['preferenceSnapshot']
    assert frozen['compositionSnapshot'] == original['compositionSnapshot']
    packet = json.loads(frozen['messages'][1]['content'])
    assert '严格生成4个一级章节' in packet['requirements']
    assert '主要做法→取得成效→经验启示→推广建议' in packet['requirements']
    assert '后来' not in dumps(frozen['messages'])


def test_preceding_frozen_outline_keeps_original_count_and_composition_text(document):
    store, gateway, service, _ = document
    project = project_for(document, 'research-report-decision')
    parent = _queue_parent(document, project)
    original = _frozen(store, parent['runId'])
    old_preferences = original['preferenceSnapshot']
    old_preferences['authorOutlineRequirement'] = (
        '章数遵从作者明确要求，未指定时参考2至8章；系统支持1至20章。'
        '一级章节写入sections，下级标题、层级及顺序写入goal。'
        '纲要是待确认计划，不写正文；各章保持请求mode。')
    old_preferences['outlineTemplateRequirement'] = preferences.VARIANT_OUTLINE_REQUIREMENTS['research-report-decision']
    old_composition = original['compositionSnapshot']
    old_composition['outlineInstructions'] = '旧任务行文安排，不强制套用固定章节数或起承转合模板。'
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(original), parent['runId']))
    _parent_context(service, gateway, parent, store.claim())
    child = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, child['runId'])
    assert frozen['preferenceSnapshot'] == old_preferences
    assert frozen['compositionSnapshot'] == old_composition
    packet = json.loads(frozen['messages'][1]['content'])
    assert '未指定时参考2至8章' in packet['requirements']
    assert packet['requirements'].endswith(old_preferences['outlineTemplateRequirement'])
    assert '严格生成3个一级章节' not in packet['requirements']
    assert old_composition['outlineInstructions'] in frozen['messages'][0]['content']
    assert composition.OUTLINE_INSTRUCTIONS not in frozen['messages'][0]['content']
