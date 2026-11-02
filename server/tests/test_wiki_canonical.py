"""Small, isolated canonical-identity fixtures; no provider or corpus processing."""
import copy

import pytest

from server.store import Store, dumps, now
from server.wiki_canonical import WikiCanonicalService


def source_page(store, number, label='知识治理', summary='知识治理保留原始证据。', kind='concept', subtype=''):
    job = store.queue('file', {}, f'样本{number}')
    claimed = store.claim()
    text = (f'样本{number}记载：知识治理保留原始证据，并允许不同来源补充。'
            '所有陈述继续关联资料版本与原始段落，可以核对适用范围、发生时间以及各自的结论差异。'
            '本段只用于隔离测试，不会触发真实资料加工或远程模型调用。')
    store.publish(claimed, {'url': f'https://example.org/canonical/{number}'}, text.encode(),
        {'title': f'样本{number}', 'body': [text], 'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': 0}],
         'language': 'zh', 'parser_version': 'canonical-fixture-1'})
    document = store.detail(store.job(job['id'])['source_id'])
    block = document['blocks'][0]
    evidence = [{'sourceId': document['id'], 'revisionId': document['revisionId'], 'blockId': block['id'],
                 'start': 0, 'end': len(text), 'quote': text, 'stale': False}]
    return {'id': f'entry_{number}', 'label': label, 'type': kind, 'subtype': subtype, 'aliases': [],
            'summary': summary, 'sourceId': document['id'], 'revisionId': document['revisionId'],
            'version': 1, 'runId': f'run_{number}', 'sections': [
                {'title': '定义与范围', 'claims': [{'text': summary, 'evidence': evidence}]}],
            'evidence': evidence, 'issues': [], 'relations': [], 'stale': False, 'archived': False}


@pytest.fixture
def environment(tmp_path):
    store = Store(tmp_path / 'isolated')
    return store, WikiCanonicalService(store)


def test_identical_fresh_concepts_merge_but_entities_and_definitions_do_not(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2, label=' 知识治理 '),
             source_page(store, 3, summary='另一种适用范围。'),
             source_page(store, 4, label='张伟', kind='entity'),
             source_page(store, 5, label='张伟', kind='entity')]
    before = copy.deepcopy(pages)
    snapshot = service.snapshot(pages)
    assert len(snapshot['groups']) == 4
    assert snapshot['entryToCanonical']['entry_1'] == snapshot['entryToCanonical']['entry_2']
    assert snapshot['entryToCanonical']['entry_4'] != snapshot['entryToCanonical']['entry_5']
    assert pages == before
    assert any(candidate['entryIds'] == ['entry_1', 'entry_3'] for candidate in snapshot['candidates'])
    assert all(group['members'] and group['sourceIds'] for group in snapshot['groups'].values())


def test_stale_concept_never_automatically_merges(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2)]
    pages[1]['stale'] = True
    assert len(service.snapshot(pages)['groups']) == 2


def test_snapshot_is_idempotent_and_restart_preserves_identity(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2)]
    first = service.snapshot(pages)
    assert service.snapshot(pages) == first
    restarted = WikiCanonicalService(Store(store.root))
    assert restarted.snapshot(pages) == first
    # Input array order is irrelevant to input identity and program IDs.
    assert restarted.snapshot(list(reversed(pages))) == first


def test_explicit_rejection_survives_rebuild_and_undo_restores_group(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2)]
    first = service.snapshot(pages)
    rejected = service.reject(['entry_1', 'entry_2'], first['snapshotId'], pages, '不同范围')
    assert len(rejected['groups']) == 2
    assert rejected['rejectedPairs'] == [['entry_1', 'entry_2']]
    assert not rejected['candidates']
    pages[1]['version'] += 1
    pages[1]['runId'] = 'run_2_new'
    rebuilt = service.snapshot(pages)
    assert len(rebuilt['groups']) == 2
    restored = service.undo(rejected['decisionId'], rebuilt['snapshotId'], pages)
    assert len(restored['groups']) == 1
    assert next(iter(restored['groups'])) == next(iter(first['groups']))
    assert next(item for item in service.decisions() if item['id'] == rejected['decisionId'])['undone']
    with pytest.raises(ValueError, match='已经撤销'):
        service.undo(rejected['decisionId'], restored['snapshotId'], pages)


def test_manual_merge_name_history_and_undo(environment):
    store, service = environment
    pages = [source_page(store, 1, label='LLM'), source_page(store, 2, label='大语言模型', summary='语言模型的一种说明。')]
    first = service.snapshot(pages)
    original_ids = set(first['groups'])
    merged = service.merge(['entry_1', 'entry_2'], '大语言模型', first['snapshotId'], pages, '人工确认同义词')
    canonical_id = next(iter(merged['groups']))
    assert merged['groups'][canonical_id]['label'] == '大语言模型'
    assert merged['groups'][canonical_id]['manual']
    old_version = merged['groups'][canonical_id]['version']
    old = service.page(canonical_id, pages, old_version)
    pages[0]['label'] = '新来源标题'
    pages[0]['version'] = 2
    changed = service.snapshot(pages)
    assert changed['groups'][canonical_id]['label'] == '大语言模型'
    assert service.page(canonical_id, pages, old_version)['members'] == old['members']
    restored = service.undo(merged['decisionId'], changed['snapshotId'], pages)
    assert set(restored['groups']) == original_ids
    assert pages[0]['label'] == '新来源标题'


