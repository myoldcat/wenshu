"""AI outputs are exercised through real SQLite jobs, with a deterministic gateway.

These tests assert publication/lineage and recovery; they do not claim model
semantic quality or real-provider availability.
"""
import json
import pytest
from server import enrichment as enrichment_module
from server.store import Store
from server.enrichment import EnrichmentService, taxonomy


class Gateway:
    def __init__(self):
        self.config = {'id': 'model_local', 'version': 1, 'name': '测试模型', 'model': 'mock',
                       'contextChars': 24000, 'maxOutputTokens': 4096, 'apiKey': 'never-store-this-secret'}
        self.preferences = {kind + 'ModelId': self.config['id'] for kind in ('summary', 'translation', 'classification')}
        self.preferences['autoProcess'] = False
        self.calls = []
        self.callback = None
        self.fail_at = None
        self.rewrite = None

    def get_preferences(self):
        return self.preferences.copy()

    def resolve(self, model_id=None, version=None):
        assert model_id == self.config['id']
        result = self.config.copy()
        if version is not None:
            result['version'] = version
        return result

    def complete(self, config, messages):
        assert sum(len(message['content']) for message in messages) <= config['contextChars'], 'input context exceeded'
        self.calls.append((config.copy(), messages))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.fail_at == len(self.calls):
            raise ValueError('模型服务暂时不可用')
        payload = json.loads(messages[1]['content'])
        if payload['task'] in ('summary', 'summary_reduce'):
            ids = [block['blockId'] for block in payload['blocks']] if 'blocks' in payload else [block for part in payload['summaries'] for point in part['keyPoints'] for block in point['blockIds']]
            refs = list(dict.fromkeys(ids))[:18]
            result = {'summary': '文档介绍公共部门知识治理并保留证据。', 'keyPoints': [{'text': '资料保留来源与原始版本。', 'blockIds': refs[start:start+3]} for start in range(0, len(refs), 3)], 'keywords': ['知识治理', '来源']}
        elif payload['task'] == 'translation':
            result = {'segments': [{'id': block['id'], 'text': '译文：' + block['text']} for block in payload['blocks']]}
        elif payload['task'] == 'classification_reduce':
            result = {'paths': [dict(payload['parts'][0]['paths'][0], primary=True)]}
        else:
            result = {'paths': [{'level1Id': 'politics_governance', 'level2': '公共治理', 'level3': '档案管理',
                                 'primary': True, 'reason': '正文围绕公共部门档案证据管理展开。', 'blockIds': [payload['blocks'][0]['blockId']]}]}
        if payload.get('articleTypePolicyVersion'):
            result['articleType'] = 'fact'
        if self.rewrite:
            result = self.rewrite(result)
        return {'text': json.dumps(result, ensure_ascii=False), 'usage': {'total_tokens': 42}}


def publish(store, text=None, url='https://example.org/document', title='知识治理'):
    blocks = text if isinstance(text, list) else [text or 'Knowledge governance records original evidence and preserves document versions for later review.']
    job = store.queue('file', {}, title)
    claimed = store.claim()
    assert claimed['id'] == job['id']
    raw = ('<html>' + '\n'.join(blocks) + '</html>').encode()
    parsed = {'title': title, 'body': blocks, 'blocks': [{'kind': 'paragraph', 'text': value, 'ordinal': index} for index, value in enumerate(blocks)],
              'language': 'en', 'parser_version': 'test-1'}
    store.publish(claimed, {'url': url}, raw, parsed)
    return store.job(job['id'])['source_id']


@pytest.fixture
def environment(tmp_path):
    store = Store(tmp_path / 'data')
    models = Gateway()
    service = EnrichmentService(store, models)
    source = publish(store)
    return store, models, service, source


def execute(store, service):
    job = store.claim()
    assert job
    service.process(job)
    return store.job(job['id'])


def test_summary_translation_classification_publish_real_lineage(environment):
    store, models, service, source = environment
    queued = service.queue(source)
    assert len(queued) == 3
    for _ in queued:
        assert execute(store, service)['status'] == 'succeeded'
    detail = service.detail(source)
    assert detail['summary']['result']['keyPoints'][0]['blockIds'] == [store.detail(source)['blocks'][0]['id']]
    assert detail['translation']['result']['segments'][0]['text'].startswith('译文：')
    assert detail['classification']['result']['applied'] is True
    assert store.detail(source)['tags'] == ['政治与公共治理', '公共治理', '档案管理']
    paths = store.detail(source)['classificationPaths']
    assert len(paths) == 1 and len(paths[0]['nodes']) == 3
    assert paths[0]['primary'] is True
    assert models.config['apiKey'] not in json.dumps(detail)
    with store.connect() as db:
        records = str([tuple(row) for row in db.execute('SELECT * FROM ai_runs')]) + str([tuple(row) for row in db.execute("SELECT input_json FROM jobs WHERE kind LIKE 'ai_%'")]) + str([tuple(row) for row in db.execute('SELECT * FROM ai_calls')])
    assert models.config['apiKey'] not in records
    assert detail['summary']['telemetry']['calls'] == 1
    assert detail['summary']['telemetry']['reportedUsage'] == {'total_tokens': 42}


