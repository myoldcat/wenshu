"""Sentence-specific evidence decisions with immutable prose and checkpoints.

Controlled support judgments test program behaviour, not a model's accuracy.
No configured provider or persistent library is used. Generation integration
cases explicitly freeze the historical inline-review task contract.
"""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_writing_integrity import writing, enqueue as queue_current, freeze_legacy_inline_review
from server.writing import CITATION_PLACEMENT, WritingConflict, WritingService
from server.writing_sentence_citations import (content_hash, relocate_citations,
                                               sentence_ranges, strip_citations,
                                               utf16_offset, valid_bindings)


def enqueue(environment):
    result, job = queue_current(environment)
    freeze_legacy_inline_review(environment[0], result['runId'])
    return result, job


def matches(text, evidence_id='E1', *, statuses=None):
    return [{'start': utf16_offset(text, sentence['start']),
             'end': utf16_offset(text, sentence['end']), 'text': sentence['text'],
             'evidenceId': evidence_id if status == 'supported' else None,
             'status': status, 'reason': '受控判断：逐句检查条件和论述范围。'}
            for sentence, status in zip(sentence_ranges(text), statuses or ['supported'] * len(sentence_ranges(text)))]


def install_matching_gateway(gateway, *, statuses=None, fail_matching=0, fail_matching_on=()):
    complete = gateway.complete
    state = {'generation': 0, 'matching': 0, 'requests': []}

    def wrapped(config, messages):
        packet = json.loads(messages[1]['content'])
        if packet.get('protocol') not in ('sentence-evidence-review-v1', 'sentence-evidence-review-v2'):
            state['generation'] += 1
            return complete(config, messages)
        state['matching'] += 1
        state['requests'].append(packet)
        if state['matching'] <= fail_matching or state['matching'] in fail_matching_on:
            raise ValueError('受控逐句核对暂时失败')
        rows = []
        for index, sentence in enumerate(packet['sentences']):
            status = statuses[index] if statuses else 'supported'
            evidence = [packet['evidence'][0]['evidenceId']] if status in ('supported', 'partial') and packet['evidence'] else []
            rows.append({'sentenceId': sentence['sentenceId'], 'evidenceIds': evidence,
                         'support': status, 'reason': '受控语义判断，检查同一对象及限定条件。',
                         **({'supportBasis': 'paraphrase' if evidence else 'none'} if packet.get('mode') is not None else {})})
        return {'text': dumps({'assignments': rows}), 'usage': {'total_tokens': 11}}

    gateway.complete = wrapped
    return state


def frozen_for(writing, run):
    with writing[0].connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])


def evidence_for(writing):
    store, _, _, source, _ = writing
    original = store.detail(source)
    block = original['blocks'][0]
    return [{'id': 'E1', 'sourceId': source, 'revisionId': original['revisionId'],
             'blockId': block['id'], 'title': original['title'], 'url': '',
             'quote': block['text'], 'start': 0, 'end': len(block['text']), 'locator': block['locator']}]


def test_relocation_moves_only_markers_and_uses_utf16_exact_sentence_ranges():
    body = '  首句😀有证据。 第二句没有证据！\n\n第三句也有证据。  '
    legacy = body + '[E1][E1]'
    evidence = [{'id': 'E1'}]
    result, binding = relocate_citations(legacy, evidence, matches(body, statuses=['supported', 'unsupported', 'supported']))
    assert strip_citations(result) == body
    assert result == '  首句😀有证据。[E1] 第二句没有证据！\n\n第三句也有证据。[E1]  '
    assert binding['content'] == result and binding['contentHash'] == content_hash(result)
    assert binding['textHash'] == content_hash(body) and valid_bindings(result, evidence, binding)
    assert binding['items'][0]['start'] == 2
    assert binding['items'][0]['end'] == 10  # Emoji occupies two UTF-16 units.
    assert 'markerStart' not in binding['items'][1]
    assert [item['status'] for item in binding['items']] == ['supported', 'unsupported', 'supported']


@pytest.mark.parametrize('claim', ['首句', '首句。第二句。'])
def test_partial_phrase_or_whole_multi_sentence_paragraph_cannot_bind(claim):
    content = '首句。第二句。'
    with pytest.raises(ValueError, match='完整句子'):
        relocate_citations(content, [{'id': 'E1'}], [{'evidenceId': 'E1', 'start': 0,
            'end': len(claim), 'text': claim, 'status': 'supported'}])


def test_every_sentence_requires_explicit_judgment_no_silent_coverage_inference():
    with pytest.raises(ValueError, match='遗漏'):
        relocate_citations('首句。第二句。', [{'id': 'E1'}], matches('首句。'))


