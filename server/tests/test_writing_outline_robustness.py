"""Regression cases from HK strict-outline failures; no live provider or data.

Controlled semantic decisions test the application gates, not model accuracy.
"""
import copy
import json

import pytest

from server.tests.test_writing_outline_planning import planning, queued
from server.writing_outline_planning import (
    OUTLINE_RESPONSE_POLICY, OUTLINE_SCOPE_REVIEW_INSTRUCTIONS, prepare_strict_outline,
)
from server.writing_reference_scope import (
    citation_scope_payload, resolve_reference_block, scope_outline_prepared, scope_prepared,
)


def scoped_case(planning):
    frozen = prepare_strict_outline(planning[4])
    result = planning[1].outline(json.loads(frozen['messages'][1]['content']))
    return frozen, result


def test_new_outline_aliases_are_frozen_presentational_and_do_not_encode_ordinals(planning):
    frozen = copy.deepcopy(planning[4])
    permanent = frozen['blocks'][0]['blockId']
    frozen['blocks'][0]['ordinal'] = 62
    packet = json.loads(frozen['messages'][1]['content'])
    packet['blocks'][0]['ordinal'] = 62
    packet['blocks'][0]['locator'] = {'ordinal': 62, 'xpath': '/html/body/p[63]'}
    frozen['messages'][1]['content'] = json.dumps(packet)
    before = copy.deepcopy(frozen)
    scoped = prepare_strict_outline(frozen)
    payload = json.loads(scoped['messages'][1]['content'])
    assert frozen == before and scoped['blocks'] == frozen['blocks']
    assert 'ordinal' not in payload['blocks'][0]
    assert 'ordinal' not in payload['blocks'][0].get('locator', {})
    assert payload['blocks'][0]['locator']['xpath'] == '/html/body/p[63]'
    assert scoped['blocks'][0]['ordinal'] == 62
    assert payload['blocks'][0]['blockId'] == 'O-B001'
    assert resolve_reference_block(scoped, 'O-B001') == permanent
    assert permanent not in scoped['messages'][1]['content']
    schema = payload['outputSchema']['sections'][0]['planningEvidence'][0]
    assert 'O-B001' in schema['blockId'] and 'citationScope.allowedBlockIds' in schema['blockId']
    scope = citation_scope_payload(scoped)
    assert scope['namespace'] == 'O' and '不得从 ordinal 推算编号' in scope['requirement']
    assert set(scope) == {'protocol', 'namespace', 'allowedBlockIds', 'requirement'}
    assert prepare_strict_outline(scoped) == scoped


@pytest.mark.parametrize('bad', ['O-B000', 'O-B062', 'O-B063', 'O-B01', 'o-B001',
                                'C01-B001', ' O-B001', 'O-B001\n'])
def test_new_outline_aliases_never_repair_ordinal_guesses_or_accept_chapter_ids(planning, bad):
    frozen, result = scoped_case(planning)
    result['sections'][1]['planningEvidence'][0]['blockId'] = bad
    with pytest.raises(ValueError, match='第2章.*第1项规划引文编号无效'):
        planning[2]._validate_planned_outline(frozen, result)


def test_new_outline_rejects_permanent_ids_but_registers_short_ids_as_exact_originals(planning):
    frozen, result = scoped_case(planning)
    before = copy.deepcopy(result)
    candidate = planning[2]._validate_planned_outline(frozen, result)
    actual = candidate['sections'][0]['planningEvidence'][0]
    block = frozen['blocks'][0]
    assert actual['blockId'] == block['blockId']
    assert block['text'][actual['start']:actual['end']] == actual['quote']
    assert result == before
    result['sections'][0]['planningEvidence'][0]['blockId'] = block['blockId']
    with pytest.raises(ValueError, match='本次纲要允许'):
        planning[2]._validate_planned_outline(frozen, result)


def test_outline_and_chapter_scopes_are_not_interchangeable_and_resume_keeps_original_map(planning):
    frozen, _ = scoped_case(planning)
    assert scope_outline_prepared(frozen) == frozen
    with pytest.raises(ValueError, match='章节不能沿用纲要'):
        scope_prepared(frozen, 1)
    chapter = scope_prepared(planning[4], 3)
    with pytest.raises(ValueError, match='纲要不能沿用章节'):
        scope_outline_prepared(chapter)
    corrupt = copy.deepcopy(frozen)
    corrupt['citationScope']['aliases']['O-B001'] = 'invented-permanent'
    with pytest.raises(ValueError, match='映射'):
        resolve_reference_block(corrupt, 'O-B001')


