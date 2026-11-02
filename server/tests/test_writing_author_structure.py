"""Author structure precedence uses pinned fake-model requests, never real data."""
import copy
import json

import pytest

from server import writing_preferences as preferences
from server.store import Store, dumps
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.tests.test_writing_composition_integration import _frozen, _steps, _queue_parent, _parent_context
from server.writing_output import parse_output
from server.writing import WritingService


POLICY = preferences.AUTHOR_STRUCTURE_POLICY
AUTHOR_FIELDS = ('authorStructurePolicy', 'authorStructureInstructions', 'authorOutlineRequirement',
                 'templateDefaultPrefix', 'authorIntroductionRequirement',
                 'authorIntroductionPolicy', 'authorIntroductionDefaultRequirement')


def prepared(document, purpose='', *, template='research-report-problem'):
    return ready(document, brief={'topic': '固定版本资料治理', 'purpose': purpose, 'targetWords': 600,
                                  'structureTemplate': template, 'writingStyle': 'official'})


def without_author_policy(frozen):
    """Model an actual preceding snapshot, including its original system suffix."""
    old = copy.deepcopy(frozen)
    if 'sectionSystemSuffix' in old:
        old['sectionSystemSuffix'] = old['sectionSystemSuffix'].replace(
            '\n' + old['authorStructureInstructions'], '').replace(old['templateDefaultPrefix'], '')
    for key in AUTHOR_FIELDS:
        old.pop(key)
    return old


@pytest.mark.parametrize('titles', [['作者指定的唯一章节'], ['来源与版本现状', '管理机制与实施安排']])
def test_explicit_count_titles_order_and_lower_levels_win_over_five_part_template(document, titles):
    store, gateway, service, _ = document
    purpose = ('只按以下顺序写' + str(len(titles)) + '章，标题原样保留：' + '；'.join(titles) +
               '。第一章下设（一）固定快照（二）人工修订；不要引言，不按五部分模板拆章。')
    project = prepared(document, purpose)
    returned = [{'title': title, 'goal': '（一）固定快照；（二）人工修订。' if index == 0 else '说明具体安排。',
                 'mode': 'strict', 'targetWords': 200} for index, title in enumerate(titles)]
    calls = []

    def outline(_config, messages):
        calls.append(copy.deepcopy(messages))
        return {'text': dumps({'sections': returned, 'issues': []}), 'usage': {'total_tokens': 1}}

    gateway.complete = outline
    queued = service.queue(project['id'], project['version'], 'outline')
    request = _frozen(store, queued['runId'])
    packet = json.loads(request['messages'][1]['content'])
    assert packet['brief']['purpose'] == purpose
    assert packet['authorStructurePolicy'] == POLICY
    assert '优先于模板' in request['messages'][0]['content']
    assert preferences.TEMPLATE_DEFAULT_PREFIX in packet['requirements']
    assert '一级章节写入sections，下级标题、层级及顺序写入goal' in packet['requirements']
    assert '生成 2 至 8 章' not in packet['requirements']
    assert '系统支持1至20章' in packet['requirements']
    assert service.get(project['id'])['sections'] == project['sections']
    job = store.claim()
    assert job['id'] == queued['job']['id']
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    saved = service.get(project['id'])
    assert [section['title'] for section in saved['sections']] == titles
    assert saved['sections'][0]['goal'] == returned[0]['goal']
    assert len(calls) == 1
    unchanged = service.patch(saved['id'], saved['version'], {'brief': copy.deepcopy(saved['brief'])})
    assert unchanged['version'] == saved['version'] and unchanged['sections'] == saved['sections']


