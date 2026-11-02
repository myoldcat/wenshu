"""Mode-aware citation judgments use isolated stores and controlled providers.

These checks enforce request contracts, structured basis and frozen policies.
They do not claim that a provider can perfectly classify semantic inferences.
"""
import copy
import json

import pytest

from server import writing_citation_matching as matching
from server.store import dumps
from server.tests.test_writing_citation_tasks import environment
from server.tests.test_writing_integrity import writing, enqueue as queue_current, freeze_legacy_inline_review
from server.tests.test_writing_sentence_citations import evidence_for
from server.writing import CITATION_PLACEMENT
from server.writing_citation_matching import (
    LEGACY_MATCHING_PROTOCOL, MATCHING_PROTOCOL, build_matching_packet,
    matching_messages, matching_policy, validate_matching_result,
)
from server.writing_sentence_citations import relocate_citations, valid_bindings


def enqueue(environment):
    """Exercise effective-mode compatibility of a genuinely old frozen job."""
    result, job = queue_current(environment)
    freeze_legacy_inline_review(environment[0], result['runId'])
    return result, job


def evidence():
    return [{'id': 'E1', 'quote': 'The source states that access requires approval.'}]


def decision(basis='paraphrase', *, support='supported', ids=None):
    return {'sentenceId': 'S1', 'evidenceIds': ['E1'] if ids is None else ids,
            'support': support, 'supportBasis': basis,
            'reason': '本句忠实归纳原文明示的审批要求，没有引入原文未作出的推理。'}


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_payload_carries_explicit_actual_mode_and_complete_body_and_quote(mode):
    content = '  可将原文审批要求归纳改写。[E1]\n\n仍保留上下文。  '
    packet = build_matching_packet(content, evidence(), mode=mode)
    payload = json.loads(matching_messages(packet)[1]['content'])
    assert packet['protocol'] == MATCHING_PROTOCOL and payload['mode'] == mode
    assert packet['content'] == content.replace('[E1]', '')
    assert ''.join(row['text'] for row in payload['sentences']) == '可将原文审批要求归纳改写。仍保留上下文。'
    assert payload['evidence'][0]['quote'] == evidence()[0]['quote']
    assert payload['policy'] == matching_policy(mode)
    assert 'supportBasis' in payload['outputSchema']['assignments'][0]
    assert ('inference' in payload['policy']['supportedBases']) == (mode == 'extended')
    assert '归纳' in payload['policy']['instruction']


@pytest.mark.parametrize('basis', ['direct', 'paraphrase'])
def test_strict_accepts_faithful_summary_without_reason_keyword_guessing(basis):
    packet = build_matching_packet('须先取得批准才能访问。', evidence(), mode='strict')
    checked = validate_matching_result(packet, {'assignments': [decision(basis)]})
    assert checked['matches'][0]['supportBasis'] == basis
    assert checked['mode'] == 'strict' and checked['policy'] == packet['policy']
    assert checked['issues'] == []


def test_same_structured_inference_rejected_only_for_supported_strict_claim():
    result = {'assignments': [decision('inference')]}
    with pytest.raises(ValueError, match='严格模式'):
        validate_matching_result(build_matching_packet('这表明另一个结论。', evidence(), mode='strict'), result)
    extended = validate_matching_result(build_matching_packet('这表明另一个结论。', evidence(), mode='extended'), result)
    assert extended['matches'][0]['supportBasis'] == 'inference'
    result['assignments'][0]['support'] = 'partial'
    strict_issue = validate_matching_result(build_matching_packet('这表明另一个结论。', evidence(), mode='strict'), result)
    assert strict_issue['issues'][0]['status'] == 'partial'
    result['assignments'][0].pop('supportBasis')
    with pytest.raises(ValueError, match='格式'):
        validate_matching_result(build_matching_packet('这表明另一个结论。', evidence(), mode='strict'), result)


