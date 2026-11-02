"""Abstract outlines organize writing; only generated prose registers evidence."""
import copy
import json

import pytest

from server.store import Store, dumps
from server.tests.test_writing_integrity import (
    ControlledGateway, add_source, controlled_matching_result, controlled_outline_result,
)
from server.writing import WritingConflict, WritingService
from server.writing_sentence_citations import strip_citations


ABSTRACT_POLICY = 'abstract-outline-v1'
BODY = '知识治理应保留原始证据和固定版本。'
TITLE = '问题的层次'
GOAL = '用抽象结构组织论述，具体内容在正文阶段根据原文展开。'


class AbstractOutlineGateway(ControlledGateway):
    def __init__(self):
        super().__init__()
        self.packets = []
        self.outline_sections = None
        self.unsolicited_evidence = None
        self.outline_issues = []

    def complete(self, config, messages):
        packet = json.loads(messages[1]['content'])
        self.packets.append(copy.deepcopy(packet))
        if packet['task'] == 'writing_automatic_material_queries':
            return {'text': dumps({'queries': [packet['topic'][:300]]}), 'usage': {}}
        if packet['task'] == 'writing_outline':
            sections = (copy.deepcopy(self.outline_sections) if self.outline_sections is not None else [
                {'title': title, 'goal': GOAL, 'mode': packet['mode'], 'targetWords': 100}
                for title in (TITLE, '后续观察')])
            if self.unsolicited_evidence is not None:
                for section in sections:
                    section['planningEvidence'] = copy.deepcopy(self.unsolicited_evidence)
            result = {'sections': sections, 'issues': copy.deepcopy(self.outline_issues)}
            # Genuine historical requests still receive their original protocol.
            result = controlled_outline_result(packet, result)
        elif packet['task'] == 'writing_citation_match':
            return controlled_matching_result(packet, self.generated_paragraphs)
        elif packet['task'] == 'writing_outline_issue_review':
            raise AssertionError('An abstract outline must not request an evidence note review')
        else:
            assert packet['task'] == 'writing_section'
            block = packet['blocks'][0]
            paragraphs = [{'text': BODY, 'kind': 'supported', 'evidence': [{
                'blockId': block['blockId'], 'quote': block['text'], 'claim': BODY}]}]
            self.generated_paragraphs = copy.deepcopy(paragraphs)
            result = {'paragraphs': paragraphs, 'issues': []}
        return {'text': dumps(result), 'usage': {'total_tokens': 12}}


@pytest.fixture
def abstract_writing(tmp_path):
    store = Store(tmp_path / 'abstract-outline-isolated')
    gateway = AbstractOutlineGateway()
    service = WritingService(store, gateway)
    source = add_source(store)
    # This gateway exercises per-section body generation; short single-request
    # drafting is covered by its dedicated automatic/document suites.
    project = service.create({'title': '资料治理的结构', 'brief': {'topic': '组织来源与版本问题', 'targetWords': 10000},
                              'mode': 'strict', 'sourceIds': [source], 'modelId': gateway.config['id']})
    return store, gateway, service, project


def in_mode(env, mode):
    store, gateway, service, project = env
    if project['mode'] != mode:
        project = service.patch(project['id'], project['version'], {'mode': mode})
    return store, gateway, service, project


def frozen_run(store, run_id):
    with store.connect() as db:
        row = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()
    return json.loads(row['input_json'])


def assert_abstract_request(packet):
    assert packet.get('outlinePolicy') == ABSTRACT_POLICY
    assert 'outlinePlanningProtocol' not in packet
    assert 'planningEvidence' not in dumps(packet.get('outputSchema', {}))
    assert 'planningReview' not in dumps(packet.get('outputSchema', {}))


