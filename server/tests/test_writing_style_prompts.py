"""Editable voice instructions use isolated stores and controlled providers only."""
import copy
import json

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.prompts import PromptLibrary, REGISTRY, STYLE_PROMPT_IDS, OFFICIAL_BODY_PROMPT_ID
from server.store import Store, dumps
from server.writing_preferences import snapshot, apply, STYLES, OFFICIAL_PROSE_INSTRUCTIONS
from server.tests.test_writing_document import document, ready
from server.tests.test_writing_composition_integration import _frozen, _steps, _queue_parent, _parent_context
from server.tests.test_writing_edit_integrity import request_for
from server.tests.test_writing_title_candidates import bench as title_bench, no_drafting_side_effects


LOCAL = {'X-Wenshu-Client': 'local'}
STYLE_IDS = (*STYLE_PROMPT_IDS.values(), OFFICIAL_BODY_PROMPT_ID)


def edit_style(store, prompt_id, marker):
    library = PromptLibrary(store)
    item = next(item for item in library.list()['items'] if item['id'] == prompt_id)
    return library.update(prompt_id, REGISTRY[prompt_id]['content'] + '\n' + marker, item['version'])


def edit_official(store, marker):
    return {prompt_id: edit_style(store, prompt_id, marker + prompt_id)
            for prompt_id in (STYLE_PROMPT_IDS['official'], OFFICIAL_BODY_PROMPT_ID)}


def job_snapshot(store, job_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                     (job_id,)).fetchone()[0])