def test_new_policy_moves_only_bounded_issues_note_into_reviewed_issues_and_keeps_audit(planning):
    frozen, result = scoped_case(planning)
    note = '上述缺口已通过缩小章节范围规避，正文不得补写或据常识扩充。'
    result.update(issues=['没有足够原文支持进一步预测。'], issues_note=note)
    before = copy.deepcopy(result)
    candidate = planning[2]._validate_planned_outline(frozen, result)
    assert candidate['issues'] == [*result['issues'], note]
    assert candidate['outlineResponsePolicy'] == OUTLINE_RESPONSE_POLICY
    assert candidate['outlineNormalizationNotices'] == [{
        'code': 'outline_note_to_issues', 'sourceField': 'issues_note', 'targetField': 'issues',
        'sourceValue': note, 'message': '模型额外说明已保留并归入作者提示，须按冻结规则逐项复核。'}]
    assert result == before


@pytest.mark.parametrize('extra', [{'conclusions': '额外事实不能丢弃'}, {'notes': '说明'},
    {'issues_note': '说明', 'newFacts': ['未登记的结论']}])
def test_unknown_extra_fields_are_rejected_without_silent_fact_loss(planning, extra):
    frozen, result = scoped_case(planning)
    result.update(extra)
    with pytest.raises(ValueError, match='未声明的额外字段') as raised:
        planning[2]._validate_planned_outline(frozen, result)
    assert 'protocol缺失' not in str(raised.value) and '不能退回' not in str(raised.value)


@pytest.mark.parametrize('note', [None, 5, [], {'fact': '推断'}, '', ' ', '长' * 501])
def test_recognized_note_field_has_type_and_size_bounds(planning, note):
    frozen, result = scoped_case(planning)
    result['issues_note'] = note
    with pytest.raises(ValueError, match='issues_note'):
        planning[2]._validate_planned_outline(frozen, result)


def test_normalization_cannot_expand_the_total_note_budget(planning):
    frozen, result = scoped_case(planning)
    result.update(issues=['提示%d' % number for number in range(12)], issues_note='第13项提示')
    with pytest.raises(ValueError, match='缺口说明格式'):
        planning[2]._validate_planned_outline(frozen, result)


def test_old_frozen_v2_does_not_inherit_note_tolerance_or_short_id_scope(planning):
    frozen = planning[4]
    result = planning[1].outline(json.loads(frozen['messages'][1]['content']))
    result['issues_note'] = '旧任务也有同一说明，但不可暗中改用新规则。'
    with pytest.raises(ValueError, match='额外字段.*issues_note'):
        planning[2]._validate_planned_outline(frozen, result)
    assert citation_scope_payload(frozen) is None
    assert resolve_reference_block(frozen, frozen['blocks'][0]['blockId']) == frozen['blocks'][0]['blockId']
    with pytest.raises(ValueError):
        resolve_reference_block(frozen, 'O-B001')


@pytest.mark.parametrize('where', ['frozen', 'packet', 'review-instruction', 'scope-requirements'])
def test_new_policy_cannot_be_enabled_by_model_or_unpaired_packet_change(planning, where):
    frozen, result = scoped_case(planning)
    if where == 'frozen':
        frozen.pop('outlineResponsePolicy')
    elif where == 'review-instruction':
        frozen.pop('outlinePlanningInstructions')
    else:
        packet = json.loads(frozen['messages'][1]['content'])
        packet.pop('outlineScopeRequirements' if where == 'scope-requirements' else 'outlineResponsePolicy')
        frozen['messages'][1]['content'] = json.dumps(packet)
    with pytest.raises(ValueError, match='返回规则或引用范围不一致'):
        planning[2]._validate_planned_outline(frozen, result)


def test_protocol_mismatch_is_distinct_from_extra_field_diagnostics(planning):
    frozen, result = scoped_case(planning)
    result['protocol'] = 'evidence-plan-v1'
    result['issues_note'] = '即使说明合法也不得降级协议。'
    with pytest.raises(ValueError, match='protocol缺失或与冻结协议不符'):
        planning[2]._validate_planned_outline(frozen, result)


