"""Article nature shares classification calls and respects immutable revisions."""
import json

import pytest

from server import enrichment as enrichment_module
from server.article_type import (ARTICLE_TYPE_INSTRUCTIONS, ARTICLE_TYPE_LABELS, ARTICLE_TYPE_POLICY_VERSION,
                                 ARTICLE_TYPES, ArticleTypeRevisionConflict, article_type_label,
                                 classification_article_type, validate_article_type)
from server.enrichment import EnrichmentService
from server.store import Store
from server.tests.test_enrichment import Gateway, execute, publish
from server.tests.test_ai_api import bench, LOCAL


@pytest.fixture
def workbench(tmp_path):
    store = Store(tmp_path / 'article-type')
    models = Gateway()
    return store, models, EnrichmentService(store, models)


def legacy_job(store, job):
    """Represent a queue-time intent predating the opt-in policy."""
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
        for name in ('articleTypePolicyVersion', 'articleTypeInstructions'):
            payload.pop(name, None)
        db.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id']))


@pytest.mark.parametrize('code,label', [('fact', '事实类'), ('opinion', '观点类'), ('technical', '技术类')])
def test_program_owned_labels_and_single_call_classification(workbench, code, label):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda value: {**value, 'articleType': code, 'articleTypeLabel': '自造标签'}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == 1
    config, messages = models.calls[0]
    request = json.loads(messages[-1]['content'])
    assert request['articleTypePolicyVersion'] == ARTICLE_TYPE_POLICY_VERSION
    assert request['allowedArticleTypes'] == list(ARTICLE_TYPES)
    assert ARTICLE_TYPE_INSTRUCTIONS in messages[0]['content']
    result = service.detail(source)['classification']['result']
    assert result['articleType'] == code and 'articleTypeLabel' not in result
    document = store.detail(source)
    assert document['articleType'] == code and document['articleTypeLabel'] == label
    assert document['articleTypeManual'] is False
    assert label not in document['tags']
    assert article_type_label(code) == ARTICLE_TYPE_LABELS[code] == label
    for listed in (store.documents()['documents'][0], store.search('governance')['results'][0]):
        assert listed['articleType'] == code and listed['articleTypeLabel'] == label


def test_old_unlabelled_documents_remain_unknown_without_processing(workbench):
    store, models, service = workbench
    source = publish(store)
    for document in (store.detail(source), store.documents()['documents'][0], store.search()['results'][0]):
        assert document['articleType'] is None and document['articleTypeLabel'] is None
        assert document['articleTypeManual'] is False
    assert models.calls == []
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM document_article_types').fetchone()[0] == 0
    assert article_type_label('自行推测') is None


@pytest.mark.parametrize('invalid', ['观点类', 'Fact', True, {}, []])
def test_invalid_model_type_is_repaired_then_rejected(workbench, invalid):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda value: {**value, 'articleType': invalid}
    service.queue(source, ['classification'])
    result = execute(store, service)
    assert result['status'] == 'failed' and '文章性质' in result['error']
    assert len(models.calls) == 2
    assert service.detail(source)['classification']['result'] is None
    assert store.detail(source)['articleType'] is None
    with pytest.raises(ValueError):
        validate_article_type(invalid)


