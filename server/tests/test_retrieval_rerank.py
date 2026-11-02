"""Optional ranking contracts with isolated originals and real mock HTTP IO."""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.retrieval import RetrievalService
from server.retrieval_rerank import (CHANGED_WARNING, FALLBACK_WARNING, MODEL_REVISION,
                                     TEMPLATE_VERSION, RerankClient)
from server.retrieval_rerank_packets import prepare_packet
from server.tests.test_retrieval import bench, publish
from server.writing_operations import WritingOperations


def fixture_sources(bench):
    store, _, service, _, _ = bench
    first = publish(store, ['香港网络安全要求保留明确的本地事件记录和原文证据。'],
                    title='香港网络安全治理', url='https://example.org/rerank/local')
    second = publish(store, ['全球网络威胁概览整理各地攻击方式和技术活动。'],
                     title='全球网络威胁概览', url='https://example.org/rerank/global')
    original = service._lexical

    def lexical(*args, **kwargs):
        values = original(*args, **kwargs)
        # Positive discovery alone must not bypass the existing topic gate.
        values['keyword'][second] = .01
        return values

    service._lexical = lexical
    return first, second


def provider(service, score_by_id=None, rewrite=None, callback=None):
    calls = []

    def handle(request):
        assert str(request.url) == 'http://127.0.0.1:8788/v1/rerank'
        body = json.loads(request.content)
        calls.append(copy.deepcopy(body))
        if callback:
            callback(body)
        result = {'modelRevision': MODEL_REVISION, 'templateVersion': TEMPLATE_VERSION,
                  'scores': [{'id': row['id'], 'score': (score_by_id or {}).get(row['id'], .2),
                              'logitDiff': (score_by_id or {}).get(row['id'], .2),
                              'inputTokens': 100, 'truncated': False}
                             for row in reversed(body['documents'])],
                  'coldStart': False, 'timing': {'totalSeconds': .01}}
        if rewrite:
            result = rewrite(result)
        return httpx.Response(200, json=result)

    service.reranker = RerankClient(transport=httpx.MockTransport(handle))
    return calls


def snapshot(store, result):
    with store.connect() as db:
        return json.loads(db.execute('SELECT content_json FROM retrieval_snapshots WHERE id=?',
                                     (result['id'],)).fetchone()[0])


def test_explicit_off_never_calls_reranker_and_keeps_original_order(bench):
    _, _, service, _, _ = bench
    first, _ = fixture_sources(bench)
    calls = provider(service)
    assert service.get_settings()['rerankEnabled'] is False
    result = service.recommend('香港网络安全')
    assert [row['sourceId'] for row in result['items']] == [first]
    assert result['rerank'] == {'enabled': False, 'status': 'disabled'}
    assert calls == []


def test_unsaved_setting_defaults_on_and_manual_off_persists(bench):
    store, models, service, _, _ = bench
    first, _ = fixture_sources(bench)
    with store.connect() as db:
        db.execute("DELETE FROM retrieval_settings WHERE key='settings'")
    assert service.get_settings()['rerankEnabled'] is True
    with store.connect() as db:
        db.execute("INSERT INTO retrieval_settings VALUES('settings',?)", (json.dumps({'queryExpansion': False}),))
    assert service.get_settings()['rerankEnabled'] is True
    calls = provider(service)
    result = service.recommend('香港网络安全', queries=['香港网络安全'])
    assert result['rerank'] == {'enabled': True, 'status': 'succeeded'}
    assert len(calls) == 1
    service.save_settings({'rerankEnabled': False})
    assert RetrievalService(store, models).get_settings()['rerankEnabled'] is False
    disabled = service.recommend('香港网络安全', queries=['香港网络安全'])
    assert [item['sourceId'] for item in disabled['items']] == [first]
    assert disabled['rerank'] == {'enabled': False, 'status': 'disabled'}
    assert len(calls) == 1


