"""Isolated illustration lifecycle tests: fake vision, actual saved image bytes."""
import base64
import copy
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from server.image_retrieval import ImageRetrievalService
from server.parser import parse_html
from server.store import Store
from server.writing import WritingConflict, WritingExportConflict, WritingService
from server.writing_images import anchor_for, asset_bytes, freeze_image_resources, valid_placement
from server.writing_image_tasks import IMAGE_PROTOCOL, LEGACY_IMAGE_PROTOCOL, LEGACY_VISION_SYSTEM, QUALITY_POLICY


SECRET = 'isolated-image-task-credential-never-persist'
PROSE = '研究团队在实验室展示设备，图片保留清晰的原始来源和固定版本。'


class VisionGateway:
    def __init__(self):
        self.config = {'id': 'image-test-model', 'version': 2, 'name': '受控视觉模型', 'model': 'mock-vision',
                       'contextChars': 20000, 'maxOutputTokens': 2048, 'timeoutSeconds': 30, 'apiKey': SECRET}
        self.enabled, self.calls, self.callback, self.invalid = True, [], None, False

    def get_preferences(self):
        return {'defaultModelId': self.config['id'] if self.enabled else None}

    def models(self):
        return [{key: copy.deepcopy(value) for key, value in self.config.items() if key != 'apiKey'}] if self.enabled else []

    def resolve(self, model_id=None, version=None):
        assert model_id == self.config['id']
        return {**self.config, 'version': self.config['version'] if version is None else version}

    def complete(self, *_args, **_kwargs):
        raise AssertionError('配图不得生成或改写正文')

    def complete_vision(self, config, messages):
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        packet = json.loads(messages[-1]['content'][0]['text'])
        judgments = [{'assetId': item['assetId'], 'relevance': 'relevant', 'readable': True,
                      'contextConsistent': True, 'reason': '受控图片与段落语境一致'} for item in packet['candidates']]
        if packet.get('task') == 'writing_image_placement':
            judgments = [{'assetId': item['assetId'], 'matches': [{'paragraphId': item['paragraphIds'][-1],
                'relevance': 'relevant', 'readable': True, 'contextConsistent': True, 'reason': '图片对应本段设备说明'}],
                'reason': '设备画面对应第二段'} for item in packet['candidates']]
        if self.invalid:
            judgments[0].pop('contextConsistent')
        return {'text': json.dumps({'items': judgments}), 'usage': {'total_tokens': 6}, 'note': SECRET}


def publish_picture(store, title='固定图片资料', url='https://example.test/image-task'):
    picture = io.BytesIO()
    Image.new('RGB', (120, 80), '#245e46').save(picture, format='PNG')
    raw_image = picture.getvalue()
    markup = ('<html><head><title>' + title + '</title></head><body><article><h1>' + title + '</h1><p>' + PROSE
              + '</p><figure><img alt="实验室设备" src="data:image/png;base64,' + base64.b64encode(raw_image).decode()
              + '"><figcaption>实验室中的设备</figcaption></figure><p>设备图片用于说明展示对象。</p></article></body></html>').encode()
    queued = store.queue('file', {'url': url}, title)
    claimed = store.claim()
    assert queued['id'] == claimed['id']
    store.publish(claimed, {'url': url}, markup, parse_html(markup))
    job = store.job(queued['id'])
    document = store.detail(job['source_id'], job['revision_id'])
    return document, raw_image


@pytest.fixture
def images_env(tmp_path, monkeypatch):
    store, gateway = Store(tmp_path / 'image-tasks-isolated'), VisionGateway()
    document, raw_image = publish_picture(store)
    service = WritingService(store, gateway)
    project = service.create({'title': '设备介绍', 'mode': 'strict', 'brief': {}, 'sourceIds': [document['id']]})
    project = service.outline(project['id'], project['version'], [{'title': '设备', 'goal': '描述已记录的设备', 'targetWords': 200}])
    project = service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'content': PROSE + '\n\n第二段说明。'})
    asset = document['assets'][0]
    env = SimpleNamespace(store=store, gateway=gateway, service=service, project_id=project['id'], section_id=project['sections'][0]['id'],
                          document=document, raw_image=raw_image, searches=[], during_search=None,
                          candidate={'assetId': asset['id'], 'assetHash': hashlib.sha256(raw_image).hexdigest(),
                                     'sourceId': document['id'], 'revisionId': document['revisionId'], 'sourceTitle': document['title'],
                                     'caption': '实验室中的设备', 'originalCaption': '实验室中的设备', 'altText': '实验室设备',
                                     'scopeEligible': True, 'scopeBlockIds': [document['blocks'][0]['id']], 'status': 'recommended'})

    def fake_search(_retrieval, frozen_project, section_id, paragraph_index, query, *, model_config=None, vision_call=None, embedding_config=None, request_guard=None, embed_call=None, vision_system=None, **scope):
        env.searches.append({'project': copy.deepcopy(frozen_project), 'sectionId': section_id, 'paragraphIndex': paragraph_index,
                             'query': query, 'model': copy.deepcopy(model_config), 'embedding': embedding_config,
                             'visionSystem': vision_system})
        if env.during_search:
            callback, env.during_search = env.during_search, None
            callback()
        if request_guard:
            request_guard()
        if vision_call and scope.get('placement_mode') != 'chapter':
            vision_call(model_config, [{'role': 'system', 'content': vision_system}, {'role': 'user', 'content': [
                {'type': 'text', 'text': json.dumps({'paragraph': PROSE, 'candidates': [{'assetId': env.candidate['assetId']}]})},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(raw_image).decode()}}]}])
        candidate = copy.deepcopy(env.candidate)
        if not model_config and candidate['status'] == 'recommended':
            candidate['status'] = 'needs_review'
        if scope.get('quality_policy'):
            candidate.setdefault('qualityGate', {'policy': scope['quality_policy'], 'passed': bool(model_config),
                                                  'reason': '受控看图核对结果'})
        return {'candidates': [candidate], 'warnings': [] if model_config else ['未执行视觉核对'], 'query': query}

    monkeypatch.setattr(ImageRetrievalService, 'search', fake_search)
    return env


