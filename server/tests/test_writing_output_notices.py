"""Usable generation is retained with notices outside the reader-facing body.

Only the controlled document gateway and pytest's isolated stores are used.
The preserved-output cases freeze the pre-six-step inline-review contract;
new default drafting and final checks are covered by the six-step suites.
"""
import copy
import json

import pytest

from server.model_gateway import ModelError
from server.store import Store, dumps
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.tests.test_writing_mode_routing import project_with_section
from server.tests.test_writing_integrity import freeze_legacy_inline_review, use_legacy_inline_fixture
from server.writing import WritingConflict, WritingService
from server.writing_sentence_citations import valid_bindings


POLICY = 'preserve-usable-v1'
BODY = '固定版本保存原始段落，修订记录便于回查。'


@pytest.fixture(autouse=True)
def historical_inline_protocol(document, monkeypatch):
    use_legacy_inline_fixture(document, monkeypatch)


def section_run(document, *, mode='extended'):
    store, gateway, service, _ = document
    project = project_with_section(document, mode, 'inherit')
    section_id = project['sections'][0]['id']
    run = service.queue(project['id'], project['version'], 'section', section_id)
    return project, run, store.claim()


def finish_section(document, project, run, job):
    store, _, service, _ = document
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded', store.job(run['job']['id'])
    return service.get(project['id'])


def assert_notices(section):
    notices = section.get('generationNotices')
    assert isinstance(notices, list) and notices
    assert all(isinstance(item, str) and item.strip() for item in notices)
    assert '待核实' not in section['content'] and '模型补充' not in section['content']
    return notices


def plain_result(text=BODY):
    return {'paragraphs': [{'text': text, 'kind': 'supported', 'evidence': []}], 'issues': []}


def test_plain_string_paragraph_remains_a_completed_strict_candidate(document):
    store, gateway, service, _ = document
    gateway.transform = lambda result, payload: {'paragraphs': [BODY], 'issues': []}
    project, run, job = section_run(document, mode='strict')
    current = finish_section(document, project, run, job)
    assert not current['runs'][0]['applied'] and current['sections'][0]['content'] == ''
    candidate = current['runs'][0]['candidate']['section']
    assert candidate['content'] == BODY and candidate['evidence'] == []
    assert candidate['citationBindingState'] == 'needs_review'
    assert_notices(candidate)
    assert len(gateway.calls) == 1


def test_invalid_extended_reference_keeps_prose_without_registering_bad_evidence(document):
    _, gateway, _, _ = document

    def invalid_reference(result, payload):
        result = plain_result()
        result['paragraphs'][0]['evidence'] = [{'blockId': payload['blocks'][0]['blockId'],
            'quote': '这一摘录没有出现在任何所选原文中。', 'claim': BODY}]
        return result

    gateway.transform = invalid_reference
    project, run, job = section_run(document)
    current = finish_section(document, project, run, job)
    section = current['sections'][0]
    assert current['runs'][0]['applied']
    assert section['content'] == BODY and section['evidence'] == []
    assert_notices(section)
    packet = gateway.calls[0]['payload']
    assert packet['generationOutputPolicy'] == POLICY
    assert len(gateway.calls) == 1


def test_strict_missing_evidence_survives_restart_and_explicit_adoption(document):
    store, gateway, _, _ = document
    gateway.transform = lambda result, payload: plain_result()
    project, run, job = section_run(document, mode='strict')
    current = finish_section(document, project, run, job)
    candidate = copy.deepcopy(current['runs'][0]['candidate']['section'])
    notices = assert_notices(candidate)
    assert candidate['content'] == BODY and candidate['evidence'] == []
    assert candidate['citationBindingState'] == 'needs_review'
    assert all(item['status'] == 'unsupported' for item in candidate['citationBindings']['items'])
    assert current['sections'][0]['content'] == '' and not current['runs'][0]['applied']
    restarted = WritingService(Store(store.root), gateway)
    reloaded = restarted.get(project['id'])
    assert reloaded['runs'][0]['candidate']['section'] == candidate
    adopted = restarted.apply(project['id'], run['runId'], reloaded['version'])
    assert adopted['sections'][0]['content'] == BODY
    assert adopted['sections'][0]['generationNotices'] == notices
    assert adopted['sections'][0]['citationBindingState'] == 'needs_review'
    assert len(gateway.calls) == 1 and not gateway.matching_calls


