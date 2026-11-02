"""Metadata saves preserve old pins without granting recommendation exemptions."""
import copy

import pytest

from server.tests.test_retrieval_integrity import retrieval, original, narrow
from server.writing import WritingService


@pytest.mark.parametrize('explicit_empty_owner', [False, True], ids=['legacy-owner-absent', 'explicit-recommended-only'])
def test_query_metadata_after_revision_advance_preserves_fixed_scope_and_later_range_checks(retrieval, explicit_empty_owner):
    store, provider, models, retrieval_service, _ = retrieval
    source, revision, blocks = original(store)
    recommendation = retrieval_service.recommend('人工智能治理')
    item = next(item for item in recommendation['items'] if item['sourceId'] == source)
    selection = {'recommendationId': recommendation['id'], 'passages': [narrow(item)]}
    if explicit_empty_owner:
        selection['userSourceIds'] = []
    writing = WritingService(store, models)
    project = writing.create({'title': '固定原文与查询条件', 'sourceIds': [source], 'materialSelection': selection})
    original(store, 'advanced-for-query-metadata')
    history = writing.history(project['id'])
    no_change = writing.patch(project['id'], project['version'], {
        'sourceIds': [source, source], 'materialSelection': copy.deepcopy(project['materialSelection'])})
    assert no_change['version'] == project['version']
    assert writing.history(project['id']) == history
    assert no_change['sources'] == project['sources']
    metadata = {**project['materialSelection'], 'queries': ['仅调整检索条件'], 'items': []}
    calls = len(provider.requests)
    changed = writing.patch(project['id'], project['version'], {'materialSelection': metadata})
    assert changed['sources'] == project['sources']
    assert changed['sources'][0]['revisionId'] == revision
    assert changed['materialSelection']['recommendationId'] == recommendation['id']
    assert 'body' not in changed['workflowPendingSteps']
    assert len(provider.requests) == calls
    if explicit_empty_owner:
        assert changed['materialSelection']['userSourceIds'] == []
    else:
        assert 'userSourceIds' not in changed['materialSelection']
    other = next(block for block in blocks if block['id'] != selection['passages'][0]['blockId'])
    new_scope = {**changed['materialSelection'], 'passages': [
        {'sourceId': source, 'revisionId': revision, 'blockId': other['id']}]}
    with pytest.raises(ValueError, match='版本已变化'):
        writing.patch(project['id'], changed['version'], {'materialSelection': new_scope})
    assert writing.get(project['id'])['version'] == changed['version']
    assert writing.get(project['id'])['sources'] == project['sources']


def test_new_recommendation_after_metadata_save_still_cannot_replace_fixed_revision(retrieval):
    store, _, models, retrieval_service, _ = retrieval
    source, revision, _ = original(store)
    writing = WritingService(store, models)
    project = writing.create({'title': '旧版本保持不动', 'sourceIds': [source]})
    metadata = {**project['materialSelection'], 'queries': ['只保存查询']}
    project = writing.patch(project['id'], project['version'], {'materialSelection': metadata})
    assert 'userSourceIds' not in project['materialSelection']
    original(store, 'advanced-before-new-recommendation')
    recommendation = retrieval_service.recommend('人工智能治理', source_ids=[source])
    with pytest.raises(ValueError, match='固定版本'):
        writing.patch(project['id'], project['version'], {'sourceIds': [source],
            'materialSelection': {'recommendationId': recommendation['id'], 'passages': []}})
    assert writing.get(project['id'])['sources'][0]['revisionId'] == revision