def latest(env):
    return env.service.get(env.project_id)


def search(env, *, legacy_quality=False):
    project = latest(env)
    result = env.service.queue_image_search(env.project_id, project['version'], env.section_id, 0, '实验室设备')
    if legacy_quality:
        with env.store.connect() as db:
            frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (result['runId'],)).fetchone()[0])
            frozen.pop('qualityPolicy')
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), result['runId']))
    claimed = env.store.claim()
    assert claimed['id'] == result['job']['id']
    env.service.process(claimed)
    return result


def accept(env, result=None, **changes):
    result = result or search(env)
    return env.service.add_illustration(env.project_id, latest(env)['version'],
                                       {'runId': result['runId'], 'assetId': env.candidate['assetId'], **changes})


def persisted_strings(env):
    with env.store.connect() as db:
        return '\n'.join(row[0] for row in db.execute('SELECT content_json FROM writing_projects UNION ALL SELECT content_json FROM writing_history'))


def test_search_freezes_model_and_body_and_persists_candidate_without_applying_it(images_env):
    env = images_env
    before = latest(env)
    queued = env.service.queue_image_search(env.project_id, before['version'], env.section_id, 0, '设备')
    env.gateway.config['version'] = 3
    env.service.process(env.store.claim())
    after = latest(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert after['version'] == before['version'] and after['sections'] == before['sections']
    assert not after.get('illustrations') and env.gateway.calls[0][0]['version'] == 2
    assert env.searches[0]['project']['sources'] == before['sources']
    candidate = next(run for run in after['runs'] if run['id'] == queued['runId'])['candidate']['imageSearch']
    assert candidate['paragraphExact'] == PROSE and not candidate['stale']
    with env.store.connect() as db:
        rows = db.execute('SELECT * FROM writing_model_interactions WHERE run_id=?', (queued['runId'],)).fetchall()
        assert len(rows) == 1 and rows[0]['status'] == 'succeeded' and rows[0]['validation_status'] == 'valid'
        assert SECRET not in str(dict(rows[0])) and '[REDACTED]' in str(dict(rows[0]))


def test_new_image_task_and_retry_keep_queue_time_vision_prompt(images_env, monkeypatch):
    import server.image_retrieval as retrieval
    env = images_env
    frozen_instruction = retrieval.VISION_SYSTEM
    before = latest(env)
    queued = env.service.queue_image_search(env.project_id, before['version'], env.section_id)
    with env.store.connect() as db:
        row = db.execute('SELECT prompt_version,input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()
    frozen = json.loads(row['input_json'])
    assert row['prompt_version'] == frozen['protocol'] == IMAGE_PROTOCOL
    assert frozen['visionSystem'] == frozen_instruction
    assert frozen['qualityPolicy'] == QUALITY_POLICY
    monkeypatch.setattr(retrieval, 'VISION_SYSTEM', '第一次升级后的视觉提示词')
    env.gateway.invalid = True
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'failed'
    env.gateway.invalid = False
    env.gateway.config['version'] = 3
    monkeypatch.setattr(retrieval, 'VISION_SYSTEM', '再次升级后的视觉提示词')
    env.store.retry(queued['job']['id'])
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert [config['version'] for config, _ in env.gateway.calls] == [2, 2]
    assert [messages[0]['content'] for _, messages in env.gateway.calls] == [frozen_instruction, frozen_instruction]
    assert all(item['visionSystem'] == frozen_instruction for item in env.searches)
    with env.store.connect() as db:
        assert json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0]) == frozen