def test_duplicate_intent_does_not_call_model_again_and_force_creates_version(environment):
    store, models, service, source = environment
    first = service.queue(source, ['summary'])[0]
    execute(store, service)
    assert service.queue(source, ['summary'])[0]['id'] == first['id']
    assert len(models.calls) == 1
    second = service.queue(source, ['summary'], force=True)[0]
    assert second['id'] != first['id']
    execute(store, service)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 2


def test_classification_prompt_upgrade_keeps_other_intents_and_previous_result(environment, monkeypatch):
    store, models, service, source = environment
    versions = service.detail(source)
    assert versions['classification']['promptVersion'] == enrichment_module.classification_rules.PROMPT_VERSION
    assert versions['summary']['promptVersion'] == enrichment_module.PROMPT_VERSION
    assert versions['translation']['promptVersion'] == enrichment_module.TRANSLATION_PROMPT_VERSION
    initial = {job['kind']: job['id'] for job in service.queue(source)}
    for _ in initial:
        assert execute(store, service)['status'] == 'succeeded'
    previous = service.detail(source)['classification']
    monkeypatch.setattr(enrichment_module.classification_rules, 'PROMPT_VERSION', 'test-classification-next')
    queued = {job['kind']: job['id'] for job in service.queue(source)}
    assert queued['ai_summary'] == initial['ai_summary']
    assert queued['ai_translation'] == initial['ai_translation']
    assert queued['ai_classification'] != initial['ai_classification']
    pending = service.detail(source)['classification']
    assert pending['promptVersion'] == 'test-classification-next'
    assert pending['resultPromptVersion'] == previous['resultPromptVersion']
    assert pending['result'] == previous['result'] and pending['previousSuccess']
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == 4
    assert service.detail(source)['classification']['resultPromptVersion'] == 'test-classification-next'


def test_pending_classification_prompt_upgrade_fails_without_invalidating_other_capabilities(environment, monkeypatch):
    store, models, service, source = environment
    service.queue(source)
    monkeypatch.setattr(enrichment_module.classification_rules, 'PROMPT_VERSION', 'test-classification-next')
    assert execute(store, service)['status'] == 'succeeded'
    assert execute(store, service)['status'] == 'succeeded'
    stale = execute(store, service)
    assert stale['status'] == 'failed' and '提示词版本已升级' in stale['error']
    assert len(models.calls) == 2
    assert service.detail(source)['classification']['result'] is None


@pytest.mark.parametrize('kind,rewrite', [
    ('summary', lambda data: {**data, 'keyPoints': [{'text': '伪造', 'blockIds': ['unknown']}]}),
    ('translation', lambda data: {'segments': []}),
    ('translation', lambda data: {'segments': [{'id': 'not-real', 'text': '错误'}]}),
    ('classification', lambda data: {'categories': [{'id': 'invented', 'reason': '假的', 'blockIds': ['unknown']}]}),
])
def test_invalid_model_outputs_never_publish(environment, kind, rewrite):
    store, models, service, source = environment
    models.rewrite = rewrite
    service.queue(source, [kind])
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)[kind]['result'] is None
    assert store.detail(source)['body']
    assert store.detail(source)['tags'] == ['未分类']
    assert service.detail(source)[kind]['telemetry']['rejectedCalls'] == (2 if kind in ('summary', 'classification') else 1)


def test_manual_classification_wins_even_during_model_call(environment):
    store, models, service, source = environment
    models.callback = lambda: store.patch(source, {'tags': ['用户自定义分类']})
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['classification']['result']
    assert result['manualOverride'] is True and result['applied'] is False
    assert result['tags'] == ['政治与公共治理', '公共治理', '档案管理']
    assert store.detail(source)['tags'] == ['用户自定义分类']
    assert store.detail(source)['classificationPaths'] == []


def test_translation_human_edit_survives_regeneration_and_restart(environment):
    store, models, service, source = environment
    service.queue(source, ['translation'])
    execute(store, service)
    block_id = store.detail(source)['blocks'][0]['id']
    service.edit_translation(source, block_id, '人工确认的译文')
    service.queue(source, ['translation'], force=True)
    execute(store, service)
    reopened = EnrichmentService(Store(store.root), models)
    translation = reopened.detail(source)['translation']
    assert translation['locked'] is True
    assert translation['result']['segments'][0] == {'blockId': block_id, 'text': '人工确认的译文', 'locked': True, 'edited': True}
    with pytest.raises(ValueError, match='不属于'):
        service.edit_translation(source, 'foreign-block', '错误')


def test_unlocked_edit_replaced_only_by_new_run(environment):
    store, models, service, source = environment
    service.queue(source, ['translation']); execute(store, service)
    block = store.detail(source)['blocks'][0]['id']
    service.edit_translation(source, block, '暂存人工译文', locked=False)
    assert service.detail(source)['translation']['result']['segments'][0]['text'] == '暂存人工译文'
    service.queue(source, ['translation'], force=True); execute(store, service)
    assert service.detail(source)['translation']['result']['segments'][0]['edited'] is False


