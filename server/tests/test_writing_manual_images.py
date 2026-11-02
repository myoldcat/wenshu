"""Direct human image selection uses saved bytes, never model work."""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.image_retrieval import ImageRetrievalService
from server.tests.test_writing_image_tasks import images_env, latest, publish_picture
from server.writing import WritingConflict
from server.writing_api import router
from server.writing_images import anchor_for, freeze_image_resources, image_issues, valid_placement


def selected(env, **changes):
    anchor = anchor_for(latest(env)['sections'][0], 1)
    return {'sectionId': env.section_id, 'assetId': env.candidate['assetId'], **anchor, **changes}


def no_work(env):
    with env.store.connect() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('jobs', 'writing_runs', 'writing_model_interactions')}


def forbid_models(env, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Manual selection must not resolve models or start image retrieval')
    for method in ('resolve', 'get_preferences', 'complete', 'complete_vision'):
        monkeypatch.setattr(env.gateway, method, forbidden)
    monkeypatch.setattr(ImageRetrievalService, 'search', forbidden)


@pytest.mark.parametrize('model_id', [None, 'deleted-model-no-longer-available'])
def test_manual_insert_is_immediate_without_models_jobs_or_runs(images_env, monkeypatch, model_id):
    env = images_env
    before, work = latest(env), no_work(env)
    forbid_models(env, monkeypatch)
    result = env.service.insert_manual_image(env.project_id, before['version'], selected(env, modelId=model_id))
    after, figure = result['project'], result['illustration']
    assert after['version'] == before['version'] + 1
    assert after['sections'] == before['sections'] and after['sources'] == before['sources']
    assert after['illustrations'] == [figure] and after['runs'] == before['runs']
    assert figure['anchor'] == anchor_for(before['sections'][0], 1)
    assert figure['reviewMethod'] == 'human' and figure['selectionMethod'] == 'manual'
    assert figure['reviewed'] is True and figure['status'] == 'accepted'
    assert 'qualityPolicy' not in figure and 'qualityGate' not in figure and 'runId' not in figure
    assert valid_placement(after, figure) and not after.get('illustrationReview')
    assert no_work(env) == work and not env.searches and not env.gateway.calls


def test_manual_api_returns_saved_figure_with_200_not_a_queued_job(images_env, monkeypatch):
    env = images_env
    payload = {'expectedVersion': latest(env)['version'], **selected(env)}
    forbid_models(env, monkeypatch)
    app = FastAPI()
    app.include_router(router(env.service))
    response = TestClient(app).post(f'/api/writing/projects/{env.project_id}/manual-image', json=payload)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['illustration']['id'] == value['project']['illustrations'][0]['id']
    assert 'job' not in value and 'runId' not in value


def test_manual_fixed_reference_keeps_its_old_revision(images_env):
    env = images_env
    old = env.document['revisionId']
    newer, _ = publish_picture(env.store, '新版图片资料')
    assert newer['revisionId'] != old
    result = env.service.insert_manual_image(env.project_id, latest(env)['version'], selected(env))
    assert result['illustration']['revisionId'] == old
    assert result['project']['sources'][0]['revisionId'] == old


def test_manual_library_selection_uses_own_human_proof_and_keeps_body_scope(images_env, monkeypatch):
    env = images_env
    other, _ = publish_picture(env.store, '独立图库图片', url='https://example.test/direct-library')
    before = latest(env)
    data = selected(env, assetId=other['assets'][0]['id'], searchScope='library')
    with pytest.raises(WritingConflict, match='单独确认'):
        env.service.insert_manual_image(env.project_id, before['version'], data)
    forbid_models(env, monkeypatch)
    result = env.service.insert_manual_image(env.project_id, before['version'], {**data, 'confirmLibrarySource': True})
    after, figure = result['project'], result['illustration']
    assert after['sources'] == before['sources'] and after['sourceIds'] == before['sourceIds']
    assert after['sections'] == before['sections']
    assert figure['sourceType'] == 'library' and figure['librarySourceConfirmed'] is True
    assert figure['imageSourceApproval']['method'] == 'manual_selection'
    assert figure['imageSourceApproval']['selectionId'] == figure['id']
    assert figure['reviewMethod'] == 'human' and 'runId' not in figure and valid_placement(after, figure)
    forged = copy.deepcopy(figure)
    forged['imageSourceApproval']['selectionId'] = 'another-selection'
    assert not valid_placement(after, forged)


def test_manual_selected_asset_must_belong_to_saved_scope(images_env):
    env = images_env
    other, _ = publish_picture(env.store, '资料范围外图片', url='https://example.test/outside-direct')
    before = latest(env)
    with pytest.raises(ValueError, match='不属于'):
        env.service.insert_manual_image(env.project_id, before['version'], selected(env, assetId=other['assets'][0]['id']))
    assert latest(env)['version'] == before['version'] and not latest(env).get('illustrations')


def test_manual_narrowed_reference_scope_cannot_be_bypassed(images_env):
    env = images_env
    with env.store.connect() as db:
        project = env.service._load(db, env.project_id)
        project['sources'][0]['blockIds'] = [env.document['blocks'][0]['id']]
        env.service._save(db, project, 'narrowed_manual_fixture')
    before = latest(env)
    with pytest.raises(ValueError, match='段落范围'):
        env.service.insert_manual_image(env.project_id, before['version'], selected(env))
    assert latest(env)['version'] == before['version'] and not latest(env).get('illustrations')


@pytest.mark.parametrize('field,value', [('paragraphExact', '并非目标段落'), ('contentHash', '0' * 64), ('paragraphIndex', 99)])
def test_manual_rejects_changed_target_without_writing(images_env, field, value):
    env = images_env
    before = latest(env)
    with pytest.raises((WritingConflict, ValueError)):
        env.service.insert_manual_image(env.project_id, before['version'], selected(env, **{field: value}))
    assert latest(env)['version'] == before['version'] and not latest(env).get('illustrations')


def test_manual_rejects_stale_version_and_locked_chapter(images_env):
    env = images_env
    before = latest(env)
    with pytest.raises(WritingConflict):
        env.service.insert_manual_image(env.project_id, before['version'] - 1, selected(env))
    env.service.save_section(env.project_id, env.section_id, before['version'], {'locked': True})
    locked = latest(env)
    with pytest.raises(WritingConflict, match='锁定'):
        env.service.insert_manual_image(env.project_id, locked['version'], selected(env))
    assert latest(env)['version'] == locked['version'] and not latest(env).get('illustrations')


def test_manual_rejects_active_tasks_and_duplicate_bytes(images_env):
    env = images_env
    before = latest(env)
    queued = env.service.queue_image_batch(env.project_id, before['version'], [env.section_id])
    with pytest.raises(WritingConflict, match='任务'):
        env.service.insert_manual_image(env.project_id, before['version'], selected(env))
    env.store.cancel(queued['job']['id'])
    result = env.service.insert_manual_image(env.project_id, latest(env)['version'], selected(env))
    after = result['project']
    with pytest.raises(ValueError, match='已用于'):
        env.service.insert_manual_image(env.project_id, after['version'], selected(env, **anchor_for(after['sections'][0], 0)))
    assert latest(env)['version'] == after['version'] and len(latest(env)['illustrations']) == 1


def test_manual_saved_picture_can_be_completed_exported_and_become_stale(images_env):
    env = images_env
    result = env.service.insert_manual_image(env.project_id, latest(env)['version'], selected(env))
    project = env.service.complete_illustrations(env.project_id, result['project']['version'])
    assert project['illustrationReview']['status'] == 'complete'
    with env.store.connect() as db:
        assert not [issue for issue in image_issues(db, project) if issue['severity'] == 'error']
        frozen = copy.deepcopy(project)
        freeze_image_resources(env.store, db, frozen)
    assert frozen['_imageResources'][result['illustration']['assetId']]['sha256'] == result['illustration']['assetHash']
    env.service.save_section(env.project_id, env.section_id, project['version'], {'content': '改变后的正文。'})
    assert latest(env)['illustrations'][0]['status'] == 'stale'


def test_manual_maximum_picture_count_is_preserved(images_env):
    env = images_env
    result = env.service.insert_manual_image(env.project_id, latest(env)['version'], selected(env))
    with env.store.connect() as db:
        project = env.service._load(db, env.project_id)
        project['illustrations'] = [{**result['illustration'], 'id': f'fixture-{index}'} for index in range(40)]
        env.service._save(db, project, 'manual_picture_capacity_fixture')
    before = latest(env)
    with pytest.raises(ValueError, match='40'):
        env.service.insert_manual_image(env.project_id, before['version'], selected(env))
    assert latest(env)['version'] == before['version'] and len(latest(env)['illustrations']) == 40


def test_manual_rejects_invalid_saved_image_bytes_without_saving(images_env):
    env = images_env
    before, work = latest(env), no_work(env)
    with env.store.connect() as db:
        row = db.execute('SELECT blob_path FROM assets WHERE id=?', (env.candidate['assetId'],)).fetchone()
    (env.store.root / row['blob_path']).write_bytes(b'not-a-decodable-picture')
    with pytest.raises(ValueError, match='损坏|格式'):
        env.service.insert_manual_image(env.project_id, before['version'], selected(env))
    assert latest(env)['version'] == before['version'] and not latest(env).get('illustrations')
    assert no_work(env) == work