def test_raw_candidate_rescued_without_probability_threshold_and_exact_originals(bench):
    store, _, service, _, _ = bench
    first, second = fixture_sources(bench)
    baseline = service.recommend('香港网络安全')
    assert [row['sourceId'] for row in baseline['items']] == [first]
    calls = provider(service, {first: .00001, second: .00002})
    service.save_settings({'rerankEnabled': True})
    result = service.recommend('香港网络安全', brief={'purpose': 'do-not-send-purpose'})
    assert result['rerank'] == {'enabled': True, 'status': 'succeeded'}
    assert [row['sourceId'] for row in result['items']] == [second, first]
    assert len(result['items']) == len(baseline['items']) + 1
    assert {row['id'] for row in calls[0]['documents']} == {first, second}
    assert 'do-not-send-purpose' not in json.dumps(calls)
    actual = store.detail(second)
    originals = {row['id']: row['text'] for row in actual['blocks']}
    assert result['items'][0]['revisionId'] == actual['revisionId']
    assert all(part['quote'] in originals[part['blockId']] for part in result['items'][0]['passages'])
    assert 'rerankExecution' not in result
    execution = snapshot(store, result)['rerankExecution']
    assert execution['baselineChosen'] == [first]
    assert execution['selectionPolicy'] == 'baseline-preserving-bounded-union-v1'
    assert execution['addedSourceIds'] == [second]
    assert execution['returnCount'] == 2
    assert execution['modelRevision'] == MODEL_REVISION
    assert execution['scoreCache'] is False
    for packet in execution['packets']:
        detail = store.detail(packet['sourceId'])
        blocks = {row['id']: row['text'] for row in detail['blocks']}
        for span in packet['originalSpans']:
            assert blocks[span['blockId']][span['start']:span['end']] == span['quote']


def test_empty_or_minimum_score_rejected_results_skip_model(bench):
    _, _, service, _, _ = bench
    fixture_sources(bench)
    calls = provider(service)
    service.save_settings({'rerankEnabled': True})
    result = service.recommend('南极深冰物理')
    assert result['items'] == []
    assert result['rerank']['status'] == 'skipped'
    assert calls == []
    service.save_settings({'minimumScore': 1})
    result = service.recommend('香港网络安全')
    assert result['items'] == []
    assert calls == []


def test_successful_rerank_cannot_replace_baseline_when_result_limit_is_full(bench):
    store, _, service, _, _ = bench
    first, second = fixture_sources(bench)
    provider(service, {first: .01, second: .99})
    service.save_settings({'rerankEnabled': True, 'resultLimit': 1})
    result = service.recommend('香港网络安全')
    assert result['rerank']['status'] == 'succeeded'
    assert [row['sourceId'] for row in result['items']] == [first]
    execution = snapshot(store, result)['rerankExecution']
    assert execution['addedSourceIds'] == []
    assert execution['budgetExcludedSourceIds'] == [second]
    assert execution['returnCount'] == 1


def test_successful_rerank_does_not_admit_discovery_below_minimum_fusion_score(bench):
    store, _, service, _, _ = bench
    first, second = fixture_sources(bench)
    provider(service, {first: .01, second: .99})
    service.save_settings({'rerankEnabled': True, 'minimumScore': .96,
        'channels': {key: key == 'keyword' for key in service.get_settings()['channels']}})
    result = service.recommend('香港网络安全')
    assert result['rerank']['status'] == 'succeeded'
    assert [row['sourceId'] for row in result['items']] == [first]
    execution = snapshot(store, result)['rerankExecution']
    assert execution['minimumScoreExcludedSourceIds'] == [second]
    assert execution['addedSourceIds'] == []