def test_retry_reuses_completed_long_document_checkpoints(environment):
    store, models, service, source = environment
    # One large source block is split for model context, then joined under the
    # original block id so reading/evidence anchors do not change.
    source = publish(store, 'Evidence and governance. ' * 500, url='https://example.org/long')
    queued = service.queue(source, ['translation'])[0]
    models.fail_at = 2
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)['translation']['result'] is None
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1
    store.retry(queued['id']); models.fail_at = None
    reopened = EnrichmentService(Store(store.root), models)
    assert execute(store, reopened)['status'] == 'succeeded'
    # First successful input is never resent after restart/retry.
    first_input = models.calls[0][1]
    assert sum(messages == first_input for _, messages in models.calls) == 1
    assert len(reopened.detail(source)['translation']['result']['segments']) == 1


def test_failed_regeneration_keeps_previous_success(environment):
    store, models, service, source = environment
    service.queue(source, ['summary']); execute(store, service)
    previous = service.detail(source)['summary']['result']
    service.queue(source, ['summary'], force=True)
    models.fail_at = 2
    assert execute(store, service)['status'] == 'failed'
    stage = service.detail(source)['summary']
    assert stage['status'] == 'failed' and stage['previousSuccess'] is True
    assert stage['result'] == previous


def test_cancel_during_model_call_cannot_publish(environment):
    store, models, service, source = environment
    job = service.queue(source, ['summary'])[0]
    models.callback = lambda: store.cancel(job['id'])
    assert execute(store, service)['status'] == 'cancelled'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 0


def test_new_force_run_fences_old_response(environment):
    store, models, service, source = environment
    first = service.queue(source, ['summary'])[0]
    models.callback = lambda: service.queue(source, ['summary'], force=True)
    assert execute(store, service)['status'] == 'cancelled'
    assert store.job(first['id'])['stage'] == 'superseded'
    assert execute(store, service)['status'] == 'succeeded'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 1


def test_old_attempt_cannot_fail_retried_task(environment):
    store, models, service, source = environment
    job = service.queue(source, ['summary'])[0]
    def restart():
        store.recover()
        newer = store.claim()
        assert newer['id'] == job['id']
    models.callback = restart
    claimed = store.claim()
    service.process(claimed)
    current = store.job(job['id'])
    assert current['status'] == 'running' and current['attempts'] == 2
    assert service.detail(source)['summary']['result'] is None


def test_old_revision_result_never_changes_new_revision_tags(environment):
    store, models, service, source = environment
    job = service.queue(source, ['classification'])[0]
    claimed = store.claim()
    old_revision = store.detail(source)['revisionId']
    def update_document():
        publish(store, 'A changed document about economic investment requires new source revision and retained old evidence.')
    models.callback = update_document
    service.process(claimed)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert store.detail(source)['revisionId'] != old_revision
    assert store.detail(source)['tags'] == ['未分类']
    assert service.detail(source)['classification']['status'] == 'not_started'
    assert service.detail(source)['stale'] is False
    assert service.detail(source)['hasPreviousRevisionResults'] is True
    old = service.detail(source, old_revision)
    assert old['classification']['result']['applied'] is False
    assert old['stale'] is True
    with pytest.raises(ValueError, match='版本已过期'):
        service.queue(source, ['summary'], revision_id=old_revision)


def test_model_version_is_frozen_at_queue_time(environment):
    store, models, service, source = environment
    service.queue(source, ['summary'])
    models.config['version'] = 2
    execute(store, service)
    assert models.calls[0][0]['version'] == 1
    assert service.detail(source)['summary']['model']['version'] == 1
    assert service.queue(source, ['summary'])[0]['status'] == 'queued'


def test_automatic_processing_is_opt_in_and_never_scans_existing(environment):
    store, models, service, source = environment
    models.preferences['autoTranslate'] = False
    assert service.maybe_queue_auto(source) == []
    models.preferences['autoProcess'] = True
    models.preferences['translationModelId'] = None
    jobs = service.maybe_queue_auto(source)
    assert len(jobs) == 2
    assert {job['kind'] for job in jobs} == {'ai_summary', 'ai_classification'}


def test_missing_binding_rejects_whole_request_before_queue(environment):
    store, models, service, source = environment
    models.preferences['translationModelId'] = None
    with pytest.raises(ValueError, match='绑定'):
        service.queue(source)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_runs').fetchone()[0] == 0
    categories = taxonomy()['categories']
    assert len(categories) == len({category['id'] for category in categories})