def legacy_image_task(env):
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id)
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        frozen['protocol'] = LEGACY_IMAGE_PROTOCOL
        frozen.pop('visionSystem')
        frozen.pop('qualityPolicy')
        db.execute('UPDATE writing_runs SET prompt_version=?,input_json=? WHERE id=?',
                   (LEGACY_IMAGE_PROTOCOL, json.dumps(frozen), queued['runId']))
    return queued


def test_unstarted_images1_uses_exact_legacy_prompt_not_new_global_default(images_env, monkeypatch):
    env = images_env
    queued = legacy_image_task(env)
    monkeypatch.setattr('server.image_retrieval.VISION_SYSTEM', '升级后的提示词不能进入旧任务')
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert env.gateway.calls[0][1][0]['content'] == LEGACY_VISION_SYSTEM


def test_images1_retry_recovers_exact_historical_prompt_from_operations(images_env, monkeypatch):
    env = images_env
    queued = legacy_image_task(env)
    env.gateway.invalid = True
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'failed'
    historical_instruction = '精确保留这次旧任务首次请求时使用的视觉核对指令。'
    with env.store.connect() as db:
        row = db.execute('SELECT id,request_json FROM writing_model_interactions WHERE run_id=?', (queued['runId'],)).fetchone()
        request = json.loads(row['request_json'])
        request['messages'][0]['content'] = historical_instruction
        db.execute('UPDATE writing_model_interactions SET request_json=? WHERE id=?', (json.dumps(request), row['id']))
    env.gateway.invalid = False
    monkeypatch.setattr('server.image_retrieval.VISION_SYSTEM', '后来升级的提示词')
    env.store.retry(queued['job']['id'])
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert env.gateway.calls[-1][1][0]['content'] == historical_instruction


@pytest.mark.parametrize('damage', ['different_versions', 'missing_system'])
def test_images1_ambiguous_or_incomplete_prompt_history_fails_before_retry_call(images_env, damage):
    env = images_env
    queued = legacy_image_task(env)
    env.gateway.invalid = True
    env.service.process(env.store.claim())
    assert len(env.gateway.calls) == 1
    with env.store.connect() as db:
        record = dict(db.execute('SELECT * FROM writing_model_interactions WHERE run_id=?', (queued['runId'],)).fetchone())
        request = json.loads(record['request_json'])
        if damage == 'missing_system':
            request['messages'][0]['role'] = 'user'
            db.execute('UPDATE writing_model_interactions SET request_json=? WHERE id=?', (json.dumps(request), record['id']))
        else:
            request['messages'][0]['content'] = '另一不一致的历史提示词'
            record.update(id=record['id'] + '_other', request_json=json.dumps(request))
            db.execute('INSERT INTO writing_model_interactions (' + ','.join(record) + ') VALUES (' + ','.join('?' for _ in record) + ')',
                       tuple(record.values()))
    env.gateway.invalid = False
    env.store.retry(queued['job']['id'])
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'failed'
    assert len(env.gateway.calls) == 1


@pytest.mark.parametrize('damage', ['missing', 'empty', 'wrong_type', 'unknown_protocol', 'inconsistent_protocol'])
def test_invalid_image_prompt_snapshot_never_falls_back_to_current_prompt(images_env, damage):
    env = images_env
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id)
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        if damage == 'missing':
            frozen.pop('visionSystem')
        elif damage == 'empty':
            frozen['visionSystem'] = ' '
        elif damage == 'wrong_type':
            frozen['visionSystem'] = ['not-a-prompt']
        else:
            frozen['protocol'] = 'writing-images-unknown' if damage == 'unknown_protocol' else LEGACY_IMAGE_PROTOCOL
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), queued['runId']))
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'failed'
    assert not env.gateway.calls and not env.searches


def test_vision_callback_refuses_instruction_substitution_before_model_call(images_env, monkeypatch):
    env = images_env
    def wrong_prompt(_retrieval, *_args, model_config=None, vision_call=None, **_kwargs):
        vision_call(model_config, [{'role': 'system', 'content': '替换任务冻结指令'}])
        raise AssertionError('must not continue after prompt mismatch')
    monkeypatch.setattr(ImageRetrievalService, 'search', wrong_prompt)
    result = search(env)
    assert env.store.job(result['job']['id'])['status'] == 'failed'
    assert not env.gateway.calls


def test_no_model_search_cannot_be_forced_through_by_manual_confirmation(images_env):
    env = images_env
    env.gateway.enabled = False
    result = search(env)
    assert env.store.job(result['job']['id'])['status'] == 'succeeded' and not env.gateway.calls
    for manual in (False, True):
        with pytest.raises(WritingConflict, match='没有合适图片可以不配图'):
            accept(env, result, confirmUnverified=manual)
    assert not latest(env).get('illustrations')