def test_old_packet_keeps_its_legacy_schema_and_policy_does_not_silently_downgrade():
    packet = build_matching_packet('原有任务正文。', evidence())
    assert packet['protocol'] == LEGACY_MATCHING_PROTOCOL and 'mode' not in packet and 'policy' not in packet
    row = decision(); row.pop('supportBasis')
    checked = validate_matching_result(packet, {'assignments': [row]})
    assert 'mode' not in checked and 'supportBasis' not in checked['matches'][0]
    mode_packet = build_matching_packet('新任务正文。', evidence(), mode='strict')
    mode_packet['mode'] = 'extended'
    with pytest.raises(ValueError, match='冻结规则'):
        validate_matching_result(mode_packet, {'assignments': [decision()]})


def test_binding_persists_mode_policy_basis_and_invalidates_mode_mismatch():
    content = '须先取得批准才能访问。'
    packet = build_matching_packet(content, evidence(), mode='strict')
    checked = validate_matching_result(packet, {'assignments': [decision()]})
    text, binding = relocate_citations(content, evidence(), checked['matches'], mode='strict', support_policy=packet['policy'])
    assert binding['mode'] == 'strict' and binding['supportPolicy'] == packet['policy']
    assert binding['items'][0]['supportBasis'] == 'paraphrase'
    assert valid_bindings(text, evidence(), binding, mode='strict')
    assert not valid_bindings(text, evidence(), binding, mode='extended')
    inference = copy.deepcopy(checked['matches']); inference[0]['supportBasis'] = 'inference'
    with pytest.raises(ValueError, match='严格模式'):
        relocate_citations(content, evidence(), inference, mode='strict')
    _, legacy = relocate_citations(content, evidence(), checked['matches'])
    assert 'mode' not in legacy and valid_bindings(legacy['content'], evidence(), legacy, mode='strict')


@pytest.mark.parametrize(('project_mode', 'section_mode', 'actual'), [
    ('strict', 'inherit', 'strict'), ('strict', 'extended', 'extended'), ('extended', 'strict', 'strict')])
def test_new_generation_freezes_effective_mode_and_persists_it(writing, project_mode, section_mode, actual):
    store, gateway, service, _, project = writing
    with store.connect() as db:
        draft = service._load(db, project['id'])
        draft['mode'] = project_mode; draft['sections'][0]['mode'] = section_mode
        service._save(db, draft, 'controlled_mode_fixture')
    run, job = enqueue(writing)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
    assert json.loads(frozen['messages'][1]['content'])['citationPlacement'] == CITATION_PLACEMENT
    assert frozen['citationMatchMode'] == actual and frozen['citationMatchPolicy'] == matching_policy(actual)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    payload = json.loads(gateway.matching_calls[0][1][1]['content'])
    assert payload['mode'] == actual and payload['policy'] == frozen['citationMatchPolicy']
    binding = service.get(project['id'])['sections'][0]['citationBindings']
    assert binding['mode'] == actual and binding['items'][0]['supportBasis'] == 'paraphrase'


def test_existing_v2_generation_job_does_not_acquire_new_mode_or_basis_schema(writing):
    store, gateway, service, _, project = writing
    run, job = enqueue(writing)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        packet = json.loads(frozen['messages'][1]['content'])
        packet['citationPlacement'] = 'sentence-evidence-reviewed-v2'
        packet.pop('citationTemporalReview', None)
        frozen['messages'][1]['content'] = dumps(packet)
        frozen.pop('citationMatchMode'); frozen.pop('citationMatchPolicy')
        frozen.pop('citationTemporalReview', None)
        frozen['citationMatchInstructions'] = '旧任务的已冻结引用审阅要求。'
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    payload = json.loads(gateway.matching_calls[0][1][1]['content'])
    assert payload['protocol'] == LEGACY_MATCHING_PROTOCOL and 'mode' not in payload
    assert 'supportBasis' not in payload['outputSchema']['assignments'][0]
    assert gateway.matching_calls[0][1][0]['content'] == '旧任务的已冻结引用审阅要求。'
    assert 'mode' not in service.get(project['id'])['sections'][0]['citationBindings']


