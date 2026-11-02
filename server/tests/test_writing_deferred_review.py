"""The six-step flow registers references while drafting and checks them last."""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_writing_automatic import automatic, run_automatic, PROSE
from server.tests.test_writing_citation_tasks import environment
from server.tests.test_writing_image_tasks import images_env, latest, accept, PROSE as IMAGE_PROSE
from server.writing_citation_matching import (
    DEFERRED_CITATION_REVIEW_POLICY, build_matching_packet, matching_messages,
    matching_sentence_limit, matching_policy, validate_matching_result, with_selective_review_scope,
)
from server.writing_sentence_citations import valid_bindings, content_hash, strip_citations
from server.writing_images import valid_placement
from server.writing import WritingService
from server.writing_automatic_materials import RetrievalService


def test_automatic_checks_references_after_both_chapters_and_images(automatic, monkeypatch):
    store, gateway, service, project = automatic
    events = []
    original_complete, original_images = gateway.complete, service._automatic_images

    def complete(config, messages):
        events.append(json.loads(messages[-1]['content'])['task'])
        return original_complete(config, messages)

    def images(*args):
        events.append('illustrations')
        return original_images(*args)

    gateway.complete = complete
    monkeypatch.setattr(service, '_automatic_images', images)
    queued, current = run_automatic(automatic)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert events.count('writing_section') == 2
    assert events[0] == 'writing_automatic_material_queries'
    assert events.index('illustrations') > max(i for i, task in enumerate(events) if task == 'writing_section')
    assert min(i for i, task in enumerate(events) if task == 'writing_citation_match') > events.index('illustrations')
    assert all(section['citationBindingState'] == 'matched' for section in current['sections'])
    with store.connect() as db:
        rows = db.execute("SELECT kind,input_json FROM writing_runs WHERE project_id=?", (project['id'],)).fetchall()
    assert len([row for row in rows if row['kind'] == 'citation_match']) == 1
    assert all(json.loads(row['input_json']).get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY
               for row in rows if row['kind'] in ('automatic', 'section'))


def test_failed_final_batch_keeps_original_marker_and_checks_later_chapter(automatic):
    store, gateway, service, project = automatic
    original, calls = gateway.complete, 0

    def fail_once(config, messages):
        nonlocal calls
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_citation_match':
            calls += 1
            if calls == 1:
                raise RuntimeError('provider-private-detail')
        return original(config, messages)

    gateway.complete = fail_once
    queued, current = run_automatic(automatic)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert calls == 2
    first, second = current['sections']
    assert first['content'] == PROSE + '[E1]'
    assert first['citationBindings']['items'][0]['status'] == 'unreviewed'
    assert valid_bindings(first['content'], first['evidence'], first['citationBindings'], mode='strict')
    assert second['citationBindingState'] == 'matched'
    report = current['automatic']['report']
    assert any(issue['code'] == 'citation_review_incomplete' for issue in report['issues'])
    assert 'provider-private-detail' not in dumps(report)
    assert report['review']


def test_new_citation_task_freezes_selective_scope_and_full_output_capacity(environment):
    store, gateway, service, project = environment
    queued = service.queue_citation_match(project['id'], project['version'])
    with store.connect() as db:
        run = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()
        steps = db.execute('SELECT input_json FROM writing_citation_match_steps WHERE run_id=?', (queued['runId'],)).fetchall()
    frozen = json.loads(run['input_json'])
    assert frozen['maxOutputTokens'] == gateway.config['maxOutputTokens']
    assert frozen['citationReviewPolicy'] == DEFERRED_CITATION_REVIEW_POLICY
    for step in steps:
        value = json.loads(step['input_json'])
        assert value['packet']['reviewScope']['explicitCitationSentenceIds'] == ['S2']
        assert '总结' in value['messages'][0]['content'] and '常识' in value['messages'][0]['content']