def test_stale_text_evidence_or_forged_occurrence_is_rejected():
    evidence = [{'id': 'E1', 'quote': '原文甲'}]
    content, binding = relocate_citations('正文。', evidence, matches('正文。'))
    assert valid_bindings(content, evidence, binding)
    assert not valid_bindings('改动' + content, evidence, binding)
    assert not valid_bindings(content, [{'id': 'E1', 'quote': '替换原文'}], binding)
    forged = copy.deepcopy(binding)
    forged['items'][0]['markerStart'] -= 1
    assert not valid_bindings(content, evidence, forged)


def test_not_required_has_no_marker_or_source_and_review_method_is_explicit():
    body = '下文从三个方面展开讨论。'
    decisions = matches(body, statuses=['not_required'])
    updated, binding = relocate_citations(body, [], decisions, method='assistant_evidence_review')
    assert updated == body and binding['method'] == 'assistant_evidence_review'
    assert binding['items'][0]['evidenceId'] is None and 'markerStart' not in binding['items'][0]
    assert valid_bindings(updated, [], binding)
    assert relocate_citations(body, [], decisions)[1]['method'] == 'model_review'
    forged = copy.deepcopy(binding)
    forged['items'][0]['evidenceId'] = 'E1'
    assert not valid_bindings(updated, [{'id': 'E1'}], forged)
    with pytest.raises(ValueError, match='来源证据'):
        relocate_citations(body, [{'id': 'E1'}], [{**decisions[0], 'evidenceId': 'E1'}])
    with pytest.raises(ValueError, match='审阅来源'):
        relocate_citations(body, [], decisions, method='verified')


def test_generation_requires_complete_sentence_then_independent_semantic_pass(writing):
    store, gateway, service, _, project = writing
    state = install_matching_gateway(gateway)
    run, job = enqueue(writing)
    frozen = frozen_for(writing, run)
    assert json.loads(frozen['messages'][1]['content'])['citationPlacement'] == CITATION_PLACEMENT
    assert frozen['citationMatchInstructions']
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    section = service.get(project['id'])['sections'][0]
    assert state['generation'] == state['matching'] == 1
    assert valid_bindings(section['content'], section['evidence'], section['citationBindings'])
    assert section['citationBindings']['items'][0]['text'] == '资料记载应保留原始证据和固定版本。'
    assert len(service.operations.calls(run['runId'])['items']) == 2


def test_semantic_partial_does_not_gain_source_marker_or_autoapply_strict_draft(writing):
    store, gateway, service, _, project = writing
    install_matching_gateway(gateway, statuses=['partial'])
    run, job = enqueue(writing)
    service.process(job)
    assert store.job(job['id'])['stage'] == 'candidate'
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == ''
    candidate = current['runs'][0]['candidate']['section']
    assert '[E1]' not in candidate['content']
    assert candidate['citationBindings']['items'][0]['status'] == 'partial'
    adopted = service.apply(project['id'], run['runId'], current['version'])
    review = service.review(project['id'], adopted['version'])['review']
    assert any(issue['code'] == 'citation_sentence_partial' for issue in review['issues'])


def test_semantic_matching_can_move_an_incorrect_last_sentence_claim_to_supported_first_sentence(writing):
    store, gateway, service, _, project = writing
    supported = '应保留固定版本以便回查原文。'
    unsupported = '因此所有内容已经获得独立事实核验。'

    def generated(result, request):
        block = request['blocks'][0]
        return {'paragraphs': [{'text': supported + unsupported, 'kind': 'supported',
                              'evidence': [{'blockId': block['blockId'], 'quote': block['text'], 'claim': unsupported}]}]}

    gateway.transform = generated
    install_matching_gateway(gateway, statuses=['supported', 'unsupported'])
    run, job = enqueue(writing)
    service.process(job)
    current = service.get(project['id'])
    candidate = current['runs'][0]['candidate']['section']
    assert candidate['content'] == supported + '[E1]' + unsupported
    assert candidate['citationBindings']['items'][0]['text'] == supported
    assert candidate['citationBindings']['items'][1]['status'] == 'unsupported'
    assert current['sections'][0]['content'] == ''  # Strict uncertainty is reviewable.
    assert store.job(job['id'])['stage'] == 'candidate'