@pytest.mark.parametrize('purpose', ['不要引言，第一章直接进入主体。', '把引言作为第一章，不另加章外引言。'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_explicit_intro_placement_can_save_zero_without_false_warning(document, purpose, wrapped):
    store, gateway, service, _ = document
    project = prepared(document, purpose)
    first = project['sections'][0]
    queued = service.queue(project['id'], project['version'], 'section', first['id'])
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert packet['authorStructurePolicy'] == POLICY
    assert '不要章外引言或把引言列为正式章节' in packet['introductionRequirement']
    assert '无章外引言填0' in packet['outputSchema']['introductionParagraphCount']

    def body(result, _packet):
        result['introductionParagraphCount'] = 0
        return {'type': 'json_object', 'content': result} if wrapped else result

    gateway.transform = body
    job = store.claim()
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    saved = service.get(project['id'])
    assert saved['sections'][0]['content']
    assert saved['sections'][0]['introductionParagraphCount'] == 0
    assert not any('分段位置未明确' in notice for notice in saved['sections'][0]['generationNotices'])


def test_numbered_introduction_stays_an_ordinary_first_chapter(document):
    store, gateway, service, _ = document
    project = prepared(document, '只设引言和调查发现两章；引言作为第一章，不加章外引言。')
    project = service.outline(project['id'], project['version'], [
        {'title': '一、引言', 'goal': '作为正式第一章说明议题。', 'targetWords': 200},
        {'title': '二、调查发现', 'goal': '说明固定资料中的发现。', 'targetWords': 200}])
    project = service.confirm(project['id'], project['version'])
    gateway.transform = lambda result, _packet: dict(result, introductionParagraphCount=0)
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    service.process(store.claim())
    saved = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert [section['title'] for section in saved['sections']] == ['一、引言', '二、调查发现']
    assert saved['sections'][0]['introductionParagraphCount'] == 0


def test_whole_draft_reuses_policy_per_chapter_and_keeps_author_order(document):
    store, gateway, service, _ = document
    project = prepared(document, '只按现状、安排两章写，不要引言；建议只按责任单位分组，不分近期中期。',
                       template='research-report-decision')
    project = service.outline(project['id'], project['version'], [
        {'title': '现状', 'goal': '按固定版本说明当前情况。', 'targetWords': 200},
        {'title': '安排', 'goal': '按责任单位分组，不分近期中期。', 'targetWords': 200}])
    project = service.confirm(project['id'], project['version'])

    def whole(result, _packet):
        result['sections'][0]['introductionParagraphCount'] = 0
        return result

    gateway.transform = whole
    queued, job = queue_and_claim(document, project)
    frozen_steps = _steps(store, queued['runId'])
    assert all(json.loads(item['messages'][1]['content'])['authorStructurePolicy'] == POLICY for item in frozen_steps)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    packet = gateway.calls[0]['payload']
    assert all(section['authorStructurePolicy'] == POLICY for section in packet['sections'])
    assert '无章外引言填0' in packet['outputSchema']['sections'][0]['introductionParagraphCount']
    saved = service.get(project['id'])
    assert [section['title'] for section in saved['sections']] == ['现状', '安排']
    assert saved['sections'][0]['introductionParagraphCount'] == 0
    assert 'introductionParagraphCount' not in saved['sections'][1]
    assert not any('分段位置未明确' in notice for notice in saved['sections'][0]['generationNotices'])


def test_author_silent_retains_template_and_positive_unnumbered_lead(document):
    store, gateway, service, _ = document
    project = prepared(document)
    queued = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert '仅模板无有效参考部分时参考2至8章' in packet['requirements']
    assert '严格生成5个一级章节' in packet['requirements']
    assert packet['requirements'].endswith(preferences.VARIANT_OUTLINE_REQUIREMENTS['research-report-problem'])
    # This test does not dispatch the outline; use its frozen rules directly.
    section_packet = {'outputSchema': {}}
    section = {'id': 'first'}
    preferences.apply('', section_packet, frozen['preferenceSnapshot'], section=section, sections=[section])
    normalized, _ = parse_output({'messages': [{'content': ''}, {'content': dumps({
        **section_packet, 'generationOutputPolicy': 'preserve-usable-v1'})}]},
        {'text': dumps({'paragraphs': [{'text': '开篇引言。'}, {'text': '第一章正文。'}], 'introductionParagraphCount': 1})}, 'section')
    assert normalized['introductionParagraphCount'] == 1


def test_explicit_chapter_generation_preserves_zero_lead_and_author_policy_after_restart(document):
    store, gateway, service, _ = document
    project = prepared(document, '只写资料现状、具体安排两章，不要章外引言。')
    project = service.patch(project['id'], project['version'],
                            {'brief': {**project['brief'], 'interactiveGeneration': 'chapter'}})
    project = service.outline(project['id'], project['version'], [
        {'title': '资料现状', 'goal': '说明资料版本情况。', 'targetWords': 200},
        {'title': '具体安排', 'goal': '说明人工修订的安排。', 'targetWords': 200}])
    project = service.confirm(project['id'], project['version'])
    first_id = project['sections'][0]['id']
    gateway.transform = lambda result, packet: ({**result, 'introductionParagraphCount': 0}
        if packet['section']['id'] == first_id else result)
    queued, job = queue_and_claim(document, project)
    assert _frozen(store, queued['runId'])['plan']['strategy'] == 'sequential'
    before = _steps(store, queued['runId'])
    first_packet = json.loads(before[0]['messages'][1]['content'])
    second_packet = json.loads(before[1]['messages'][1]['content'])
    assert first_packet['authorStructurePolicy'] == second_packet['authorStructurePolicy'] == POLICY
    assert first_packet['authorIntroductionPolicy'] == preferences.AUTHOR_INTRODUCTION_POLICY
    assert 'introductionPolicy' not in second_packet
    WritingService(Store(store.root), gateway).process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    saved = service.get(project['id'])
    assert [section['title'] for section in saved['sections']] == ['资料现状', '具体安排']
    assert saved['sections'][0]['introductionParagraphCount'] == 0
    assert 'introductionParagraphCount' not in saved['sections'][1]
    assert not any('分段位置未明确' in notice for notice in saved['sections'][0]['generationNotices'])
    assert len(gateway.calls) == 2
    assert all(call['payload']['authorStructurePolicy'] == POLICY for call in gateway.calls)


@pytest.mark.parametrize('value', [False, -1, '0'])
def test_zero_authorization_does_not_accept_invalid_count_types(document, value):
    store, gateway, service, _ = document
    project = prepared(document, '不要引言。')
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    candidate = service._candidate({'kind': 'section'}, frozen, {'text': dumps({
        'paragraphs': gateway.paragraphs(packet), 'introductionParagraphCount': value})})
    assert 'introductionParagraphCount' not in candidate['section']
    assert any('分段位置未明确' in notice for notice in candidate['section']['generationNotices'])


def test_transport_split_and_empty_skip_do_not_turn_authorized_zero_into_positive():
    packet = {'generationOutputPolicy': 'preserve-usable-v1', 'authorStructurePolicy': POLICY,
              'introductionPolicy': preferences.INTRODUCTION_POLICY}
    result, _ = parse_output({'messages': [{'content': ''}, {'content': dumps(packet)}]},
        {'text': dumps({'paragraphs': [{'text': ''}, {'text': '正文一。\n\n正文二。'}],
                       'introductionParagraphCount': 0})}, 'section')
    assert len(result['paragraphs']) == 2 and result['introductionParagraphCount'] == 0


@pytest.mark.parametrize('kind', ['outline', 'section'])
def test_historical_parent_does_not_acquire_precedence_or_zero_contract(document, kind):
    store, gateway, service, _ = document
    project = prepared(document, '不要引言。')
    parent = _queue_parent(document, project)
    frozen_parent = _frozen(store, parent['runId'])
    old = without_author_policy(frozen_parent['preferenceSnapshot'])
    frozen_parent['preferenceSnapshot'] = old
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen_parent), parent['runId']))
    _parent_context(service, gateway, parent, store.claim())
    queued = service.queue(project['id'], project['version'], kind,
                           project['sections'][0]['id'] if kind == 'section' else None)
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert frozen['preferenceSnapshot'] == old
    assert 'authorStructurePolicy' not in packet
    if kind == 'outline':
        assert '生成 2 至 8 章' in packet['requirements']
        assert '系统支持1至20章' not in packet['requirements']
        return
    candidate = service._candidate({'kind': 'section'}, frozen, {'text': dumps({
        'paragraphs': gateway.paragraphs(packet), 'introductionParagraphCount': 0,
        'authorStructurePolicy': POLICY})})
    assert 'introductionParagraphCount' not in candidate['section']
    assert any('分段位置未明确' in notice for notice in candidate['section']['generationNotices'])
    old_outline = {'requirements': ''}
    preferences.apply('', old_outline, old)
    assert old_outline['requirements'] == old['outlineTemplateRequirement']


