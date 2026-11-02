"""Frozen source dates and coverage survive both whole-draft strategies."""
import copy
import json

import pytest

from server.store import Store, dumps
from server.tests.test_writing_citation_contract import add_source
from server.tests.test_writing_integrity import controlled_matching_result
from server.writing import WritingService


FIELDS = ('originalInputPolicy', 'sourceContext', 'temporalAndAttributionRequirements', 'inputCoverage')


class MaterialDocumentGateway:
    def __init__(self):
        self.config = {'id': 'material-document-model', 'version': 1, 'model': 'controlled',
                       'name': '材料日期隔离测试', 'contextChars': 24000, 'maxOutputTokens': 8000}
        self.calls, self.generated_sections = [], {}

    def get_preferences(self):
        return {'defaultModelId': self.config['id']}

    def resolve(self, model_id=None, version=None):
        return {**self.config, 'version': version or self.config['version']}

    def complete(self, config, messages):
        assert sum(len(message['content']) for message in messages) <= config['contextChars']
        packet = json.loads(messages[1]['content'])
        if packet['task'] == 'writing_citation_match':
            return controlled_matching_result(packet, self.generated_sections[packet['sectionId']])
        self.calls.append({'payload': copy.deepcopy(packet)})
        chapters = packet['sections'] if packet['task'] == 'writing_document' else [packet]
        outputs = []
        for chapter in chapters:
            section_id = chapter['id'] if packet['task'] == 'writing_document' else chapter['section']['id']
            block = chapter['blocks'][0]
            text = '本段忠实归纳所选原文，并保留原始范围和明确日期。'
            paragraphs = [{'text': text, 'kind': 'supported', 'evidence': [
                {'blockId': block['blockId'], 'quote': block['text'], 'claim': text}]}]
            self.generated_sections[section_id] = copy.deepcopy(paragraphs)
            outputs.append({'sectionId': section_id, 'paragraphs': paragraphs})
        result = {'sections': outputs, 'issues': []} if packet['task'] == 'writing_document' else {
            'paragraphs': outputs[0]['paragraphs'], 'issues': []}
        return {'text': dumps(result)}


@pytest.fixture
def document(tmp_path):
    store, gateway = Store(tmp_path / 'material-document'), MaterialDocumentGateway()
    service = WritingService(store, gateway)
    source, _ = add_source(store)
    return store, gateway, service, source


def ready(document, *, words):
    _, gateway, service, source = document
    project = service.create({'title': '冻结日期与资料覆盖', 'mode': 'strict', 'sourceIds': [source],
        'brief': {'targetWords': sum(words)},
        'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': f'章节{i}', 'goal': '忠实归纳原文明示内容', 'mode': 'strict', 'targetWords': target}
        for i, target in enumerate(words, 1)])
    return service.confirm(project['id'], project['version'])


def queue_and_claim(document, project):
    store, _, service, _ = document
    run = service.queue_document(project['id'], project['version'])
    job = store.claim()
    assert job['id'] == run['job']['id']
    return run, job


def steps_for(store, run_id):
    with store.connect() as db:
        envelope = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])
        steps = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run_id,))]
    return envelope, steps


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_real_generation_request_keeps_frozen_dates_scope_and_coverage_in_both_strategies(document, strategy):
    store, gateway, service, source = document
    gateway.config['maxOutputTokens'] = 8000 if strategy == 'single' else 1800
    date = '2026-09-17T00:00:00+08:00'
    with store.connect() as db:
        db.execute('UPDATE revisions SET published_at=? WHERE source_id=?', (date, source))
    project = ready(document, words=(200, 200))
    run, job = queue_and_claim(document, project)
    envelope, steps = steps_for(store, run['runId'])
    assert envelope['plan']['strategy'] == strategy
    frozen_packets = [json.loads(item['messages'][1]['content']) for item in steps]
    with store.connect() as db:
        db.execute('UPDATE revisions SET published_at=? WHERE source_id=?', ('2099-01-01', source))
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    sent = (gateway.calls[0]['payload']['sections'] if strategy == 'single'
            else [call['payload'] for call in gateway.calls])
    assert len(sent) == len(frozen_packets) == 2
    for actual, frozen in zip(sent, frozen_packets):
        for key in FIELDS:
            assert actual[key] == frozen[key]
        assert actual['sourceContext'][0]['publishedAt'] == date
        assert actual['inputCoverage']['complete'] is True
        assert actual['inputCoverage']['includedSources'] == 1
        assert actual['inputCoverage']['truncatedBlocks'] == 0
        assert '调查中' in actual['temporalAndAttributionRequirements']
    if strategy == 'single':
        messages = service._document_messages(envelope, steps)
        assert sum(len(item['content']) for item in messages) == envelope['plan']['estimatedInputChars']
        assert envelope['plan']['estimatedInputChars'] <= gateway.config['contextChars']
    else:
        assert all(sum(len(message['content']) for message in item['messages']) <= gateway.config['contextChars']
                   for item in steps)


def test_combined_coverage_metadata_counts_toward_budget_and_selects_sequential_when_needed(document):
    store, gateway, service, _ = document
    source, _ = add_source(store, paragraphs=[
        '知识治理应保存固定版本和原始段落，逐段回查依据、数据口径与适用范围。' * 10 + f'第{i}段。'
        for i in range(50)], url='https://example.test/document-material-budget')
    gateway.config.update(contextChars=18000, maxOutputTokens=12000)
    project = ready(document, words=(200, 200))
    project = service.patch(project['id'], project['version'], {'sourceIds': [source]})
    with store.connect() as db:
        plan, steps = service._document_plan(db, project, gateway.config)
        combined = service._document_messages(project, steps, db=db)
    assert plan['canGenerate'], plan['blockingReasons']
    assert plan['strategy'] == 'sequential'
    assert plan['estimatedInputChars'] == sum(len(message['content']) for message in combined)
    assert plan['estimatedInputChars'] > gateway.config['contextChars']
    assert all(sum(len(message['content']) for message in step['messages']) <= gateway.config['contextChars']
               for step in steps)
    for chapter in json.loads(combined[1]['content'])['sections']:
        assert chapter['inputCoverage']['includedBlocks'] < chapter['inputCoverage']['availableBlocks']
        assert chapter['inputCoverage']['complete'] is False


def test_reassembly_never_backfills_new_metadata_into_old_frozen_chapters(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200, 200))
    with store.connect() as db:
        _, steps = service._document_plan(db, project, gateway.config)
    historical = copy.deepcopy(steps)
    for item in historical:
        packet = json.loads(item['messages'][1]['content'])
        for key in FIELDS:
            packet.pop(key)
        item['messages'][1]['content'] = dumps(packet)
    before = copy.deepcopy(historical)
    reconstructed = json.loads(service._document_messages(project, historical)[1]['content'])
    assert all(not any(key in chapter for key in FIELDS) for chapter in reconstructed['sections'])
    assert historical == before


def test_mixed_history_copies_metadata_only_for_chapters_that_froze_it(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200, 200))
    with store.connect() as db:
        _, steps = service._document_plan(db, project, gateway.config)
    first = json.loads(steps[0]['messages'][1]['content'])
    for key in FIELDS:
        first.pop(key)
    steps[0]['messages'][1]['content'] = dumps(first)
    sections = json.loads(service._document_messages(project, steps)[1]['content'])['sections']
    assert not any(key in sections[0] for key in FIELDS)
    assert all(key in sections[1] for key in FIELDS)