def test_matching_failure_retry_reuses_successful_prose_generation(writing):
    store, gateway, service, _, project = writing
    state = install_matching_gateway(gateway, fail_matching=1)
    run, job = enqueue(writing)
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    current = service.get(project['id'])
    assert current['runs'][0]['candidate']['citationMatchingPending'] is True
    with pytest.raises(ValueError, match='完成'):
        service.apply(project['id'], run['runId'], current['version'])
    store.retry(job['id'])
    service.process(store.claim())
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert state['generation'] == 1 and state['matching'] == 2


def test_completed_matching_batch_is_checkpointed_and_reused_after_next_batch_fails(writing):
    store, gateway, service, _, project = writing
    gateway.config['maxOutputTokens'] = 600  # Current retention policy reserves one judgment per call.
    sentences = ['第一句保留原始证据。', '第二句保留固定版本。', '第三句可回查原文。']

    def generated(result, request):
        block = request['blocks'][0]
        return {'paragraphs': [{'text': ''.join(sentences), 'kind': 'supported',
                              'evidence': [{'blockId': block['blockId'], 'quote': block['text'], 'claim': sentence}
                                           for sentence in sentences]}]}

    gateway.transform = generated
    state = install_matching_gateway(gateway, fail_matching_on={2})
    run, job = enqueue(writing)
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    candidate = service.get(project['id'])['runs'][0]['candidate']
    assert len(candidate['citationMatchingCheckpoints']) == 1
    completed = next(iter(candidate['citationMatchingCheckpoints'].values()))
    assert [item['sentenceId'] for item in completed['assignments']] == ['S1']
    assert completed['assignments'][0]['support'] == 'supported'
    assert candidate['citationMatchingPending'] is True
    original_candidate = copy.deepcopy(candidate['section'])
    assert [[item['sentenceId'] for item in request['sentences']] for request in state['requests']] == [['S1'], ['S2']]
    store.retry(job['id'])
    restarted = WritingService(store, gateway)
    restarted.process(store.claim())
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert state['generation'] == 1 and state['matching'] == 4
    assert [[item['sentenceId'] for item in request['sentences']] for request in state['requests']] == [
        ['S1'], ['S2'], ['S2'], ['S3']]
    assert state['requests'][1] == state['requests'][2]  # Retry only the failed frozen batch.
    current = restarted.get(project['id'])
    section = current['sections'][0]
    assert section['content'] == original_candidate['content']
    assert strip_citations(section['content']) == ''.join(sentences)
    assert section['evidence'] == original_candidate['evidence']
    assert valid_bindings(section['content'], section['evidence'], section['citationBindings'], mode='strict')
    assert [item['text'] for item in section['citationBindings']['items']] == sentences
    assert all(item['status'] == 'supported' for item in section['citationBindings']['items'])
    assert 'citationMatchingCheckpoints' not in current['runs'][0]['candidate']


def test_citation_only_rebind_preserves_manual_locked_prose_and_immutable_evidence(writing):
    store, _, service, _, project = writing
    sid = project['sections'][0]['id']
    original = '  首句😀已有原文支持。 第二句待核实。[E1]\n\n第三句有原文支持。  '
    saved = service.save_section(project['id'], sid, project['version'], {'content': original})
    evidence = evidence_for(writing)
    with store.connect() as db:
        draft = service._load(db, project['id'])
        draft['sections'][0]['evidence'] = evidence
        draft['sections'][0]['locked'] = True
        service._save(db, draft, 'controlled_fixture')
    before = service.get(project['id'])
    plain = strip_citations(original)
    result = service.rebind_citations(project['id'], before['version'], [
        {'sectionId': sid, 'contentHash': content_hash(original),
         'matches': matches(plain, statuses=['supported', 'unsupported', 'supported'])}])
    section = result['sections'][0]
    assert strip_citations(section['content']) == plain
    assert section['evidence'] == evidence and section['locked'] and section['manual']
    assert valid_bindings(section['content'], evidence, section['citationBindings'])
    assert service.historical(project['id'], before['version'])['sections'][0]['content'] == original
    assert service.history(project['id'])['versions'][0]['reason'] == 'citations_rematched'
    with pytest.raises(WritingConflict):
        service.rebind_citations(project['id'], before['version'], [{'sectionId': sid, 'contentHash': content_hash(original), 'matches': matches(plain)}])


def test_saving_changed_body_invalidates_precise_binding_but_keeps_evidence(writing):
    _, gateway, service, _, project = writing
    install_matching_gateway(gateway)
    run, job = enqueue(writing)
    service.process(job)
    current = service.get(project['id'])
    section = current['sections'][0]
    same = service.save_section(project['id'], section['id'], current['version'], {'content': section['content']})
    assert same['sections'][0]['citationBindings'] == section['citationBindings']
    changed = service.save_section(project['id'], section['id'], same['version'], {'content': '人工新论述。' + section['content']})
    assert 'citationBindings' not in changed['sections'][0]
    assert changed['sections'][0]['citationBindingState'] == 'stale'
    assert changed['sections'][0]['evidence'] == section['evidence']


