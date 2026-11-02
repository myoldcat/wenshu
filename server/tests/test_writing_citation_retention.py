"""Incomplete review retains exact generated citations without a support claim."""
import copy
import json

import pytest

from server.model_gateway import ModelError
from server.store import dumps
from server.tests.test_writing_automatic import PROSE, automatic, run_automatic
from server.writing import WritingService
from server.writing_citation_matching import build_matching_packet, matching_policy, validate_matching_result
from server.writing_review import review_requires_draft
from server.writing_sentence_citations import (
    RESOLVED_STATUSES, relocate_citations, sentence_ranges, strip_citations,
    unreviewed_matches, utf16_offset, valid_bindings,
)


def registry(*ids):
    return [{'id': eid, 'sourceId': 'selected-source', 'revisionId': 'selected-revision',
             'blockId': 'selected-block-' + eid, 'quote': '已选原文。', 'start': 0, 'end': 5}
            for eid in ids]


def uncertain_matches(content):
    plain = strip_citations(content)
    return [{'sentenceId': 'S%d' % index, 'start': utf16_offset(plain, item['start']),
             'end': utf16_offset(plain, item['end']), 'text': item['text'], 'evidenceId': None,
             'status': 'uncertain', 'supportBasis': 'none', 'reason': '引用核对未完成，尚未判断支持关系。'}
            for index, item in enumerate(sentence_ranges(plain), 1)]


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_unreviewed_recovery_keeps_only_original_sentence_markers_and_utf16_coordinates(mode):
    content = '  首句😀保留来源。[E2][E1] 第二句没有引用。\n\n末句仍保留来源。[E2]  '
    evidence, matches = registry('E1', 'E2'), uncertain_matches(content)
    before = copy.deepcopy((content, evidence, matches))
    recovered = unreviewed_matches(content, evidence, matches)
    assert [item['evidenceId'] for item in recovered] == ['E2', 'E1', None, 'E2']
    assert {item['status'] for item in recovered} == {'unreviewed'}
    assert {item['supportBasis'] for item in recovered} == {'none'}
    assert (content, evidence, matches) == before
    updated, binding = relocate_citations(content, evidence, recovered, mode=mode)
    assert updated == content and valid_bindings(updated, evidence, binding, mode=mode)
    assert 'unreviewed' not in RESOLVED_STATUSES
    first = binding['items'][0]
    assert first['start'] == 2 and first['end'] == utf16_offset(content, content.index('[E2]'))
    assert binding['items'][2]['evidenceId'] is None and 'markerStart' not in binding['items'][2]
    assert all(item['sourceId'] == 'selected-source' and item['revisionId'] == 'selected-revision' for item in evidence)


def test_recovery_does_not_move_mid_sentence_or_whitespace_separated_markers_to_other_sentences():
    content = '首句[E1]还未结束。 第二句。[E2] 第三句。 [E1]'
    evidence = registry('E1', 'E2')
    recovered = unreviewed_matches(content, evidence, uncertain_matches(content))
    updated, binding = relocate_citations(content, evidence, recovered, mode='strict')
    assert updated == '首句还未结束。 第二句。[E2] 第三句。 '
    assert [item['evidenceId'] for item in binding['items']] == [None, 'E2', None]
    assert valid_bindings(updated, evidence, binding, mode='strict')


def test_unknown_and_duplicate_original_markers_cannot_create_extra_references():
    content = '第一句。[E1][E1][E99]第二句。[E2]'
    evidence = registry('E1')
    matches = uncertain_matches(content)
    matches[0]['evidenceId'] = 'E99'  # A failed review cannot choose its own replacement source.
    recovered = unreviewed_matches(content, evidence, [matches[0], copy.deepcopy(matches[0]), matches[1]])
    updated, binding = relocate_citations(content, evidence, recovered, mode='strict')
    assert updated == '第一句。[E1]第二句。'
    assert [item['evidenceId'] for item in recovered] == ['E1', None]
    assert valid_bindings(updated, evidence, binding)