@pytest.mark.parametrize('purpose,count', [('开头先写不编号、不计章的引言，再写既定章节。', 1), ('', 0)])
def test_author_can_request_introduction_with_nonreport_template(document, purpose, count):
    store, gateway, service, _ = document
    project = prepared(document, purpose, template='opinion')
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert packet['brief']['purpose'] == purpose
    assert packet['authorIntroductionPolicy'] == preferences.AUTHOR_INTRODUCTION_POLICY
    assert '模板默认无章外引言' in packet['introductionRequirement']
    assert frozen['preferenceSnapshot']['authorIntroductionDefaultRequirement'] in packet['introductionRequirement']
    gateway.transform = lambda result, _packet: {**result, 'introductionParagraphCount': count}
    job = store.claim()
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    section = service.get(project['id'])['sections'][0]
    assert section['introductionParagraphCount'] == count
    assert not any('分段位置未明确' in notice for notice in section.get('generationNotices', []))


def test_existing_author_snapshot_without_introduction_capability_keeps_nonreport_contract():
    frozen = preferences.snapshot({'structureTemplate': 'opinion'})
    frozen.pop('authorIntroductionPolicy')
    frozen.pop('authorIntroductionDefaultRequirement')
    section = {'id': 'first'}
    packet = {'outputSchema': {}}
    preferences.apply('', packet, frozen, section=section, sections=[section])
    assert packet['authorStructurePolicy'] == POLICY
    assert 'introductionPolicy' not in packet
    assert 'authorIntroductionPolicy' not in packet
    assert 'introductionParagraphCount' not in packet['outputSchema']