@pytest.mark.parametrize('mode', ['strict', 'extended'])
@pytest.mark.parametrize('unsolicited', [None, [{'blockId': 'outside-selected-source', 'quote': '不在原文中的摘录。'}], 'invalid-evidence'])
def test_stepwise_abstract_outline_has_no_reference_gate_then_body_uses_exact_sources(abstract_writing, mode, unsolicited):
    store, gateway, service, project = in_mode(abstract_writing, mode)
    gateway.unsolicited_evidence = unsolicited
    queued = service.queue(project['id'], project['version'], 'outline')
    frozen = frozen_run(store, queued['runId'])
    assert frozen.get('outlinePolicy') == ABSTRACT_POLICY
    assert_abstract_request(json.loads(frozen['messages'][1]['content']))
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    current = service.get(project['id'])
    assert [packet['task'] for packet in gateway.packets] == ['writing_outline']
    assert current['runs'][0]['applied'] and not current['outlineConfirmed']
    assert current['sections'][0]['title'] == TITLE
    assert all(not section.get('planningEvidence') and not section.get('planningReview')
               for section in current['sections'])
    assert all(not section['content'] and not section['evidence'] for section in current['sections'])
    confirmed = service.confirm(current['id'], current['version'])
    queued_body = service.queue(confirmed['id'], confirmed['version'], 'section', confirmed['sections'][0]['id'])
    service.process(store.claim())
    result = service.get(project['id'])
    assert store.job(queued_body['job']['id'])['status'] == 'succeeded'
    section = result['sections'][0]
    assert strip_citations(section['content']) == BODY
    assert section['evidence'] and section['citationBindingState'] == 'unreviewed'
    assert not any(packet['task'] == 'writing_citation_match' for packet in gateway.packets)
    checked = service.queue_citation_match(result['id'], result['version'])
    service.process(store.claim())
    assert store.job(checked['job']['id'])['status'] == 'succeeded'
    assert service.get(project['id'])['sections'][0]['citationBindingState'] == 'matched'
    assert not any(packet.get('reviewPurpose') == 'outline_scope' for packet in gateway.packets)
    reference = section['evidence'][0]
    with store.connect() as db:
        block = db.execute('SELECT text FROM blocks WHERE id=? AND revision_id=?',
                           (reference['blockId'], reference['revisionId'])).fetchone()
    assert block['text'][reference['start']:reference['end']] == reference['quote']


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_automatic_abstract_outline_ignores_unsolicited_bad_quotes_and_notes_are_report_only(abstract_writing, mode):
    store, gateway, service, project = in_mode(abstract_writing, mode)
    gateway.unsolicited_evidence = [{'blockId': 'unknown-block', 'quote': '根本不在所选原文中的规划摘录。'}]
    gateway.outline_issues = ['抽象标题仅用于组织内容，正文仍须核对来源。']
    queued = service.queue_automatic(project['id'], project['version'])
    assert frozen_run(store, queued['runId']).get('outlinePolicy') == ABSTRACT_POLICY
    service.process(store.claim())
    current = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert [section['title'] for section in current['sections']] == [TITLE, '后续观察']
    assert all(strip_citations(section['content']) == BODY for section in current['sections'])
    assert all(section['evidence'] and not section.get('planningReview') for section in current['sections'])
    assert all(not section.get('planningEvidence') for section in current['sections'])
    assert not any(packet.get('reviewPurpose') == 'outline_scope' or packet['task'] == 'writing_outline_issue_review'
                   for packet in gateway.packets)
    assert sum(packet['task'] == 'writing_outline' for packet in gateway.packets) == 1
    assert sum(packet['task'] == 'writing_section' for packet in gateway.packets) == 2
    assert sum(packet['task'] == 'writing_citation_match' for packet in gateway.packets) == 2
    report = current['automatic']['report']
    assert report['outputAvailable'] and report['summary']['generatedSections'] == 2
    assert report['summary']['skippedSections'] == 0
    assert any(issue['message'] == gateway.outline_issues[0] for issue in report['issues'])
    assert all(issue['code'] not in ('outline_narrowed', 'no_supported_outline', 'chapter_plan_unsupported')
               for issue in report['issues'])
    outline_run = next(run for run in current['runs'] if run['kind'] == 'outline')
    assert frozen_run(store, outline_run['id']).get('outlinePolicy') == ABSTRACT_POLICY
    assert_abstract_request(next(packet for packet in gateway.packets if packet['task'] == 'writing_outline'))


def test_automatic_uses_existing_abstract_chapters_without_planning_evidence(abstract_writing):
    store, gateway, service, project = abstract_writing
    project = service.outline(project['id'], project['version'], [
        {'title': TITLE, 'goal': GOAL, 'mode': 'strict', 'targetWords': 100}])
    project = service.confirm(project['id'], project['version'])
    assert not project['sections'][0].get('planningEvidence')
    queued = service.queue_automatic(project['id'], project['version'])
    service.process(store.claim())
    current = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['sections'][0]['title'] == TITLE
    assert strip_citations(current['sections'][0]['content']) == BODY
    assert not any(packet['task'] == 'writing_outline' or packet.get('reviewPurpose') == 'outline_scope'
                   for packet in gateway.packets)
    assert current['automatic']['report']['summary']['generatedSections'] == 1


def test_empty_automatic_outline_finishes_with_a_durable_report(abstract_writing):
    store, gateway, service, project = abstract_writing
    gateway.outline_sections = []
    gateway.outline_issues = ['本次没有返回可用章节。']
    queued = service.queue_automatic(project['id'], project['version'])
    service.process(store.claim())
    current = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['automatic']['status'] == 'completed_with_issues'
    report = current['automatic']['report']
    assert current['sections'] == [] and not report['outputAvailable']
    assert report['issues'] and report['review'] is not None
    assert all(packet['task'] in ('writing_outline', 'writing_automatic_material_queries') for packet in gateway.packets)
    assert WritingService(store, gateway).get(project['id'])['automatic']['report'] == report
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone()


