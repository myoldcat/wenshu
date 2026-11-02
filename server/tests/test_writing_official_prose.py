"""Official prose requests keep stage scope, mode boundaries and frozen text.

Only isolated stores and simulated providers are used. These checks verify
instruction delivery and persistence, not a live model's prose quality.
"""
import copy
import json

import pytest

from server import writing_preferences as preferences
from server.store import dumps
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.tests.test_writing_composition_integration import (
    _frozen, _steps, _queue_parent, _parent_context,
)
from server.tests.test_writing_edit_integrity import request_for
from server.tests.test_writing_automatic_retries import retries, requests_for, execute
from server.tests.test_writing_citation_contract import add_source


@pytest.mark.parametrize('stage', ['outline', 'title', 'section', 'edit'])
def test_official_body_instructions_apply_only_to_body_stages(stage):
    frozen = preferences.snapshot({'writingStyle': 'official', 'structureTemplate': 'research-report'})
    before = copy.deepcopy(frozen)
    section = {'id': 'chapter', 'title': '一、既定标题'}
    packet = {'requirements': '当前任务要求。', 'outputSchema': {'paragraphs': []}}
    system = preferences.apply('原有任务指令。', packet, frozen,
        section=section if stage in ('section', 'edit') else None,
        edit=stage == 'edit', title=stage == 'title')
    assert frozen['officialProsePolicy'] == preferences.OFFICIAL_PROSE_POLICY
    assert frozen['officialProseInstructions'] == preferences.OFFICIAL_PROSE_INSTRUCTIONS
    assert (preferences.OFFICIAL_PROSE_INSTRUCTIONS in system) == (stage in ('section', 'edit'))
    assert frozen == before
    # The long body contract is not duplicated into each chapter's metadata.
    assert preferences.OFFICIAL_PROSE_INSTRUCTIONS not in dumps(packet['writingPreferences'])
    if stage == 'outline':
        assert '正式章标题须结合主题及本章内容补充完善' in system
    if stage == 'edit':
        assert '仅修改选区' in system


@pytest.mark.parametrize('brief', [
    {'writingStyle': 'technical'}, {'writingStyle': 'news'},
    {'style': '作者保存的自由文风'}, {},
])
def test_other_and_historical_styles_do_not_acquire_official_body_rules(brief):
    frozen = preferences.snapshot(brief)
    assert 'officialProsePolicy' not in frozen
    assert 'officialProseInstructions' not in frozen
    system = preferences.apply('', {'outputSchema': {}}, frozen, section={'id': 'chapter'})
    assert preferences.OFFICIAL_PROSE_INSTRUCTIONS not in system


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_actual_chapter_request_freezes_official_rules_without_changing_mode_or_outline(document, mode):
    store, gateway, service, _ = document
    project = ready(document, modes=[mode] * 3)
    before = copy.deepcopy(project['sections'])
    section = project['sections'][1]
    queued = service.queue(project['id'], project['version'], 'section', section['id'])
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    instructions = frozen['preferenceSnapshot']['officialProseInstructions']
    assert instructions == preferences.OFFICIAL_PROSE_INSTRUCTIONS
    assert frozen['messages'][0]['content'].count(instructions) == 1
    assert packet['mode'] == mode and packet['section']['mode'] == mode
    assert packet['section']['title'] == section['title']
    assert [item['title'] for item in packet['outline']] == [item['title'] for item in before]
    assert 'evidence' in packet['outputSchema']['paragraphs'][0]
    assert 'issues' in packet['outputSchema']
    assert packet['blocks'] and packet['citationReviewPolicy']
    if mode == 'extended':
        assert packet['unmatchedCitationPolicy']
    else:
        assert 'unmatchedCitationPolicy' not in packet
    assert service.get(project['id'])['sections'] == before and not gateway.calls


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_official_document_shares_one_frozen_body_contract_and_preserves_each_step(document, strategy):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    # Three mixed-mode chapters plus the full official body contract need more
    # input than the fixture's small 24k connection. Only this fake is changed.
    gateway.config['contextChars'] = 28000
    words = (200, 200, 200) if strategy == 'single' else (3334, 3333, 3333)
    project = ready(document, words=words, modes=['strict', 'extended', 'strict'])
    queued, job = queue_and_claim(document, project)
    frozen = _frozen(store, queued['runId'])
    steps = _steps(store, queued['runId'])
    instructions = preferences.OFFICIAL_PROSE_INSTRUCTIONS
    assert frozen['plan']['strategy'] == strategy
    assert frozen['documentRequirements'].count(instructions) == 1
    for step in steps:
        assert step['preferenceSnapshot']['officialProseInstructions'] == instructions
        assert step['messages'][0]['content'].count(instructions) == 1
    messages = service._document_messages(frozen, steps)
    packet = json.loads(messages[1]['content'])
    assert messages[0]['content'].count(instructions) == 1
    assert all(instructions not in item['content'] for item in packet['instructionCatalog'].values())
    assert all('writingPreferences' not in item for item in packet['sections'])
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == (1 if strategy == 'single' else 3)
    assert [item['title'] for item in service.get(project['id'])['sections']] == [
        item['title'] for item in project['sections']]


