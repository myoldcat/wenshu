"""New bounded planning evidence keeps the exact old frozen 12-item contract."""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_writing_outline_planning import planning, queued
from server.writing_outline_planning import (
    OUTLINE_EVIDENCE_LIMIT, OUTLINE_EVIDENCE_POLICY, prepare_strict_outline,
)


def evidence_case(planning, *, new=True, old_notes=False):
    frozen = copy.deepcopy(planning[4])
    quotes = ['第%02d项固定原文事实。' % number for number in range(1, 42)]
    original = ''.join(quotes)
    frozen['blocks'][0]['text'] = original
    packet = json.loads(frozen['messages'][1]['content'])
    packet['blocks'][0]['text'] = original
    frozen['messages'][1]['content'] = dumps(packet)
    if new or old_notes:
        frozen = prepare_strict_outline(frozen)
    if old_notes:
        packet = json.loads(frozen['messages'][1]['content'])
        for key in ('outlineEvidencePolicy', 'outlineEvidenceLimit', 'outlineEvidenceRequirements'):
            frozen.pop(key, None)
            packet.pop(key, None)
        frozen['messages'][1]['content'] = dumps(packet)
    packet = json.loads(frozen['messages'][1]['content'])
    result = planning[1].outline(packet)
    model_id = packet['blocks'][0]['blockId']
    return frozen, result, [{'blockId': model_id, 'quote': quote} for quote in quotes]


def test_new_request_freezes_independent_evidence_limit_in_program_and_model_packet(planning):
    frozen, _, _ = evidence_case(planning)
    packet = json.loads(frozen['messages'][1]['content'])
    for container in (frozen, packet):
        assert container['outlineEvidencePolicy'] == OUTLINE_EVIDENCE_POLICY
        assert container['outlineEvidenceLimit'] == OUTLINE_EVIDENCE_LIMIT == 40
    assert '最多40项' in packet['outlineEvidenceRequirements']
    assert '唯一逐字定位' in packet['outlineEvidenceRequirements']
    assert prepare_strict_outline(frozen) == frozen


@pytest.mark.parametrize('count', range(13, 41))
def test_all_new_limits_from_13_through_40_register_every_original_quote_without_trimming(planning, count):
    frozen, result, references = evidence_case(planning)
    result['sections'][0]['planningEvidence'] = copy.deepcopy(references[:count])
    before = copy.deepcopy((frozen, result))
    candidate = planning[2]._validate_planned_outline(frozen, result)
    registered = candidate['sections'][0]['planningEvidence']
    assert len(registered) == count
    assert [reference['quote'] for reference in registered] == [reference['quote'] for reference in references[:count]]
    assert [reference['id'] for reference in registered] == ['P%d' % number for number in range(1, count + 1)]
    original = frozen['blocks'][0]
    assert all(reference['blockId'] == original['blockId'] and
               original['text'][reference['start']:reference['end']] == reference['quote'] for reference in registered)
    assert (frozen, result) == before


def test_41_new_references_are_rejected_without_mutating_or_slicing_the_model_result(planning):
    frozen, result, references = evidence_case(planning)
    result['sections'][0]['planningEvidence'] = copy.deepcopy(references)
    before = copy.deepcopy(result)
    with pytest.raises(ValueError, match='至多40项'):
        planning[2]._validate_planned_outline(frozen, result)
    assert result == before and len(result['sections'][0]['planningEvidence']) == 41


@pytest.mark.parametrize('old_notes', [False, True], ids=['before-notes-policy', 'frozen-notes-v1'])
def test_old_requests_including_notes_v1_keep_the_12_reference_limit(planning, old_notes):
    frozen, result, references = evidence_case(planning, new=False, old_notes=old_notes)
    if old_notes:
        assert prepare_strict_outline(frozen) == frozen  # Never silently upgrade an existing frozen policy.
    result['sections'][0]['planningEvidence'] = copy.deepcopy(references[:12])
    assert len(planning[2]._validate_planned_outline(frozen, result)['sections'][0]['planningEvidence']) == 12
    result['sections'][0]['planningEvidence'].append(copy.deepcopy(references[12]))
    with pytest.raises(ValueError, match='至多12项'):
        planning[2]._validate_planned_outline(frozen, result)