def test_long_summary_reduce_retains_real_source_ids(environment):
    store, models, service, source = environment
    models.config['contextChars'] = 16000
    source = publish(store, ['Knowledge management with traceable evidence. ' * 80 for _ in range(4)], url='https://example.org/summary-long')
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['summary']['result']
    refs = {ref for point in result['keyPoints'] for ref in point['blockIds']}
    assert refs == {block['id'] for block in store.detail(source)['blocks']}
    assert any(json.loads(messages[1]['content'])['task'] == 'summary_reduce' for _, messages in models.calls)


def test_short_article_with_many_dom_blocks_stays_in_one_summary_request(environment):
    store, models, service, source = environment
    models.config.update(contextChars=16000, maxOutputTokens=8192)
    fragments = ['财经', '股票', '基金'] * 10 + ['铜价上升，但投资者仍在等待关税政策明确。' * 20] + ['分享', '推荐'] * 20 + ['来源：新闻社']
    source = publish(store, fragments, url='https://example.org/fragmented')
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == 1
    sent = json.loads(models.calls[0][1][1]['content'])['blocks']
    assert [block['text'] for block in sent] == fragments
    assert {block['blockId'] for block in sent} == {block['id'] for block in store.detail(source)['blocks']}


@pytest.mark.parametrize('invalid', ['empty_points', 'unknown_reference'])
def test_summary_repairs_once_using_original_evidence_and_records_both_calls(environment, invalid):
    store, models, service, source = environment
    def first_invalid(value):
        if len(models.calls) > 1:
            return value
        return {**value, 'keyPoints': [] if invalid == 'empty_points' else [{'text': '错误', 'blockIds': ['unknown']} ]}
    models.rewrite = first_invalid
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == 2
    assert models.calls[0][1][1] == models.calls[1][1][1]
    assert len(models.calls[1][1]) == 2  # Invalid model content is never added.
    telemetry = service.detail(source)['summary']['telemetry']
    assert telemetry['calls'] == 2 and telemetry['rejectedCalls'] == 1 and telemetry['validatedCalls'] == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1


def test_cancel_after_invalid_summary_prevents_repair(environment):
    store, models, service, source = environment
    job = service.queue(source, ['summary'])[0]
    models.callback = lambda: store.cancel(job['id'])
    models.rewrite = lambda value: {**value, 'keyPoints': []}
    assert execute(store, service)['status'] == 'cancelled'
    assert len(models.calls) == 1
    assert service.detail(source)['summary']['result'] is None


def test_evidence_batches_keep_all_escaped_characters_within_budget():
    text = '\"\n\\\t测试' * 7000
    batches = EnrichmentService._evidence_batches([{'id': 'block1', 'kind': 'paragraph', 'text': text}], {'contextChars': 8000})
    assert ''.join(piece['text'] for batch in batches for piece in batch) == text
    assert all(len(json.dumps(batch, ensure_ascii=False, separators=(',', ':'))) <= 2000 for batch in batches)


def test_thousand_block_summary_keeps_reduction_within_context(environment):
    store, models, service, source = environment
    source = publish(store, ['Knowledge governance preserves evidence and context. ' + str(index) for index in range(1000)], url='https://example.org/many-blocks')
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['summary']['result']
    assert len(result['keyPoints']) <= 6
    assert all(1 <= len(point['blockIds']) <= 3 for point in result['keyPoints'])
    assert all(sum(len(message['content']) for message in messages) <= config['contextChars'] for config, messages in models.calls)


@pytest.mark.parametrize('translated', [
    '票价上涨0.6美元至1.8美元。',
    '票价上涨 $0.6 至 $1.8。',
    '票价上涨 $ 0.60 至 $ 1.80。',
    '票价上涨 $0.6 至 $2.8。',
])
def test_translation_amount_content_does_not_gate_structurally_valid_output(environment, translated):
    store, models, service, source = environment
    source = publish(store, 'Fares will rise by $0.6-$1.8 under the announced adjustment. The statement does not spell out a currency.', url='https://example.org/fares')
    models.rewrite = lambda value: {'segments': [{**segment, 'text': translated} for segment in value['segments']]}
    service.queue(source, ['translation'])
    result = execute(store, service)
    # Adoption confirms structural alignment only, not monetary correctness.
    assert result['status'] == 'succeeded'
    assert service.detail(source)['translation']['result']['segments'][0]['text'] == translated


def test_default_taxonomy_has_fifteen_general_topics_without_old_demo_categories():
    categories = {entry['id']: entry for entry in taxonomy()['categories']}
    assert len(categories) == 15
    assert {'artificial_intelligence', 'cybersecurity', 'military_security'} <= categories.keys()
    assert 'knowledge_management' not in categories and 'uncategorized' not in categories
    assert taxonomy()['levels'] == 3 and taxonomy()['maxPaths'] == 3
    assert all(entry['scope'] for entry in categories.values())