def test_new_request_freezes_complete_scope_temporal_and_investigation_review(planning):
    frozen, _ = scoped_case(planning)
    payload = json.loads(frozen['messages'][1]['content'])
    assert frozen['outlinePlanningInstructions'].endswith(OUTLINE_SCOPE_REVIEW_INSTRUCTIONS)
    assert payload['outlineScopeRequirements'] == OUTLINE_SCOPE_REVIEW_INSTRUCTIONS
    for phrase in ('标题与目标的全部', '调查状态', '调查是否外泄尚未确定', '相对时间', '已登记原文', '当前写作日期'):
        assert phrase in frozen['outlinePlanningInstructions']
    with pytest.raises(ValueError, match='超过模型输入预算'):
        prepare_strict_outline(planning[4], context_chars=100)


def test_new_queued_note_is_not_silently_classed_advisory_and_raw_return_stays_auditable(planning):
    store, gateway, service, before, _ = planning
    note = '正文要求的事故调查结论仍未得到原文支持。'
    gateway.transform = lambda result: result.update(issues_note=note)
    run, claimed = queued(planning)
    service.process(claimed)
    after = service.get(before['id'])
    candidate = after['runs'][0]['candidate']
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert candidate['outlinePlanningState'] == 'needs_review' and after['version'] == before['version']
    assert candidate['issues'] == [note] and candidate['outlineIssueReviews'][0]['disposition'] == 'blocking'
    with store.connect() as db:
        row = db.execute('SELECT response_text FROM writing_model_interactions WHERE run_id=? ORDER BY started_at,id LIMIT 1',
                         (run['runId'],)).fetchone()
    assert row is not None and 'issues_note' in row[0] and note in row[0]


@pytest.mark.parametrize('field,claim,reason', [
    ('title', '已通报资料外泄事故：医疗、教育及邮政', '邮政原文仅确认调查，标题把尚待确定的外泄强化为已确认事故。'),
    ('goal', '数字办将在下个月继续牵头主办演练。', '未登记讲话日期，下个月未归属于原文讲话时点。'),
])
def test_explicit_semantic_rejection_of_title_or_relative_date_keeps_original_outline(planning, field, claim, reason):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: result['sections'][0].update({field: claim})
    complete = gateway.complete

    def review(config, messages):
        response = complete(config, messages)
        packet = json.loads(messages[1]['content'])
        if packet['task'] == 'writing_citation_match':
            result = json.loads(response['text'])
            by_id = {sentence['sentenceId']: sentence for sentence in packet['sentences']}
            for assignment in result['assignments']:
                if by_id[assignment['sentenceId']]['text'] == claim:
                    assignment.update(support='partial', reason=reason)
            response['text'] = json.dumps(result, ensure_ascii=False)
        return response

    gateway.complete = review
    run, claimed = queued(planning)
    service.process(claimed)
    after = service.get(before['id'])
    candidate = after['runs'][0]['candidate']
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert candidate['outlinePlanningState'] == 'needs_review'
    assert candidate['outlinePlanningIssues'][0]['field'] == field
    assert candidate['outlinePlanningIssues'][0]['sentence'] == claim
    assert candidate['outlinePlanningIssues'][0]['reason'] == reason
    assert after['sections'] == before['sections'] and after['version'] == before['version']


def test_scope_review_registers_every_sentence_of_title_and_goal_without_coverage_gaps(planning):
    store, gateway, service, before, _ = planning
    title, goal = '原文明示机理。探测特点。', '介绍固定方式。保留原有范围和不确定性。'
    gateway.transform = lambda result: result['sections'][0].update(title=title, goal=goal)
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    candidate = service.get(before['id'])['runs'][0]['candidate']
    assignments = candidate['sections'][0]['planningReview']['assignments']
    assert [item['text'] for item in assignments] == ['原文明示机理。', '探测特点。', '介绍固定方式。', '保留原有范围和不确定性。']
    roles = gateway.reviews[0][1]['unitRoles']
    assert list(roles.values()) == ['title', 'title', 'goal', 'goal']
