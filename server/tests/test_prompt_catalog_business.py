"""Business catalogue integration uses isolated stores and fake providers."""
import copy
import json

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server import prompt_defaults
from server.prompts import PromptLibrary, REGISTRY, compiled_snapshot_text
from server.store import Store, dumps
from server.tests.test_writing_title_candidates import bench as title_bench, no_drafting_side_effects
from server.tests.test_retrieval import bench as retrieval_bench, publish as publish_retrieval
from server.tests.test_article_type import workbench, legacy_job
from server.tests.test_enrichment import execute, publish
from server.tests.test_writing_document import document, ready
from server.tests.test_writing_composition_integration import _frozen, _queue_parent, _parent_context
from server.tests.test_writing_image_tasks import images_env, latest
from server.prompt_task_defaults import VISION_SYSTEM, PLACEMENT_SYSTEM


LOCAL = {'X-Wenshu-Client': 'local'}
BUSINESS_IDS = ('writing_assist_title', 'writing_assist_title_candidates', 'writing_assist_material_queries',
                'retrieval_query_expansion', 'writing_material_queries', 'writing_image_vision',
                'writing_image_placement', 'classification_article_type')


def edit(store, prompt_id, marker):
    library = PromptLibrary(store)
    current = next(item for item in library.list()['items'] if item['id'] == prompt_id)
    return library.update(prompt_id, REGISTRY[prompt_id]['content'] + '\n' + marker, current['version'])


def frozen_prompts(store, job_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (job_id,)).fetchone()[0])