def test_classification_reuses_frozen_paths_and_keeps_all_level_tags(environment):
    store, models, service, source = environment
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    existing = store.detail(source)['classificationPaths']
    source2 = publish(store, 'Another article about public archival governance.', url='https://example.org/another')
    queued = service.queue(source2, ['classification'])[0]
    with store.connect() as db:
        snapshot = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (queued['id'],)).fetchone()[0])
    assert snapshot['existingPaths'] == [{'level1Id': 'politics_governance', 'level2': '公共治理', 'level3': '档案管理'}]
    store.patch(source, {'tags': ['人工调整'], 'classificationPaths': []})
    assert execute(store, service)['status'] == 'succeeded'
    sent = json.loads(models.calls[-1][1][1]['content'])
    assert sent['existingPaths'] == snapshot['existingPaths']
    assert [node['id'] for node in store.detail(source2)['classificationPaths'][0]['nodes']] == [node['id'] for node in existing[0]['nodes']]


def test_long_classification_selects_complete_paths_with_original_evidence(environment):
    store, models, service, source = environment
    models.config['contextChars'] = 16000
    source = publish(store, ['公共部门档案治理需要保留可核查的完整来源与证据。' * 300 for _ in range(5)], url='https://example.org/classification-long')
    service.queue(source, ['classification'])
    finished = execute(store, service)
    assert finished['status'] == 'succeeded', finished['error']
    result = service.detail(source)['classification']['result']
    assert len(result['classificationPaths']) == 1
    assert [node['name'] for node in result['classificationPaths'][0]['nodes']] == ['政治与公共治理', '公共治理', '档案管理']
    assert set(result['classificationPaths'][0]['blockIds']) <= {block['id'] for block in store.detail(source)['blocks']}
    assert any(json.loads(messages[1]['content'])['task'] == 'classification_reduce' for _, messages in models.calls)
    assert all(sum(len(message['content']) for message in messages) <= config['contextChars'] for config, messages in models.calls)


def test_long_classification_reducer_cannot_invent_a_path(environment):
    store, models, service, source = environment
    models.config['contextChars'] = 16000
    source = publish(store, '公共档案记录和核验。' * 3000, url='https://example.org/classification-invalid-reduce')
    def rewrite(value):
        payload = json.loads(models.calls[-1][1][1]['content'])
        if payload['task'] == 'classification_reduce':
            value['paths'][0]['level3'] = '凭空拼接的主题'
        return value
    models.rewrite = rewrite
    service.queue(source, ['classification'])
    finished = execute(store, service)
    assert finished['status'] == 'failed' and '完整候选路径' in finished['error']
    assert store.detail(source)['classificationPaths'] == []


