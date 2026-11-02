"""Independent source choices affect new prose while preserving old evidence."""
import copy
import json
from types import SimpleNamespace

import pytest

from server.store import dumps, ident, now
from server.tests.test_writing_automatic import automatic, PROSE
from server.tests.test_writing_integrity import add_source
from server.writing import WritingService
from server.writing_automatic_materials import RetrievalService, AUTOMATIC_SOURCE_OPTIONS_POLICY
from server.writing_citations import build_citation_plan
from server.writing_source_scope import citation_sources, preserved_citation_scope_valid


@pytest.fixture
def sources_env(automatic, monkeypatch):
    store, gateway, service, project = automatic
    source_a = project['sourceIds'][0]
    source_b = add_source(store, text='系统推荐资料也要求保留原始证据和固定版本，并记录核对依据。读者需要能够回查原文、数据口径及适用范围，修订时继续保留人工记录。',
                          url='https://example.test/recommended-writing-source')
    env = SimpleNamespace(store=store, gateway=gateway, service=service, project=project,
        source_a=source_a, source_b=source_b, searches=[], packets=[], recommended=[source_b])
    original = gateway.complete

    def complete(config, messages):
        packet = json.loads(messages[-1]['content'])
        env.packets.append(copy.deepcopy(packet))
        return original(config, messages)

    def recommend(self, topic, brief, source_ids, model_id, queries):
        env.searches.append({'seedIds': list(source_ids), 'queries': list(queries), 'modelId': model_id})
        items, versions = [], {}
        with store.connect() as db:
            for source_id in env.recommended:
                row = db.execute('SELECT s.current_revision,r.title FROM sources s JOIN revisions r ON r.id=s.current_revision WHERE s.id=?', (source_id,)).fetchone()
                versions[source_id] = row['current_revision']
                items.append({'sourceId': source_id, 'revisionId': row['current_revision'], 'title': row['title'],
                              'score': 1, 'summary': '受控推荐', 'channels': ['keyword'], 'reasons': ['关键词命中'], 'passages': []})
            result = {'id': ident('recommendation'), 'items': items, 'queries': list(queries), 'warnings': []}
            db.execute('INSERT INTO retrieval_snapshots VALUES(?,?,?)',
                       (result['id'], dumps({**result, 'selectionVersions': versions}), now()))
        return result

    gateway.complete = complete
    monkeypatch.setattr(RetrievalService, 'recommend', recommend)
    return env


def configure(env, selected, recommended, mode='strict', *, user_ids=None):
    project = env.service.get(env.project['id'])
    project = env.service.patch(project['id'], project['version'], {
        'sourceIds': [env.source_a, env.source_b],
        'brief': {**project['brief'], 'automaticOptions': {'useSelectedSources': selected,
            'useRecommendedSources': recommended, 'mode': mode, 'includeImages': False}},
        # This deliberately contains an old recommendation and a confirmed
        # decision. V2 must honor the new switches rather than preserve it.
        'materialSelection': {'recommendationId': None, 'passages': [], 'queries': ['旧检索条件'],
            'items': [{'sourceId': env.source_b, 'title': '旧推荐', 'score': 1}],
            'userSourceIds': [env.source_a] if user_ids is None else user_ids, 'confirmed': True}})
    env.project = project
    return project


def run(env):
    current = env.service.get(env.project['id'])
    queued = env.service.queue_automatic(current['id'], current['version'])
    claimed = env.store.claim()
    assert claimed['id'] == queued['job']['id']
    env.service.process(claimed)
    return queued, env.service.get(current['id'])


def saved_input(env, run_id):
    with env.store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