def test_legacy_no_model_search_preserves_original_manual_adoption(images_env):
    env = images_env
    env.gateway.enabled = False
    result = search(env, legacy_quality=True)
    project = accept(env, result, confirmUnverified=True)
    assert project['illustrations'][0]['reviewMethod'] == 'human'
    assert 'qualityPolicy' not in project['illustrations'][0]


@pytest.mark.parametrize('gate', [None, {}, {'policy': QUALITY_POLICY, 'passed': False},
    {'policy': QUALITY_POLICY, 'passed': 1}, {'policy': 'unknown', 'passed': True}])
def test_quality_gate_cannot_be_bypassed_by_status_or_two_manual_confirmations(images_env, gate):
    env = images_env
    external_candidate(env)
    env.candidate['qualityGate'] = gate
    queued = scoped_search(env, search_scope='library')
    before = latest(env)
    with pytest.raises(WritingConflict, match='尚未通过图文匹配'):
        accept(env, queued, confirmUnverified=True, confirmLibrarySource=True)
    assert latest(env)['sections'] == before['sections']
    assert not latest(env).get('illustrations')


@pytest.mark.parametrize('policy', ['', None, False, 'unknown-quality'])
def test_invalid_frozen_quality_policy_fails_before_model_or_retrieval(images_env, policy):
    env = images_env
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id)
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        frozen['qualityPolicy'] = policy
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), queued['runId']))
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'failed'
    assert not env.gateway.calls and not env.searches


@pytest.mark.parametrize('change_body', [False, True])
def test_quality_approved_image_cannot_be_moved_or_reanchored_to_unreviewed_body(images_env, change_body):
    env = images_env
    project = accept(env)
    figure = project['illustrations'][0]
    assert figure['qualityPolicy'] == QUALITY_POLICY
    if change_body:
        project = env.service.save_section(env.project_id, env.section_id, project['version'], {'content': '已改变正文。\n\n第二段说明。'})
    with pytest.raises(WritingConflict, match='重新检索'):
        env.service.patch_illustration(env.project_id, figure['id'], project['version'],
            {'paragraphIndex': 0 if change_body else 1, 'confirmUnverified': True})


def test_quality_approved_image_allows_confirmed_caption_edit_at_same_exact_anchor(images_env):
    env = images_env
    project = accept(env)
    original = copy.deepcopy(project['illustrations'][0])
    edited = env.service.patch_illustration(env.project_id, original['id'], project['version'],
        {'caption': '已人工核对的客观图注', 'confirmUnverified': True})
    assert edited['illustrations'][0]['anchor'] == original['anchor']
    assert edited['illustrations'][0]['qualityPolicy'] == QUALITY_POLICY


def test_candidate_quality_pass_does_not_bypass_unapproved_chapter_position(images_env, monkeypatch):
    env = images_env
    def placement(section, candidates, **kwargs):
        candidates[0]['qualityGate'] = {'policy': QUALITY_POLICY, 'passed': True, 'reason': '图片存在明确匹配'}
        candidates[0]['placementSuggestions'] = [{**anchor_for(section, 1), 'status': 'needs_review', 'reason': '未经质量核对的位置'}]
        return {'candidates': candidates, 'coverage': {}, 'warnings': []}
    monkeypatch.setattr('server.image_placement.recommend_placements', placement)
    queued = scoped_search(env, placement_mode='chapter')
    with pytest.raises(WritingConflict, match='这个位置尚未通过'):
        accept(env, queued, paragraphIndex=1, confirmUnverified=True)
    assert not latest(env).get('illustrations')


def test_image_model_override_does_not_change_writing_or_default_model(images_env):
    env = images_env
    env.gateway.enabled = False
    before = latest(env)
    queued = env.service.queue_image_search(env.project_id, before['version'], env.section_id,
                                            model_id=env.gateway.config['id'])
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert latest(env)['modelId'] == before['modelId']
    assert env.gateway.get_preferences()['defaultModelId'] is None
    assert env.gateway.calls[0][0]['id'] == env.gateway.config['id']


def test_escaped_credential_is_removed_from_nested_model_response(images_env):
    from server.writing_operations import _clean
    escaped = ''.join('\\u' + format(ord(char), '04x') for char in SECRET)
    response = {'choices': [{'message': {'content': '{"reason":"' + escaped + '"}'}}]}
    cleaned = _clean(response, SECRET)
    assert SECRET not in json.loads(cleaned['choices'][0]['message']['content'])['reason']
    assert '[REDACTED]' in str(cleaned)


def test_invalid_vision_schema_is_logged_as_invalid_and_never_becomes_candidate(images_env):
    env = images_env
    env.gateway.invalid = True
    before = latest(env)
    result = search(env)
    assert env.store.job(result['job']['id'])['status'] == 'failed'
    with env.store.connect() as db:
        assert db.execute('SELECT validation_status FROM writing_model_interactions WHERE run_id=?', (result['runId'],)).fetchone()[0] == 'invalid'
        assert db.execute('SELECT result_json FROM writing_runs WHERE id=?', (result['runId'],)).fetchone()[0] is None
    assert latest(env)['sections'] == before['sections']