@pytest.mark.parametrize('rewrite', [
    lambda row: dict(row, scores=[]),
    lambda row: dict(row, modelRevision='another-model-version'),
    lambda row: dict(row, templateVersion='another-template'),
    lambda row: dict(row, scores=[row['scores'][0], row['scores'][0]]),
    lambda row: dict(row, scores=[dict(value, score=True) for value in row['scores']]),
    lambda row: dict(row, scores=[dict(value, score=-1) for value in row['scores']]),
    lambda row: dict(row, scores=[dict(value, logitDiff='0.1') for value in row['scores']]),
    lambda row: dict(row, scores=[dict(value, inputTokens=4097) for value in row['scores']]),
    lambda row: dict(row, scores=[dict(value, truncated='false') for value in row['scores']]),
])
def test_invalid_provider_response_restores_exact_baseline(bench, rewrite):
    store, _, service, _, _ = bench
    fixture_sources(bench)
    baseline = service.recommend('香港网络安全')
    calls = provider(service, rewrite=rewrite)
    service.save_settings({'rerankEnabled': True})
    result = service.recommend('香港网络安全')
    assert result['items'] == baseline['items']
    assert result['rerank']['status'] == 'fallback'
    assert result['warnings'] == [FALLBACK_WARNING]
    assert len(calls) == 1
    assert snapshot(store, result)['rerankExecution']['status'] == 'fallback'


def test_http_failure_keeps_actual_response_only_in_operations(bench):
    store, _, service, _, _ = bench
    fixture_sources(bench)
    service.operations = WritingOperations(store)
    service.reranker = RerankClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, json={'error': 'private-provider-trace'})))
    baseline = service.recommend('香港网络安全')
    service.save_settings({'rerankEnabled': True})
    result = service.recommend('香港网络安全')
    assert result['items'] == baseline['items']
    assert 'private-provider-trace' not in json.dumps(result)
    with store.connect() as db:
        row = db.execute('SELECT * FROM writing_material_interactions WHERE recommendation_id=?',
                         (result['id'],)).fetchone()
    assert row['status'] == 'failed'
    assert row['validation_status'] == 'unavailable'
    assert row['http_status'] == 503
    assert 'private-provider-trace' in row['response_raw']
    assert 'documents' in json.loads(row['request_json'])
    assert row['transport'] == 'rerank_http'
    assert json.loads(row['model_json'])['phase'] == 'rerank'


def test_no_score_cache_and_effective_summary_input_changes_are_bound(bench):
    store, _, service, _, _ = bench
    first, second = fixture_sources(bench)
    calls = provider(service, {first: .9, second: .1})
    service.operations = WritingOperations(store)
    service.save_settings({'rerankEnabled': True})
    one = service.recommend('香港网络安全')
    service.recommend('香港网络安全')
    revision = store.detail(second)['revisionId']
    with store.connect() as db:
        db.execute('UPDATE revisions SET summary=? WHERE id=?', ('新摘要，仅作发现索引。', revision))
    three = service.recommend('香港网络安全')
    assert len(calls) == 3
    assert calls[0] == calls[1]
    assert calls[1] != calls[2]
    before = {row['sourceId']: row for row in snapshot(store, one)['rerankExecution']['packets']}
    after = {row['sourceId']: row for row in snapshot(store, three)['rerankExecution']['packets']}
    assert before[second]['dependencyHash'] != after[second]['dependencyHash']
    with store.connect() as db:
        rows = db.execute('SELECT * FROM writing_material_interactions').fetchall()
    assert len(rows) == 3
    assert all(row['status'] == 'succeeded' and row['validation_status'] == 'valid' for row in rows)


def test_metadata_changed_during_model_call_discards_ranking(bench):
    store, _, service, _, _ = bench
    first, second = fixture_sources(bench)
    baseline = service.recommend('香港网络安全')
    revision = store.detail(second)['revisionId']

    def change(body):
        with store.connect() as db:
            db.execute('UPDATE revisions SET summary=? WHERE id=?', ('排序期间摘要已修改。', revision))

    provider(service, {first: .1, second: .9}, callback=change)
    service.save_settings({'rerankEnabled': True})
    result = service.recommend('香港网络安全')
    assert result['items'] == baseline['items']
    assert result['rerank']['status'] == 'fallback'
    assert CHANGED_WARNING in result['warnings']
    assert snapshot(store, result)['rerankExecution']['failure'] == 'input-dependencies-changed'


