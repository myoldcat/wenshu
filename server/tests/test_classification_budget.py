"""Configured-window and safe budget failure checks using isolated SQLite jobs.

These tests use a deterministic gateway, not the real model service. The existing
six-candidate aggregation regression remains in test_enrichment.py unchanged.
"""
import json

from server.prompts import PromptLibrary
from server.tests.test_enrichment import environment, execute, publish


def sizeable_document(store, suffix):
    paragraphs = [('公共部门保存政策实施记录，明确职责衔接、资料来源和服务办理过程。' * 160) + str(index)
                  for index in range(12)]
    return publish(store, paragraphs, url='https://example.org/classification-budget-' + suffix,
                   title='公共政策资料'), paragraphs


def test_million_character_window_classifies_large_document_in_one_complete_batch(environment):
    store, models, service, _ = environment
    models.config.update(contextChars=1_000_000, maxOutputTokens=8192)
    source, paragraphs = sizeable_document(store, 'single-batch')
    service.queue(source, ['classification'])
    finished = execute(store, service)
    assert finished['status'] == 'succeeded', finished['error']
    assert len(models.calls) == 1
    config, messages = models.calls[0]
    payload = json.loads(messages[1]['content'])
    assert config['contextChars'] == 1_000_000
    assert payload['task'] == 'classification'
    assert [piece['text'] for piece in payload['blocks']] == paragraphs
    assert 16000 < sum(len(message['content']) for message in messages) <= 1_000_000
    document = store.detail(source)
    assert {piece['blockId'] for piece in payload['blocks']} == {block['id'] for block in document['blocks']}
    stage = service.detail(source)['classification']
    assert stage['result']['applied'] is True
    assert stage['result']['classificationPaths'] == document['classificationPaths']


def test_low_window_and_large_edited_reduce_prompt_fail_without_replacing_success(environment):
    store, models, service, _ = environment
    models.config.update(contextChars=1_000_000, maxOutputTokens=8192)
    source, _ = sizeable_document(store, 'safe-failure')
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    previous = service.detail(source)['classification']
    previous_document = store.detail(source)
    with store.connect() as db:
        output_count = db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0]

    # A user-edited reduction prompt can exhaust even an otherwise supported
    # window. Force that condition explicitly instead of assuming 16k always
    # fails for every prompt or weakening the six-candidate regression.
    library = PromptLibrary(store)
    entry = next(item for item in library.list()['items'] if item['id'] == 'classification_reduce')
    assert len(entry['content']) < 21000
    library.update(entry['id'], entry['content'] + '\n' + '测' * (21000-len(entry['content'])-1), entry['version'])
    models.config['contextChars'] = 16000
    models.calls.clear()
    service.queue(source, ['classification'], force=True)
    finished = execute(store, service)
    assert finished['status'] == 'failed'
    assert 'contextChars' in finished['error']
    assert len(models.calls) >= 2
    assert all(json.loads(messages[1]['content'])['task'] == 'classification' for _, messages in models.calls)
    assert all(sum(len(message['content']) for message in messages) <= 16000 for _, messages in models.calls)
    stage = service.detail(source)['classification']
    assert stage['status'] == 'failed' and stage['previousSuccess'] is True
    assert stage['resultRunId'] == previous['resultRunId']
    assert stage['runId'] != previous['runId']
    document = store.detail(source)
    assert document['classificationPaths'] == previous_document['classificationPaths']
    assert document['tags'] == previous_document['tags']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == output_count