def test_stale_expected_version_and_active_task_prevent_competing_image_actions(images_env):
    env = images_env
    project = latest(env)
    with pytest.raises(WritingConflict):
        env.service.queue_image_search(env.project_id, project['version'] - 1, env.section_id)
    queued = env.service.queue_image_search(env.project_id, project['version'], env.section_id)
    with pytest.raises(WritingConflict):
        env.service.queue_image_search(env.project_id, project['version'], env.section_id)
    with pytest.raises(WritingConflict):
        env.service.complete_illustrations(env.project_id, project['version'], True)
    env.store.cancel(queued['job']['id'])
    assert latest(env)['version'] == project['version']


def test_manual_edit_during_search_keeps_body_and_marks_old_candidates_stale(images_env):
    env = images_env
    before = latest(env)
    env.gateway.callback = lambda: env.service.save_section(env.project_id, env.section_id, before['version'], {'content': '人工更新的新段落。'})
    result = search(env)
    project = latest(env)
    assert project['sections'][0]['content'] == '人工更新的新段落。'
    assert project['runs'][0]['candidate']['imageSearch']['stale'] is True
    with pytest.raises(WritingConflict, match='变化'):
        accept(env, result, confirmUnverified=True)


def test_cancelled_search_does_not_publish_late_model_result(images_env):
    env = images_env
    before = latest(env)
    queued = env.service.queue_image_search(env.project_id, before['version'], env.section_id)
    env.gateway.callback = lambda: env.store.cancel(queued['job']['id'])
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'cancelled'
    with env.store.connect() as db:
        assert db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0] is None
    assert latest(env)['sections'] == before['sections']


def test_locked_chapter_and_locked_figure_require_explicit_unlock(images_env):
    env = images_env
    result = search(env)
    project = env.service.save_section(env.project_id, env.section_id, latest(env)['version'], {'locked': True})
    with pytest.raises(WritingConflict, match='锁定'):
        accept(env, result)
    project = env.service.save_section(env.project_id, env.section_id, project['version'], {'locked': False})
    project = accept(env, result)
    figure = project['illustrations'][0]
    project = env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'locked': True})
    for action in (lambda: env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'caption': '改图注', 'confirmUnverified': True}),
                   lambda: env.service.remove_illustration(env.project_id, figure['id'], project['version'])):
        with pytest.raises(WritingConflict, match='锁定'):
            action()
    project = env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'locked': False})
    project = env.service.remove_illustration(env.project_id, figure['id'], project['version'])
    assert not project['illustrations']


def test_orphaned_locked_figure_can_only_be_unlocked_then_removed(images_env):
    env = images_env
    project = accept(env)
    figure_id = project['illustrations'][0]['id']
    project = env.service.patch_illustration(env.project_id, figure_id, project['version'], {'locked': True})
    with env.store.connect() as db:
        orphaned = env.service._load(db, env.project_id)
        orphaned['sections'] = []
        env.service._save(db, orphaned, 'controlled_legacy_orphaned_figure')
    project = latest(env)
    with pytest.raises(WritingConflict, match='锁定'):
        env.service.remove_illustration(env.project_id, figure_id, project['version'])
    with pytest.raises(WritingConflict, match='章节'):
        env.service.patch_illustration(env.project_id, figure_id, project['version'], {'caption': '不可调整', 'confirmUnverified': True})
    project = env.service.patch_illustration(env.project_id, figure_id, project['version'], {'locked': False})
    assert not project['illustrations'][0]['locked'] and project['illustrations'][0]['status'] == 'stale'
    project = env.service.remove_illustration(env.project_id, figure_id, project['version'])
    assert not project['illustrations']


def test_legacy_changed_caption_requires_confirmation_and_preserves_body(images_env):
    env = images_env
    result, before = search(env, legacy_quality=True), latest(env)
    with pytest.raises(WritingConflict, match='图注'):
        accept(env, result, caption='人工客观图注')
    project = accept(env, result, caption='人工客观图注', confirmUnverified=True)
    figure = project['illustrations'][0]
    with pytest.raises(WritingConflict, match='确认'):
        env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'paragraphIndex': 1})
    changed = env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'paragraphIndex': 1, 'confirmUnverified': True})
    assert changed['illustrations'][0]['anchor']['paragraphExact'] == '第二段说明。'
    assert changed['sections'] == before['sections']