@pytest.mark.parametrize('prompt_id', BUSINESS_IDS)
def test_business_entries_http_edit_cas_restart_and_no_jobs(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    app = create_app(data_dir=store.root)
    with TestClient(app) as client:
        items = client.get('/api/prompts').json()['items']
        assert len(items) == 50
        item = next(item for item in items if item['id'] == prompt_id)
        assert item['version'] == 1 and item['content'] == REGISTRY[prompt_id]['content']
        body = {'content': item['content'] + '\n合成验收表达。', 'expectedVersion': 1}
        saved = client.put('/api/prompts/' + prompt_id, headers=LOCAL, json=body)
        assert saved.status_code == 200 and saved.json()['version'] == 2
        assert client.put('/api/prompts/' + prompt_id, headers=LOCAL, json=body).status_code == 409
        assert client.put('/api/prompts/' + prompt_id, headers=LOCAL,
                          json={'content': ' ', 'expectedVersion': 2}).status_code == 422
    restarted = PromptLibrary(Store(store.root))
    assert next(item for item in restarted.list()['items'] if item['id'] == prompt_id) == saved.json()
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 0


def test_effective_defaults_and_fixed_supplements_are_read_only_views(tmp_path):
    store = Store(tmp_path / 'effective')
    library = PromptLibrary(store)
    before = library.list()
    for kind in ('outline', 'section', 'document'):
        for mode in ('strict', 'extended'):
            prompt_id = 'writing_' + kind + '_' + mode
            item = next(item for item in before['items'] if item['id'] == prompt_id)
            assert item['content'] == REGISTRY[prompt_id]['content']
            assert item['effectiveContent'] == getattr(prompt_defaults, 'WRITING_COMPOSITION_' + kind.upper() + '_' + mode.upper())
            assert item['supplementalRules']
    edited = edit(store, 'writing_section_strict', '作者新增表达。')
    assert edited['content'].endswith('作者新增表达。')
    assert '作者新增表达。' in edited['effectiveContent']
    assert library.list() == library.list()
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM prompt_overrides').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


@pytest.mark.parametrize('prompt_id', ['writing_image_vision', 'writing_image_placement'])
def test_image_editor_limit_includes_compiled_fixed_contract(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    library = PromptLibrary(store)
    item = next(item for item in library.list()['items'] if item['id'] == prompt_id)
    maximum = item['maxContentLength']
    assert maximum < 20000
    with pytest.raises(ValueError, match=str(maximum)):
        library.update(prompt_id, '甲' * (maximum + 1), 1)
    saved = library.update(prompt_id, '甲' * maximum, 1)
    assert len(compiled_snapshot_text(prompt_id, saved)) == 20000


@pytest.mark.parametrize(('kind', 'count', 'prompt_id'), [
    ('title', None, 'writing_assist_title'), ('title', 3, 'writing_assist_title_candidates'),
    ('material-queries', None, 'writing_assist_material_queries')])
def test_sync_assistance_reads_edited_task_once_and_records_version(title_bench, kind, count, prompt_id):
    store, gateway, service = title_bench
    edit(store, prompt_id, '本次甲')
    complete = gateway.complete

    def concurrent_edit(config, messages):
        edit(store, prompt_id, '后续乙')
        return complete(config, messages)

    gateway.complete = concurrent_edit
    request = {'topic': '合成资料治理'}
    if count:
        request['candidateCount'] = count
    service.assist(kind, request)
    assert len(gateway.calls) == 1
    system = gateway.calls[0][1][0]['content']
    assert '本次甲' in system and '后续乙' not in system
    assert '程序固定约束：' in system
    with store.connect() as db:
        model = json.loads(db.execute('SELECT model_json FROM writing_material_interactions').fetchone()[0])
    assert model['promptSnapshot'][prompt_id]['version'] == 2
    no_drafting_side_effects(store)


def test_retrieval_expansion_freezes_text_and_version_in_recommendation(retrieval_bench):
    store, models, service, model_id, calls = retrieval_bench
    publish_retrieval(store, ['maritime shipping preserves original records.'])
    models.save_preferences({'defaultModelId': model_id})
    service.save_settings({'queryExpansion': True})
    edit(store, 'retrieval_query_expansion', '本次检索甲')
    result = service.recommend('港口贸易')
    assert result['queryOrigin'] == 'model' and len(calls) == 1
    assert '本次检索甲' in calls[0][1]['messages'][0]['content']
    with store.connect() as db:
        saved = json.loads(db.execute('SELECT content_json FROM retrieval_snapshots WHERE id=?', (result['id'],)).fetchone()[0])
    assert saved['queryPromptSnapshot']['retrieval_query_expansion']['version'] == 2
    edit(store, 'retrieval_query_expansion', '后续检索乙')
    with store.connect() as db:
        assert json.loads(db.execute('SELECT content_json FROM retrieval_snapshots WHERE id=?', (result['id'],)).fetchone()[0]) == saved


def test_automatic_parent_freezes_material_queries_and_images_before_dispatch(document):
    store, gateway, service, _ = document
    project = ready(document)
    for prompt_id in ('writing_material_queries', 'writing_image_vision', 'writing_image_placement'):
        edit(store, prompt_id, '父任务甲')
    parent = _queue_parent(document, project)
    saved = _frozen(store, parent['runId'])
    assert '父任务甲' in saved['materialQueryInstructions']
    assert saved['materialQueryPromptSnapshot']['writing_material_queries']['version'] == 2
    assert all(item['version'] == 2 for item in saved['imagePromptSnapshot'].values())
    for prompt_id in ('writing_material_queries', 'writing_image_vision', 'writing_image_placement'):
        edit(store, prompt_id, '未来乙')
        assert frozen_prompts(store, parent['job']['id'])[prompt_id]['version'] == 2
    assert _frozen(store, parent['runId']) == saved and not gateway.calls


@pytest.mark.parametrize('placement', ['paragraph', 'chapter'])
def test_manual_image_task_freezes_edited_instructions_and_version(images_env, placement):
    env = images_env
    for prompt_id in ('writing_image_vision', 'writing_image_placement'):
        edit(env.store, prompt_id, '图片队列甲')
    before = latest(env)
    queued = env.service.queue_image_search(env.project_id, before['version'], env.section_id,
                                             placement_mode=placement)
    saved = _frozen(env.store, queued['runId'])
    assert '图片队列甲' in saved['visionSystem']
    if placement == 'chapter':
        assert '图片队列甲' in saved['placementSystem']
    for prompt_id in saved['imagePromptSnapshot']:
        assert frozen_prompts(env.store, queued['job']['id'])[prompt_id]['version'] == 2
        edit(env.store, prompt_id, '图片未来乙')
    if placement == 'paragraph':
        env.service.process(env.store.claim())
        assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
        assert '图片队列甲' in env.gateway.calls[0][1][0]['content']
        assert '图片未来乙' not in env.gateway.calls[0][1][0]['content']
    assert _frozen(env.store, queued['runId']) == saved
    assert latest(env)['sections'] == before['sections']


@pytest.mark.parametrize('legacy', [False, True])
def test_image_child_inherits_parent_or_legacy_builtin_without_live_override(images_env, legacy, monkeypatch):
    env = images_env
    project = latest(env)
    for prompt_id in ('writing_image_vision', 'writing_image_placement'):
        edit(env.store, prompt_id, '父图甲')
    parent = _queue_parent((env.store, env.gateway, env.service, env.document['id']), project)
    parent_frozen = _frozen(env.store, parent['runId'])
    if legacy:
        parent_frozen.pop('imagePromptSnapshot')
        with env.store.connect() as db:
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(parent_frozen), parent['runId']))
    for prompt_id in ('writing_image_vision', 'writing_image_placement'):
        edit(env.store, prompt_id, '实时乙')
        monkeypatch.setitem(REGISTRY[prompt_id], 'readOnlyContract', '后续程序合同不得重编译旧父任务')
    _parent_context(env.service, env.gateway, parent, env.store.claim())
    queued = env.service.queue_image_search(env.project_id, project['version'], env.section_id, placement_mode='chapter')
    child = _frozen(env.store, queued['runId'])
    if legacy:
        assert 'imagePromptSnapshot' not in child
        assert child['visionSystem'] == VISION_SYSTEM and child['placementSystem'] == PLACEMENT_SYSTEM
    else:
        assert child['imagePromptSnapshot'] == parent_frozen['imagePromptSnapshot']
        assert child['visionSystem'] == parent_frozen['imageInstructions']['writing_image_vision']
        assert child['placementSystem'] == parent_frozen['imageInstructions']['writing_image_placement']
        assert '父图甲' in child['visionSystem'] and '父图甲' in child['placementSystem']
    assert '实时乙' not in child['visionSystem'] and '实时乙' not in child['placementSystem']
    assert not env.gateway.calls