@pytest.mark.parametrize('damage', ['missing-program-policy', 'missing-model-policy', 'missing-program-limit',
    'missing-model-limit', 'wrong-program-policy', 'wrong-model-policy', 'wrong-program-limit',
    'wrong-model-limit', 'both-limits-41', 'both-limits-string', 'both-limits-bool'])
def test_partial_or_corrupt_frozen_evidence_policy_cannot_change_the_allowed_cap(planning, damage):
    frozen, result, references = evidence_case(planning)
    packet = json.loads(frozen['messages'][1]['content'])
    result['sections'][0]['planningEvidence'] = references[:13]
    if damage.startswith('missing-'):
        _, location, field = damage.split('-')
        (frozen if location == 'program' else packet).pop('outlineEvidencePolicy' if field == 'policy' else 'outlineEvidenceLimit')
    elif damage.startswith('wrong-'):
        _, location, field = damage.split('-')
        (frozen if location == 'program' else packet)['outlineEvidencePolicy' if field == 'policy' else 'outlineEvidenceLimit'] = (
            'unrecognised-policy' if field == 'policy' else 12)
    else:
        value = 41 if damage.endswith('41') else '40' if damage.endswith('string') else True
        frozen['outlineEvidenceLimit'] = packet['outlineEvidenceLimit'] = value
    frozen['messages'][1]['content'] = dumps(packet)
    with pytest.raises(ValueError, match='规划证据规则或上限不一致'):
        planning[2]._validate_planned_outline(frozen, result)


def test_a_valid_larger_registry_still_rejects_out_of_scope_or_non_verbatim_references(planning):
    for damage in ('scope', 'quote', 'duplicate'):
        frozen, result, references = evidence_case(planning)
        result['sections'][0]['planningEvidence'] = copy.deepcopy(references[:16])
        last = result['sections'][0]['planningEvidence'][-1]
        if damage == 'scope':
            last['blockId'] = 'O-B999'
        elif damage == 'quote':
            last['quote'] = '原文没有的补写事实。'
        else:
            result['sections'][0]['planningEvidence'][-1] = copy.deepcopy(references[0])
        with pytest.raises(ValueError, match='编号无效|必须逐字来自|重复登记'):
            planning[2]._validate_planned_outline(frozen, result)


def test_model_cannot_enable_a_larger_limit_by_returning_new_policy_fields(planning):
    frozen, result, references = evidence_case(planning, new=False)
    result['sections'][0]['planningEvidence'] = references[:16]
    result.update(outlineEvidencePolicy=OUTLINE_EVIDENCE_POLICY, outlineEvidenceLimit=40)
    with pytest.raises(ValueError, match='额外字段'):
        planning[2]._validate_planned_outline(frozen, result)


def test_real_new_queue_and_review_keep_all_16_planning_excerpts(planning):
    store, gateway, service, before, _ = planning
    quotes = ['第%02d项固定原文事实。' % number for number in range(1, 17)]
    block_id = planning[4]['blocks'][0]['blockId']
    with store.connect() as db:
        db.execute('UPDATE blocks SET text=? WHERE id=?', (''.join(quotes), block_id))

    def complete_plan(result):
        packet = gateway.generations[-1]
        model_id = packet['blocks'][0]['blockId']
        for section in result['sections']:
            section['planningEvidence'] = [{'blockId': model_id, 'quote': quote} for quote in quotes]

    gateway.transform = complete_plan
    run, job = queued(planning)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    after = service.get(before['id'])
    assert after['runs'][0]['applied']
    assert all(len(section['planningEvidence']) == 16 and section['planningReview']['state'] == 'supported'
               for section in after['sections'])
    assert all(len(packet['evidence']) == 16 for _, packet in gateway.reviews)