@pytest.mark.parametrize('prompt_id', STYLE_IDS)
def test_voice_catalogue_http_save_conflict_restore_and_no_work(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    app = create_app(data_dir=store.root)
    with TestClient(app) as client:
        catalog = client.get('/api/prompts').json()
        assert {'id': 'styles', 'name': '文风'} in catalog['categories']
        styles = [item for item in catalog['items'] if item['category'] == 'styles']
        assert {item['id'] for item in styles} == set(STYLE_IDS)
        item = next(item for item in styles if item['id'] == prompt_id)
        assert item['content'] == REGISTRY[prompt_id]['content']
        assert item['version'] == 1 and item['updatedAt'] is None
        body = {'content': item['content'] + '\n合成验收用表达要求。', 'expectedVersion': 1}
        saved = client.put('/api/prompts/' + prompt_id, headers=LOCAL, json=body)
        assert saved.status_code == 200 and saved.json()['version'] == 2
        assert client.put('/api/prompts/' + prompt_id, headers=LOCAL, json=body).status_code == 409
        restored = client.put('/api/prompts/' + prompt_id, headers=LOCAL,
                              json={'content': item['content'], 'expectedVersion': 2})
        assert restored.status_code == 200 and restored.json()['version'] == 3
        assert restored.json()['content'] == item['content']
    restarted = PromptLibrary(Store(store.root))
    assert next(item for item in restarted.list()['items'] if item['id'] == prompt_id) == restored.json()
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM writing_projects').fetchone()[0] == 0


@pytest.mark.parametrize('style', STYLES)
@pytest.mark.parametrize('stage', ['title', 'outline', 'section', 'edit'])
def test_edited_voice_only_applies_to_selected_style_and_correct_stage(tmp_path, style, stage):
    store = Store(tmp_path / (style + stage))
    for prompt_id in STYLE_IDS:
        edit_style(store, prompt_id, '验收标记_' + prompt_id)
    with store.connect() as db:
        frozen = snapshot({'writingStyle': style}, db=db)
    section = {'id': 'isolated', 'title': '已确认标题'}
    packet = {'requirements': '既有任务要求', 'outputSchema': {}}
    system = apply('既有基础指令', packet, frozen, section=section if stage in ('section', 'edit') else None,
                   title=stage == 'title', edit=stage == 'edit')
    assert '验收标记_' + STYLE_PROMPT_IDS[style] in system
    for other in set(STYLES) - {style}:
        assert '验收标记_' + STYLE_PROMPT_IDS[other] not in system
    assert ('验收标记_' + OFFICIAL_BODY_PROMPT_ID in system) == (style == 'official' and stage in ('section', 'edit'))
    assert '程序固定约束：' in system and '固定资料及事实边界' in system
    assert 'stylePromptSnapshot' not in packet['writingPreferences']
    assert all(item['version'] == 2 for item in frozen['stylePromptSnapshot'].values())


def test_builtin_voice_text_remains_identical_without_edits():
    for style, (_, instructions) in STYLES.items():
        frozen = snapshot({'writingStyle': style})
        assert frozen['style']['instructions'] == instructions
        if style == 'official':
            assert frozen['officialProseInstructions'] == OFFICIAL_PROSE_INSTRUCTIONS


def test_general_and_body_read_one_database_snapshot_before_concurrent_edit(tmp_path):
    store = Store(tmp_path / 'atomic-voice')
    before = edit_official(store, '同一旧版本_')
    reads = []

    class ConcurrentEdit:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def execute(self, sql, parameters=()):
            if sql.startswith('SELECT * FROM prompt_overrides'):
                reads.append(parameters[0])
                if parameters[0] == OFFICIAL_BODY_PROMPT_ID:
                    edit_official(store, '并发新版本_')
            return self.db.execute(sql, parameters)

    with store.connect() as db:
        frozen = snapshot({'writingStyle': 'official'}, db=ConcurrentEdit(db))
    assert reads == [STYLE_PROMPT_IDS['official'], OFFICIAL_BODY_PROMPT_ID]
    assert frozen['stylePromptSnapshot'] == {
        prompt_id: {key: item[key] for key in ('content', 'version')} for prompt_id, item in before.items()}
    assert '同一旧版本_' in frozen['style']['instructions']
    assert '同一旧版本_' in frozen['officialProseInstructions']
    assert '并发新版本_' not in frozen['sectionSystemSuffix']
    with store.connect() as db:
        after = snapshot({'writingStyle': 'official'}, db=db)
    assert all(item['version'] == 3 for item in after['stylePromptSnapshot'].values())


def test_queued_chapter_uses_saved_voice_and_new_chapter_uses_new_edit(document):
    store, gateway, service, _ = document
    gateway.config['contextChars'] = 40000
    project = ready(document)
    edit_official(store, '本任务甲_')
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, queued['runId'])
    saved = copy.deepcopy(frozen)
    assert all(item['version'] == 2 for item in frozen['preferenceSnapshot']['stylePromptSnapshot'].values())
    assert all(job_snapshot(store, queued['job']['id'])[prompt_id]['version'] == 2
               for prompt_id in frozen['preferenceSnapshot']['stylePromptSnapshot'])
    edit_official(store, '之后乙_')
    assert service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])['runId'] == queued['runId']
    captured = []
    complete = gateway.complete
    gateway.complete = lambda config, messages: (captured.append(copy.deepcopy(messages)), complete(config, messages))[1]
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert '本任务甲_' in captured[0][0]['content'] and '之后乙_' not in captured[0][0]['content']
    assert _frozen(store, queued['runId']) == saved
    project = service.get(project['id'])
    next_run = service.queue(project['id'], project['version'], 'section', project['sections'][1]['id'])
    assert '之后乙_' in _frozen(store, next_run['runId'])['messages'][0]['content']


