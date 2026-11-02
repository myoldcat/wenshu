"""Isolated picture selection, durable chapter batches and explicit removal."""
import copy
import json

import pytest

from server.image_retrieval import ImageRetrievalService
from server.store import Store
from server.writing import WritingConflict
from server.tests.test_writing_image_tasks import images_env, latest, publish_picture, search, accept


def finish(env, queued):
    claimed = env.store.claim()
    assert claimed['id'] == queued['job']['id']
    env.service.process(claimed)
    return env.store.job(claimed['id'])


def test_catalog_is_read_only_and_keeps_reference_revision(images_env):
    env = images_env
    before = latest(env)
    old_revision = env.document['revisionId']
    newer, _ = publish_picture(env.store, '新版图片资料')
    with env.store.connect() as db:
        before_tables = list(db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
    catalog = env.service.image_assets(env.project_id)
    assert catalog['total'] == 1
    assert catalog['items'][0]['revisionId'] == old_revision
    assert catalog['items'][0]['assetId'] == env.candidate['assetId']
    assert latest(env)['version'] == before['version'] and not env.gateway.calls
    assert not latest(env)['runs']
    assert env.service.image_assets(env.project_id, query='无此标题')['total'] == 0
    assert env.service.image_assets(env.project_id, query='实验室中的设备')['total'] == 1
    with env.store.connect() as db:
        assert list(db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")) == before_tables


def test_catalog_flags_picture_outside_narrowed_reference_blocks(images_env):
    env = images_env
    with env.store.connect() as db:
        project = env.service._load(db, env.project_id)
        project['sources'][0]['blockIds'] = [env.document['blocks'][0]['id']]
        env.service._save(db, project, 'controlled_narrowed_reference_fixture')
    catalog = env.service.image_assets(env.project_id)
    assert len(catalog['items']) == 1
    assert catalog['items'][0]['scopeEligible'] is False
    assert '段落范围' in catalog['items'][0]['scopeReason']


def test_manual_selection_checks_exact_paragraph_and_preserves_body(images_env):
    env = images_env
    before = latest(env)
    queued = env.service.queue_manual_image(env.project_id, before['version'], {
        'sectionId': env.section_id, 'paragraphIndex': 1, 'assetId': env.candidate['assetId']})
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
    assert frozen['tasks'][0]['selectedAssetId'] == env.candidate['assetId']
    assert frozen['tasks'][0]['anchor']['paragraphIndex'] == 1
    assert frozen['tasks'][0]['qualityPolicy']
    assert finish(env, queued)['status'] == 'succeeded'
    after = latest(env)
    assert len(after['illustrations']) == 1
    assert after['illustrations'][0]['anchor']['paragraphIndex'] == 1
    assert after['sections'] == before['sections'] and after['sources'] == before['sources']
    assert after.get('illustrationReview') is None


def test_manual_quality_rejection_cannot_be_overridden(images_env):
    env = images_env
    env.candidate['qualityGate'] = {'policy': 'writing-image-quality-1', 'passed': False, 'reason': '画面与正文不符'}
    before = latest(env)
    queued = env.service.queue_manual_image(env.project_id, before['version'], {
        'sectionId': env.section_id, 'paragraphIndex': 0, 'assetId': env.candidate['assetId'], 'caption': '手动确认也不得绕过'})
    assert finish(env, queued)['status'] == 'succeeded'
    after = latest(env)
    assert not after.get('illustrations', []) and after['version'] == before['version']
    result = next(run for run in after['runs'] if run['id'] == queued['runId'])['candidate']['imageBatch']
    assert result['issues'] and result['completedSectionIds'] == [env.section_id]


def test_manual_outside_scope_and_locked_chapter_rejected(images_env):
    env = images_env
    other, _ = publish_picture(env.store, '其他资料', url='https://example.test/other')
    with pytest.raises(ValueError, match='不属于'):
        env.service.queue_manual_image(env.project_id, latest(env)['version'], {
            'sectionId': env.section_id, 'paragraphIndex': 0, 'assetId': other['assets'][0]['id']})
    project = env.service.save_section(env.project_id, env.section_id, latest(env)['version'], {'locked': True})
    with pytest.raises(ValueError, match='未锁定'):
        env.service.queue_image_batch(env.project_id, project['version'], [env.section_id])
    assert not project.get('illustrations', [])


@pytest.mark.parametrize('manual', [True, False])
def test_library_insertion_requires_explicit_scope_approval_and_preserves_evidence(images_env, manual):
    env = images_env
    other, raw = publish_picture(env.store, '图库设备图片', url='https://example.test/approved-library')
    env.candidate.update(assetId=other['assets'][0]['id'], sourceId=other['id'], revisionId=other['revisionId'],
                         sourceTitle=other['title'], sourceType='library')
    before = latest(env)
    options = {'searchScope': 'library', 'confirmLibrarySource': True, 'sectionId': env.section_id,
               'paragraphIndex': 0, 'assetId': env.candidate['assetId']}
    if manual:
        with pytest.raises(ValueError, match='单独确认'):
            env.service.queue_manual_image(env.project_id, before['version'], {**options, 'confirmLibrarySource': False})
        queued = env.service.queue_manual_image(env.project_id, before['version'], options)
    else:
        queued = env.service.queue_image_batch(env.project_id, before['version'], [env.section_id],
            search_scope='library', confirm_library_source=True)
    assert finish(env, queued)['status'] == 'succeeded'
    after = latest(env)
    assert len(after['illustrations']) == 1
    figure = after['illustrations'][0]
    assert figure['sourceType'] == 'library' and figure['librarySourceConfirmed']
    assert figure['reviewMethod'] == ('human' if manual else 'automatic_quality_checked')
    assert after['sources'] == before['sources'] and after['sections'] == before['sections']


def test_batch_freezes_actual_model_and_prompts_and_completes(images_env):
    env = images_env
    before = latest(env)
    queued = env.service.queue_image_batch(env.project_id, before['version'], [env.section_id])
    env.gateway.config['version'] = 3
    assert finish(env, queued)['status'] == 'succeeded'
    after = latest(env)
    assert len(after['illustrations']) == 1 and after['illustrationReview']['status'] == 'complete'
    assert env.searches[0]['model']['version'] == 2
    assert after['sections'] == before['sections'] and after['sources'] == before['sources']
    parent = next(run for run in after['runs'] if run['id'] == queued['runId'])
    assert parent['candidate']['imageBatch']['status'] == 'complete'
    assert parent['candidate']['imageBatch']['completedSectionIds'] == [env.section_id]
    assert parent['candidate']['imageBatch']['expectedVersion'] == after['version']


def test_selected_batch_does_not_touch_unselected_chapter(images_env):
    env = images_env
    before = latest(env)
    outline = [{key: section[key] for key in ('id', 'title', 'goal', 'mode', 'targetWords')} for section in before['sections']]
    outline.append({'title': '未选章节', 'goal': '保留', 'targetWords': 200})
    project = env.service.outline(env.project_id, before['version'], outline)
    section = project['sections'][1]
    project = env.service.save_section(env.project_id, section['id'], project['version'], {'content': '未选章节正文保持。'})
    queued = env.service.queue_image_batch(env.project_id, project['version'], [env.section_id])
    assert finish(env, queued)['status'] == 'succeeded'
    assert all(item['sectionId'] == env.section_id for item in latest(env)['illustrations'])
    assert latest(env)['sections'][1] == project['sections'][1]


def test_cancellation_during_quality_check_never_adopts_late_image(images_env):
    env = images_env
    before = latest(env)
    queued = env.service.queue_manual_image(env.project_id, before['version'], {
        'sectionId': env.section_id, 'paragraphIndex': 0, 'assetId': env.candidate['assetId']})
    env.during_search = lambda: env.store.cancel(queued['job']['id'])
    assert finish(env, queued)['status'] == 'cancelled'
    after = latest(env)
    assert not after.get('illustrations', []) and after['version'] == before['version']
    assert not any(run['status'] in ('queued', 'running') for run in after['runs'])


def test_batch_recovery_keeps_completed_selection_and_does_not_duplicate(images_env):
    env = images_env
    before = latest(env)
    queued = env.service.queue_image_batch(env.project_id, before['version'], [env.section_id])
    assert finish(env, queued)['status'] == 'succeeded'
    after = latest(env)
    calls = len(env.gateway.calls)
    with env.store.connect() as db:
        db.execute("UPDATE jobs SET status='failed',stage='failed' WHERE id=?", (queued['job']['id'],))
    env.store.retry(queued['job']['id'])
    assert finish(env, queued)['status'] == 'succeeded'
    assert len(latest(env)['illustrations']) == 1 and len(env.gateway.calls) == calls
    assert latest(env)['version'] == after['version']


def test_no_image_clear_is_explicit_atomic_and_preserves_sources(images_env):
    env = images_env
    accept(env)
    before = latest(env)
    with pytest.raises(ValueError, match='已有配图'):
        env.service.complete_illustrations(env.project_id, before['version'], skip=True)
    after = env.service.complete_illustrations(env.project_id, before['version'], skip=True, clear_existing=True)
    assert not after['illustrations'] and after['illustrationReview']['status'] == 'skipped'
    assert after['sources'] == before['sources'] and after['sections'] == before['sections']


def test_no_image_clear_cannot_remove_locked_picture(images_env):
    env = images_env
    accept(env)
    project = latest(env)
    project = env.service.patch_illustration(env.project_id, project['illustrations'][0]['id'], project['version'], {'locked': True})
    with pytest.raises(WritingConflict, match='已锁定'):
        env.service.complete_illustrations(env.project_id, project['version'], skip=True, clear_existing=True)
    assert latest(env)['illustrations'] == project['illustrations'] and latest(env)['version'] == project['version']


def test_targeted_retrieval_does_not_choose_a_different_asset(tmp_path):
    store = Store(tmp_path / 'targeted-picture-catalog')
    first, _ = publish_picture(store, '第一张图片')
    second, _ = publish_picture(store, '第二张图片', url='https://example.test/second')
    project = {'id': 'fixture', 'sources': [{'sourceId': item['id'], 'revisionId': item['revisionId']} for item in (first, second)],
               'sections': [{'id': 'chapter', 'title': '设备', 'content': '本段说明设备。', 'evidence': []}]}
    result = ImageRetrievalService(store).search(project, 'chapter', selected_asset_id=second['assets'][0]['id'])
    assert [candidate['assetId'] for candidate in result['candidates']] == [second['assets'][0]['id']]