def test_new_policy_requires_explicit_type_but_permits_abstaining(workbench):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda value: {key: result for key, result in value.items() if key != 'articleType'}
    service.queue(source, ['classification'])
    failed = execute(store, service)
    assert failed['status'] == 'failed' and 'articleType' in failed['error']
    assert len(models.calls) == 2
    models.rewrite = lambda value: {**value, 'articleType': None}
    service.queue(source, ['classification'], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    assert store.detail(source)['articleType'] is None
    assert service.detail(source)['classification']['result']['articleType'] is None
    assert classification_article_type({}) is None


def test_manual_topic_tags_do_not_block_independent_nature(workbench):
    store, models, service = workbench
    source = publish(store)
    before = store.patch(source, {'tags': ['人工资料标签']})
    models.rewrite = lambda value: {**value, 'articleType': 'technical'}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    document = store.detail(source)
    assert document['tags'] == before['tags'] and document['classificationPaths'] == before['classificationPaths']
    assert document['classificationManual'] is True
    assert document['articleType'] == 'technical' and document['articleTypeManual'] is False
    assert service.detail(source)['classification']['result']['applied'] is False


def test_manual_override_survives_auto_and_null_restores_latest_auto(workbench):
    store, models, service = workbench
    source = publish(store)
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    document = store.detail(source)
    manual = store.patch(source, {'articleType': 'opinion', 'revisionId': document['revisionId']})
    assert manual['articleType'] == 'opinion' and manual['articleTypeManual'] is True
    assert manual['tags'] == document['tags'] and manual['classificationManual'] is False
    models.rewrite = lambda value: {**value, 'articleType': 'technical'}
    service.queue(source, ['classification'], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    assert store.detail(source)['articleType'] == 'opinion'
    restored = store.patch(source, {'articleType': None, 'revisionId': document['revisionId']})
    assert restored['articleType'] == 'technical' and restored['articleTypeManual'] is False
    assert Store(store.root).detail(source)['articleType'] == 'technical'


def test_manual_reset_without_auto_restores_unknown_and_revision_guard_is_atomic(workbench):
    store, models, service = workbench
    source = publish(store)
    original = store.detail(source)
    with pytest.raises(ValueError, match='revisionId'):
        store.patch(source, {'articleType': 'fact', 'favorite': True})
    with pytest.raises(ArticleTypeRevisionConflict):
        store.patch(source, {'articleType': 'fact', 'revisionId': 'foreign-revision', 'tags': ['错误'], 'favorite': True})
    assert store.detail(source) == original
    manual = store.patch(source, {'articleType': 'fact', 'revisionId': original['revisionId']})
    assert manual['articleType'] == 'fact'
    reset = store.patch(source, {'articleType': None, 'revisionId': original['revisionId']})
    assert reset['articleType'] is None and reset['articleTypeManual'] is False
    assert models.calls == []


def test_api_validates_type_revision_and_preserves_explicit_null(bench):
    client, store, service, model_service, provider = bench
    source = publish(store)
    revision = store.detail(source)['revisionId']
    path = '/api/documents/' + source
    assert client.patch(path, json={'articleType': '其他', 'revisionId': revision}, headers=LOCAL).status_code == 422
    assert client.patch(path, json={'articleType': 'fact'}, headers=LOCAL).status_code == 422
    assert client.patch(path, json={'articleType': 'fact', 'revisionId': 'stale'}, headers=LOCAL).status_code == 409
    applied = client.patch(path, json={'articleType': 'opinion', 'revisionId': revision}, headers=LOCAL)
    assert applied.status_code == 200, applied.text
    assert applied.json()['document']['articleType'] == 'opinion'
    reset = client.patch(path, json={'articleType': None, 'revisionId': revision}, headers=LOCAL)
    assert reset.status_code == 200, reset.text
    assert reset.json()['document']['articleType'] is None
    assert reset.json()['document']['articleTypeManual'] is False
    assert provider.calls == []


def test_type_is_revision_owned_and_late_old_output_cannot_label_current(workbench):
    store, models, service = workbench
    source = publish(store, 'Original governance report preserves source evidence and immutable document versions.', url='https://example.org/article-versions')
    old_revision = store.detail(source)['revisionId']
    job = service.queue(source, ['classification'])[0]
    claimed = store.claim()
    assert claimed['id'] == job['id']
    same_source = publish(store, 'A new original source revision records different content and keeps its evidence separately.', url='https://example.org/article-versions')
    assert same_source == source
    assert store.detail(source)['revisionId'] != old_revision
    service.process(claimed)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert store.detail(source, old_revision)['articleType'] == 'fact'
    assert store.detail(source)['articleType'] is None
    with pytest.raises(ArticleTypeRevisionConflict):
        store.patch(source, {'articleType': 'opinion', 'revisionId': old_revision})


def test_manual_type_does_not_leak_to_new_revision(workbench):
    store, models, service = workbench
    source = publish(store, 'First version of a news commentary discusses the governance evidence and competing interpretations.', url='https://example.org/manual-versions')
    old = store.detail(source)
    store.patch(source, {'articleType': 'opinion', 'revisionId': old['revisionId']})
    publish(store, 'A later version of a technical method explains implementation steps and reproducible procedures.', url='https://example.org/manual-versions')
    assert store.detail(source)['articleType'] is None
    historic = store.detail(source, old['revisionId'])
    assert historic['articleType'] == 'opinion' and historic['articleTypeManual'] is True


def test_reducer_uses_full_article_decision_without_program_majority_vote(workbench):
    store, models, service = workbench
    models.config['contextChars'] = 16000
    source = publish(store, '公共部门报道中引用观点，全文目的仍须结合原文判断。' * 2500)
    reductions = []
    def choose(value):
        payload = json.loads(models.calls[-1][1][-1]['content'])
        if payload['task'] == 'classification_reduce':
            assert all('articleType' in part and part['languageWeight'] > 0 for part in payload['parts'])
            reductions.append(payload['parts'])
            return {**value, 'articleType': 'opinion'}
        return {**value, 'articleType': 'fact'}
    models.rewrite = choose
    service.queue(source, ['classification'])
    finished = execute(store, service)
    assert finished['status'] == 'succeeded', finished['error']
    assert reductions and store.detail(source)['articleType'] == 'opinion'
    assert all(ARTICLE_TYPE_INSTRUCTIONS in messages[0]['content'] for _, messages in models.calls)


def test_frozen_instructions_are_not_replaced_after_queue(workbench, monkeypatch):
    store, models, service = workbench
    source = publish(store)
    queued = service.queue(source, ['classification'])[0]
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (queued['id'],)).fetchone()[0])
    assert payload['articleTypeInstructions'] == ARTICLE_TYPE_INSTRUCTIONS
    monkeypatch.setattr(enrichment_module, 'ARTICLE_TYPE_INSTRUCTIONS', '后来的指令不应进入旧任务')
    assert execute(store, service)['status'] == 'succeeded'
    assert ARTICLE_TYPE_INSTRUCTIONS in models.calls[0][1][0]['content']
    assert '后来的指令不应进入旧任务' not in models.calls[0][1][0]['content']


def test_legacy_frozen_job_and_checkpoints_do_not_gain_new_policy(workbench):
    store, models, service = workbench
    models.config['contextChars'] = 16000
    source = publish(store, 'Original evidence and governance records. ' * 1800)
    queued = service.queue(source, ['classification'])[0]
    legacy_job(store, queued)
    models.fail_at = 2
    assert execute(store, service)['status'] == 'failed'
    first_messages = models.calls[0][1]
    with store.connect() as db:
        saved = json.loads(db.execute('SELECT result_json FROM ai_checkpoints LIMIT 1').fetchone()[0])
    assert 'articleType' not in saved
    models.fail_at = None
    store.retry(queued['id'])
    finished = execute(store, service)
    assert finished['status'] == 'succeeded', finished['error']
    assert sum(messages == first_messages for _, messages in models.calls) == 1
    assert all('articleTypePolicyVersion' not in json.loads(messages[-1]['content']) for _, messages in models.calls)
    assert all(ARTICLE_TYPE_INSTRUCTIONS not in messages[0]['content'] for _, messages in models.calls)
    assert store.detail(source)['articleType'] is None
    assert 'articleType' not in service.detail(source)['classification']['result']