@pytest.mark.parametrize('status', ['supported', 'partial', 'unsupported', 'not_required'])
def test_recovery_does_not_relabel_completed_semantic_judgments(status):
    evidence, content = registry('E1'), '已有引用的句子。[E1]'
    judgment = uncertain_matches(content)[0]
    judgment.update(status=status, evidenceId='E1' if status in ('supported', 'partial') else None,
                    supportBasis='paraphrase' if status in ('supported', 'partial') else 'none')
    result = unreviewed_matches(content, evidence, [judgment])
    assert result == [judgment] and result[0] is not judgment


@pytest.mark.parametrize('mutation', ['marker', 'sentence', 'basis', 'registry', 'hash', 'mode'])
def test_unreviewed_bindings_reject_forged_locations_support_registry_and_mode(mutation):
    content, evidence = '😀正文引用。[E1]', registry('E1')
    updated, bindings = relocate_citations(content, evidence,
        unreviewed_matches(content, evidence, uncertain_matches(content)), mode='strict')
    assert valid_bindings(updated, evidence, bindings, mode='strict')
    forged, altered_evidence, mode = copy.deepcopy(bindings), copy.deepcopy(evidence), 'strict'
    if mutation == 'marker':
        forged['items'][0]['markerStart'] += 1
    elif mutation == 'sentence':
        forged['items'][0]['start'] = 1  # Inside the emoji's UTF-16 surrogate pair.
    elif mutation == 'basis':
        forged['items'][0]['supportBasis'] = 'direct'
    elif mutation == 'registry':
        altered_evidence[0]['revisionId'] = 'another-source-version'
    elif mutation == 'hash':
        forged['textHash'] = 'forged'
    else:
        mode = 'extended'
    assert not valid_bindings(updated, altered_evidence, forged, mode=mode)


def test_source_free_unreviewed_state_has_no_marker_and_is_not_semantic_unsupported():
    content = '本句没有生成引用。'
    recovered = unreviewed_matches(content, [], uncertain_matches(content))
    updated, binding = relocate_citations(content, [], recovered, mode='strict')
    assert updated == content and recovered[0]['status'] == 'unreviewed'
    assert recovered[0]['evidenceId'] is None and recovered[0]['supportBasis'] == 'none'
    assert valid_bindings(updated, [], binding, mode='strict')
    assert 'markerStart' not in binding['items'][0]
    forged = copy.deepcopy(binding)
    forged['items'][0]['markerStart'] = len(content)
    assert not valid_bindings(updated, [], forged, mode='strict')


def test_model_cannot_return_program_owned_unreviewed_status():
    packet = build_matching_packet('正文。', registry('E1'), mode='strict', policy=matching_policy('strict'))
    with pytest.raises(ValueError, match='支持状态'):
        validate_matching_result(packet, {'assignments': [{'sentenceId': packet['sentences'][0]['sentenceId'],
            'evidenceIds': ['E1'], 'support': 'unreviewed', 'supportBasis': 'none', 'reason': '模型不能声明程序恢复状态。'}]})


def test_recovery_rejects_ambiguous_registry_and_non_sentence_coordinates():
    content = '😀完整句子。[E1]'
    with pytest.raises(ValueError, match='不唯一'):
        unreviewed_matches(content, registry('E1', 'E1'), uncertain_matches(content))
    matches = uncertain_matches(content)
    matches[0]['start'] = 1
    with pytest.raises(ValueError):
        unreviewed_matches(content, registry('E1'), matches)


def install_batch_failure(gateway, *, power_loss=None):
    complete = gateway.complete
    sentences = ['第%d句保留原始资料。' % index for index in range(1, 11)]
    state = {'generation': 0, 'requests': [], 'failed': False, 'lost': False}
    gateway.config['maxOutputTokens'] = 20000

    def wrapped(config, messages):
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_citation_match' and packet.get('reviewPurpose') != 'outline_scope':
            state['requests'].append({'packet': copy.deepcopy(packet), 'config': copy.deepcopy(config)})
            if not state['failed']:
                state['failed'] = True
                raise ModelError('受控模型核对暂时不可用。')
            if power_loss and not state['lost']:
                state['lost'] = True
                raise power_loss()
        response = complete(config, messages)
        if packet['task'] == 'writing_section':
            state['generation'] += 1
            if packet['section']['title'] == '原始证据':
                block = packet['blocks'][0]
                response = {**response, 'text': dumps({'paragraphs': [{'text': ''.join(sentences), 'kind': 'supported',
                    'evidence': [{'blockId': block['blockId'], 'quote': block['text'], 'claim': sentence}
                                 for sentence in sentences]}], 'issues': []})}
        return response

    gateway.complete = wrapped
    return state, sentences