def test_empty_outline_candidate_cannot_erase_existing_outline_even_when_explicitly_applied(abstract_writing):
    store, gateway, service, project = abstract_writing
    project = service.outline(project['id'], project['version'], [
        {'title': '已有空白章节', 'goal': '保留现有结构等待正文生成。', 'mode': 'strict', 'targetWords': 100}])
    before = copy.deepcopy(project)
    gateway.outline_sections = []
    gateway.outline_issues = ['现有资料未形成新的可用纲要。']
    queued = service.queue(project['id'], project['version'], 'outline')
    service.process(store.claim())
    current = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    candidate_run = next(run for run in current['runs'] if run['id'] == queued['runId'])
    assert candidate_run['candidate']['sections'] == [] and not candidate_run['applied']
    assert current['sections'] == before['sections'] and current['version'] == before['version']
    assert current['outlineConfirmed'] == before['outlineConfirmed']
    with pytest.raises(WritingConflict, match='本次没有可用纲要'):
        service.apply(project['id'], queued['runId'], current['version'])
    after = service.get(project['id'])
    assert after['sections'] == before['sections'] and after['version'] == before['version']
    assert after['outlineConfirmed'] == before['outlineConfirmed']


@pytest.mark.parametrize('problem', ['wrong_mode', 'empty_title'])
def test_abstract_outline_still_validates_ordinary_structure(abstract_writing, problem):
    store, gateway, service, project = abstract_writing
    gateway.outline_sections = [{'title': TITLE, 'goal': GOAL, 'mode': 'strict', 'targetWords': 100}]
    if problem == 'wrong_mode':
        gateway.outline_sections[0]['mode'] = 'extended'
    else:
        gateway.outline_sections[0]['title'] = ' '
    queued = service.queue(project['id'], project['version'], 'outline')
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'failed'
    assert service.get(project['id'])['sections'] == []
    assert [packet['task'] for packet in gateway.packets] == ['writing_outline']


def test_fresh_body_ignores_obsolete_planning_evidence_and_registers_current_originals(abstract_writing):
    store, gateway, service, project = abstract_writing
    project = service.outline(project['id'], project['version'], [
        {'title': TITLE, 'goal': GOAL, 'mode': 'strict', 'targetWords': 100}])
    project = service.confirm(project['id'], project['version'])
    with store.connect() as db:
        historical = service._load(db, project['id'])
        section = historical['sections'][0]
        section['planningEvidence'] = [{'blockId': 'historical-missing-block', 'quote': '旧的规划摘录'}]
        section['planningReview'] = {'state': 'supported', 'content': TITLE + '\n' + GOAL}
        service._save(db, historical, 'historical_planning_fixture')
    project = service.get(project['id'])
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = frozen_run(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert 'plannedContent' not in packet
    assert 'historical-missing-block' not in dumps(packet)
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    result = service.get(project['id'])['sections'][0]
    assert strip_citations(result['content']) == BODY
    assert result['evidence'] and result['citationBindingState'] == 'unreviewed'
    assert all(reference['blockId'] != 'historical-missing-block' for reference in result['evidence'])


def test_explicit_historical_message_path_keeps_evidence_planning_schema(abstract_writing):
    store, gateway, service, project = abstract_writing
    with store.connect() as db:
        current_messages, _ = service._messages(project, 'outline', None, gateway.config, db)
        historical_messages, _ = service._messages(project, 'outline', None, gateway.config, db, outline_policy=None)
    current = json.loads(current_messages[1]['content'])
    historical = json.loads(historical_messages[1]['content'])
    assert_abstract_request(current)
    assert historical.get('outlinePlanningProtocol') == 'evidence-plan-v2'
    assert 'planningEvidence' in dumps(historical['outputSchema'])
    assert historical.get('outlinePolicy') != ABSTRACT_POLICY


def test_old_automatic_parent_does_not_silently_adopt_abstract_outline_policy(abstract_writing):
    store, gateway, service, project = abstract_writing
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        frozen = frozen_run(store, queued['runId'])
        frozen.pop('outlinePolicy', None)
        frozen.pop('citationReviewPolicy', None)
        frozen.pop('automaticMaterialPolicy', None)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    WritingService(store, gateway).process(store.claim())
    current = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    outline_request = next(packet for packet in gateway.packets if packet['task'] == 'writing_outline')
    assert outline_request.get('outlinePlanningProtocol') == 'evidence-plan-v2'
    assert outline_request.get('outlinePolicy') != ABSTRACT_POLICY
    assert 'planningEvidence' in dumps(outline_request['outputSchema'])
    assert sum(packet.get('reviewPurpose') == 'outline_scope' for packet in gateway.packets) == 2
    assert all(section.get('planningEvidence') and section.get('planningReview', {}).get('state') == 'supported'
               for section in current['sections'])
    assert all(strip_citations(section['content']) == BODY for section in current['sections'])
    outline_run = next(run for run in current['runs'] if run['kind'] == 'outline')
    assert frozen_run(store, outline_run['id']).get('outlinePolicy') != ABSTRACT_POLICY
