"""Automatic picture decisions exercise real assets and chapter vision checks.

The retrieval fixture controls discovery, while the normal placement,
versioning, quality gate, illustration save and audit paths remain active.
"""
import json

import pytest

from server.tests.test_writing_automatic import automatic, run_automatic
from server.tests.test_writing_image_tasks import images_env, latest, SECRET
from server.writing_image_tasks import QUALITY_POLICY
from server.store import dumps
from server.writing_sentence_citations import strip_citations


def finish_picture_automation(env):
    original_complete = env.gateway.complete

    def citation_review(config, messages):
        packet = json.loads(messages[-1]['content'])
        if packet.get('task') != 'writing_citation_match':
            return original_complete(config, messages)
        return {'text': dumps({'assignments': [{'sentenceId': sentence['sentenceId'],
            'evidenceIds': [], 'support': 'unsupported', 'supportBasis': 'none',
            'reason': '本测试的人工正文未登记原文摘录，事实仍须核对。'}
            for sentence in packet['sentences']]}), 'usage': {}}

    env.gateway.complete = citation_review
    before = latest(env)
    queued = env.service.queue_automatic(env.project_id, before['version'])
    claimed = env.store.claim()
    assert claimed['id'] == queued['job']['id']
    env.service.process(claimed)
    return before, latest(env), queued


def assert_manual_prose_preserved(before, current):
    assert len(current['sections']) == len(before['sections'])
    for original, reviewed in zip(before['sections'], current['sections']):
        assert strip_citations(reviewed['content']) == strip_citations(original['content'])
        assert all(reviewed[key] == original[key] for key in ('id', 'title', 'goal', 'evidence', 'manual', 'locked'))
        assert reviewed['citationBindings']['items']


def test_automatic_accepts_only_quality_checked_reference_at_recommended_position(images_env):
    env = images_env
    before, current, queued = finish_picture_automation(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert_manual_prose_preserved(before, current)
    assert len(env.searches) == len(env.gateway.calls) == 1
    assert len(current['illustrations']) == 1
    figure = current['illustrations'][0]
    assert figure['assetId'] == env.candidate['assetId']
    assert figure['anchor']['paragraphIndex'] == 1
    assert figure['sourceType'] == 'reference' and figure['qualityPolicy'] == QUALITY_POLICY
    assert figure['reviewMethod'] == 'automatic_quality_checked'
    assert not figure.get('librarySourceConfirmed') and not figure.get('imageSourceApproval')
    assert figure['originalCaption'] == figure['caption'] == '实验室中的设备'
    assert current['illustrationReview']['status'] == 'complete'
    image_run = next(item for item in current['runs'] if item['kind'] == 'images')
    candidate = image_run['candidate']['imageSearch']['candidates'][0]
    assert candidate['qualityGate']['passed'] and candidate['status'] == 'recommended'
    assert candidate['placementSuggestions'][0]['qualityPassed']
    assert candidate['placementSuggestions'][0]['status'] == 'recommended'
    report = current['automatic']['report']
    assert report['summary']['generatedSections'] == 0 and report['summary']['preservedSections'] == 1
    assert report['summary']['illustrations'] == 1
    assert SECRET not in json.dumps(report, ensure_ascii=False)
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (image_run['id'],)).fetchone()[0])
        assert frozen['searchScope'] == 'references' and frozen['placementMode'] == 'chapter'
        interactions = db.execute("SELECT kind,status,validation_status FROM writing_model_interactions WHERE kind='images'").fetchall()
        assert len(interactions) == 1 and interactions[0]['kind'] == 'images'
        assert interactions[0]['status'] == 'succeeded' and interactions[0]['validation_status'] == 'valid'


@pytest.mark.parametrize('reason', ['unreadable', 'needs_human_review'])
def test_automatic_skips_unqualified_or_human_review_pictures_and_completes_plain_text(images_env, reason):
    env = images_env
    if reason == 'unreadable':
        original_vision = env.gateway.complete_vision

        def unreadable(config, messages):
            response = original_vision(config, messages)
            result = json.loads(response['text'])
            for item in result['items']:
                for match in item.get('matches', []):
                    match['readable'] = False
            return {**response, 'text': json.dumps(result)}

        env.gateway.complete_vision = unreadable
    else:
        # A real positive picture/paragraph match does not resolve an existing
        # source/identity concern or authorize a human confirmation flag.
        env.candidate['issues'] = ['来源身份尚须人工核对。']
    before, current, queued = finish_picture_automation(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert_manual_prose_preserved(before, current)
    assert not current.get('illustrations')
    assert current['illustrationReview']['status'] == 'skipped'
    assert len(env.gateway.calls) == 1
    image_run = next(item for item in current['runs'] if item['kind'] == 'images')
    candidate = image_run['candidate']['imageSearch']['candidates'][0]
    if reason == 'unreadable':
        assert not candidate['qualityGate']['passed'] and not candidate['placementSuggestions']
    else:
        assert candidate['qualityGate']['passed'] and candidate['status'] == 'needs_review'
        assert all(item['status'] == 'needs_review' for item in candidate['placementSuggestions'])
    report = current['automatic']['report']
    assert report['outputAvailable'] and report['summary']['illustrations'] == 0
    assert any(item['stage'] == 'images' and item['status'] == 'complete' and '纯文字' in item['message']
               for item in report['stages'])
    assert not any(run['status'] in ('queued', 'running') for run in current['runs'])


def test_context_change_during_body_generation_does_not_start_semantic_matching(automatic):
    store, gateway, service, project = automatic
    original_complete = gateway.complete
    changed = False

    def change_during_body(config, messages):
        nonlocal changed
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_section' and not changed:
            changed = True
            current = service.get(project['id'])
            service.patch(project['id'], current['version'], {'title': '写作期间修改的要求'})
        return original_complete(config, messages)

    gateway.complete = change_during_body
    _, current = run_automatic(automatic)
    assert changed and current['title'] == '写作期间修改的要求'
    assert all(not section['content'] for section in current['sections'])
    first_body = next(index for index, (task, _, _) in enumerate(gateway.generated) if task == 'writing_section')
    assert not any(task == 'writing_citation_match' for task, _, _ in gateway.generated[first_body + 1:])
    assert sum(task == 'writing_section' for task, _, _ in gateway.generated) == 1
    assert any(issue['code'] == 'project_changed' for issue in current['automatic']['report']['issues'])
