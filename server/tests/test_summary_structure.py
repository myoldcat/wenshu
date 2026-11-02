"""Synthetic summary results exercise structural adoption and safety fences.

These tests intentionally accept foreign prose and varied keyword spellings.
Passing this gate establishes shape and references, not translation quality.
No real document library or model provider is used.
"""
from copy import deepcopy
import json

import pytest

from server.enrichment import EnrichmentService, _json
from server.prompts import CHINESE_OUTPUT_CONTRACT, PromptLibrary, prompt_text
from server.store import Store
from server.tests.test_enrichment import Gateway, execute, publish


ALLOWED = {'p1', 'p2', 'p3', 'p4'}


def valid_result():
    return {'summary': '资料保留原文与来源。',
            'keyPoints': [{'text': '原始证据可查。', 'blockIds': ['p1']}],
            'keywords': ['原始证据']}


@pytest.fixture
def workbench(tmp_path):
    store = Store(tmp_path / 'isolated-summary-structure')
    models = Gateway()
    return store, models, EnrichmentService(store, models)


@pytest.mark.parametrize('field,replacement', [
    ('summary', 'The source retains its original evidence.'),
    ('keyPoints', [{'text': 'The original evidence remains available.', 'blockIds': ['p1']}]),
    ('keywords', ['openai']),
    ('keywords', ['data security']),
    ('keywords', ['AI/ML']),
    ('keywords', ['lowercase']),
    ('keywords', ['camelCase']),
    ('keywords', ['openai', 'data security', 'AI/ML']),
])
def test_foreign_summary_fields_and_keyword_spellings_do_not_trigger_repair(workbench, field, replacement):
    store, models, service = workbench
    source = publish(store)

    def rewrite(result):
        value = deepcopy(result)
        value[field] = deepcopy(replacement)
        if field == 'keyPoints':
            value[field][0]['blockIds'] = result['keyPoints'][0]['blockIds']
        return value

    models.rewrite = rewrite
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    stage = service.detail(source)['summary']
    assert len(models.calls) == 1
    assert stage['telemetry']['rejectedCalls'] == 0
    assert stage['telemetry']['validatedCalls'] == 1
    if field == 'keyPoints':
        assert stage['result'][field][0]['text'] == replacement[0]['text']
        assert stage['result'][field][0]['blockIds'] == [store.detail(source)['blocks'][0]['id']]
    else:
        assert stage['result'][field] == replacement


def english_result(result):
    return {**result,
            'summary': 'The source preserves original evidence and source references.',
            'keyPoints': [dict(point, text='The original evidence remains available.')
                          for point in result['keyPoints']],
            'keywords': ['openai', 'data security', 'AI/ML']}


def test_long_document_reduction_accepts_foreign_fields_and_preserves_real_refs(workbench):
    store, models, service = workbench
    models.config['contextChars'] = 16000
    source = publish(store, ['Knowledge management preserves original evidence for later review. ' * 80
                             for _ in range(4)])
    models.rewrite = english_result
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    tasks = [json.loads(messages[1]['content'])['task'] for _, messages in models.calls]
    assert 'summary_reduce' in tasks
    stage = service.detail(source)['summary']
    assert stage['result']['summary'].startswith('The source preserves')
    assert stage['result']['keywords'] == ['openai', 'data security', 'AI/ML']
    assert {ref for point in stage['result']['keyPoints'] for ref in point['blockIds']} == {
        block['id'] for block in store.detail(source)['blocks']}
    assert stage['telemetry']['rejectedCalls'] == 0


@pytest.mark.parametrize('field,replacement', [
    ('summary', None),
    ('summary', ''),
    ('summary', ' \n\t'),
    ('summary', 42),
    ('summary', 'a' * 801),
    ('keyPoints', None),
    ('keyPoints', {}),
    ('keyPoints', []),
    ('keyPoints', [{'text': '有效', 'blockIds': ['p1']}] * 7),
    ('keyPoints', [None]),
    ('keyPoints', [{'blockIds': ['p1']}]),
    ('keyPoints', [{'text': '', 'blockIds': ['p1']}]),
    ('keyPoints', [{'text': 'a' * 301, 'blockIds': ['p1']}]),
    ('keyPoints', [{'text': '有效'}]),
    ('keyPoints', [{'text': '有效', 'blockIds': []}]),
    ('keyPoints', [{'text': '有效', 'blockIds': 'p1'}]),
    ('keyPoints', [{'text': '有效', 'blockIds': [42]}]),
    ('keyPoints', [{'text': '有效', 'blockIds': ['unknown']}]),
    ('keyPoints', [{'text': '有效', 'blockIds': ['p1', 'p2', 'p3', 'p4']}]),
    ('keywords', None),
    ('keywords', 'keyword'),
    ('keywords', []),
    ('keywords', ['keyword'] * 13),
    ('keywords', [None]),
    ('keywords', [42]),
    ('keywords', [' \t ']),
    ('keywords', ['a' * 41]),
])
def test_invalid_summary_structure_is_still_rejected(field, replacement):
    value = valid_result()
    value[field] = deepcopy(replacement)
    with pytest.raises(ValueError):
        EnrichmentService._summary(value, ALLOWED)