def test_manual_pair_decision_overrides_automatic_and_latest_explicit_choice_wins(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2), source_page(store, 3)]
    first = service.snapshot(pages)
    rejected = service.reject(['entry_1', 'entry_2'], first['snapshotId'], pages)
    assert rejected['entryToCanonical']['entry_1'] != rejected['entryToCanonical']['entry_2']
    merged = service.merge(['entry_1', 'entry_2'], '人工确认', rejected['snapshotId'], pages)
    assert len(merged['groups']) == 1
    assert not merged['rejectedPairs']
    undone = service.undo(merged['decisionId'], merged['snapshotId'], pages)
    assert undone['entryToCanonical']['entry_1'] != undone['entryToCanonical']['entry_2']


def test_old_snapshot_cannot_apply_decision(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2, summary='不同定义。')]
    first = service.snapshot(pages)
    service.merge(['entry_1', 'entry_2'], '知识治理', first['snapshotId'], pages)
    with pytest.raises(ValueError, match='快照已更新'):
        service.reject(['entry_1', 'entry_2'], first['snapshotId'], pages)
    assert len([item for item in service.decisions() if item['kind'] == 'merge']) == 1


def test_types_cannot_merge_and_validation_is_atomic(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2, kind='entity')]
    first = service.snapshot(pages)
    with pytest.raises(ValueError, match='不同类型'):
        service.merge(['entry_1', 'entry_2'], '错误合并', first['snapshotId'], pages)
    assert service.snapshot(pages) == first
    assert not service.decisions()


def test_incremental_contribution_replaces_removed_claim_and_preserves_history(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2)]
    first = service.snapshot(pages)
    canonical_id = next(iter(first['groups']))
    original_version = first['groups'][canonical_id]['version']
    removed_source = pages[1]['sourceId']
    updated = service.snapshot(pages[:1])
    assert next(iter(updated['groups'])) == canonical_id
    group = updated['groups'][canonical_id]
    assert group['entryIds'] == ['entry_1']
    assert all(section['sourceId'] != removed_source for section in group['synthesis']['sections'])
    assert len(service.page(canonical_id, pages[:1], original_version)['members']) == 2


def test_changed_definition_splits_auto_group_without_reassigning_unchanged_identity(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2)]
    first = service.snapshot(pages)
    original_id = first['entryToCanonical']['entry_2']
    pages[0]['summary'] = '新版本明确改用另一种定义。'
    pages[0]['sections'][0]['claims'][0]['text'] = pages[0]['summary']
    pages[0]['version'] += 1
    changed = service.snapshot(pages)
    assert len(changed['groups']) == 2
    assert changed['entryToCanonical']['entry_2'] == original_id
    assert changed['entryToCanonical']['entry_1'] != original_id


def test_synthesis_is_grounded_versioned_and_fts_published_atomically(environment):
    store, service = environment
    pages = [source_page(store, 1), source_page(store, 2)]
    first = service.snapshot(pages)
    canonical_id = next(iter(first['groups']))
    frozen = service.prepare_synthesis(canonical_id, pages)
    content = {'sections': [{'title': '综合', 'claims': [{'text': '保留证据并比较来源。', 'evidence': frozen['evidence']}]}], 'issues': []}
    published = service.publish_synthesis(canonical_id, frozen, content, pages)
    assert published['version'] == 1
    current = service.page(canonical_id, pages)
    actual_sections = current['synthesis']['sections']
    assert len(actual_sections) == 1
    assert actual_sections[0]['title'] == '综合'
    actual_claim = actual_sections[0]['claims'][0]
    assert actual_claim['text'] == content['sections'][0]['claims'][0]['text']
    assert [{key: value for key, value in reference.items() if key != 'stale'}
            for reference in actual_claim['evidence']] == frozen['evidence']
    assert all(reference['stale'] is False for reference in actual_claim['evidence'])
    assert current['version'] == 2
    assert service.page(canonical_id, pages, 1)['synthesis']['mode'] == 'source_claims'
    with store.connect() as db:
        row = db.execute('SELECT body_tokens FROM wiki_canonical_search_index WHERE canonical_id=?', (canonical_id,)).fetchone()
    assert '比较' in row[0]
    assert service.get_synthesis(canonical_id, pages)['sections'] == content['sections']


