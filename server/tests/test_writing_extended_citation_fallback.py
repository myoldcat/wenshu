"""Extended-mode citation failures retain prose without inventing evidence.

Persisted jobs use isolated stores and deterministic fake model responses. These
checks freeze pre-six-step inline-review requests and historical project report
rules; the default deferred flow is covered by the six-step suites separately.
"""
import copy
import json
import re

import pytest

from server.store import dumps
from server.tests.test_writing_document import document, queue_and_claim, ready
from server.tests.test_writing_edit_integrity import edit_environment, queued_edit, write
from server.tests.test_writing_integrity import (freeze_legacy_generation_output, freeze_legacy_inline_review,
                                                 use_legacy_inline_fixture)
from server.tests.test_writing_mode_routing import project_with_section
from server.writing import WritingConflict
from server.writing_output import CITATION_RETENTION_POLICY
from server.writing_sentence_citations import valid_bindings


POLICY = 'retain-as-prose-v2'
LEGACY_POLICY = 'retain-as-unverified-v1'
PREFIX = '【模型补充·待核实】'
BAD_BODY = '新增讨论仍应保留原有措辞。'
GOOD_BODY = '修订应保留人工修改记录。'


@pytest.fixture(autouse=True)
def historical_inline_protocol(document, monkeypatch):
    use_legacy_inline_fixture(document, monkeypatch)


def reference(payload, text, *, quote=None):
    block = payload['blocks'][0]
    return {'blockId': block['blockId'], 'quote': quote or block['text'], 'claim': text}


def generated(payload, problem='foreign_block'):
    bad = {'text': BAD_BODY, 'kind': 'supported',
           'evidence': [reference(payload, BAD_BODY)]}
    ref = bad['evidence'][0]
    if problem == 'foreign_block':
        ref['blockId'] = 'C99-B999'
    elif problem == 'false_quote':
        ref['quote'] = '这段原文不存在，不能登记为证据。'
    elif problem == 'ambiguous_quote':
        ref['quote'] = '。'
    elif problem == 'missing_claim':
        ref.pop('claim')
    elif problem == 'false_claim':
        ref['claim'] = '这不是本段中的句子。'
    elif problem == 'partial_claim':
        ref['claim'] = '原有措辞'
    elif problem == 'ambiguous_claim':
        bad['text'] = '重复句子。重复句子。'
        ref['claim'] = '重复句子。'
    elif problem == 'multiple_claims':
        bad['text'] = '第一句讨论。第二句讨论。'
        ref['claim'] = bad['text']
    elif problem == 'non_list':
        bad['evidence'] = {'blockId': ref['blockId'], 'quote': ref['quote']}
    elif problem == 'non_object':
        bad['evidence'] = ['并非引用对象']
    elif problem == 'too_many':
        bad['evidence'] = [copy.deepcopy(ref) for _ in range(13)]
    elif problem == 'supplement_with_evidence':
        bad['kind'] = 'model_supplement'
    elif problem == 'self_numbered':
        bad['text'] += '[E9]'
    else:
        raise AssertionError(problem)
    return [bad, {'text': GOOD_BODY, 'kind': 'supported',
                  'evidence': [reference(payload, GOOD_BODY)]}]


def install_response(gateway, *, problem='foreign_block', malicious_matching=False, paragraphs=None):
    """Only explicitly valid fixture claims receive a fake support decision."""
    original_complete = gateway.complete
    gateway.paragraphs = paragraphs or (lambda packet: generated(packet, problem))

    def complete(config, messages):
        packet = json.loads(messages[1]['content'])
        if packet.get('task') != 'writing_citation_match':
            return original_complete(config, messages)
        gateway.matching_calls.append({'config': copy.deepcopy(config), 'payload': copy.deepcopy(packet)})
        assignments = []
        valid = gateway.generated_sections.get(packet['sectionId'], [])
        for sentence in packet['sentences']:
            matching = [ref for paragraph in valid if paragraph['text'] == sentence['text']
                        and paragraph.get('kind') == 'supported'
                        and isinstance(paragraph.get('evidence'), list)
                        for ref in paragraph['evidence'] if isinstance(ref, dict)
                        and ref.get('claim') == sentence['text']]
            ids = list(dict.fromkeys(item['evidenceId'] for ref in matching
                                    for item in packet['evidence'] if item['quote'] == ref.get('quote')))
            if malicious_matching:
                ids = [item['evidenceId'] for item in packet['evidence']]
            assignments.append({'sentenceId': sentence['sentenceId'], 'evidenceIds': ids,
                                'support': 'supported' if ids else 'unsupported',
                                'reason': '隔离测试预设的逐句复核结论。',
                                **({'supportBasis': 'paraphrase' if ids else 'none'}
                                   if packet.get('mode') is not None else {})})
        return {'text': dumps({'assignments': assignments}), 'usage': {'total_tokens': 7}}

    gateway.complete = complete