def test_same_block_classification_pieces_keep_actual_offsets_and_path_coverage(environment):
    store, models, service, source = environment
    models.config['contextChars'] = 16000
    text = ''.join(chr(0x4e00 + index // 1000) for index in range(70000))
    source = publish(store, text, url='https://example.org/coverage-pieces')
    raw_support = {}
    def preserve_topics(value):
        payload = json.loads(models.calls[-1][1][1]['content'])
        if payload['task'] == 'classification':
            pieces = payload['blocks']
            topic = '尾部主题' if pieces[-1]['end'] == len(text) else '主体主题'
            raw_support.setdefault(topic, {}).update({p['pieceId']: p for p in pieces})
            value['paths'][0]['level3'] = topic
            # A provider cannot manufacture coverage metadata.
            value['paths'][0]['supportChars'] = 999999999
            return value
        unique = {}
        for part in payload['parts']:
            for path in part['paths']:
                topic = path['level3']
                # Each path retains only its own original pieces, even after
                # passing through groups that also contain the other topic.
                assert path['supportChars'] <= sum(p['end'] - p['start'] for p in raw_support[topic].values())
                for evidence in path['evidence']:
                    assert evidence['excerpt'] == text[evidence['start']:evidence['start'] + len(evidence['excerpt'])]
                    assert evidence['pieceId'] in raw_support[topic]
                unique.setdefault(topic, path)
        return {**value, 'paths': [dict(path, primary=index == 0) for index, path in enumerate(unique.values())]}
    models.rewrite = preserve_topics
    service.queue(source, ['classification'])
    finished = execute(store, service)
    assert finished['status'] == 'succeeded', finished['error']
    output = service.detail(source)['classification']['result']['classificationPaths']
    assert len(output) == 2
    assert len({piece['pieceId'] for values in raw_support.values() for piece in values.values()}) >= 4
    for path in output:
        coverage = path['evidenceCoverage']
        expected = raw_support[path['nodes'][-1]['name']]
        assert coverage['isUpperBound'] is True and coverage['totalChars'] == len(text)
        assert coverage['supportChars'] == sum(p['end'] - p['start'] for p in expected.values())
        assert {piece['pieceId'] for piece in coverage['pieces']} == set(expected)
    assert any(e['start'] > 0 for _, messages in models.calls
               if (payload := json.loads(messages[1]['content']))['task'] == 'classification_reduce'
               for part in payload['parts'] for path in part['paths'] for e in path['evidence'])


def test_six_maximal_classification_candidates_fit_sixteen_thousand_char_budget(environment):
    store, models, service, source = environment
    models.config['contextChars'] = 16000
    source = publish(store, ['公共治理证据材料与原始来源需要持续核验。' * 800 for _ in range(8)],
                     url='https://example.org/maximal-path-candidates', title='题' * 1000)
    seen_six = []
    def maximal(value):
        payload = json.loads(models.calls[-1][1][1]['content'])
        if payload['task'] == 'classification':
            refs = list(dict.fromkeys(piece['blockId'] for piece in payload['blocks']))[:3]
            return {**value, 'paths': [dict(value['paths'][0], level2='主' * 31 + str(index), level3='细' * 31 + str(index),
                                   primary=index == 0, reason='具' * 300, blockIds=refs) for index in range(3)]}
        candidates = [path for part in payload['parts'] for path in part['paths']]
        seen_six.append(len(candidates))
        assert {item['id'] for item in payload['taxonomy']['categories']} == {path['level1Id'] for path in candidates}
        for path in candidates:
            assert len(path['reason']) == 300
            assert set(path['blockIds']).issubset({piece['blockId'] for piece in path['evidence']})
            assert all(piece['excerpt'] and piece['start'] < piece['end'] for piece in path['evidence'])
        unique = {}
        for path in candidates:
            unique.setdefault(path['level3'], path)
        result = {**value, 'paths': [dict(path, primary=index == 0, reason='据' * 300) for index, path in enumerate(unique.values())]}
        if '上次结果未通过程序校验' not in models.calls[-1][1][0]['content']:
            result['paths'][0]['blockIds'] = ['invalid-reduction-reference']
        return result
    models.rewrite = maximal
    service.queue(source, ['classification'])
    finished = execute(store, service)
    assert finished['status'] == 'succeeded', finished['error']
    assert 6 in seen_six
    assert all(sum(len(message['content']) for message in messages) <= 16000 for _, messages in models.calls)
    assert any('上次结果未通过程序校验' in messages[0]['content'] for _, messages in models.calls)
    assert len(service.detail(source)['classification']['result']['classificationPaths']) == 3


def test_classification_cancel_and_new_revision_leave_paths_unpublished(environment):
    store, models, service, source = environment
    job = service.queue(source, ['classification'])[0]
    models.callback = lambda: store.cancel(job['id'])
    assert execute(store, service)['status'] == 'cancelled'
    assert store.detail(source)['classificationPaths'] == []
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0


@pytest.mark.parametrize('invalid,feedback', [('piece_id', '不能使用 pieceId'), ('too_many', '最多3个')])
def test_classification_repairs_reference_contract_once_without_reusing_invalid_output(environment, invalid, feedback):
    store, models, service, source = environment
    models.config['contextChars'] = 16000
    source = publish(store, ['独立段落说明公共档案的来源保存与证据核查。' + str(index) for index in range(5)],
                     url='https://example.org/classification-repair')
    def first_invalid(value):
        if len(models.calls) == 1:
            payload = json.loads(models.calls[-1][1][1]['content'])
            value['paths'][0]['blockIds'] = ([payload['blocks'][0]['pieceId']] if invalid == 'piece_id'
                                            else [block['blockId'] for block in payload['blocks']])
            value['paths'][0]['reason'] = 'THIS_INVALID_OUTPUT_MUST_NOT_BE_REUSED'
        return value
    models.rewrite = first_invalid
    service.queue(source, ['classification'])
    finished = execute(store, service)
    assert finished['status'] == 'succeeded', finished['error']
    assert len(models.calls) == 2
    assert models.calls[0][1][1] == models.calls[1][1][1]
    assert len(models.calls[1][1]) == 2
    assert feedback in models.calls[1][1][0]['content']
    assert 'THIS_INVALID_OUTPUT_MUST_NOT_BE_REUSED' not in json.dumps(models.calls[1][1])
    telemetry = service.detail(source)['classification']['telemetry']
    assert telemetry['calls'] == 2 and telemetry['rejectedCalls'] == 1 and telemetry['validatedCalls'] == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1


def test_classification_cancellation_after_invalid_response_stops_before_repair(environment):
    store, models, service, source = environment
    queued = service.queue(source, ['classification'])[0]
    models.callback = lambda: store.cancel(queued['id'])
    models.rewrite = lambda value: {'paths': [{**value['paths'][0], 'blockIds': ['not-a-real-block']}]}
    assert execute(store, service)['status'] == 'cancelled'
    assert len(models.calls) == 1
    assert store.detail(source)['classificationPaths'] == []
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0


def test_failed_classification_repair_preserves_previous_success(environment):
    store, models, service, source = environment
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    previous = store.detail(source)['classificationPaths']
    models.rewrite = lambda value: {'paths': [{**value['paths'][0], 'blockIds': ['not-a-real-block']}]}
    service.queue(source, ['classification'], force=True)
    assert execute(store, service)['status'] == 'failed'
    stage = service.detail(source)['classification']
    assert stage['telemetry']['calls'] == 2 and stage['telemetry']['rejectedCalls'] == 2
    assert stage['previousSuccess'] is True
    assert store.detail(source)['classificationPaths'] == previous


def empty_taxonomy(monkeypatch):
    monkeypatch.setattr(enrichment_module, 'CATEGORY_NAMES', ())
    monkeypatch.setattr(enrichment_module, 'CATEGORIES', {})
    monkeypatch.setattr(enrichment_module, 'TAXONOMY_VERSION', 'test-taxonomy-empty-v2')


def test_empty_taxonomy_default_processing_keeps_other_capabilities(environment, monkeypatch):
    store, models, service, source = environment
    original = store.detail(source)
    empty_taxonomy(monkeypatch)
    jobs = service.queue(source)
    assert {job['kind'] for job in jobs} == {'ai_summary', 'ai_translation'}
    for _ in jobs:
        assert execute(store, service)['status'] == 'succeeded'
    detail = service.detail(source)
    assert detail['classificationAvailable'] is False
    assert detail['classification']['result'] is None
    assert detail['summary']['result'] and detail['translation']['result']
    assert {json.loads(messages[1]['content'])['task'] for _, messages in models.calls} == {'summary', 'translation'}
    assert store.detail(source)['tags'] == original['tags']
    assert store.detail(source)['body'] == original['body']


@pytest.mark.parametrize('kinds', [['classification'], ['summary', 'classification']])
@pytest.mark.parametrize('force', [False, True])
def test_empty_taxonomy_explicit_classification_rejects_entire_request(environment, monkeypatch, kinds, force):
    store, models, service, source = environment
    empty_taxonomy(monkeypatch)
    with pytest.raises(ValueError, match='暂无可用的分类体系'):
        service.queue(source, kinds=kinds, force=force)
    assert models.calls == []
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_runs').fetchone()[0] == 0


@pytest.mark.parametrize('only_classification', [False, True])
def test_empty_taxonomy_automatic_processing_skips_classification(environment, monkeypatch, only_classification):
    store, models, service, source = environment
    empty_taxonomy(monkeypatch)
    models.preferences['autoProcess'] = True
    if only_classification:
        models.preferences.update(summaryModelId=None, translationModelId=None)
    jobs = service.maybe_queue_auto(source)
    expected = set() if only_classification else {'ai_summary', 'ai_translation'}
    assert {job['kind'] for job in jobs} == expected
    assert models.calls == []


@pytest.mark.parametrize('recover_running', [False, True])
@pytest.mark.parametrize('remove_categories', [False, True])
def test_retired_taxonomy_rejects_old_queued_or_recovered_classification(environment, monkeypatch, recover_running, remove_categories):
    store, models, service, source = environment
    before = store.detail(source)
    queued = service.queue(source, ['classification'])[0]
    if recover_running:
        assert store.claim()['id'] == queued['id']
    if remove_categories:
        empty_taxonomy(monkeypatch)
    else:
        monkeypatch.setattr(enrichment_module, 'TAXONOMY_VERSION', 'test-taxonomy-v2')
    if recover_running:
        store.recover()
    finished = execute(store, service)
    assert finished['id'] == queued['id']
    assert finished['status'] == 'failed'
    assert models.calls == []
    assert store.detail(source)['tags'] == before['tags']
    assert service.detail(source)['classification']['result'] is None
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 0


def test_legacy_classification_without_taxonomy_snapshot_never_calls_model(environment):
    store, models, service, source = environment
    queued = service.queue(source, ['classification'])[0]
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (queued['id'],)).fetchone()[0])
        payload.pop('taxonomyVersion')
        db.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), queued['id']))
    assert execute(store, service)['status'] == 'failed'
    assert models.calls == []
    assert service.detail(source)['classification']['result'] is None