def test_not_required_is_allowed_for_transition_but_not_explicit_citation():
    evidence = [{'id': 'E1', 'quote': '原始资料要求保留固定版本。'}]
    content = '下面回顾前文。原始资料要求保留固定版本。[E1]'
    packet = with_selective_review_scope(build_matching_packet(content, evidence, mode='strict'), content)
    result = {'assignments': [
        {'sentenceId': 'S1', 'evidenceIds': [], 'support': 'not_required', 'supportBasis': 'none', 'reason': '内容过渡。'},
        {'sentenceId': 'S2', 'evidenceIds': ['E1'], 'support': 'supported', 'supportBasis': 'paraphrase', 'reason': '原文明示固定版本。'},
    ]}
    checked = validate_matching_result(packet, result)
    assert checked['issues'] == []
    result['assignments'][1].update(evidenceIds=[], support='not_required', supportBasis='none')
    with pytest.raises(ValueError, match='明确标注引文'):
        validate_matching_result(packet, result)


def test_reasoning_budget_reduces_batch_size_without_mutating_config():
    low = {'maxOutputTokens': 4096, 'reasoningEffort': 'low'}
    high = {**low, 'reasoningEffort': 'high'}
    before = copy.deepcopy(high)
    assert matching_sentence_limit(high) < matching_sentence_limit(low)
    assert high == before


def test_uncited_transition_can_be_reviewed_as_not_required_without_new_evidence(environment):
    store, gateway, service, project = environment
    with store.connect() as db:
        current = service._load(db, project['id'])
        for section in current['sections']:
            section['content'] = '以下回顾前文。'
            section['evidence'] = []
        service._save(db, current, 'uncited_transition_fixture')
    current = service.get(project['id'])
    calls = []

    def transitions(config, messages):
        packet = json.loads(messages[1]['content'])
        calls.append(packet)
        assert packet['evidence'] == []
        return {'text': dumps({'assignments': [{'sentenceId': sentence['sentenceId'],
            'evidenceIds': [], 'support': 'not_required', 'supportBasis': 'none',
            'reason': '仅是内容过渡，没有新增事实或观点。'} for sentence in packet['sentences']]}), 'usage': {}}

    gateway.complete = transitions
    queued = service.queue_citation_match(current['id'], current['version'])
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(calls) == len(current['sections'])
    for section in service.get(project['id'])['sections']:
        assert section['content'] == '以下回顾前文。' and section['evidence'] == []
        assert section['citationBindingState'] == 'matched'
        assert {item['status'] for item in section['citationBindings']['items']} == {'not_required'}


def test_material_screening_freezes_queries_and_reuses_results_after_restart(automatic, monkeypatch):
    store, gateway, service, project = automatic
    calls = []
    original = RetrievalService.recommend

    def recommend(self, topic, brief, source_ids, model_id, queries):
        calls.append(copy.deepcopy(queries))
        assert queries == [project['title']]
        return original(self, topic, brief, source_ids, model_id, queries)

    monkeypatch.setattr(RetrievalService, 'recommend', recommend)
    checkpoint = service._automatic_checkpoint

    class PowerLoss(BaseException):
        pass

    def lose_power(run, job, state):
        checkpoint(run, job, state)
        if state.get('materialsReady'):
            raise PowerLoss()

    monkeypatch.setattr(service, '_automatic_checkpoint', lose_power)
    monkeypatch.setattr(service, '_automatic_finish', lambda *args, **kwargs: None)
    queued = service.queue_automatic(project['id'], project['version'])
    with pytest.raises(PowerLoss):
        service.process(store.claim())
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        state = json.loads(db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])['automatic']
        assert state['materialsReady'] and state['materialQueries'] == [project['title']]
        assert frozen['materialSelection']['confirmed']
        assert frozen['materialSelection']['userSourceIds'] == project['sourceIds']
        assert all(source_id in [source['sourceId'] for source in frozen['sources']] for source_id in project['sourceIds'])
        assert frozen['materialSelection']['passages'] == []
        assert all('blockIds' not in source for source in frozen['sources'])
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (queued['job']['id'],))
    restarted = WritingService(store, gateway)
    restarted.process(store.claim())
    current = restarted.get(project['id'])
    assert len(calls) == 1
    assert sum(task == 'writing_automatic_material_queries' for task, _, _ in gateway.generated) == 1
    assert current['automatic']['report']['summary']['generatedSections'] == 2