def test_legacy_completion_invalidates_after_body_edit_and_explicit_reanchor_can_restore(images_env):
    env = images_env
    project = accept(env, search(env, legacy_quality=True))
    figure = project['illustrations'][0]
    project = env.service.complete_illustrations(env.project_id, project['version'])
    assert project['illustrationReview']['status'] == 'complete'
    changed = env.service.save_section(env.project_id, env.section_id, project['version'], {'content': '新增开头。\n\n' + project['sections'][0]['content']})
    assert changed['illustrationReview']['status'] == 'stale' and changed['illustrations'][0]['status'] == 'stale'
    assert not valid_placement(changed, changed['illustrations'][0])
    with pytest.raises(WritingConflict):
        env.service.complete_illustrations(env.project_id, changed['version'])
    restored = env.service.patch_illustration(env.project_id, figure['id'], changed['version'], {'paragraphIndex': 1, 'confirmUnverified': True})
    assert valid_placement(restored, restored['illustrations'][0])
    restored = env.service.complete_illustrations(env.project_id, restored['version'])
    assert restored['illustrationReview']['status'] == 'complete'


@pytest.mark.parametrize('change', ['title', 'mode', 'sources'])
def test_changed_scope_invalidates_accepted_illustrations(images_env, change):
    env = images_env
    project = accept(env)
    data = {'title': '改变后的标题'} if change == 'title' else {'mode': 'extended'} if change == 'mode' else {'sourceIds': []}
    changed = env.service.patch(env.project_id, project['version'], data)
    assert changed['illustrations'][0]['status'] == 'stale'
    if change == 'sources':
        with pytest.raises(WritingConflict, match='来源'):
            env.service.patch_illustration(env.project_id, changed['illustrations'][0]['id'], changed['version'], {'confirmUnverified': True})


@pytest.mark.parametrize('damage', ['unsuitable', 'out_of_scope', 'wrong_revision', 'wrong_hash'])
def test_manual_confirmation_cannot_bypass_candidate_scope_or_asset_integrity(images_env, damage):
    env = images_env
    if damage == 'unsuitable':
        env.candidate['status'] = 'unsuitable'
    elif damage == 'out_of_scope':
        env.candidate['scopeEligible'] = False
    elif damage == 'wrong_revision':
        env.candidate['revisionId'] = 'revision_unrelated'
    else:
        env.candidate['assetHash'] = '0' * 64
    result = search(env)
    with pytest.raises(ValueError):
        accept(env, result, confirmUnverified=True)
    assert not latest(env).get('illustrations')


def test_narrowed_material_scope_cannot_be_bypassed_by_reanchoring(images_env):
    env = images_env
    project = accept(env)
    with env.store.connect() as db:
        narrowed = env.service._load(db, env.project_id)
        other = next(block['id'] for block in env.document['blocks'] if block['id'] not in env.candidate['scopeBlockIds'])
        narrowed['sources'][0]['blockIds'] = [other]
        env.service._save(db, narrowed, 'controlled_narrowed_materials')
    project = latest(env)
    with pytest.raises(WritingConflict, match='来源'):
        env.service.patch_illustration(env.project_id, project['illustrations'][0]['id'], project['version'], {'confirmUnverified': True})


def test_skip_without_images_is_explicit_and_invalidates_on_later_body_change(images_env):
    env = images_env
    project = latest(env)
    project = env.service.complete_illustrations(env.project_id, project['version'], True)
    assert project['illustrationReview']['status'] == 'skipped'
    changed = env.service.save_section(env.project_id, env.section_id, project['version'], {'content': PROSE + '新的补充。'})
    assert changed['illustrationReview']['status'] == 'stale'


def test_export_freezes_bytes_without_persisting_resources_or_changing_manuscript(images_env):
    env = images_env
    project = accept(env)
    project = env.service.complete_illustrations(env.project_id, project['version'])
    before, history = copy.deepcopy(project), env.service.history(env.project_id)
    snapshot = env.service.export_snapshot(env.project_id, project['version'], 'html', True)
    assert 'data:image/png;base64,' in snapshot['content'] and '实验室中的设备' in snapshot['content']
    current = latest(env)
    assert current['illustrations'] == before['illustrations'] and current['sections'] == before['sections']
    assert env.service.history(env.project_id) == history and '_imageResources' not in persisted_strings(env)
    assert base64.b64encode(env.raw_image).decode() not in persisted_strings(env)
    frozen = copy.deepcopy(project)
    with env.store.connect() as db:
        freeze_image_resources(env.store, db, frozen)
    assert frozen['_imageResources'][env.candidate['assetId']]['sha256'] == env.candidate['assetHash']


def test_blob_changed_after_acceptance_blocks_export_even_for_draft(images_env):
    env = images_env
    project = accept(env)
    with env.store.connect() as db:
        blob = db.execute('SELECT blob_path FROM assets WHERE id=?', (env.candidate['assetId'],)).fetchone()[0]
    replacement = io.BytesIO()
    Image.new('RGB', (120, 80), 'red').save(replacement, format='PNG')
    (env.store.root / blob).write_bytes(replacement.getvalue())
    with pytest.raises(WritingExportConflict) as caught:
        env.service.export_snapshot(env.project_id, project['version'], 'html', True)
    assert caught.value.detail['code'] == 'illustrations_invalid'
    assert latest(env)['illustrations'] == project['illustrations']