def test_synthesis_input_and_publication_cas_prevent_parallel_overwrite(environment):
    store, service = environment
    pages = [source_page(store, 1)]
    canonical_id = next(iter(service.snapshot(pages)['groups']))
    frozen = service.prepare_synthesis(canonical_id, pages)
    content = {'sections': frozen['sections'], 'issues': []}
    service.publish_synthesis(canonical_id, frozen, content, pages)
    with pytest.raises(ValueError, match='已有更新'):
        service.publish_synthesis(canonical_id, frozen, content, pages)
    newer = service.prepare_synthesis(canonical_id, pages)
    pages[0]['version'] = 2
    with pytest.raises(ValueError, match='快照已更新'):
        service.publish_synthesis(canonical_id, newer, content, pages)
    assert service.get_synthesis(canonical_id, pages)['mode'] == 'source_claims'


@pytest.mark.parametrize('damage', ['invented_quote', 'invented_offset', 'empty_claim_evidence', 'stale_source', 'edited_frozen_registry'])
def test_invalid_synthesis_evidence_cannot_publish(environment, damage):
    store, service = environment
    pages = [source_page(store, 1)]
    canonical_id = next(iter(service.snapshot(pages)['groups']))
    frozen = service.prepare_synthesis(canonical_id, pages)
    content = {'sections': [{'title': '综合', 'claims': [{'text': '记录原始证据。', 'evidence': copy.deepcopy(frozen['evidence'])}]}]}
    reference = content['sections'][0]['claims'][0]['evidence'][0]
    if damage == 'invented_quote':
        reference['quote'] = '模型编造的原文'
    elif damage == 'invented_offset':
        reference['start'] += 1
    elif damage == 'empty_claim_evidence':
        content['sections'][0]['claims'][0]['evidence'] = []
    elif damage == 'stale_source':
        with store.connect() as db:
            db.execute('UPDATE sources SET current_revision=NULL WHERE id=?', (pages[0]['sourceId'],))
    else:
        reference['quote'] = '编造引用'
        frozen['evidence'] = [copy.deepcopy(reference)]
    with pytest.raises(ValueError):
        service.publish_synthesis(canonical_id, frozen, content, pages)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_canonical_syntheses').fetchone()[0] == 0


def test_cancelled_synthesis_job_cannot_publish(environment):
    store, service = environment
    pages = [source_page(store, 1)]
    canonical_id = next(iter(service.snapshot(pages)['groups']))
    frozen = service.prepare_synthesis(canonical_id, pages)
    job = store.queue('wiki_synthesize', {}, '隔离测试')
    content = {'sections': frozen['sections']}
    with pytest.raises(ValueError, match='取消或已被替代'):
        service.publish_synthesis(canonical_id, frozen, content, pages, job_id=job['id'], attempt=1)


def test_snapshot_can_share_callers_transaction(environment):
    store, service = environment
    pages = [source_page(store, 1)]
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        first = service.snapshot(pages, db=db)
        assert service.snapshot(pages, db=db) == first


def test_real_publication_input_ignores_navigation_decoration_and_checks_live_version(environment):
    from server.wiki import SCHEMA as WIKI_SCHEMA
    store, service = environment
    page = source_page(store, 1)
    with store.connect() as db:
        db.executescript(WIKI_SCHEMA)
        ingest_job = db.execute('SELECT id FROM jobs WHERE source_id=?', (page['sourceId'],)).fetchone()[0]
        db.execute('''INSERT INTO wiki_runs(id,job_id,source_id,revision_id,model_id,model_version,
            model_json,prompt_version,fingerprint,created_at) VALUES(?,?,?,?,?,1,?,?,?,?)''',
            (page['runId'], ingest_job, page['sourceId'], page['revisionId'], 'mock', '{}', 'fixture', 'fixture', now()))
        db.execute('INSERT INTO wiki_entries VALUES(?,?,?,?)', (page['id'], page['sourceId'], 'fixture', now()))
        db.execute('INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?)',
            (page['id'], page['version'], page['runId'], page['sourceId'], page['revisionId'], dumps(page), now()))
        db.execute('INSERT INTO wiki_publications VALUES(?,?,?)', (page['sourceId'], page['runId'], now()))
    original = service.snapshot([page])
    decorated = copy.deepcopy(page)
    decorated['issues'].append('其他来源存在同名词条，点击导航查看。')
    decorated['navigationGroups'] = [{'id': 'ui-only'}]
    decorated['relations'].append({'targetId': 'navigation-only'})
    assert service.snapshot([decorated]) == original
    decorated['version'] = 2
    with pytest.raises(ValueError, match='来源版本已更新'):
        service.snapshot([decorated])
    with store.connect() as db:
        db.execute('UPDATE sources SET current_revision=NULL WHERE id=?', (page['sourceId'],))
    assert next(iter(service.snapshot([page])['groups'].values()))['stale']


def test_old_identity_retains_history_and_redirect_after_manual_merge(environment):
    store, service = environment
    pages = [source_page(store, 1, label='LLM'), source_page(store, 2, label='大语言模型')]
    first = service.snapshot(pages)
    old_id = first['entryToCanonical']['entry_2']
    merged = service.merge(['entry_1', 'entry_2'], '大语言模型', first['snapshotId'], pages)
    old = service.page(old_id, pages)
    assert old['archived']
    assert old['redirectId'] == merged['entryToCanonical']['entry_2']