def test_actual_author_policy_is_frozen_and_body_edit_keeps_confirmed_outline(document, monkeypatch):
    store, gateway, service, _ = document
    project = prepared(document, '按甲、乙两章写，不要引言。')
    parent = _queue_parent(document, project)
    original = _frozen(store, parent['runId'])['preferenceSnapshot']
    monkeypatch.setattr(preferences, 'AUTHOR_STRUCTURE_INSTRUCTIONS', '后来规则不得进入旧请求。')
    _parent_context(service, gateway, parent, store.claim())
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, queued['runId'])
    assert frozen['preferenceSnapshot'] == original
    assert original['authorStructureInstructions'] in frozen['messages'][0]['content']
    assert '后来规则' not in frozen['messages'][0]['content']
    assert service.get(project['id'])['sections'] == project['sections']
    monkeypatch.setattr(preferences, 'AUTHOR_STRUCTURE_INSTRUCTIONS', original['authorStructureInstructions'])
    section = {**project['sections'][0], 'content': '原有正文。'}
    target = {'startCodepoint': 0, 'endCodepoint': 5, 'selectedExact': '原有正文。',
              'unit': 'utf16', 'start': 0, 'end': 5}
    with store.connect() as db:
        messages, _ = service._edit_messages(project, section, target, 'polish', '', gateway.config, db)
    assert '已确认或人工调整的纲要' in messages[0]['content']
    assert '不擅自重排' in messages[0]['content']
    assert '选区只改selectedText' in messages[0]['content']