def test_taxonomy_retired_before_publication_cannot_change_document_tags(environment, monkeypatch):
    store, models, service, source = environment
    original_tags = store.detail(source)['tags']
    original_publish = service._publish
    def retire_before_publish(run, attempt, result):
        empty_taxonomy(monkeypatch)
        original_publish(run, attempt, result)
    monkeypatch.setattr(service, '_publish', retire_before_publish)
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'failed'
    assert len(models.calls) == 1
    assert store.detail(source)['tags'] == original_tags
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0


@pytest.mark.parametrize('remove_categories', [False, True])
def test_retired_taxonomy_outputs_are_hidden_but_history_and_other_results_remain(environment, monkeypatch, remove_categories):
    store, models, service, source = environment
    jobs = service.queue(source, ['summary', 'classification'])
    for _ in jobs:
        assert execute(store, service)['status'] == 'succeeded'
    before = service.detail(source)
    assert before['classification']['result']
    with store.connect() as db:
        history = [tuple(row) for row in db.execute('SELECT run_id,result_json FROM ai_outputs ORDER BY run_id')]
    if remove_categories:
        empty_taxonomy(monkeypatch)
    else:
        monkeypatch.setattr(enrichment_module, 'TAXONOMY_VERSION', 'test-taxonomy-v2')
    after = service.detail(source)
    assert after['classificationAvailable'] is (not remove_categories)
    assert after['classification']['result'] is None
    assert after['classification']['retiredResult'] is True
    assert after['summary']['result'] == before['summary']['result']
    with store.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT run_id,result_json FROM ai_outputs ORDER BY run_id')] == history


