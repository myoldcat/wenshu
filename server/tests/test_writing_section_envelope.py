"""Section envelopes preserve prose and evidence under a frozen opt-in policy."""
import copy
import json

import pytest

from server.store import Store, dumps
from server.tests.test_writing_automatic import automatic, run_automatic
from server.tests.test_writing_composition_integration import _parent_context, _queue_parent
from server.tests.test_writing_document import document, ready
from server.writing import WritingService
from server.writing_output import OUTPUT_POLICY, SECTION_ENVELOPE_POLICY, parse_output
from server.writing_sentence_citations import strip_citations


BODY = '固定版本保留原始段落，引用对应原文。'
ISSUE = '具体日期仍需确认。'


def request(policy=SECTION_ENVELOPE_POLICY):
    packet = {'generationOutputPolicy': OUTPUT_POLICY}
    if policy is not None:
        packet['sectionOutputEnvelopePolicy'] = policy
    return {'messages': [{'role': 'system', 'content': '写作'},
                         {'role': 'user', 'content': dumps(packet)}]}


def result():
    return {'paragraphs': [{'text': BODY, 'kind': 'supported', 'evidence': [
        {'blockId': 'C05-B001', 'quote': BODY, 'claim': BODY}]}], 'issues': [ISSUE]}


def envelope(content=None, **outer):
    return {'type': 'json_object', 'content': result() if content is None else content,
            'contentStatus': 'usable', **outer}


def parsed(value, policy=SECTION_ENVELOPE_POLICY, kind='section', **metadata):
    return parse_output(request(policy), {'text': dumps(value), **metadata}, kind)


def frozen(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


def test_exact_envelope_keeps_paragraph_evidence_together_and_issues_outside_body():
    value = envelope()
    before = copy.deepcopy(value)
    output, notices = parsed(value)
    assert output['paragraphs'] == before['content']['paragraphs']
    assert output['issues'] == []
    assert ISSUE in notices
    assert output['contentStatus'] == 'usable'
    assert value == before


@pytest.mark.parametrize('policy', [None, 'section-json-object-content-v2', True])
def test_old_or_unknown_frozen_policy_keeps_original_failure(policy):
    with pytest.raises(ValueError, match='未返回可用正文'):
        parsed(envelope(), policy)


@pytest.mark.parametrize('kind', ['outline', 'edit'])
def test_new_section_policy_does_not_unwrap_other_operations(kind):
    value = envelope()
    output, notices = parsed(value, kind=kind)
    assert output == value and not notices


@pytest.mark.parametrize('level', ['outer', 'inner'])
@pytest.mark.parametrize('status,message', [('interrupted', '异常中断'), ('unrelated', '完全无关')])
def test_neither_envelope_status_can_hide_a_severe_result(level, status, message):
    value = envelope()
    target = value if level == 'outer' else value['content']
    target['contentStatus'] = status
    with pytest.raises(ValueError, match=message):
        parsed(value)


def test_transport_interruption_cannot_be_hidden_by_usable_envelope():
    with pytest.raises(ValueError, match='异常中断'):
        parsed(envelope(), finish_reason='length')


def test_outer_and_inner_issues_survive_without_entering_prose():
    output, notices = parsed(envelope(issues=['外层提示。']))
    assert ISSUE in notices and '外层提示。' in notices
    assert output['paragraphs'][0]['text'] == BODY


@pytest.mark.parametrize('value', [
    {'content': result()},
    {'type': 'other', 'content': result()},
    {'type': 'json_object', 'content': {'text': BODY}},
    {'type': 'json_object', 'content': {'paragraphs': []}},
    {'type': 'json_object', 'content': result(), 'sections': []},
    {'type': 'json_object', 'content': {'type': 'json_object', 'content': result()}},
])
def test_unrecognized_ambiguous_or_empty_envelope_is_not_treated_as_prose(value):
    with pytest.raises(ValueError, match='未返回可用正文'):
        parsed(value)


def test_explicit_top_level_paragraphs_are_never_replaced_by_nested_content():
    value = envelope(paragraphs=[{'text': '顶层正文。', 'evidence': []}])
    output, notices = parsed(value)
    assert output['paragraphs'] == value['paragraphs'] and not notices


def test_explicit_top_level_text_keeps_existing_plain_body_contract():
    value = envelope(text='顶层正文。')
    output, notices = parsed(value)
    assert output['paragraphs'] == [{'text': '顶层正文。', 'evidence': []}]
    assert notices


def test_outer_section_identity_is_preserved_and_conflicts_are_rejected():
    output, _ = parsed(envelope(sectionId='current-section'))
    assert output['sectionId'] == 'current-section'
    value = envelope({**result(), 'sectionId': 'other-section'}, sectionId='current-section')
    with pytest.raises(ValueError, match='未返回可用正文'):
        parsed(value)


def test_unwrapped_evidence_still_goes_through_fixed_source_registration(document):
    store, gateway, service, _ = document
    project = ready(document)
    gateway.transform = lambda value, packet: envelope({**value, 'issues': [ISSUE]})
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    saved = frozen(store, queued['runId'])
    assert saved['sectionOutputEnvelopePolicy'] == SECTION_ENVELOPE_POLICY
    assert json.loads(saved['messages'][1]['content'])['sectionOutputEnvelopePolicy'] == SECTION_ENVELOPE_POLICY
    restarted = WritingService(Store(store.root), gateway)
    restarted.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    current = restarted.get(project['id'])
    section = current['sections'][0]
    expected = gateway.paragraphs(gateway.calls[0]['payload'])
    assert strip_citations(section['content']) == '\n\n'.join(p['text'] for p in expected)
    assert section['evidence'] and section['citationBindingState'] == 'unreviewed'
    for item in section['evidence']:
        with store.connect() as db:
            block = db.execute('SELECT text,revision_id FROM blocks WHERE id=?', (item['blockId'],)).fetchone()
        assert block['revision_id'] == item['revisionId']
        assert block['text'][item['start']:item['end']] == item['quote']
    assert ISSUE in section['generationNotices'] and ISSUE not in section['content']
    assert len(gateway.calls) == 1 and not gateway.matching_calls


def test_invalid_nested_reference_does_not_bypass_source_checks(document):
    store, gateway, service, _ = document
    project = ready(document)

    def invalid(value, packet):
        value['paragraphs'][0]['evidence'][0]['quote'] = '不在原文中的捏造摘录。'
        return envelope(value)

    gateway.transform = invalid
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    current = service.get(project['id'])
    assert not current['sections'][0]['content']
    section = current['runs'][0]['candidate']['section']
    assert section['content'] and section['citationBindingState'] == 'needs_review'
    assert all(item['quote'] != '不在原文中的捏造摘录。' for item in section['evidence'])


@pytest.mark.parametrize('legacy', [False, True])
def test_automatic_parent_freezes_envelope_policy_and_legacy_children_do_not_upgrade(document, legacy):
    store, gateway, service, _ = document
    project = ready(document)
    parent = _queue_parent(document, project)
    saved = frozen(store, parent['runId'])
    assert saved['sectionOutputEnvelopePolicy'] == SECTION_ENVELOPE_POLICY
    if legacy:
        saved.pop('sectionOutputEnvelopePolicy')
        with store.connect() as db:
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(saved), parent['runId']))
    restarted = WritingService(Store(store.root), gateway)
    _parent_context(restarted, gateway, parent, store.claim())
    child = restarted.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    child_frozen = frozen(store, child['runId'])
    packet = json.loads(child_frozen['messages'][1]['content'])
    assert ('sectionOutputEnvelopePolicy' in packet) is not legacy
    assert ('sectionOutputEnvelopePolicy' in child_frozen) is not legacy
    with store.connect() as db:
        _, prepared = restarted._document_plan(db, project, gateway.resolve())
    assert prepared
    assert all(('sectionOutputEnvelopePolicy' in json.loads(item['messages'][1]['content'])) is not legacy
               for item in prepared)
    assert frozen(store, parent['runId']) == saved