def assert_retained_batch(current, sentences):
    first, second = current['sections']
    assert strip_citations(first['content']) == ''.join(sentences)
    assert first['content'].count('[E1]') == 10 and first['evidence'][0]['id'] == 'E1'
    statuses = [item['status'] for item in first['citationBindings']['items']]
    assert statuses == ['unreviewed'] * 8 + ['supported'] * 2
    assert first['citationBindingState'] == 'needs_review'
    assert all(item['supportBasis'] == 'none' for item in first['citationBindings']['items'][:8])
    assert valid_bindings(first['content'], first['evidence'], first['citationBindings'], mode='strict')
    assert strip_citations(second['content']) == PROSE and second['citationBindingState'] == 'matched'
    report = current['automatic']['report']
    assert report['summary']['generatedSections'] == 2
    assert any(issue['code'] == 'citation_review_incomplete' for issue in report['review']['issues'])
    assert any(issue['code'] == 'citation_review_incomplete' for issue in report['issues'])
    assert review_requires_draft(report['review'])


def test_automatic_model_error_preserves_exact_generated_markers_and_limits_review_batches(automatic):
    store, gateway, _, _ = automatic
    state, sentences = install_batch_failure(gateway)
    queued, current = run_automatic(automatic)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert_retained_batch(current, sentences)
    assert [len(item['packet']['sentences']) for item in state['requests']] == [8, 2, 1]
    assert {item['config']['maxOutputTokens'] for item in state['requests']} == {20000}
    assert state['generation'] == 2


def test_small_output_budget_uses_smaller_batches_without_raising_configured_limit(automatic):
    store, gateway, _, _ = automatic
    state, sentences = install_batch_failure(gateway)
    gateway.config['maxOutputTokens'] = 600
    queued, current = run_automatic(automatic)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    first = current['sections'][0]
    assert strip_citations(first['content']) == ''.join(sentences)
    assert first['content'].count('[E1]') == 10
    assert [item['status'] for item in first['citationBindings']['items']] == ['unreviewed'] + ['supported'] * 9
    assert len(state['requests']) == 11
    assert {len(item['packet']['sentences']) for item in state['requests']} == {1}
    assert {item['config']['maxOutputTokens'] for item in state['requests']} == {600}
    assert review_requires_draft(current['automatic']['report']['review'])


def test_recovery_keeps_failed_batch_operational_status_and_reuses_its_checkpoint(automatic, monkeypatch):
    store, gateway, service, project = automatic

    class PowerLoss(BaseException):
        pass

    state, sentences = install_batch_failure(gateway, power_loss=PowerLoss)
    monkeypatch.setattr(service, '_automatic_finish', lambda *_args, **_kwargs: None)
    queued = service.queue_automatic(project['id'], project['version'])
    with pytest.raises(PowerLoss):
        service.process(store.claim())
    with store.connect() as db:
        checkpoints = db.execute('''SELECT s.result_json FROM writing_citation_match_steps s
            JOIN writing_runs r ON r.id=s.run_id WHERE r.project_id=? AND s.result_json IS NOT NULL''',
            (project['id'],)).fetchall()
        assert len(checkpoints) == 1
        assert json.loads(checkpoints[0]['result_json'])['reviewIncomplete'] is True
        db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
    restarted = WritingService(store, gateway)
    restarted.process(store.claim())
    current = restarted.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert_retained_batch(current, sentences)
    assert state['generation'] == 2
    assert [len(item['packet']['sentences']) for item in state['requests']] == [8, 2, 2, 1]