@pytest.mark.parametrize(('selected', 'recommended'), [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_source_switches_freeze_exact_generation_inputs(sources_env, selected, recommended, mode):
    env = sources_env
    configure(env, selected, recommended, mode)
    queued, after = run(env)
    expected = ({env.source_a} if selected else set()) | ({env.source_b} if recommended else set())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert set(after['sourceIds']) == expected
    assert {source['sourceId'] for source in after['sources']} == expected
    actual_mode = mode if selected or recommended else 'extended'
    assert after['mode'] == actual_mode
    assert after['brief']['automaticOptions']['mode'] == mode
    assert len(env.searches) == int(recommended)
    if recommended:
        assert env.searches[0]['seedIds'] == ([env.source_a] if selected else [])
    generation = [packet for packet in env.packets if packet['task'] in ('writing_outline', 'writing_section')]
    assert generation
    for packet in generation:
        assert packet['mode'] == actual_mode
        assert {source['sourceId'] for source in packet['sourceContext']} == expected
        assert {block['sourceId'] for block in packet['blocks']} <= expected
        if not expected:
            assert packet['blocks'] == packet['sourceContext'] == []
    frozen = saved_input(env, queued['runId'])
    assert frozen['automaticOptionsPolicy'] == AUTOMATIC_SOURCE_OPTIONS_POLICY
    assert frozen['automaticEffectiveMode'] == actual_mode
    assert [source['sourceId'] for source in frozen['automaticSelectedSources']] == [env.source_a]
    assert after['materialSelection']['userSourceIds'] == ([env.source_a] if selected else [])
    if not recommended:
        assert after['materialSelection']['recommendationId'] is None
        assert after['materialSelection']['items'] == after['materialSelection']['queries'] == []
        assert not any(packet['task'] == 'writing_automatic_material_queries' for packet in env.packets)


def test_explicit_empty_selected_column_does_not_fall_back_to_old_recommendations(sources_env):
    env = sources_env
    configure(env, True, False, user_ids=[])
    queued, after = run(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert after['sources'] == [] and after['mode'] == 'strict'
    assert not env.searches
    assert not any(packet['task'] in ('writing_outline', 'writing_section') for packet in env.packets)
    assert not after['automatic']['report']['outputAvailable']
    assert any(issue['code'] == 'materials_missing' for issue in after['automatic']['report']['issues'])


def test_recommendation_only_empty_result_never_retains_global_selected_sources(sources_env):
    env = sources_env
    configure(env, False, True)
    env.recommended = []
    queued, after = run(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert env.searches[0]['seedIds'] == []
    assert after['sources'] == [] and after['sourceIds'] == [] and after['mode'] == 'strict'
    assert not after['automatic']['report']['outputAvailable']
    assert any(issue['code'] == 'materials_missing' for issue in after['automatic']['report']['issues'])


@pytest.mark.parametrize(('selected', 'recommended', 'old_source'), [
    (False, True, 'source_a'), (True, False, 'source_b'), (False, False, 'source_a')])
def test_old_citation_survives_new_source_choice_and_final_review_and_export(sources_env, selected, recommended, old_source):
    env = sources_env
    project = configure(env, selected, recommended)
    project = env.service.outline(project['id'], project['version'], [
        {'title': '已写正文', 'goal': '保留既有正文', 'mode': 'inherit', 'targetWords': 100},
        {'title': '继续编写', 'goal': '仅使用本次允许的资料', 'mode': 'inherit', 'targetWords': 100}])
    source_id = getattr(env, old_source)
    original = env.store.detail(source_id)
    block = original['blocks'][0]
    with env.store.connect() as db:
        current = env.service._load(db, project['id'])
        old = current['sections'][0]
        old.update(content=PROSE + '[E1]', manual=True, locked=True,
            evidence=[{'id': 'E1', 'sourceId': source_id, 'revisionId': original['revisionId'],
                'blockId': block['id'], 'quote': block['text'], 'start': 0, 'end': len(block['text']),
                'title': original['title'], 'url': original.get('url') or 'https://example.test/source', 'locator': {}}])
        env.service._save(db, current, 'controlled_existing_citation')
    before = env.service.get(project['id'])
    original_fields = {key: copy.deepcopy(before['sections'][0][key]) for key in ('content', 'evidence', 'manual', 'locked')}
    queued, after = run(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    old = after['sections'][0]
    assert all(old[key] == value for key, value in original_fields.items())
    assert old['citationSourceScopeValid'] and old['evidenceHash'] == old['citationSourceScope']['evidenceHash']
    assert old['citationBindings']['items'][0]['status'] == 'supported'
    assert not any(issue['code'] == 'citation_out_of_scope' and issue['sectionId'] == old['id'] for issue in after['review']['issues'])
    expected = ({env.source_a} if selected else set()) | ({env.source_b} if recommended else set())
    new_packets = [packet for packet in env.packets if packet['task'] == 'writing_section']
    assert len(new_packets) == 1
    assert {block['sourceId'] for block in new_packets[0]['blocks']} <= expected
    assert {source['sourceId'] for source in new_packets[0]['sourceContext']} == expected
    assert 'citationSourceScope' not in new_packets[0]
    assert 'citationSourceScope' not in after['sections'][1]
    numbering = build_citation_plan(after, after['review'])
    assert numbering['sectionNumbers'][0]['E1'] == 1
    assert numbering['references'][0]['reference']['sourceId'] == source_id
    assert not any(item['sectionNumber'] == 1 for item in numbering['unresolved'])
    html, _ = env.service.export(project['id'])
    assert block['text'] in html


def test_archive_is_bound_to_original_chapter_registry_and_body(sources_env):
    env = sources_env
    # Run the high-value old-citation scenario to obtain a program-created scope.
    test_old_citation_survives_new_source_choice_and_final_review_and_export(env, False, True, 'source_a')
    project = env.service.get(env.project['id'])
    old = project['sections'][0]
    assert preserved_citation_scope_valid(old)
    modified = copy.deepcopy(old)
    modified['evidence'][0]['quote'] += '伪造内容'
    assert not preserved_citation_scope_valid(modified)
    assert citation_sources(project, modified) == project['sources']
    modified = copy.deepcopy(old)
    modified['content'] = '新增正文。' + modified['content']
    assert not preserved_citation_scope_valid(modified)
    copied = copy.deepcopy(old)
    copied['id'] = project['sections'][1]['id']
    assert not preserved_citation_scope_valid(copied)
    assert citation_sources(project, copied) == project['sources']


def test_source_choices_and_recommendation_checkpoint_survive_restart(sources_env, monkeypatch):
    env = sources_env
    project = configure(env, False, True)
    checkpoint = env.service._automatic_checkpoint

    class PowerLoss(BaseException):
        pass

    def stop(run, job, state):
        checkpoint(run, job, state)
        if state.get('materialsReady'):
            raise PowerLoss()

    monkeypatch.setattr(env.service, '_automatic_checkpoint', stop)
    monkeypatch.setattr(env.service, '_automatic_finish', lambda *args, **kwargs: None)
    queued = env.service.queue_automatic(project['id'], project['version'])
    with pytest.raises(PowerLoss):
        env.service.process(env.store.claim())
    env.recommended = [env.source_a]
    env.gateway.config['version'] = 9
    with env.store.connect() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (queued['job']['id'],))
    restarted = WritingService(env.store, env.gateway)
    restarted.process(env.store.claim())
    after = restarted.get(project['id'])
    assert after['sourceIds'] == [env.source_b]
    assert len(env.searches) == 1
    assert saved_input(env, queued['runId'])['automaticOptionsPolicy'] == AUTOMATIC_SOURCE_OPTIONS_POLICY
    assert all(version == 1 for _, _, version in env.gateway.generated)


def test_new_default_fields_and_legacy_shapes_are_distinguished_without_upgrading_old_options():
    requested = WritingService._automatic_requested_options
    assert requested({'brief': {'automaticOptions': {'mode': 'strict'}}}) == {
        'useSelectedSources': True, 'useRecommendedSources': True, 'mode': 'strict', 'includeImages': True}
    assert requested({'brief': {'automaticOptions': {}}}) == {'restrictToSelectedSources': False, 'includeImages': True}
    assert requested({'brief': {}}) is None
    assert requested({'brief': {'automaticOptions': {'restrictToSelectedSources': True}}}) == {
        'restrictToSelectedSources': True, 'includeImages': True}
    with pytest.raises(ValueError):
        requested({'brief': {'automaticOptions': {'useSelectedSources': True, 'restrictToSelectedSources': True}}})