def test_automatic_wrapped_chapters_finish_in_one_attempt_with_original_evidence(automatic):
    store, gateway, service, _ = automatic
    complete = gateway.complete

    def wrapped(config, messages):
        answer = complete(config, messages)
        if json.loads(messages[-1]['content'])['task'] == 'writing_section':
            answer['text'] = dumps(envelope(json.loads(answer['text'])))
        return answer

    gateway.complete = wrapped
    queued, current = run_automatic(automatic)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['automatic']['report']['summary']['generatedSections'] == 2
    assert all(section['content'] and section['evidence'] for section in current['sections'])
    with store.connect() as db:
        rows = db.execute("SELECT attempt,status FROM writing_automatic_retries WHERE owner_run_id=? AND logical_key LIKE 'generation:%'",
                          (queued['runId'],)).fetchall()
    assert len(rows) == 3 and all(row['attempt'] == 1 and row['status'] == 'succeeded' for row in rows)


def test_whole_draft_sequential_chapters_use_their_persisted_envelope_policy(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 5000
    project = ready(document, words=(800, 800, 800))
    gateway.transform = lambda value, packet: envelope(value)
    queued = service.queue_document(project['id'], project['version'])
    saved = frozen(store, queued['runId'])
    assert saved['sectionOutputEnvelopePolicy'] == SECTION_ENVELOPE_POLICY
    assert saved['plan']['strategy'] == 'sequential'
    WritingService(Store(store.root), gateway).process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert all(section['content'] and section['evidence'] for section in service.get(project['id'])['sections'])
    assert len(gateway.calls) == 3 and not gateway.matching_calls


def test_whole_draft_single_request_accepts_wrapped_chapters_without_losing_identity(document):
    store, gateway, service, _ = document
    project = ready(document)

    def wrap_chapters(value, packet):
        assert packet['task'] == 'writing_document'
        return {'sections': [envelope({'paragraphs': chapter['paragraphs'], 'issues': [ISSUE]},
                                      sectionId=chapter['sectionId']) for chapter in value['sections']], 'issues': []}

    gateway.transform = wrap_chapters
    queued = service.queue_document(project['id'], project['version'])
    assert frozen(store, queued['runId'])['plan']['strategy'] == 'single'
    WritingService(Store(store.root), gateway).process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    for section in service.get(project['id'])['sections']:
        assert section['content'] and section['evidence']
        assert ISSUE in section['generationNotices']
    assert len(gateway.calls) == 1 and not gateway.matching_calls