def test_asset_loader_rejects_path_outside_library_assets(images_env):
    env = images_env
    secret = env.store.root / 'private-not-an-asset.png'
    secret.write_bytes(env.raw_image)
    with env.store.connect() as db:
        db.execute('UPDATE assets SET blob_path=? WHERE id=?', ('private-not-an-asset.png', env.candidate['assetId']))
    with env.store.connect() as db, pytest.raises(ValueError, match='文件不可用'):
        asset_bytes(env.store, db, env.candidate['assetId'], env.candidate['revisionId'])


def external_candidate(env):
    document, raw = publish_picture(env.store, title='其他图库来源', url='https://example.test/external-picture')
    env.candidate.update(sourceId=document['id'], revisionId=document['revisionId'], assetId=document['assets'][0]['id'],
                         assetHash=hashlib.sha256(raw).hexdigest(), sourceType='library', status='needs_review',
                         sourceTitle=document['title'], scopeBlockIds=[document['blocks'][0]['id']])
    return document


def scoped_search(env, **kwargs):
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id, **kwargs)
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    return queued


def test_library_image_requires_separate_confirmation_and_never_expands_prose_scope(images_env):
    env = images_env
    document = external_candidate(env)
    before = latest(env)
    queued = scoped_search(env, search_scope='library')
    with pytest.raises(WritingConflict, match='单独确认'):
        accept(env, queued, confirmUnverified=True)
    with pytest.raises(WritingConflict, match='单独确认'):
        accept(env, queued, confirmLibrarySource=True)
    project = accept(env, queued, confirmUnverified=True, confirmLibrarySource=True)
    assert project['sources'] == before['sources'] and project['sections'] == before['sections']
    item = project['illustrations'][0]
    assert item['sourceId'] == document['id'] and item['librarySourceConfirmed'] is True
    assert valid_placement(project, item)
    project = env.service.complete_illustrations(env.project_id, project['version'])
    exported = env.service.export_snapshot(env.project_id, project['version'], 'html', True)
    assert 'data:image/png;base64,' in exported['content'] and document['title'] in exported['content']


def test_source_imported_after_search_queue_is_not_in_frozen_library(images_env):
    env = images_env
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id, search_scope='library')
    # The newly imported picture is not part of this queued job's manifest.
    with env.store.connect() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (queued['job']['id'],))
    external_candidate(env)
    with env.store.connect() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (queued['job']['id'],))
    env.service.process(env.store.claim())
    with pytest.raises(ValueError, match='冻结图库范围'):
        accept(env, queued, confirmUnverified=True, confirmLibrarySource=True)


@pytest.mark.parametrize('damage', ['source', 'asset', 'scope', 'confirmed', 'review_method'])
def test_external_source_proof_cannot_be_reused_for_another_asset_or_scope(images_env, damage):
    env = images_env
    external_candidate(env)
    project = accept(env, scoped_search(env, search_scope='library'), confirmUnverified=True, confirmLibrarySource=True)
    item = project['illustrations'][0]
    if damage == 'source':
        item['sourceId'] = 'source_unrelated'
    elif damage == 'asset':
        item['assetId'] = 'asset_unrelated'
    elif damage == 'scope':
        item['imageSourceApproval']['scopeHash'] = 'old-scope'
    elif damage == 'confirmed':
        item['librarySourceConfirmed'] = False
    else:
        item['reviewMethod'] = 'model_and_acceptance'
    assert not valid_placement(project, item)
    with env.store.connect() as db, pytest.raises(ValueError, match='配图'):
        freeze_image_resources(env.store, db, project)


def test_library_source_ownership_checked_even_when_asset_revision_matches(images_env):
    env = images_env
    external_candidate(env)
    env.candidate['sourceId'] = env.document['id']
    queued = scoped_search(env, search_scope='library')
    with pytest.raises(ValueError):
        accept(env, queued, confirmUnverified=True, confirmLibrarySource=True)


def test_chapter_adoption_only_accepts_frozen_exact_recommended_positions(images_env, monkeypatch):
    env = images_env
    def placement(section, candidates, **kwargs):
        candidates[0]['placementSuggestions'] = [{**anchor_for(section, 1), 'status': 'needs_review', 'reason': '第二段对应图片', 'qualityPassed': True}]
        return {'candidates': candidates, 'coverage': {}, 'warnings': []}
    monkeypatch.setattr('server.image_placement.recommend_placements', placement)
    queued = scoped_search(env, placement_mode='chapter')
    for data in ({}, {'paragraphIndex': 0}, {'paragraphIndex': 10}):
        with pytest.raises(ValueError, match='推荐的配图位置'):
            accept(env, queued, confirmUnverified=True, **data)
    with pytest.raises(WritingConflict, match='明确确认'):
        accept(env, queued, paragraphIndex=1)
    project = accept(env, queued, paragraphIndex=1, confirmUnverified=True)
    assert project['illustrations'][0]['anchor'] == anchor_for(project['sections'][0], 1)