def queue_section(document, project, *, prior_paragraph_fallback=False):
    store, _, service, _ = document
    run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    job = store.claim()
    assert job['id'] == run['job']['id']
    if prior_paragraph_fallback:
        freeze_paragraph_fallback(store, run['runId'])
    return run, job


def frozen_run(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


def freeze_paragraph_fallback(store, run_id, *, whole_document=False):
    """Preserve the prior paragraph-wide contract independently of plain-prose v2."""
    freeze_legacy_inline_review(store, run_id, whole_document=whole_document)
    with store.connect() as db:
        table = 'writing_document_steps' if whole_document else 'writing_runs'
        column = 'run_id' if whole_document else 'id'
        pending = " AND status!='skipped'" if whole_document else ''
        rows = db.execute(f'SELECT rowid,input_json FROM {table} WHERE {column}=?{pending}', (run_id,)).fetchall()
        assert rows
        for row in rows:
            frozen = json.loads(row['input_json'])
            packet = json.loads(frozen['messages'][1]['content'])
            assert packet.pop('citationRetentionPolicy') == CITATION_RETENTION_POLICY
            assert packet['generationOutputPolicy'] == 'preserve-usable-v1'
            if frozen['effectiveMode'] == 'extended':
                assert packet['unmatchedCitationPolicy'] == POLICY
            frozen['messages'][1]['content'] = dumps(packet)
            db.execute(f'UPDATE {table} SET input_json=? WHERE rowid=?', (dumps(frozen), row['rowid']))


def replace_frozen_policy(store, run_id, whole_document, policy):
    """Emulate jobs already frozen before the current publication policy."""
    freeze_legacy_generation_output(store, run_id, whole_document=whole_document)
    with store.connect() as db:
        table = 'writing_document_steps' if whole_document else 'writing_runs'
        column = 'run_id' if whole_document else 'id'
        rows = db.execute(f'SELECT rowid,input_json FROM {table} WHERE {column}=?', (run_id,)).fetchall()
        assert rows
        for row in rows:
            frozen = json.loads(row['input_json'])
            packet = json.loads(frozen['messages'][1]['content'])
            packet.pop('citationRetentionPolicy', None)
            assert packet.pop('unmatchedCitationPolicy') == POLICY
            if policy is not None:
                packet['unmatchedCitationPolicy'] = policy
            frozen['messages'][1]['content'] = dumps(packet)
            db.execute(f'UPDATE {table} SET input_json=? WHERE rowid=?', (dumps(frozen), row['rowid']))


def assert_fallback(section, raw_bad=BAD_BODY):
    paragraphs = section['content'].split('\n\n')
    assert paragraphs[0] == raw_bad
    assert paragraphs[1] == GOOD_BODY + '[E1]'
    assert [item['id'] for item in section['evidence']] == ['E1']
    assert section['extendedContentPolicy'] == POLICY
    assert not section.get('citationIssues')
    unmatched = [item for item in section['citationBindings']['items'] if item['status'] == 'unsupported']
    assert unmatched and all(item.get('evidenceId') is None for item in unmatched)
    assert PREFIX not in section['content']


@pytest.mark.parametrize('problem', [
    'foreign_block', 'false_quote', 'ambiguous_quote', 'missing_claim', 'false_claim',
    'partial_claim', 'ambiguous_claim', 'multiple_claims', 'non_list', 'non_object',
    'too_many', 'supplement_with_evidence',
])
def test_prior_extended_frozen_request_keeps_paragraph_fallback_and_other_evidence(document, problem):
    store, gateway, service, source = document
    project = project_with_section(document, 'strict', 'extended')
    original = store.detail(source)
    install_response(gateway, problem=problem)
    run, job = queue_section(document, project, prior_paragraph_fallback=True)
    frozen = frozen_run(store, run['runId'])
    assert frozen['effectiveMode'] == 'extended'
    assert json.loads(frozen['messages'][1]['content'])['unmatchedCitationPolicy'] == POLICY
    assert 'citationRetentionPolicy' not in json.loads(frozen['messages'][1]['content'])
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    output = service.get(project['id'])['sections'][0]
    sent = gateway.calls[0]['payload']
    assert_fallback(output, generated(sent, problem)[0]['text'])
    assert output['evidence'][0]['sourceId'] == source
    assert output['evidence'][0]['revisionId'] == original['revisionId']
    assert output['evidence'][0]['quote'] == original['blocks'][0]['text']
    assert store.detail(source) == original


def test_prior_frozen_paragraph_rolls_back_partial_evidence_and_leaves_contiguous_ids(document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    first_body = '固定版本和原始段落应予保存。'

    def paragraphs(packet):
        quotes = [value + '。' for value in packet['blocks'][0]['text'].split('。') if value]
        return [
            {'text': first_body, 'kind': 'supported', 'evidence': [reference(packet, first_body, quote=quotes[0])]},
            {'text': BAD_BODY, 'kind': 'supported', 'evidence': [
                reference(packet, BAD_BODY, quote=quotes[1]),
                {**reference(packet, BAD_BODY), 'blockId': 'C99-B999'}]},
            {'text': GOOD_BODY, 'kind': 'supported', 'evidence': [reference(packet, GOOD_BODY, quote=quotes[1])]},
        ]

    install_response(gateway, paragraphs=paragraphs)
    _, job = queue_section(document, project, prior_paragraph_fallback=True)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    section = service.get(project['id'])['sections'][0]
    assert section['content'] == first_body + '[E1]\n\n' + BAD_BODY + '\n\n' + GOOD_BODY + '[E2]'
    assert [item['id'] for item in section['evidence']] == ['E1', 'E2']
    assert re.sub(r'\[E\d+\]', '', section['content']) == '\n\n'.join(
        paragraph['text'] for paragraph in paragraphs(gateway.calls[0]['payload']))


@pytest.mark.parametrize('bad_first', [False, True])
def test_current_extended_policy_retains_valid_ref_when_same_paragraph_has_foreign_ref(document, bad_first):
    store, gateway, service, source = document
    project = project_with_section(document, 'strict', 'extended')
    original = store.detail(source)
    first_body = '固定版本和原始段落应予保存。'

    def paragraphs(packet):
        quotes = [value + '。' for value in packet['blocks'][0]['text'].split('。') if value]
        refs = [reference(packet, BAD_BODY, quote=quotes[1]),
                {**reference(packet, BAD_BODY), 'blockId': 'C99-B999'}]
        return [
            {'text': first_body, 'kind': 'supported', 'evidence': [reference(packet, first_body, quote=quotes[0])]},
            {'text': BAD_BODY, 'kind': 'supported', 'evidence': list(reversed(refs)) if bad_first else refs},
            {'text': GOOD_BODY, 'kind': 'supported', 'evidence': [reference(packet, GOOD_BODY, quote=quotes[1])]},
        ]

    install_response(gateway, paragraphs=paragraphs)
    run, job = queue_section(document, project)
    packet = json.loads(frozen_run(store, run['runId'])['messages'][1]['content'])
    assert packet['citationRetentionPolicy'] == CITATION_RETENTION_POLICY
    assert packet['unmatchedCitationPolicy'] == POLICY
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    section = current['sections'][0]
    assert section['content'] == first_body + '[E1]\n\n' + BAD_BODY + '[E2]\n\n' + GOOD_BODY + '[E2]'
    assert [item['id'] for item in section['evidence']] == ['E1', 'E2']
    assert len(section['citationBindings']['items']) == 3
    assert all(item['status'] == 'supported' for item in section['citationBindings']['items'])
    assert valid_bindings(section['content'], section['evidence'], section['citationBindings'], mode='extended')
    for item in section['evidence']:
        assert item['sourceId'] == source and item['revisionId'] == original['revisionId']
        block = next(block for block in original['blocks'] if block['id'] == item['blockId'])
        assert block['text'][item['start']:item['end']] == item['quote']
    issues = current['runs'][0]['candidate']['citationReferenceIssues']
    assert issues == [{'paragraphIndex': 2, 'referenceIndex': 1 if bad_first else 2,
                       'reason': '原文位置不在本章允许范围内，或引用信息不完整'}]
    assert any(f'第2段第{1 if bad_first else 2}项引用未采用' in notice
               for notice in section['generationNotices'])
    assert all('C99-B999' not in notice for notice in section['generationNotices'])
    assert store.detail(source) == original


def test_prior_frozen_matching_cannot_rebind_discarded_paragraph_to_another_valid_quote(document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway, malicious_matching=True)
    _, job = queue_section(document, project, prior_paragraph_fallback=True)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert_fallback(service.get(project['id'])['sections'][0])


@pytest.mark.parametrize(('project_mode', 'section_mode'), [('strict', 'inherit'), ('extended', 'strict')])
def test_legacy_strict_actual_mode_still_rejects_unmatched_reference(document, project_mode, section_mode):
    store, gateway, service, _ = document
    project = project_with_section(document, project_mode, section_mode)
    install_response(gateway)
    run, job = queue_section(document, project)
    freeze_legacy_generation_output(store, run['runId'])
    packet = json.loads(frozen_run(store, run['runId'])['messages'][1]['content'])
    assert 'unmatchedCitationPolicy' not in packet
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert service.get(project['id'])['sections'] == project['sections']


@pytest.mark.parametrize('whole_document', [False, True])
def test_older_extended_frozen_request_does_not_acquire_fallback_on_worker_upgrade(document, whole_document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway)
    run, job = (queue_and_claim if whole_document else queue_section)(document, project)
    replace_frozen_policy(store, run['runId'], whole_document, None)
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert service.get(project['id'])['sections'] == project['sections']


@pytest.mark.parametrize('whole_document', [False, True])
def test_legacy_unverified_frozen_request_keeps_original_markers_and_review(document, whole_document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway)
    run, job = (queue_and_claim if whole_document else queue_section)(document, project)
    replace_frozen_policy(store, run['runId'], whole_document, LEGACY_POLICY)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    section = current['sections'][0]
    assert section['content'] == PREFIX + BAD_BODY + '\n\n' + GOOD_BODY + '[E1]'
    assert section.get('extendedContentPolicy') != POLICY
    assert [item['id'] for item in section['evidence']] == ['E1']
    assert any('待核实' in (item.get('message') or item.get('reason', ''))
               for item in section['citationIssues'])
    codes = {item['code'] for item in service.review(project['id'], current['version'])['review']['issues']}
    assert {'uncited_paragraph', 'citation_sentence_unsupported', 'model_supplement'} <= codes


@pytest.mark.parametrize('problem', ['scope_map', 'duplicate_block'])
def test_corrupted_frozen_source_scope_is_not_downgraded_to_supplement(document, problem):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway)
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        row = db.execute('SELECT input_json FROM writing_document_steps WHERE run_id=?', (run['runId'],)).fetchone()
        frozen = json.loads(row['input_json'])
        if problem == 'scope_map':
            alias = next(iter(frozen['citationScope']['aliases']))
            frozen['citationScope']['aliases'][alias] = 'outside-frozen-source'
        else:
            frozen['blocks'].append({**copy.deepcopy(frozen['blocks'][0]), 'revisionId': 'foreign-version'})
        db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=?', (dumps(frozen), run['runId']))
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert service.get(project['id'])['sections'] == project['sections']


@pytest.mark.parametrize(('maximum', 'strategy'), [(8000, 'single'), (1800, 'sequential')])
@pytest.mark.parametrize('modes', [('extended', 'strict'), ('strict', 'extended')])
def test_mixed_document_retains_extended_failure_and_valid_strict_chapter(document, maximum, strategy, modes):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = maximum
    project = ready(document, words=(200, 200), modes=modes)

    def paragraphs(packet):
        return generated(packet) if packet['mode'] == 'extended' else [
            {'text': GOOD_BODY, 'kind': 'supported', 'evidence': [reference(packet, GOOD_BODY)]}]

    install_response(gateway, paragraphs=paragraphs)
    run, job = queue_and_claim(document, project)
    freeze_paragraph_fallback(store, run['runId'], whole_document=True)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    assert current['runs'][0]['plan']['strategy'] == strategy
    for section, mode in zip(current['sections'], modes):
        if mode == 'extended':
            assert_fallback(section)
        else:
            assert section['content'] == GOOD_BODY + '[E1]'
            assert len(section['evidence']) == 1
    sent = (gateway.calls[0]['payload']['sections'] if strategy == 'single'
            else [call['payload'] for call in gateway.calls])
    for packet, mode in zip(sent, modes):
        assert packet.get('unmatchedCitationPolicy') == (POLICY if mode == 'extended' else None)
    assert len(gateway.calls) == (1 if strategy == 'single' else 2)


@pytest.mark.parametrize('change', ['manual', 'lock'])
def test_extended_fallback_does_not_overwrite_inflight_manual_or_locked_section(document, change):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    section_id = project['sections'][0]['id']
    install_response(gateway)
    run, job = queue_section(document, project, prior_paragraph_fallback=True)
    manual = '正在编辑的原稿必须保留。'
    gateway.callback = lambda *_: service.save_section(project['id'], section_id, project['version'],
                                                        {'content': manual} if change == 'manual' else {'locked': True})
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == (manual if change == 'manual' else '')
    assert current['runs'][0]['candidate'] and not current['runs'][0]['applied']
    assert_fallback(current['runs'][0]['candidate']['section'])
    with pytest.raises(WritingConflict):
        service.apply(project['id'], run['runId'], current['version'])


def test_new_extended_fallback_removes_unregistered_markers_without_refusing_prose(document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway, problem='self_numbered')
    _, job = queue_section(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    section = service.get(project['id'])['sections'][0]
    assert_fallback(section)
    assert '[E9]' not in section['content']
    assert section['generationNotices'] and all(isinstance(item, str) for item in section['generationNotices'])


def test_document_fallback_preserves_existing_generated_manual_and_locked_chapters(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200, 200, 200, 200), modes=('extended',) * 4)
    install_response(gateway)
    _, first_job = queue_section(document, project, prior_paragraph_fallback=True)
    service.process(first_job)
    assert store.job(first_job['id'])['status'] == 'succeeded', store.job(first_job['id'])
    project = service.get(project['id'])
    project = service.save_section(project['id'], project['sections'][1]['id'], project['version'],
                                   {'content': '已保存的人工正文。'})
    project = service.save_section(project['id'], project['sections'][2]['id'], project['version'], {'locked': True})
    preserved = copy.deepcopy(project['sections'][:3])
    run, job = queue_and_claim(document, project)
    freeze_paragraph_fallback(store, run['runId'], whole_document=True)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    assert current['sections'][:3] == preserved
    assert_fallback(current['sections'][3])
    assert [section['id'] for section in gateway.calls[-1]['payload']['sections']] == [project['sections'][3]['id']]


@pytest.mark.parametrize('kind', ['supported', 'model_supplement'])
def test_new_extended_paragraph_without_evidence_is_plain_prose(document, kind):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway, paragraphs=lambda packet: [
        {'text': BAD_BODY, 'kind': kind, 'evidence': []},
        {'text': GOOD_BODY, 'kind': 'supported', 'evidence': [reference(packet, GOOD_BODY)]}])
    _, job = queue_section(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    assert_fallback(current['sections'][0])
    report = service.review(project['id'], current['version'])['review']
    assert not {'uncited_paragraph', 'citation_sentence_unsupported', 'model_supplement', 'pending_content'} & {
        item['code'] for item in report['issues']}
    assert report['coverage']['pendingParagraphs'] == 0
    assert report['coverage']['validlyCitedParagraphs'] == 1


def test_prior_extended_paragraph_fallback_does_not_create_pending_review_issues(document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'strict', 'extended')
    install_response(gateway)
    _, job = queue_section(document, project, prior_paragraph_fallback=True)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    assert_fallback(current['sections'][0])
    report = service.review(project['id'], current['version'])['review']
    assert not {'uncited_paragraph', 'citation_sentence_unsupported', 'model_supplement', 'pending_content'} & {
        item['code'] for item in report['issues']}
    assert report['coverage']['pendingParagraphs'] == 0
    assert report['coverage']['matchedSentences'] == 1
    assert report['coverage']['unresolvedSentences'] >= 1


def test_plain_extended_policy_keeps_real_citation_errors_and_manual_pending_text(document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway)
    _, job = queue_section(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    section = current['sections'][0]
    manual = section['content'] + '\n\n人工保留：此处仍待核实。[E99]'
    current = service.save_section(project['id'], section['id'], current['version'], {'content': manual})
    assert current['sections'][0]['content'] == manual
    report = service.review(project['id'], current['version'])['review']
    codes = {item['code'] for item in report['issues']}
    assert {'unknown_citation', 'pending_content'} <= codes
    assert any(item['code'] == 'unknown_citation' and item['evidenceId'] == 'E99' for item in report['issues'])
    assert service.get(project['id'])['sections'][0]['content'] == manual


def test_changing_plain_extended_section_to_strict_restores_evidence_requirement(document):
    store, gateway, service, _ = document
    project = project_with_section(document, 'extended', 'inherit')
    install_response(gateway, paragraphs=lambda packet: [
        {'text': BAD_BODY, 'kind': 'model_supplement', 'evidence': []}])
    _, job = queue_section(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project['id'])
    section = current['sections'][0]
    assert section['content'] == BAD_BODY and section['evidence'] == []
    current = service.save_section(project['id'], section['id'], current['version'], {'mode': 'strict'})
    report = service.review(project['id'], current['version'])['review']
    assert any(item['code'] == 'uncited_paragraph' and item['severity'] == 'error' for item in report['issues'])


@pytest.mark.parametrize('policy', [POLICY, LEGACY_POLICY, None])
@pytest.mark.parametrize('model_supplement', [False, True])
def test_extended_expand_uses_its_frozen_plain_or_legacy_contract(edit_environment, policy, model_supplement):
    store, gateway, service, project_id, _ = edit_environment
    project = write(edit_environment, '来源之外的一点思考。')
    project = service.patch(project_id, project['version'], {'mode': 'extended'})
    project = service.confirm(project_id, project['version'])
    original = project['sections'][0]['content']
    replacement = '可以保留的进一步讨论。'
    gateway.response.update(replacementText=replacement, supplement=model_supplement)
    run, job = queued_edit(edit_environment, project, 0, len(original), 'expand')
    packet = json.loads(frozen_run(store, run['runId'])['messages'][1]['content'])
    assert packet['unmatchedCitationPolicy'] == POLICY
    if policy != POLICY:
        replace_frozen_policy(store, run['runId'], False, policy)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    current = service.get(project_id)
    candidate = current['runs'][0]['candidate']['edit']
    assert current['sections'][0]['content'] == original
    assert candidate['semanticVerified'] is False
    if policy == POLICY:
        assert candidate['replacementText'] == replacement
        assert candidate['extendedContentPolicy'] == POLICY
        assert not any('待核实' in issue for issue in candidate['issues'])
    else:
        assert candidate['replacementText'].startswith(PREFIX)
        assert candidate.get('extendedContentPolicy') != POLICY
        assert any('待核实' in issue for issue in candidate['issues'])
    accepted = service.apply(project_id, run['runId'], current['version'])
    assert accepted['sections'][0]['content'] == candidate['replacementText']
    if policy == POLICY:
        assert accepted['sections'][0]['extendedContentPolicy'] == POLICY
