"""Future-run choices keep only already checked, unchanged old illustrations."""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_writing_image_tasks import images_env, accept, latest
from server.writing_images import (valid_placement, preserve_approved_illustrations,
                                   reanchor_citation_only_images, refresh_illustrations)


def approved(env):
    project = accept(env)
    figure = project['illustrations'][0]
    project = env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'locked': True})
    return env.service.complete_illustrations(env.project_id, project['version'])


def choices(project, *, purpose=None):
    brief = {**project['brief'], 'automation': 'automatic', 'automaticOptions': {
        'useSelectedSources': False, 'useRecommendedSources': False, 'mode': 'strict', 'includeImages': False}}
    if purpose is not None:
        brief['purpose'] = purpose
    return {'brief': brief, 'mode': 'extended'}


def test_options_only_save_then_no_sources_skipped_images_still_export_old_approved_picture(images_env):
    env = images_env
    before = approved(env)
    image = copy.deepcopy(before['illustrations'][0])
    texts = [(s['content'], s['evidence'], s['locked']) for s in before['sections']]
    project = env.service.patch(env.project_id, before['version'], choices(before))
    assert project['illustrations'][0]['status'] == 'accepted'
    assert project['illustrations'][0]['reviewed'] and project['illustrations'][0]['locked']
    assert valid_placement(project, project['illustrations'][0])
    assert project['illustrationReview']['status'] == 'complete'
    calls = []

    def check_only(config, messages):
        packet = json.loads(messages[-1]['content'])
        calls.append(packet['task'])
        assert packet['task'] == 'writing_citation_match'
        return {'text': dumps({'assignments': [{'sentenceId': sentence['sentenceId'],
            'evidenceIds': [], 'support': 'unsupported', 'supportBasis': 'none', 'reason': '本句没有登记引文。'}
            for sentence in packet['sentences']]}), 'usage': {}}

    env.gateway.complete = check_only
    queued = env.service.queue_automatic(env.project_id, project['version'])
    env.service.process(env.store.claim())
    after = latest(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert after['sources'] == after['sourceIds'] == []
    assert len(env.searches) == 1 and all(task == 'writing_citation_match' for task in calls)
    assert [(s['content'], s['evidence'], s['locked']) for s in after['sections']] == texts
    kept = after['illustrations'][0]
    assert kept['status'] == 'accepted' and kept['reviewed'] and kept['locked']
    assert valid_placement(after, kept)
    assert all(kept[key] == image[key] for key in ('id', 'assetId', 'assetHash', 'sourceId', 'revisionId', 'caption', 'altText', 'anchor'))
    assert after['illustrationReview']['status'] == 'complete'
    rendered = env.service.export_snapshot(env.project_id, after['version'], 'html', allow_issues=True)
    assert '<figure' in rendered['content'] and 'data:image/png;base64,' in rendered['content']


@pytest.mark.parametrize('change', ['purpose', 'sources', 'already_stale', 'unchecked_quality'])
def test_options_change_does_not_resurrect_unapproved_or_substantively_changed_images(images_env, change):
    env = images_env
    before = approved(env)
    if change in ('already_stale', 'unchecked_quality'):
        with env.store.connect() as db:
            project = env.service._load(db, env.project_id)
            if change == 'already_stale':
                project['illustrations'][0].update(status='stale', reviewed=False)
            else:
                project['illustrations'][0].pop('qualityPolicy')
            env.service._save(db, project, 'isolated_unapproved_figure')
        before = latest(env)
    patch = choices(before, purpose='另一种需要重新核对的内容要求' if change == 'purpose' else None)
    if change == 'sources':
        patch['sourceIds'] = []
    after = env.service.patch(env.project_id, before['version'], patch)
    assert after['illustrations'][0]['status'] == 'stale'
    assert not after['illustrations'][0]['reviewed']


def test_saved_picture_approval_cannot_authorize_changed_prose_another_asset_or_another_chapter(images_env):
    env = images_env
    before = approved(env)
    project = env.service.patch(env.project_id, before['version'], choices(before))
    for mutate in (
        lambda value: value['sections'][0].update(content='完全不同的正文。'),
        lambda value: value['illustrations'][0].update(assetId='another-asset'),
        lambda value: value['illustrations'][0].update(caption='编造的另一种图注'),
        lambda value: value['illustrations'][0].update(sectionId='another-section'),
    ):
        changed = copy.deepcopy(project)
        mutate(changed)
        preserve_approved_illustrations(project, changed, allow_source_change=True)
        refresh_illustrations(changed)
        assert changed['illustrations'][0]['status'] == 'stale'
        assert not valid_placement(changed, changed['illustrations'][0])


def test_program_owned_image_scope_cannot_be_submitted_through_section_or_figure_apis(images_env):
    env = images_env
    before = approved(env)
    project = env.service.patch(env.project_id, before['version'], choices(before))
    figure = project['illustrations'][0]
    proof = copy.deepcopy(figure['preservedSourceScope'])
    project = env.service.save_section(env.project_id, env.section_id, project['version'], {'preservedSourceScope': {'forged': True}})
    project = env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'locked': False})
    project = env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'preservedSourceScope': {'forged': True}})
    assert project['illustrations'][0]['preservedSourceScope'] == proof
    assert 'preservedSourceScope' not in project['sections'][0]
    assert valid_placement(project, project['illustrations'][0])


def test_citation_marker_only_relocation_keeps_the_saved_picture_approval(images_env):
    env = images_env
    before = approved(env)
    before = env.service.patch(env.project_id, before['version'], choices(before))
    changed = copy.deepcopy(before)
    changed['sections'][0]['content'] += '[E1]'
    reanchor_citation_only_images(before, changed)
    refresh_illustrations(changed)
    assert changed['illustrations'][0]['status'] == 'accepted'
    assert valid_placement(changed, changed['illustrations'][0])