def test_official_rules_report_insufficient_input_without_queuing_or_changing_capacity(document):
    store, gateway, service, _ = document
    gateway.config.update(contextChars=12000, maxOutputTokens=65536)
    source, _ = add_source(store, paragraphs=[
        f'第{index}段原文必须保留。' + '保存原文版本与人工修订，明确资料范围。' * 28
        for index in range(30)])
    project = ready((store, gateway, service, source), words=(200,),
                    brief={'topic': '固定版本与人工修订', 'targetWords': 10000})
    before = copy.deepcopy(service.get(project['id']))
    jobs = store.jobs()['counts']
    config = copy.deepcopy(gateway.config)
    plan = service.generation_plan(project['id'])
    assert not plan['canGenerate']
    assert any('输入预算' in reason for reason in plan['blockingReasons'])
    with pytest.raises(ValueError, match='输入预算'):
        service.queue_document(project['id'], project['version'])
    assert store.jobs()['counts'] == jobs and not gateway.calls
    assert gateway.config == config and service.get(project['id']) == before
    # An explicit increase on the simulated connection leaves the original
    # output allowance and all confirmed originals untouched.
    gateway.config['contextChars'] = 16000
    with store.connect() as db:
        plan, steps = service._document_plan(db, project, gateway.config)
    assert plan['canGenerate'] and steps[0]['blocks']
    assert plan['maxOutputTokens'] == config['maxOutputTokens']
    assert all(sum(len(message['content']) for message in step['messages']) <= 16000 for step in steps)
    assert store.jobs()['counts'] == jobs and service.get(project['id']) == before


@pytest.mark.parametrize('operation', ['polish', 'rewrite', 'shorten', 'expand'])
def test_official_selection_edit_persists_rules_and_keeps_selected_scope(document, monkeypatch, operation):
    store, gateway, service, _ = document
    project = ready(document, modes=['extended'] * 3)
    text = '段首先交代原有结论。选中这一句调整表达。段尾保留既有内容。'
    project = service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'content': text})
    selected = '选中这一句调整表达。'
    start = text.index(selected)
    queued = service.queue_edit(project['id'], project['version'], project['sections'][0]['id'],
                                request_for(project, start, start + len(selected), operation))
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    original_instructions = preferences.OFFICIAL_PROSE_INSTRUCTIONS
    assert frozen['messages'][0]['content'].count(original_instructions) == 1
    assert packet['selectedText'] == selected and packet['operation'] == operation
    assert packet['outputSchema']['replacementText'] and packet['outputSchema']['issues'] == []
    assert '仅修改选区' in frozen['messages'][0]['content']
    monkeypatch.setattr(preferences, 'OFFICIAL_PROSE_INSTRUCTIONS', '后续规则不能改写已派发的选区请求。')
    assert _frozen(store, queued['runId']) == frozen
    assert service.get(project['id'])['sections'][0]['content'] == text and not gateway.calls


@pytest.mark.parametrize('with_suffix', [False, True])
@pytest.mark.parametrize('stage', ['section', 'edit'])
def test_legacy_preference_snapshot_never_backfills_official_body_rules(with_suffix, stage):
    frozen = preferences.snapshot({'writingStyle': 'official'})
    old_instructions = frozen.pop('officialProseInstructions')
    frozen.pop('officialProsePolicy')
    if with_suffix:
        # A pre-upgrade snapshot's exact suffix contains only its old rules.
        frozen['sectionSystemSuffix'] = '旧任务已冻结的公文文风要求。'
    else:
        frozen.pop('sectionSystemSuffix')
    before = copy.deepcopy(frozen)
    system = preferences.apply('', {'outputSchema': {}}, frozen,
                               section={'id': 'chapter'}, edit=stage == 'edit')
    assert old_instructions not in system
    assert frozen == before
    if with_suffix and stage == 'section':
        assert system == frozen['sectionSystemSuffix']


def test_automatic_child_uses_parent_actual_official_text_after_default_changes(document, monkeypatch):
    store, gateway, service, _ = document
    project = ready(document)
    parent = _queue_parent(document, project)
    parent_frozen = _frozen(store, parent['runId'])
    original = parent_frozen['preferenceSnapshot']['officialProseInstructions']
    actual = '父任务已经冻结的政府公文正文要求。'
    preference = parent_frozen['preferenceSnapshot']
    preference['officialProseInstructions'] = actual
    preference['sectionSystemSuffix'] = preference['sectionSystemSuffix'].replace(original, actual)
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(parent_frozen), parent['runId']))
    monkeypatch.setattr(preferences, 'OFFICIAL_PROSE_INSTRUCTIONS', '后续版本的新公文要求。')
    _parent_context(service, gateway, parent, store.claim())
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, queued['runId'])
    assert frozen['preferenceSnapshot'] == preference
    assert actual in frozen['messages'][0]['content']
    assert original not in frozen['messages'][0]['content']
    assert preferences.OFFICIAL_PROSE_INSTRUCTIONS not in frozen['messages'][0]['content']
    repeated = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    assert repeated['runId'] == queued['runId'] and _frozen(store, queued['runId']) == frozen


def test_automatic_network_retry_reuses_official_text_frozen_before_first_attempt(retries, monkeypatch):
    store, gateway, _, _ = retries
    original = preferences.OFFICIAL_PROSE_INSTRUCTIONS
    replacement = '首次请求之后的新公文规则不能进入异常重试或后续章节。'
    gateway.failures = ['network']
    gateway.before_target = lambda _: monkeypatch.setattr(preferences, 'OFFICIAL_PROSE_INSTRUCTIONS', replacement)
    queued, project = execute(retries)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    calls = requests_for(gateway)
    assert len(calls) == 3 and calls[0] == calls[1]
    assert all(original in messages[0]['content'] and replacement not in messages[0]['content']
               for _, messages in calls)
    assert _frozen(store, queued['runId'])['preferenceSnapshot']['officialProseInstructions'] == original
    assert all(section['content'] for section in project['sections'])