def test_issues_are_persisted_outside_body_without_rewriting_body(document):
    store, gateway, _, _ = document
    body = '  固定版本保存原始段落。\n修订记录便于回查。  '
    warning = '本次目标字数较少，可按需要增加说明。'
    gateway.transform = lambda result, payload: {**plain_result(body), 'issues': [warning]}
    project, run, job = section_run(document)
    current = finish_section(document, project, run, job)
    section = current['sections'][0]
    assert section['content'] == body and warning not in section['content']
    assert warning in assert_notices(section)
    reloaded = WritingService(Store(store.root), gateway).get(project['id'])
    assert reloaded['sections'][0]['content'] == body
    assert reloaded['sections'][0]['generationNotices'] == section['generationNotices']


def test_matching_validation_error_becomes_conservative_notice_without_regeneration(document):
    _, gateway, _, _ = document
    complete = gateway.complete

    def malformed_match(config, messages):
        packet = json.loads(messages[1]['content'])
        if packet['task'] == 'writing_citation_match':
            gateway.matching_calls.append({'payload': copy.deepcopy(packet)})
            return {'text': '{not valid matching JSON'}
        return complete(config, messages)

    gateway.complete = malformed_match
    project, run, job = section_run(document, mode='strict')
    freeze_legacy_inline_review(document[0], run['runId'])
    current = finish_section(document, project, run, job)
    candidate = current['runs'][0]['candidate']['section']
    generated = gateway.generated_sections[project['sections'][0]['id']]
    assert gateway.calls[0]['payload']['citationRetentionPolicy'] == 'preserve-generated-citations-v1'
    assert candidate['content'] == '\n\n'.join(paragraph['text'] + '[E1]' for paragraph in generated)
    assert len(candidate['evidence']) == 1
    assert candidate['evidence'][0]['quote'] == generated[0]['evidence'][0]['quote']
    assert candidate['evidence'][0]['sourceId'] == project['sourceIds'][0]
    assert candidate['citationBindingState'] == 'needs_review'
    assert all(item['status'] == 'unreviewed' and item['supportBasis'] == 'none'
               for item in candidate['citationBindings']['items'])
    assert valid_bindings(candidate['content'], candidate['evidence'], candidate['citationBindings'], mode='strict')
    assert_notices(candidate)
    assert len(gateway.calls) == 1 and len(gateway.matching_calls) == 1
    assert not current['runs'][0]['applied']


def test_matching_network_error_still_fails_and_does_not_publish(document):
    store, gateway, service, _ = document
    complete = gateway.complete

    def unavailable_match(config, messages):
        packet = json.loads(messages[1]['content'])
        if packet['task'] == 'writing_citation_match':
            raise ModelError('测试引用核对网络连接中断')
        return complete(config, messages)

    gateway.complete = unavailable_match
    project, run, job = section_run(document)
    freeze_legacy_inline_review(store, run['runId'])
    # Extended fixture normally has no citations and requires no matching call.
    gateway.transform = lambda result, payload: {'paragraphs': gateway.paragraphs({**payload, 'mode': 'strict'}), 'issues': []}
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'failed'
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == '' and not current['runs'][0]['applied']
    assert len(gateway.calls) == 1


def test_truncated_response_still_fails_even_if_returned_json_is_usable(document):
    store, gateway, service, _ = document
    complete = gateway.complete

    def truncated(config, messages):
        response = complete(config, messages)
        if json.loads(messages[1]['content'])['task'] != 'writing_citation_match':
            response['finishReason'] = 'length'
        return response

    gateway.complete = truncated
    project, run, job = section_run(document)
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'failed'
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == '' and current['runs'][0]['candidate'] is None


@pytest.mark.parametrize('status', ['unrelated', 'interrupted'])
def test_explicit_severe_content_status_still_fails(document, status):
    store, gateway, service, _ = document
    gateway.transform = lambda result, payload: {**plain_result(), 'contentStatus': status}
    project, run, job = section_run(document)
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'failed'
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == '' and current['runs'][0]['candidate'] is None