@pytest.mark.parametrize('strategy', ['single', 'sequential'])
def test_document_freezes_one_voice_for_all_chapters(document, strategy):
    store, gateway, service, _ = document
    gateway.config.update(contextChars=40000, maxOutputTokens=20000)
    words = (200, 200, 200) if strategy == 'single' else (3334, 3333, 3333)
    project = ready(document, words=words)
    edit_official(store, '整稿甲_')
    queued = service.queue_document(project['id'], project['version'])
    frozen = _frozen(store, queued['runId'])
    steps = _steps(store, queued['runId'])
    assert frozen['plan']['strategy'] == strategy
    assert all(step['preferenceSnapshot'] == frozen['preferenceSnapshot'] for step in steps)
    assert all(item['version'] == 2 for item in frozen['preferenceSnapshot']['stylePromptSnapshot'].values())
    edit_official(store, '后来乙_')
    messages = service._document_messages(frozen, steps)
    assert messages[0]['content'].count('整稿甲_' + OFFICIAL_BODY_PROMPT_ID) == 1
    assert '后来乙_' not in dumps(messages)
    captured = []
    complete = gateway.complete
    gateway.complete = lambda config, messages: (captured.append(copy.deepcopy(messages)), complete(config, messages))[1]
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(captured) == (1 if strategy == 'single' else 3)
    assert all('整稿甲_' in messages[0]['content'] and '后来乙_' not in dumps(messages) for messages in captured)


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('kind', ['outline', 'section', 'document'])
def test_automatic_children_inherit_actual_voice_without_backfilling_old_metadata(document, legacy, kind):
    store, gateway, service, _ = document
    gateway.config.update(contextChars=40000, maxOutputTokens=20000)
    project = ready(document)
    edit_official(store, '父任务甲_')
    parent = _queue_parent(document, project)
    frozen = _frozen(store, parent['runId'])
    preference = frozen['preferenceSnapshot']
    if legacy:
        preference.pop('stylePromptSnapshot')
        preference.pop('structurePromptSnapshot', None)
        frozen['compositionSnapshot'].pop('compositionPromptSnapshot', None)
        with store.connect() as db:
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), parent['runId']))
    edit_official(store, '实时乙_')
    _parent_context(service, gateway, parent, store.claim())
    queued = (service.queue_document(project['id'], project['version']) if kind == 'document' else
              service.queue(project['id'], project['version'], kind,
                            project['sections'][0]['id'] if kind == 'section' else None))
    child = _frozen(store, queued['runId'])
    assert child['preferenceSnapshot'] == preference
    assert ('stylePromptSnapshot' not in child['preferenceSnapshot']) == legacy
    messages = child.get('messages') or [child['documentRequirements']]
    assert '父任务甲_' in dumps(messages) and '实时乙_' not in dumps(messages)
    child_prompts = job_snapshot(store, queued['job']['id'])
    assert (OFFICIAL_BODY_PROMPT_ID not in child_prompts) == legacy


@pytest.mark.parametrize('operation', ['polish', 'rewrite', 'shorten', 'expand'])
def test_selection_edit_freezes_voice_versions_and_selected_range(document, operation):
    store, gateway, service, _ = document
    gateway.config['contextChars'] = 40000
    project = ready(document, modes=['extended'] * 3)
    text = '段首保留。选中句调整表达。段尾保留。'
    project = service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'content': text})
    edit_official(store, '选区甲_')
    selected = '选中句调整表达。'
    start = text.index(selected)
    queued = service.queue_edit(project['id'], project['version'], project['sections'][0]['id'],
                                request_for(project, start, start + len(selected), operation))
    frozen = _frozen(store, queued['runId'])
    assert all(item['version'] == 2 for item in frozen['preferenceSnapshot']['stylePromptSnapshot'].values())
    assert all(job_snapshot(store, queued['job']['id'])[prompt_id]['version'] == 2
               for prompt_id in frozen['preferenceSnapshot']['stylePromptSnapshot'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert packet['selectedText'] == selected and packet['operation'] == operation
    assert '仅修改选区' in frozen['messages'][0]['content']
    edit_official(store, '之后乙_')
    assert _frozen(store, queued['runId']) == frozen
    assert service.get(project['id'])['sections'][0]['content'] == text and not gateway.calls


@pytest.mark.parametrize('style', STYLES)
def test_title_assistance_uses_selected_live_general_voice_only(title_bench, style):
    store, gateway, service = title_bench
    for prompt_id in STYLE_IDS:
        edit_style(store, prompt_id, '拟题标记_' + prompt_id)
    service.assist('title', {'topic': '合成资料治理', 'candidateCount': 3, 'writingStyle': style})
    assert len(gateway.calls) == 1
    system = gateway.calls[0][1][0]['content']
    assert '拟题标记_' + STYLE_PROMPT_IDS[style] in system
    assert '拟题标记_' + OFFICIAL_BODY_PROMPT_ID not in system
    assert OFFICIAL_PROSE_INSTRUCTIONS not in system
    no_drafting_side_effects(store)