def test_new_whole_document_steps_freeze_each_actual_section_mode(writing):
    store, gateway, service, _, project = writing
    project = service.patch(project['id'], project['version'], {'brief': {**project['brief'], 'targetWords': 200}})
    project = service.outline(project['id'], project['version'], [
        {'title': '忠实归纳', 'goal': '', 'mode': 'inherit', 'targetWords': 100},
        {'title': '扩展分析', 'goal': '', 'mode': 'extended', 'targetWords': 100}])
    project = service.confirm(project['id'], project['version'])
    result = service.queue_document(project['id'], project['version'])
    freeze_legacy_inline_review(store, result['runId'], whole_document=True)
    with store.connect() as db:
        steps = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (result['runId'],))]
    assert [step['citationMatchMode'] for step in steps] == ['strict', 'extended']
    for step in steps:
        payload = json.loads(step['messages'][1]['content'])
        assert payload['citationPlacement'] == CITATION_PLACEMENT
        assert step['citationMatchMode'] == payload['mode']
        assert step['citationMatchPolicy'] == matching_policy(payload['mode'])
    assert not gateway.calls and not gateway.matching_calls


def test_manual_rematch_freezes_mixed_effective_modes_and_reuses_policy_on_retry(environment, monkeypatch):
    store, gateway, service, project = environment
    with store.connect() as db:
        draft = service._load(db, project['id'])
        draft['sections'][0]['mode'] = 'inherit'; draft['sections'][1]['mode'] = 'extended'
        service._save(db, draft, 'controlled_mixed_modes')
    project = service.get(project['id'])
    run = service.queue_citation_match(project['id'], project['version'])
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        steps = [json.loads(row[0]) for row in db.execute('SELECT input_json FROM writing_citation_match_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],))]
    assert [step['packet']['mode'] for step in steps] == ['strict', 'extended']
    gateway.fail_calls = {2}; service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'failed'
    def no_runtime_policy(*args, **kwargs):
        raise AssertionError('A retry must not replace its frozen policy')
    monkeypatch.setattr(matching, 'matching_policy', no_runtime_policy)
    store.retry(run['job']['id']); service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded', store.job(run['job']['id'])
    assert gateway.calls[1][1] == gateway.calls[2][1]
    for section, actual in zip(service.get(project['id'])['sections'], ('strict', 'extended')):
        assert section['citationBindings']['mode'] == actual
        assert section['citationBindings']['supportPolicy'] == frozen['citationMatchPolicies'][section['id']]['supportPolicy']


@pytest.mark.parametrize('change', ['project', 'section'])
def test_switching_an_inference_binding_to_strict_clears_it_without_changing_prose(writing, change):
    store, _, service, _, project = writing
    refs = evidence_for(writing); content = '作者据此作出进一步推论。'
    packet = build_matching_packet(content, refs, mode='extended')
    checked = validate_matching_result(packet, {'assignments': [decision('inference')]})
    text, binding = relocate_citations(content, refs, checked['matches'], mode='extended')
    with store.connect() as db:
        draft = service._load(db, project['id']); draft['mode'] = 'extended'
        section = draft['sections'][0]; section.update(mode='inherit' if change == 'project' else 'extended', content=text, evidence=refs, citationBindings=binding)
        service._save(db, draft, 'controlled_expanded_binding')
    saved = service.get(project['id'])
    if change == 'project':
        updated = service.patch(project['id'], saved['version'], {'mode': 'strict'})
    else:
        updated = service.save_section(project['id'], saved['sections'][0]['id'], saved['version'], {'mode': 'strict'})
    section = updated['sections'][0]
    assert section['content'] == text and section['evidence'] == refs
    assert 'citationBindings' not in section and section['citationBindingState'] == 'stale'