def test_candidate_budget_preserves_baseline_and_reports_trimmed_union(bench):
    store, _, service, _, _ = bench
    relevant = [publish(store, ['香港网络安全治理记录本地事件与明确政策。'],
                        url=f'https://example.org/budget/{index}') for index in range(23)]
    service.save_settings({'candidateLimit': 20, 'resultLimit': 2})
    baseline = service.recommend('香港网络安全')
    original = service._lexical

    def discovery(*args, **kwargs):
        values = original(*args, **kwargs)
        values['summary'].update({sid: .01 for sid in relevant})
        return values

    service._lexical = discovery
    calls = provider(service)
    service.save_settings({'rerankEnabled': True})
    result = service.recommend('香港网络安全')
    execution = snapshot(store, result)['rerankExecution']
    assert len(calls[0]['documents']) == 20
    assert {row['sourceId'] for row in baseline['items']} <= {row['id'] for row in calls[0]['documents']}
    assert execution['rawCandidateCount'] == 23
    assert execution['trimmedCandidateCount'] == 3


@pytest.mark.parametrize('url', ['https://outside.example', 'http://127.0.0.1:8788/private',
                                'http://user:pass@127.0.0.1:8788', 'http://127.0.0.1:8788?secret=x'])
def test_service_endpoint_must_be_fixed_local_root(bench, url):
    _, _, service, _, _ = bench
    fixture_sources(bench)
    calls = []
    service.reranker = RerankClient(url, transport=httpx.MockTransport(lambda req: calls.append(req)))
    service.save_settings({'rerankEnabled': True})
    result = service.recommend('香港网络安全')
    assert result['rerank']['status'] == 'fallback'
    assert calls == []


def test_setting_persists_and_strict_api_rejects_null_and_non_boolean(bench):
    store, models, service, _, _ = bench
    assert service.save_settings({'rerankEnabled': True})['rerankEnabled'] is True
    assert RetrievalService(store, models).get_settings()['rerankEnabled'] is True
    with pytest.raises(ValueError):
        service.save_settings({'rerankEnabled': 1})
    app = create_app(data_dir=store.root, model_service=models)
    with TestClient(app) as client:
        assert client.get('/api/retrieval/settings').json()['settings']['rerankEnabled'] is True
        for value in ('false', 1, None):
            response = client.put('/api/retrieval/settings', headers={'X-Wenshu-Client': 'local'},
                                  json={'rerankEnabled': value})
            assert response.status_code in (400, 422)
        result = client.put('/api/retrieval/settings', headers={'X-Wenshu-Client': 'local'},
                            json={'rerankEnabled': False})
        assert result.status_code == 200
        assert result.json()['settings']['rerankEnabled'] is False


def test_short_dom_fragments_group_without_losing_exact_spans(bench):
    store, _, service, _, _ = bench
    source = publish(store, [f'原文第{index}段保留。' for index in range(150)],
                     title='香港网络安全', url='https://example.org/dom')
    detail = store.detail(source)
    doc = {'revisionId': detail['revisionId'], 'title': detail['title'],
           'originalTitle': detail['title'], 'summary': ''}
    with store.connect() as db:
        packet = prepare_packet(db, source, doc, '香港网络安全', ['香港网络安全'], [], [])
    assert packet['shortFullText'] is True
    assert len(packet['parts']) < 8
    assert len(packet['originalSpans']) == 150
    blocks = {row['id']: row['text'] for row in detail['blocks']}
    assert all(blocks[span['blockId']][span['start']:span['end']] == span['quote']
               for span in packet['originalSpans'])