def test_chapter_search_with_no_suggestion_never_guesses_first_paragraph(images_env, monkeypatch):
    env = images_env
    monkeypatch.setattr('server.image_placement.recommend_placements',
        lambda section, candidates, **kwargs: {'candidates': candidates, 'coverage': {}, 'warnings': []})
    queued = scoped_search(env, placement_mode='chapter')
    with pytest.raises(ValueError, match='推荐的配图位置'):
        accept(env, queued, paragraphIndex=0, confirmUnverified=True)


def test_public_image_target_uses_frozen_scope_even_before_result(images_env):
    env = images_env
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id,
                                           search_scope='library', placement_mode='chapter')
    project = env.service.public_image_targets(latest(env))
    run = next(run for run in project['runs'] if run['id'] == queued['runId'])
    assert run['imageTarget'] == {'sectionId': env.section_id, 'paragraphIndex': 0,
                                  'searchScope': 'library', 'placementMode': 'chapter', 'qualityPolicy': QUALITY_POLICY}
    assert 'libraryScope' not in run and 'input_json' not in run


def test_images2_keeps_its_frozen_vision_prompt_and_original_paragraph_mode(images_env):
    from server.writing_image_tasks import FROZEN_VISION_PROTOCOL
    env = images_env
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id)
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        frozen['protocol'] = FROZEN_VISION_PROTOCOL
        for key in ('searchScope', 'placementMode', 'libraryScope', 'placementSystem', 'qualityPolicy'):
            frozen.pop(key)
        db.execute('UPDATE writing_runs SET prompt_version=?,input_json=? WHERE id=?',
                   (FROZEN_VISION_PROTOCOL, json.dumps(frozen), queued['runId']))
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    project = accept(env, queued)
    assert project['illustrations'][0]['anchor']['paragraphIndex'] == 0
    assert env.gateway.calls[0][1][0]['content'] == frozen['visionSystem']


def test_chapter_position_call_captures_actual_frozen_prompt_and_validates_full_schema(images_env, monkeypatch):
    from server.image_placement import PLACEMENT_SYSTEM
    env = images_env
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id, placement_mode='chapter')
    monkeypatch.setattr('server.image_placement.PLACEMENT_SYSTEM', '后续更新不可改变排队任务')
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(env.gateway.calls) == 1 and env.gateway.calls[0][1][0]['content'] == PLACEMENT_SYSTEM
    run = next(run for run in latest(env)['runs'] if run['id'] == queued['runId'])
    suggestion = run['candidate']['imageSearch']['candidates'][0]['placementSuggestions'][0]
    assert suggestion['paragraphIndex'] == 1 and suggestion['paragraphExact'] == '第二段说明。'
    with env.store.connect() as db:
        record = dict(db.execute('SELECT * FROM writing_model_interactions WHERE run_id=?', (queued['runId'],)).fetchone())
        assert record['validation_status'] == 'valid' and SECRET not in str(record)
        assert 'writing_image_placement' in record['request_json']


def test_earlier_chapter_task_without_alias_protocol_keeps_real_asset_ids(images_env):
    env = images_env
    queued = env.service.queue_image_search(env.project_id, latest(env)['version'], env.section_id, placement_mode='chapter')
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        frozen.pop('placementProtocol')
        frozen.pop('qualityPolicy')
        frozen['placementSystem'] = '此前冻结的位置提示词，返回真实图片ID。'
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), queued['runId']))
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    packet = json.loads(env.gateway.calls[0][1][-1]['content'][0]['text'])
    assert packet['candidates'][0]['assetId'] == env.candidate['assetId']
    assert env.gateway.calls[0][1][0]['content'] == frozen['placementSystem']


@pytest.mark.parametrize('invalid_protocol', ['', None])
def test_chapter_task_rejects_explicit_empty_alias_protocol(images_env, invalid_protocol):
    env = images_env
    before = latest(env)
    queued = env.service.queue_image_search(env.project_id, before['version'], env.section_id, placement_mode='chapter')
    with env.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        frozen['placementProtocol'] = invalid_protocol
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), queued['runId']))
    env.service.process(env.store.claim())
    job = env.store.job(queued['job']['id'])
    assert job['status'] == 'failed'
    assert '规则版本无效' in job['error']
    assert not env.gateway.calls
    with env.store.connect() as db:
        assert db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0] is None
    after = latest(env)
    assert after['sections'] == before['sections']
    assert after.get('illustrations', []) == before.get('illustrations', [])