def test_stale_material_recommendation_preserves_user_sources_and_continues(automatic, monkeypatch):
    store, gateway, service, project = automatic

    def stale(self, topic, brief, source_ids, model_id, queries):
        return {'id': 'missing-snapshot', 'items': [{'sourceId': 'no-longer-existing', 'score': 1}], 'warnings': []}

    monkeypatch.setattr(RetrievalService, 'recommend', stale)
    queued, current = run_automatic(automatic)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['sourceIds'] == project['sourceIds']
    assert current['materialSelection']['confirmed']
    assert current['automatic']['report']['summary']['generatedSections'] == 2
    assert any(issue['code'] == 'material_selection_stale' for issue in current['automatic']['report']['issues'])


@pytest.mark.parametrize('with_picture', [False, True])
def test_citation_only_rebinding_keeps_picture_confirmation_but_prose_edit_invalidates(images_env, with_picture):
    env = images_env
    block = env.document['blocks'][0]
    with env.store.connect() as db:
        project = env.service._load(db, env.project_id)
        section = project['sections'][0]
        section['content'] = IMAGE_PROSE + '[E1]\n\n第二段说明。'
        section['evidence'] = [{'id': 'E1', 'sourceId': env.document['id'],
            'revisionId': env.document['revisionId'], 'blockId': block['id'],
            'quote': block['text'], 'start': 0, 'end': len(block['text']),
            'title': env.document['title'], 'url': 'https://example.test/image-task', 'locator': {}}]
        env.service._save(db, project, 'controlled_existing_citations')
    project = accept(env) if with_picture else latest(env)
    before = env.service.complete_illustrations(env.project_id, project['version'], skip=not with_picture)
    section = before['sections'][0]
    packet = build_matching_packet(section['content'], section['evidence'], mode='strict')
    checked = validate_matching_result(packet, {'assignments': [{
        'sentenceId': sentence['sentenceId'], 'evidenceIds': [],
        'support': 'unsupported' if index == 0 else 'not_required', 'supportBasis': 'none',
        'reason': '受控复核移除不支持的引文；正文逐字保留。'}
        for index, sentence in enumerate(packet['sentences'])]})
    after = env.service.rebind_citations(env.project_id, before['version'], [{
        'sectionId': section['id'], 'contentHash': content_hash(section['content']),
        'matches': checked['matches'], 'mode': 'strict', 'supportPolicy': matching_policy('strict')}])
    assert after['sections'][0]['content'] == IMAGE_PROSE + '\n\n第二段说明。'
    assert strip_citations(after['sections'][0]['content']) == strip_citations(section['content'])
    assert after['illustrationReview']['status'] == ('complete' if with_picture else 'skipped')
    assert after['illustrationReview']['checkedAt'] == before['illustrationReview']['checkedAt']
    assert after['sections'][0]['evidence'] == section['evidence']
    assert after['sections'][0]['manual'] == section['manual']
    if with_picture:
        previous, figure = before['illustrations'][0], after['illustrations'][0]
        assert valid_placement(after, figure)
        assert figure['status'] == 'accepted' and figure['reviewed']
        assert all(figure[key] == previous[key] for key in ('id', 'assetId', 'assetHash', 'caption', 'qualityPolicy', 'reviewMethod'))
        assert figure['anchor']['paragraphIndex'] == previous['anchor']['paragraphIndex']
        assert figure['anchor']['paragraphExact'] == IMAGE_PROSE
    historical = env.service.historical(env.project_id, before['version'])
    assert historical['sections'][0]['content'] == section['content']
    changed = env.service.save_section(env.project_id, section['id'], after['version'],
        {'content': '增加真实正文内容。' + after['sections'][0]['content']})
    assert changed['illustrationReview']['status'] == 'stale'
    if with_picture:
        assert changed['illustrations'][0]['status'] == 'stale'