def test_assistant_review_preserves_prose_and_provenance_without_fabricated_model_calls(writing):
    store, _, service, _, project = writing
    sid = project['sections'][0]['id']
    evidence = evidence_for(writing)
    transition = '下文将从资料保存、引用回查与修订记录三个方面展开讨论。'
    prose = transition + '\n\n' + evidence[0]['quote']
    with store.connect() as db:
        draft = service._load(db, project['id'])
        section = draft['sections'][0]
        section.update(content=prose + '[E1]', evidence=evidence, manual=True, locked=True, targetWords=100)
        service._save(db, draft, 'controlled_fixture')
        before_rows = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                       for table in ('jobs', 'writing_runs', 'writing_model_interactions')}
    before = service.get(project['id'])
    matched = service.rebind_citations(project['id'], before['version'], [
        {'sectionId': sid, 'contentHash': content_hash(before['sections'][0]['content']),
         'matches': matches(prose, statuses=['not_required', 'supported'])}], method='assistant_evidence_review')
    section = matched['sections'][0]
    assert strip_citations(section['content']) == prose and section['manual'] and section['locked']
    assert section['citationBindingState'] == 'matched' and section['citationIssues'] == []
    assert section['citationBindings']['method'] == 'assistant_evidence_review'
    report = service.review(project['id'], matched['version'])['review']
    assert report['coverage']['notRequiredSentences'] == 1
    assert report['coverage']['unresolvedSentences'] == 0
    assert report['citationReviewMethods'] == ['assistant_evidence_review']
    assert '辅助证据审阅' in report['notice'] and '为模型' not in report['notice']
    assert not any(issue['code'].startswith('citation_') or issue['code'] in ('uncited_paragraph', 'manual_edit_recheck')
                   for issue in report['issues'])
    with store.connect() as db:
        assert {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in before_rows} == before_rows


def test_strict_generation_treats_neutral_sentence_as_processed_without_false_citation(writing):
    store, gateway, service, _, project = writing
    transition = '下文从三个方面展开讨论。'
    supported = '原文保留固定版本，读者能够回查依据。'

    def generated(result, request):
        block = request['blocks'][0]
        return {'paragraphs': [{'text': transition + supported, 'kind': 'supported',
                              'evidence': [{'blockId': block['blockId'], 'quote': block['text'], 'claim': supported}]}]}

    gateway.transform = generated
    install_matching_gateway(gateway, statuses=['not_required', 'supported'])
    run, job = enqueue(writing)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    section = service.get(project['id'])['sections'][0]
    assert section['content'] == transition + supported + '[E1]'
    assert section['citationBindingState'] == 'matched' and section['citationIssues'] == []
    assert valid_bindings(section['content'], section['evidence'], section['citationBindings'])


def test_review_groups_multiple_partial_excerpt_bindings_into_one_sentence_issue(writing):
    store, _, service, _, project = writing
    evidence = evidence_for(writing)
    second = copy.deepcopy(evidence[0])
    second.update(id='E2', quote=second['quote'][:12], end=12)
    evidence.append(second)
    body = '该句包含一部分受支持内容及仍缺少原文依据的结论。'
    with store.connect() as db:
        draft = service._load(db, project['id'])
        draft['sections'][0].update(content=body, evidence=evidence, manual=True)
        service._save(db, draft, 'controlled_fixture')
    before = service.get(project['id'])
    decision = matches(body, statuses=['partial'])[0]
    decisions = [{**decision, 'evidenceId': eid} for eid in ('E1', 'E2')]
    rebound = service.rebind_citations(project['id'], before['version'], [
        {'sectionId': before['sections'][0]['id'], 'contentHash': content_hash(body), 'matches': decisions}],
        method='assistant_evidence_review')
    report = service.review(project['id'], rebound['version'])['review']
    partial_issues = [issue for issue in report['issues'] if issue['code'] == 'citation_sentence_partial']
    assert len(partial_issues) == 1
    assert partial_issues[0]['sentence'] == body and partial_issues[0]['reason'] == decision['reason']
    assert partial_issues[0]['evidenceId'] == 'E1' and partial_issues[0]['evidenceIds'] == ['E1', 'E2']
    assert report['coverage']['unresolvedSentences'] == 1
    assert not any(issue['code'] == 'manual_edit_recheck' for issue in report['issues'])