@pytest.mark.parametrize('legacy', [False, True])
def test_selected_chapter_image_batch_keeps_queued_text_for_later_children(images_env, legacy):
    env = images_env
    before = latest(env)
    if not legacy:
        for prompt_id in ('writing_image_vision', 'writing_image_placement'):
            edit(env.store, prompt_id, '批量队列甲')
    queued = env.service.queue_image_batch(env.project_id, before['version'], [env.section_id])
    parent = _frozen(env.store, queued['runId'])
    if legacy:
        parent.pop('imagePromptSnapshot')
        for task in parent['tasks']:
            task.pop('imagePromptSnapshot')
        with env.store.connect() as db:
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(parent), queued['runId']))
    else:
        assert all(item['version'] == 2 for item in parent['imagePromptSnapshot'].values())
    for prompt_id in ('writing_image_vision', 'writing_image_placement'):
        edit(env.store, prompt_id, '批量未来乙')
    env.service.process(env.store.claim())
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    with env.store.connect() as db:
        state = json.loads(db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])['imageBatch']
    child = state['children'][env.section_id]
    frozen = _frozen(env.store, child['runId'])
    assert frozen['visionSystem'] == parent['tasks'][0]['visionSystem']
    assert frozen['placementSystem'] == parent['tasks'][0]['placementSystem']
    assert '批量未来乙' not in frozen['visionSystem'] and '批量未来乙' not in frozen['placementSystem']
    assert ('imagePromptSnapshot' not in frozen) == legacy
    if not legacy:
        assert frozen_prompts(env.store, child['jobId']) == parent['imagePromptSnapshot']
    assert latest(env)['sections'] == before['sections']


def test_article_type_uses_queue_time_edit_and_fixed_enumeration(workbench):
    store, models, service = workbench
    source = publish(store)
    edit(store, 'classification_article_type', '性质甲')
    queued = service.queue(source, ['classification'])[0]
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (queued['id'],)).fetchone()[0])
    assert payload['articleTypePromptVersion'] == 2 and '性质甲' in payload['articleTypeInstructions']
    assert frozen_prompts(store, queued['id'])['classification_article_type']['version'] == 2
    edit(store, 'classification_article_type', '性质乙')
    assert execute(store, service)['status'] == 'succeeded'
    assert all('性质甲' in messages[0]['content'] and '性质乙' not in messages[0]['content'] for _, messages in models.calls)
    packet = json.loads(models.calls[0][1][-1]['content'])
    assert packet['allowedArticleTypes'] == ['fact', 'opinion', 'technical']
    assert service.queue(source, ['classification'])[0]['id'] != queued['id']


def test_old_classification_without_opt_in_never_acquires_new_type_override(workbench):
    store, models, service = workbench
    source = publish(store)
    queued = service.queue(source, ['classification'])[0]
    legacy_job(store, queued)
    edit(store, 'classification_article_type', '后来新增性质指令')
    assert execute(store, service)['status'] == 'succeeded'
    assert '后来新增性质指令' not in models.calls[0][1][0]['content']
    assert 'articleTypePolicyVersion' not in json.loads(models.calls[0][1][-1]['content'])
