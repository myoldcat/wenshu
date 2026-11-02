"""Automatic outline citation faults stay in reports and isolate their scope.

These isolated stores and controlled semantic judgments exercise orchestration;
they do not establish support quality for a real model's proposed outline.
"""
import copy
import json

import pytest

from server.model_gateway import ModelError
from server.store import dumps
from server.tests.test_writing_automatic import automatic, run_automatic
from server.tests.test_writing_outline_planning import planning
from server.writing_outline_planning import prepare_strict_outline
from server.writing_output import AUTOMATIC_CITATION_POLICY


def alter_outline(gateway, change):
    complete = gateway.complete

    def altered(config, messages):
        response = complete(config, messages)
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_outline':
            result = json.loads(response['text'])
            change(result)
            return {**response, 'text': dumps(result)}
        return response

    gateway.complete = altered


def assert_finished(current, generated):
    assert current['automatic']['status'] == 'completed_with_issues'
    report = current['automatic']['report']
    assert report['summary']['generatedSections'] == generated
    assert {'chapters', 'images', 'review'} <= {stage['stage'] for stage in report['stages']}
    assert not any(run['status'] in ('queued', 'running') for run in current['runs'])
    return report


@pytest.mark.parametrize('fault', ['unknown_block', 'false_quote', 'invalid_item', 'duplicate'])
def test_automatic_discards_only_the_bad_planning_reference_and_reviews_remaining_scope(automatic, fault):
    _, gateway, _, _ = automatic

    def change(result):
        refs = result['sections'][0]['planningEvidence']
        bad = copy.deepcopy(refs[0])
        if fault == 'unknown_block':
            bad['blockId'] = 'O-B999'
        elif fault == 'false_quote':
            bad['quote'] = '这段原文从未提供，不能登记为来源。'
        elif fault == 'invalid_item':
            bad = '并非引用对象'
        refs.append(bad)

    alter_outline(gateway, change)
    _, current = run_automatic(automatic, legacy_outline=True)
    report = assert_finished(current, 2)
    assert len(current['sections']) == 2 and all(section['content'] for section in current['sections'])
    assert all(len(section['planningEvidence']) == 1 for section in current['sections'])
    assert all(section['planningReview']['state'] == 'supported' for section in current['sections'])
    assert any('未采用这条引用' in issue['message'] for issue in report['issues'])
    assert all('O-B999' not in dumps(section['planningEvidence']) for section in current['sections'])


@pytest.mark.parametrize('refs', [None, '不可用的引用列表', [{'blockId': 'O-B999', 'quote': '不存在的摘录'}]])
def test_automatic_unsupported_chapter_does_not_discard_other_valid_outline_chapters(automatic, refs):
    _, gateway, _, _ = automatic
    alter_outline(gateway, lambda result: result['sections'][0].update(planningEvidence=refs))
    _, current = run_automatic(automatic, legacy_outline=True)
    report = assert_finished(current, 1)
    assert [section['title'] for section in current['sections']] == ['版本保存']
    assert current['sections'][0]['content']
    assert report['summary']['skippedSections'] == 1
    assert any(issue['code'] == 'outline_narrowed' for issue in report['issues'])
    assert not any(task == 'writing_section' and title == '原始证据' for task, title, _ in gateway.generated)
    outline = next(run for run in current['runs'] if run['kind'] == 'outline')
    rejected = outline['candidate']['sections'][0]
    assert rejected['planningEvidence'] == []
    assert rejected['planningReview']['state'] == 'needs_review'


@pytest.mark.parametrize('failure', ['malformed', 'transport'])
def test_automatic_scope_review_failure_is_recorded_and_other_chapters_finish(automatic, failure):
    store, gateway, _, _ = automatic
    complete, broken = gateway.complete, False

    def malformed(config, messages):
        nonlocal broken
        packet = json.loads(messages[-1]['content'])
        response = complete(config, messages)
        if packet.get('reviewPurpose') == 'outline_scope' and not broken:
            broken = True
            if failure == 'transport':
                raise ModelError('integrity-private-token: confidential model transport details')
            return {**response, 'text': '{malformed citation review'}
        return response

    gateway.complete = malformed
    _, current = run_automatic(automatic, legacy_outline=True)
    report = assert_finished(current, 1)
    assert [section['title'] for section in current['sections']] == ['版本保存']
    outline = next(run for run in current['runs'] if run['kind'] == 'outline')
    rejected = outline['candidate']['sections'][0]
    assert rejected['planningReview']['state'] == 'needs_review'
    assert all(item['support'] == 'unsupported' and not item['evidenceIds']
               for item in rejected['planningReview']['assignments'])
    assert any('规划引用未能完成自动核对' in issue['message'] for issue in report['issues'])
    assert 'integrity-private-token' not in dumps(report) and 'confidential' not in dumps(report)
    with store.connect() as db:
        validation = 'invalid' if failure == 'malformed' else 'unavailable'
        assert db.execute('SELECT 1 FROM writing_model_interactions WHERE run_id=? AND validation_status=?',
                          (outline['id'], validation)).fetchone()


def test_malformed_outline_note_review_does_not_forge_a_resolved_scope_gap(automatic):
    _, gateway, _, _ = automatic
    alter_outline(gateway, lambda result: result.update(issues=['有待确认的规划范围缺口。']))
    complete = gateway.complete

    def malformed(config, messages):
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_outline_issue_review':
            return {'text': '{malformed note review'}
        return complete(config, messages)

    gateway.complete = malformed
    _, current = run_automatic(automatic, legacy_outline=True)
    report = current['automatic']['report']
    assert current['automatic']['status'] == 'completed_with_issues'
    assert not current['sections'] and not report['outputAvailable']
    assert any('提示未能完成自动核对' in issue['message'] for issue in report['issues'])
    assert any(stage['stage'] == 'review' for stage in report['stages'])
    outline = next(run for run in current['runs'] if run['kind'] == 'outline')
    assert all(item['disposition'] == 'blocking' for item in outline['candidate']['outlineIssueReviews'])


def test_automatic_tolerance_never_swallows_a_corrupt_program_owned_alias_map(planning):
    _, gateway, service, _, frozen = planning
    frozen = prepare_strict_outline(frozen)
    frozen['automaticCitationPolicy'] = AUTOMATIC_CITATION_POLICY
    result = gateway.outline(json.loads(frozen['messages'][1]['content']))
    frozen['citationScope']['aliases']['O-B001'] = 'invented-original-block'
    with pytest.raises(ValueError, match='映射'):
        service._validate_planned_outline(frozen, result)


@pytest.mark.parametrize('policy', [None, 'old-policy'])
def test_ordinary_and_historical_outlines_keep_their_reference_rejection_contract(planning, policy):
    _, gateway, service, _, frozen = planning
    frozen = prepare_strict_outline(frozen)
    if policy is not None:
        frozen['automaticCitationPolicy'] = policy
    result = gateway.outline(json.loads(frozen['messages'][1]['content']))
    result['sections'][0]['planningEvidence'][0]['blockId'] = 'O-B999'
    with pytest.raises(ValueError, match='规划引文编号无效'):
        service._validate_planned_outline(frozen, result)