def test_generated_translation_is_simplified_but_manual_and_source_stay_untouched(environment):
    store, models, service, source = environment
    traditional = '根據批准的調整方案，月票價格維持$190不變。這份原文保留原本的字形與版本資料。'
    original = 'Under the approved adjustment, the monthly pass remains $190. The original record and version stay available.'
    source = publish(store, original, url='https://example.org/foreign-to-chinese')
    models.rewrite = lambda value: {'segments': [{**segment, 'text': traditional} for segment in value['segments']]}
    service.queue(source, ['translation']); execute(store, service)
    result = service.detail(source)['translation']['result']
    assert result['segments'][0]['text'].startswith('根据批准的调整方案，月票价格维持$190不变')
    assert result['normalizerVersion'] == 'opencc-t2s-1.4.2'
    assert store.detail(source)['blocks'][0]['text'] == original
    block_id = store.detail(source)['blocks'][0]['id']
    service.edit_translation(source, block_id, '人工確認：保留繁體字。')
    assert service.detail(source)['translation']['result']['segments'][0]['text'] == '人工確認：保留繁體字。'


def test_generated_summary_fields_use_simplified_chinese(environment):
    store, models, service, source = environment
    def traditional_summary(value):
        value['summary'] = '根據文檔記錄，知識管理保留來源與原始版本。'
        value['keyPoints'][0]['text'] = '資料與證據關聯。'
        value['keywords'] = ['知識管理', '證據']
        return value
    models.rewrite = traditional_summary
    service.queue(source, ['summary']); execute(store, service)
    result = service.detail(source)['summary']['result']
    assert result['summary'] == '根据文档记录，知识管理保留来源与原始版本。'
    assert result['keyPoints'][0]['text'] == '资料与证据关联。'
    assert result['keywords'] == ['知识管理', '证据']
    assert result['normalizerVersion'] == 'opencc-t2s-1.4.2'


def publish_table(store, rows):
    from server.parser import parse_html
    from html import escape
    table = '<table>' + ''.join('<tr>' + ''.join('<td>' + escape(cell) + '</td>' for cell in row) + '</tr>' for row in rows) + '</table>'
    raw = ('<html><head><title>Trial results</title></head><body><article><h1>Trial results</h1><p>This table preserves the number of documents in the local trial and its original column headers.</p>' + table + '</article></body></html>').encode()
    job = store.queue('file', {}, 'Trial results')
    claimed = store.claim()
    store.publish(claimed, {'url': 'https://example.org/table'}, raw, parse_html(raw))
    return store.job(job['id'])['source_id']


@pytest.mark.parametrize('omit_cell', [False, True])
def test_table_translation_program_preserves_cells_and_rejects_missing_cell(environment, omit_cell):
    store, models, service, source = environment
    original = [['Metric', 'Count'], ['Documents in this trial', '12']]
    source = publish_table(store, original)
    translated = {'Metric': '指标', 'Count': '数量', 'Documents in this trial': '本次试验中的文档', '12': '12'}
    def translate_cells(value):
        result = []
        for segment in value['segments']:
            raw_text = segment['text'].removeprefix('译文：')
            if omit_cell and raw_text == 'Count':
                continue
            result.append({**segment, 'text': translated.get(raw_text, segment['text'])})
        return {'segments': result}
    models.rewrite = translate_cells
    service.queue(source, ['translation'])
    job = execute(store, service)
    assert job['status'] == ('failed' if omit_cell else 'succeeded')
    result = service.detail(source)['translation']['result']
    if omit_cell:
        assert result is None
    else:
        table = next(segment for segment in result['segments'] if 'table' in segment)
        assert table['table'] == [['指标', '数量'], ['本次试验中的文档', '12']]
        assert table['text'] == '指标 | 数量\n本次试验中的文档 | 12'
        assert len(table['text'].splitlines()) == 2
        assert all(len(line.split(' | ')) == 2 for line in table['text'].splitlines())
    assert store.detail(source)['tables'] == [original]


def test_long_table_cells_and_empty_cells_keep_original_geometry(environment):
    store, models, service, source = environment
    original = [['Description', ''], ['Evidence stays in the original cell. ' * 200, '12']]
    source = publish_table(store, original)
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['translation']['result']
    table = next(segment['table'] for segment in result['segments'] if 'table' in segment)
    assert [len(row) for row in table] == [2, 2]
    assert table[0][1] == ''
    sent_parts = [block for _, messages in models.calls for block in json.loads(messages[1]['content'])['blocks'] if block['kind'] == 'table_cell' and block['row'] == 1 and block['column'] == 0]
    assert len(sent_parts) > 1
    # All original cell characters are sent exactly once, without truncation.
    assert ''.join(part['text'] for part in sent_parts) == original[1][0].strip()
    assert store.detail(source)['tables'][0][0][1] == ''