@pytest.mark.parametrize('missing', ['summary', 'keyPoints', 'keywords'])
def test_required_summary_fields_cannot_be_omitted(missing):
    value = valid_result()
    del value[missing]
    with pytest.raises(ValueError):
        EnrichmentService._summary(value, ALLOWED)


def test_total_summary_budget_is_enforced_after_normalization():
    value = valid_result()
    result = EnrichmentService._summary(value, ALLOWED)
    serialized_size = len(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    assert EnrichmentService._summary(value, ALLOWED, serialized_size) == result
    with pytest.raises(ValueError, match='预算'):
        EnrichmentService._summary(value, ALLOWED, serialized_size - 1)


def test_simplified_normalization_and_keyword_deduplication_remain():
    value = {'summary': '保留來源與資料。',
             'keyPoints': [{'text': '證據可查。', 'blockIds': ['p1', 'p1']}],
             'keywords': ['資料', '资料', 'openai', 'openai', 'AI/ML']}
    result = EnrichmentService._summary(value, ALLOWED)
    assert result['summary'] == '保留来源与资料。'
    assert result['keyPoints'] == [{'text': '证据可查。', 'blockIds': ['p1']}]
    assert result['keywords'] == ['资料', 'openai', 'AI/ML']


@pytest.mark.parametrize('text', ['not json', '{"summary":', '[]', 'null'])
def test_invalid_model_json_still_fails_before_summary_validation(text):
    with pytest.raises(ValueError):
        _json(text)


def test_one_structural_repair_can_adopt_valid_foreign_output(workbench):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda result: ({**result, 'keyPoints': []} if len(models.calls) == 1
                                    else english_result(result))
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    stage = service.detail(source)['summary']
    assert len(models.calls) == 2
    assert stage['telemetry']['rejectedCalls'] == 1
    assert stage['telemetry']['validatedCalls'] == 1
    assert stage['result']['summary'].startswith('The source preserves')
    assert models.calls[0][1][1] == models.calls[1][1][1]
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1


def test_two_structural_failures_keep_previous_summary_without_more_repairs(workbench):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = english_result
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    previous = service.detail(source)['summary']['result']
    models.rewrite = lambda result: {**result, 'keyPoints': []}
    service.queue(source, ['summary'], force=True)
    assert execute(store, service)['status'] == 'failed'
    stage = service.detail(source)['summary']
    assert len(models.calls) == 3  # One original success, then two failed attempts.
    assert stage['previousSuccess'] is True and stage['result'] == previous
    assert stage['telemetry']['rejectedCalls'] == 2
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 1


def test_cancelled_summary_cannot_publish_late_valid_foreign_output(workbench):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = english_result
    job = service.queue(source, ['summary'])[0]
    models.callback = lambda: store.cancel(job['id'])
    assert execute(store, service)['status'] == 'cancelled'
    assert service.detail(source)['summary']['result'] is None
    assert len(models.calls) == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 0


@pytest.mark.parametrize('prompt_id', ['summary', 'summary_reduce'])
def test_default_summary_prompts_still_request_simplified_chinese(prompt_id):
    assert '简体中文' in prompt_text(prompt_id, values={'summaryBudget': 6000})


@pytest.mark.parametrize('prompt_id', ['summary', 'summary_reduce'])
def test_custom_summary_prompts_keep_fixed_simplified_chinese_instruction(tmp_path, prompt_id):
    store = Store(tmp_path / 'isolated-summary-prompts')
    library = PromptLibrary(store)
    custom = 'Respond only in English. Budget: {{summaryBudget}}.'
    saved = library.update(prompt_id, custom, 1)
    assert saved['content'] == custom
    with store.connect() as db:
        text = prompt_text(prompt_id, db=db, values={'summaryBudget': 6000})
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
    assert text.startswith('Respond only in English.')
    assert CHINESE_OUTPUT_CONTRACT in text.split('程序固定约束：', 1)[1]
    assert '统一使用简体中文' in text