def test_older_automatic_parent_does_not_acquire_generated_citation_retention(automatic):
    store, gateway, service, project = automatic
    original, failed = gateway.complete, False

    def fail_first_review(config, messages):
        nonlocal failed
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_citation_match' and packet.get('reviewPurpose') != 'outline_scope' and not failed:
            failed = True
            raise ModelError('受控旧任务核对失败。')
        return original(config, messages)

    gateway.complete = fail_first_review
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        row = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()
        frozen = json.loads(row['input_json'])
        frozen.pop('citationRetentionPolicy', None)
        frozen.pop('citationReviewPolicy', None)
        frozen.pop('automaticMaterialPolicy', None)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    service.process(store.claim())
    current = service.get(project['id'])
    assert failed and current['sections'][0]['content'] == PROSE
    assert '[E1]' not in current['sections'][0]['content']
    assert {item['status'] for item in current['sections'][0]['citationBindings']['items']} == {'unsupported'}
    with store.connect() as db:
        rows = db.execute("SELECT input_json FROM writing_runs WHERE project_id=? AND kind='section'", (project['id'],)).fetchall()
    assert all('citationRetentionPolicy' not in json.loads(json.loads(row['input_json'])['messages'][1]['content']) for row in rows)


@pytest.mark.parametrize('retain_current', [True, False])
@pytest.mark.parametrize('bad_first', [True, False])
def test_one_bad_quote_keeps_other_exact_citations_in_same_paragraph_only_under_new_policy(automatic, retain_current, bad_first):
    store, gateway, service, project = automatic
    complete = gateway.complete
    uncited = '另外的情况尚未得到原文支持。'
    source_quote = store.detail(project['sourceIds'][0])['blocks'][0]['text']

    def mixed_references(config, messages):
        packet = json.loads(messages[-1]['content'])
        response = complete(config, messages)
        if packet['task'] == 'writing_section' and packet['section']['title'] == '原始证据':
            block = packet['blocks'][0]
            valid = {'blockId': block['blockId'], 'quote': block['text'], 'claim': PROSE}
            invalid = {'blockId': block['blockId'], 'quote': '这段摘录完全不存在于原文中。', 'claim': uncited}
            response = {**response, 'text': dumps({'paragraphs': [{'text': PROSE + uncited,
                'kind': 'supported', 'evidence': [invalid, valid] if bad_first else [valid, invalid]}], 'issues': []})}
        elif packet['task'] == 'writing_citation_match' and any(sentence['text'] == uncited for sentence in packet['sentences']):
            # A successful review keeps the valid sentence's actual evidence;
            # an unrelated second sentence cannot borrow the same paragraph's source.
            response = {**response, 'text': dumps({'assignments': [{
                'sentenceId': sentence['sentenceId'],
                'evidenceIds': [item['evidenceId'] for item in packet['evidence']] if sentence['text'] == PROSE else [],
                'support': 'supported' if sentence['text'] == PROSE else 'unsupported',
                'supportBasis': 'paraphrase' if sentence['text'] == PROSE else 'none',
                'reason': '受控测试只支持第一句，第二句没有对应证据。'} for sentence in packet['sentences']]})}
        return response

    gateway.complete = mixed_references
    queued = service.queue_automatic(project['id'], project['version'])
    if not retain_current:
        with store.connect() as db:
            row = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()
            frozen = json.loads(row['input_json'])
            frozen.pop('citationRetentionPolicy', None)
            frozen.pop('citationReviewPolicy', None)
            frozen.pop('automaticMaterialPolicy', None)
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    service.process(store.claim())
    current = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    first = current['sections'][0]
    assert strip_citations(first['content']) == PROSE + uncited
    if retain_current:
        assert first['content'] == PROSE + '[E1]' + uncited
        assert len(first['evidence']) == 1 and first['evidence'][0]['quote'] == source_quote
        reference = first['evidence'][0]
        assert source_quote[reference['start']:reference['end']] == reference['quote']
        assert [item['status'] for item in first['citationBindings']['items']] == ['supported', 'unsupported']
        assert any('引用未采用' in issue['message'] for issue in current['automatic']['report']['issues'])
    else:
        assert first['content'] == PROSE + uncited and first['evidence'] == []
        assert {item['status'] for item in first['citationBindings']['items']} == {'unsupported'}
    assert strip_citations(current['sections'][1]['content']) == PROSE
    assert current['automatic']['report']['summary']['generatedSections'] == 2