def test_old_frozen_packet_keeps_its_missing_evidence_error(document):
    store, gateway, service, _ = document
    gateway.transform = lambda result, payload: plain_result()
    project, run, job = section_run(document, mode='strict')
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        frozen.pop('generationOutputPolicy', None)
        packet = json.loads(frozen['messages'][1]['content'])
        packet.pop('generationOutputPolicy')
        frozen['messages'][1]['content'] = dumps(packet)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'failed'
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == '' and current['runs'][0]['candidate'] is None


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_whole_draft_minor_issues_do_not_stop_any_chapter(document, strategy):
    store, gateway, service, _ = document
    if strategy == 'sequential':
        gateway.config['maxOutputTokens'] = 5000
    words = (200, 200, 200) if strategy == 'single' else (800, 800, 800)
    project = ready(document, words=words, modes=('extended',) * 3)
    notice = '章节存在可继续润色的表达。'

    def minor_output(result, payload):
        if payload['task'] == 'writing_document':
            for index, chapter in enumerate(result['sections']):
                chapter.update(plain_result(BODY + str(index)))
                chapter['issues'] = [notice]
        else:
            result = {**plain_result(), 'issues': [notice]}
        return result

    gateway.transform = minor_output
    run, job = queue_and_claim(document, project)
    WritingService(Store(store.root), gateway).process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    current = service.get(project['id'])
    assert current['runs'][0]['applied']
    assert all(section['content'].startswith(BODY) and notice in assert_notices(section)
               for section in current['sections'])
    assert len(gateway.calls) == (1 if strategy == 'single' else 3)
    for call in gateway.calls:
        packet = call['payload']
        if strategy == 'single':
            assert all(section['generationOutputPolicy'] == POLICY for section in packet['sections'])
        else:
            assert packet['generationOutputPolicy'] == POLICY


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_strict_minor_chapter_does_not_block_other_whole_draft_chapters(document, strategy):
    store, gateway, service, _ = document
    # Select the intended strategy explicitly through its real input/output
    # budgets instead of depending on a particular builtin prompt length.
    gateway.config['contextChars'] = 48000
    if strategy == 'sequential':
        gateway.config['maxOutputTokens'] = 5000
    words = (200, 200, 200) if strategy == 'single' else (800, 800, 800)
    project = ready(document, words=words, modes=('strict', 'strict', 'extended'))

    def first_missing_evidence(result, payload):
        if payload['task'] == 'writing_document':
            result['sections'][0].update(plain_result())
        elif payload['section']['id'] == project['sections'][0]['id']:
            result.update(plain_result())
        return result

    gateway.transform = first_missing_evidence
    run, job = queue_and_claim(document, project)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
    assert frozen['plan']['strategy'] == strategy
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    current = service.get(project['id'])
    assert not current['sections'][0]['content']
    assert current['sections'][1]['content'] and current['sections'][2]['content']
    steps = current['runs'][0]['candidate']['document']['steps']
    assert [step['status'] for step in steps] == ['candidate', 'applied', 'applied']
    assert steps[0]['canApply'] and assert_notices(steps[0]['section'])
    restarted = WritingService(Store(store.root), gateway)
    adopted = restarted.apply_document_section(project['id'], run['runId'], project['sections'][0]['id'], current['version'])
    assert adopted['sections'][0]['content'] == BODY
    assert adopted['sections'][0]['generationNotices'] == steps[0]['section']['generationNotices']
    assert adopted['sections'][0]['citationBindingState'] == 'needs_review'
    assert len(gateway.calls) == (1 if strategy == 'single' else 3)


def test_notice_candidate_cannot_bypass_manual_lock_or_version_protection(document):
    store, gateway, service, _ = document
    gateway.transform = lambda result, payload: {**plain_result(), 'issues': ['此处仍可润色。']}
    project, run, job = section_run(document)
    section_id = project['sections'][0]['id']
    manual = '生成期间人工补写的版本必须保存。'
    gateway.callback = lambda count, payload: service.save_section(project['id'], section_id,
        service.get(project['id'])['version'], {'content': manual})
    current = finish_section(document, project, run, job)
    assert current['sections'][0]['content'] == manual and not current['runs'][0]['applied']
    candidate = current['runs'][0]['candidate']['section']
    assert_notices(candidate)
    with pytest.raises(WritingConflict):
        service.apply(project['id'], run['runId'], project['version'], replace_manual=True)
    current = service.save_section(project['id'], section_id, current['version'], {'locked': True})
    with pytest.raises(WritingConflict):
        service.apply(project['id'], run['runId'], current['version'], replace_manual=True)
    current = service.save_section(project['id'], section_id, current['version'], {'locked': False})
    with pytest.raises(WritingConflict):
        service.apply(project['id'], run['runId'], current['version'])
    assert service.get(project['id'])['sections'][0]['content'] == manual
    assert len(gateway.calls) == 1
