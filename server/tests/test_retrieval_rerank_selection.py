"""Bounded rerank merge contracts; no live model or application state."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from server.retrieval_rerank import merge_ranked_candidates, rerank


def test_twenty_two_baseline_survive_large_raw_pool_with_two_late_core_results():
    baseline = [f'qualified-{index}' for index in range(22)]
    discoveries = [f'discovery-{index}' for index in range(101)]
    # Shape of the Hong Kong QA regression: 123 raw candidates, 22 baseline,
    # two core articles reranked to positions 25/32, and a 40-result cap.
    ranked = baseline[:20] + discoveries[:4] + [baseline[20]] + discoveries[4:10] + [baseline[21]] + discoveries[10:]
    assert len(ranked) == 123
    assert ranked.index(baseline[20]) == 24
    assert ranked.index(baseline[21]) == 31
    selected, details = merge_ranked_candidates(baseline, ranked, dict.fromkeys(ranked, .2), 0, 40)
    assert set(baseline) <= set(selected)
    assert selected == [sid for sid in ranked if sid in set(baseline) | set(ranked[:22])]
    assert len(selected) == len(set(selected)) == 24
    assert details['addedSourceIds'] == discoveries[:2]
    assert details['budgetExcludedSourceIds'] == []
    assert details['returnCount'] == 24


def test_overflow_preserves_baseline_and_admits_only_best_discoveries():
    baseline = ['a', 'b', 'c']
    ranked = ['x', 'y', 'z', 'b', 'c', 'a']
    selected, details = merge_ranked_candidates(baseline, ranked, dict.fromkeys(ranked, .2), 0, 4)
    assert selected == ['x', 'b', 'c', 'a']
    assert details['unionCandidateCount'] == 6
    assert details['budgetExcludedSourceIds'] == ['y', 'z']
    assert details['overflowPolicy'] == 'preserve-baseline-fill-rerank'


def test_full_baseline_does_not_expand_beyond_result_limit():
    selected, details = merge_ranked_candidates(['a', 'b'], ['x', 'b', 'a'], {'x': 1}, 0, 2)
    assert selected == ['b', 'a']
    assert details['addedSourceIds'] == []
    assert details['budgetExcludedSourceIds'] == ['x']


def test_new_discoveries_must_meet_minimum_fusion_score_including_boundary():
    selected, details = merge_ranked_candidates(['a', 'b'], ['low', 'at', 'above', 'b', 'a'],
        {'low': .49, 'at': .5, 'above': .7}, .5, 10)
    assert selected == ['at', 'above', 'b', 'a']
    assert details['minimumScoreExcludedSourceIds'] == ['low']


def test_merge_deduplicates_without_filling_to_result_limit():
    selected, details = merge_ranked_candidates(['a', 'a'], ['a', 'a', 'b'], {'a': .8, 'b': .7}, 0, 40)
    assert selected == ['a']
    assert details['baselineCount'] == details['returnCount'] == 1
    assert details['addedSourceIds'] == []


def test_empty_baseline_does_not_admit_raw_candidates():
    selected, details = merge_ranked_candidates([], ['x'], {'x': 1}, 0, 40)
    assert selected == []
    assert details['returnCount'] == 0


@pytest.mark.parametrize('baseline,ranked,limit', [(['a'], ['x'], 2), (['a', 'b'], ['a', 'b'], 1)])
def test_invalid_merge_inputs_raise_for_exact_baseline_fallback(baseline, ranked, limit):
    with pytest.raises(ValueError):
        merge_ranked_candidates(baseline, ranked, {}, 0, limit)


@pytest.mark.parametrize('failure', [False, True])
def test_rerank_returns_bounded_union_or_exact_baseline_on_provider_failure(monkeypatch, failure):
    baseline = ['b', 'a']
    ranked = ['new', 'a', 'b']
    sent = []

    def score(payload, **kwargs):
        sent.append(payload)
        if failure:
            raise RuntimeError('private-provider-error')
        return ranked, {'scores': 'mock-only'}, {}

    monkeypatch.setattr('server.retrieval_rerank.prepare_packet',
        lambda db, sid, *args: {'sourceId': sid, 'parts': [{'text': 'original fixture'}]})
    service = SimpleNamespace(store=SimpleNamespace(connect=lambda: nullcontext(None)),
        reranker=SimpleNamespace(score=score), operations=None)
    selected, execution, packets, fused, hits = rerank(service,
        topic='香港网络安全', queries=['香港网络安全'], documents={sid: {} for sid in ranked},
        scores={'keyword': dict.fromkeys(ranked, 1)}, vector_result={'sourceScores': {}, 'matches': {}},
        anchors={}, settings={'channels': {'keyword': True}, 'candidateLimit': 20,
            'resultLimit': 4, 'minimumScore': 0}, chosen=baseline,
        recommendation_id='test', kinds=[])
    assert len(sent) == 1
    if failure:
        assert selected == baseline
        assert execution['status'] == 'fallback'
        assert execution['returnCount'] == 2
        assert packets == [] and fused == {} and hits == {}
        assert 'private-provider-error' not in str(execution)
    else:
        assert selected == ['new', 'a', 'b']
        assert execution['status'] == 'succeeded'
        assert execution['returnCount'] == 3
        assert execution['baselineChosen'] == baseline
        assert execution['addedSourceIds'] == ['new']
        assert {packet['sourceId'] for packet in packets} == set(ranked)
