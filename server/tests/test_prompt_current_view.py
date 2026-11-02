"""Effective editing is explicit, idempotent and isolated from frozen requests."""
import json

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.prompts import (PromptLibrary, PromptConflict, REGISTRY, EFFECTIVE_CONTENT_FORMAT,
                            EFFECTIVE_CONTENT_LIMIT, _WRITING_COMPOSITION_DEFAULTS,
                            _effective_composition_suffix, snapshot, freeze, job_prompts,
                            prompt_text)
from server.prompt_defaults import WRITING_COMPOSITION_POLICY
from server.store import Store
from server.tests.test_writing_document import document, ready
from server.tests.test_writing_composition_integration import _frozen


LOCAL = {'X-Wenshu-Client': 'local'}
MODE_IDS = tuple(_WRITING_COMPOSITION_DEFAULTS)


def item_for(library, prompt_id):
    return next(item for item in library.list()['items'] if item['id'] == prompt_id)


def counts(store):
    with store.connect() as db:
        return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                     for table in ('prompt_overrides', 'prompt_content_formats', 'jobs', 'sources'))


@pytest.mark.parametrize('prompt_id', MODE_IDS)
def test_current_view_read_and_unchanged_effective_put_never_migrate(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    app = create_app(data_dir=store.root)
    before = counts(store)
    with TestClient(app) as client:
        initial = next(item for item in client.get('/api/prompts').json()['items'] if item['id'] == prompt_id)
        assert initial['content'] == REGISTRY[prompt_id]['content']
        assert initial['displayContent'] == initial['editableContent'] == initial['effectiveContent']
        assert initial['editableContent'] == _WRITING_COMPOSITION_DEFAULTS[prompt_id]
        assert initial['contentView'] == 'effective' and 'contentFormat' not in initial
        response = client.put('/api/prompts/' + prompt_id, headers=LOCAL, json={
            'content': initial['editableContent'], 'expectedVersion': 1, 'contentView': 'effective'})
        assert response.status_code == 200 and response.json() == initial
        assert len(client.get('/api/prompts').json()['items']) == 50
    assert counts(store) == before == (0, 0, 0, 0)


@pytest.mark.parametrize('prompt_id', MODE_IDS)
def test_effective_save_and_repeated_edit_preserve_one_current_instruction(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    library = PromptLibrary(store)
    initial = item_for(library, prompt_id)
    submitted = initial['editableContent'] + '\n作者明确的新表达。'
    saved = library.update(prompt_id, submitted, 1, content_view='effective')
    suffix = _effective_composition_suffix(prompt_id)
    assert saved['version'] == 2 and saved['contentFormat'] == EFFECTIVE_CONTENT_FORMAT
    assert saved['content'] == submitted
    assert saved['editableContent'] == submitted
    assert saved['displayContent'] == submitted + suffix and saved['fixedContent'] == suffix
    assert saved['effectiveContent'].count(_WRITING_COMPOSITION_DEFAULTS[prompt_id]) == 1
    assert saved['effectiveContent'].count(suffix) == 1
    assert library.update(prompt_id, saved['editableContent'], 2, content_view='effective') == saved
    assert library.update(prompt_id, saved['editableContent'] + suffix, 2, content_view='effective') == saved
    assert counts(store) == (1, 1, 0, 0)
    rewritten = library.update(prompt_id, '作者完整重写的当前指令。', 2, content_view='effective')
    assert rewritten['version'] == 3
    assert _WRITING_COMPOSITION_DEFAULTS[prompt_id] not in rewritten['effectiveContent']
    assert rewritten['effectiveContent'] == '作者完整重写的当前指令。' + suffix
    assert 'mode=' + prompt_id.rsplit('_', 1)[1] in rewritten['effectiveContent']
    assert 'outputSchema' in rewritten['effectiveContent']
    assert item_for(PromptLibrary(Store(store.root)), prompt_id) == rewritten


@pytest.mark.parametrize('prompt_id', MODE_IDS)
def test_legacy_edited_row_remains_raw_until_a_real_effective_change(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    library = PromptLibrary(store)
    raw = REGISTRY[prompt_id]['content'] + '\n原有显式覆盖。'
    old = library.update(prompt_id, raw, 1)
    assert old['content'] == raw and old['version'] == 2 and 'contentFormat' not in old
    assert '原有显式覆盖。' in old['editableContent']
    assert library.update(prompt_id, old['editableContent'], 2, content_view='effective') == old
    assert counts(store) == (1, 0, 0, 0)
    changed = library.update(prompt_id, old['editableContent'] + '\n当前修改。', 2, content_view='effective')
    assert changed['version'] == 3 and changed['contentFormat'] == EFFECTIVE_CONTENT_FORMAT
    assert changed['effectiveContent'].count(_WRITING_COMPOSITION_DEFAULTS[prompt_id]) == 1
    assert '原有显式覆盖。' in changed['effectiveContent']
    with pytest.raises(PromptConflict):
        library.update(prompt_id, old['editableContent'] + '\n迟到修改。', 2, content_view='effective')
    assert item_for(library, prompt_id) == changed


def test_source_items_keep_existing_save_protocol_and_old_clients_preserve_marker(tmp_path):
    store = Store(tmp_path / 'source')
    library = PromptLibrary(store)
    source = item_for(library, 'writing_style_official')
    assert source['displayContent'] == source['editableContent'] == source['content']
    assert source['contentView'] == 'source'
    edited = library.update(source['id'], source['content'] + '\n补充措辞。', 1, content_view='source')
    assert edited['version'] == 2 and 'contentFormat' not in edited
    with pytest.raises(ValueError, match='原有内容'):
        library.update(source['id'], edited['content'], 2, content_view='effective')
    current = library.update('writing_section_strict', '当前正文指令。', 1, content_view='effective')
    updated = library.update('writing_section_strict', current['content'] + '\n旧客户端显式修改。', 2)
    assert updated['contentFormat'] == EFFECTIVE_CONTENT_FORMAT and updated['version'] == 3
    assert updated['effectiveContent'].count(_effective_composition_suffix('writing_section_strict')) == 1
    assert _WRITING_COMPOSITION_DEFAULTS['writing_section_strict'] not in updated['effectiveContent']


@pytest.mark.parametrize('prompt_id', MODE_IDS)
def test_effective_limit_includes_fixed_contract_and_remains_editable(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    library = PromptLibrary(store)
    suffix = _effective_composition_suffix(prompt_id)
    body_limit = EFFECTIVE_CONTENT_LIMIT - len(suffix)
    with pytest.raises(ValueError, match='包含程序固定约束'):
        library.update(prompt_id, '甲' * (body_limit + 1), 1, content_view='effective')
    assert counts(store) == (0, 0, 0, 0)
    saved = library.update(prompt_id, '甲' * body_limit, 1, content_view='effective')
    assert len(saved['editableContent']) == saved['maxContentLength'] == body_limit
    assert len(saved['displayContent']) == EFFECTIVE_CONTENT_LIMIT
    assert library.update(prompt_id, saved['editableContent'], 2, content_view='effective') == saved
    assert counts(store) == (1, 1, 0, 0)


@pytest.mark.parametrize('prompt_id', MODE_IDS)
def test_appending_preferences_to_editor_body_never_duplicates_generated_contract(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    library = PromptLibrary(store)
    saved = library.update(prompt_id, '作者当前指令。', 1, content_view='effective')
    suffix = _effective_composition_suffix(prompt_id)
    for index in range(1, 4):
        submitted = saved['editableContent'] + f'\n第{index}条新偏好。'
        saved = library.update(prompt_id, submitted, saved['version'], content_view='effective')
        assert saved['content'] == saved['editableContent'] == submitted
        assert suffix not in saved['editableContent']
        assert saved['effectiveContent'] == submitted + suffix
        assert saved['effectiveContent'].count(suffix) == 1
        assert _WRITING_COMPOSITION_DEFAULTS[prompt_id] not in saved['effectiveContent']
        assert saved['version'] == index + 2


@pytest.mark.parametrize('prompt_id', MODE_IDS)
def test_full_view_client_append_removes_only_previously_generated_span(tmp_path, prompt_id):
    store = Store(tmp_path / prompt_id)
    library = PromptLibrary(store)
    saved = library.update(prompt_id, '作者当前指令。', 1, content_view='effective')
    suffix = _effective_composition_suffix(prompt_id)
    for index in range(1, 4):
        submitted = saved['displayContent'] + f'\n旧客户端第{index}条新偏好。'
        expected = saved['editableContent'] + f'\n旧客户端第{index}条新偏好。'
        saved = library.update(prompt_id, submitted, saved['version'], content_view='effective')
        assert saved['editableContent'] == expected
        assert saved['effectiveContent'] == expected + suffix
        assert saved['effectiveContent'].count(suffix) == 1


@pytest.mark.parametrize('wrapper', ['json', 'quote', 'code'])
def test_contract_literals_inside_author_json_quotes_and_code_are_preserved(tmp_path, wrapper):
    prompt_id = 'writing_section_strict'
    store = Store(tmp_path / wrapper)
    library = PromptLibrary(store)
    suffix = _effective_composition_suffix(prompt_id)
    literal = (json.dumps({'说明': suffix}, ensure_ascii=False) if wrapper == 'json' else
               '「' + suffix + '」' if wrapper == 'quote' else '```text\n' + suffix + '\n```')
    body = '作者说明与字面示例：\n' + literal
    saved = library.update(prompt_id, body, 1, content_view='effective')
    assert saved['content'] == saved['editableContent'] == body
    assert saved['displayContent'] == body + suffix
    # Accept the earlier full-view protocol at its known generated span;
    # do not remove an identical literal inside the author's example.
    changed = library.update(prompt_id, saved['displayContent'] + '\n新增偏好。', 2, content_view='effective')
    assert changed['editableContent'] == body + '\n新增偏好。'
    assert literal in changed['editableContent']
    revised_body = changed['editableContent'].replace('作者说明', '作者修订说明') + '\n再次补充。'
    revised = library.update(prompt_id, revised_body, 3, content_view='effective')
    assert revised['editableContent'] == revised_body and literal in revised['editableContent']
    assert revised['displayContent'] == revised_body + suffix


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_effective_outline_contract_remains_abstract_and_has_no_body_protocol(tmp_path, mode):
    prompt_id = 'writing_outline_' + mode
    suffix = _effective_composition_suffix(prompt_id)
    for forbidden in ('kind=', 'model_supplement', 'paragraphs', 'evidence', 'blockId', 'quote', 'claim', '原文明示'):
        assert forbidden not in suffix
    assert 'mode=' + mode in suffix
    assert 'outputSchema' in suffix and 'sections和issues' in suffix
    assert '不写正文或登记规划引文' in suffix
    assert '不要求标题或goal逐项对应具体引文' in suffix
    assert '不能把资料缺口冒充已知事实' in suffix
    store = Store(tmp_path / mode)
    saved = PromptLibrary(store).update(prompt_id, '按主题组织抽象纲要。', 1, content_view='effective')
    assert saved['effectiveContent'] == saved['editableContent'] + suffix


@pytest.mark.parametrize('prompt_id', MODE_IDS)
@pytest.mark.parametrize('compiled', [False, True])
def test_existing_frozen_mode_request_never_reloads_effective_edits(tmp_path, prompt_id, compiled):
    store = Store(tmp_path / (prompt_id + str(compiled)))
    library = PromptLibrary(store)
    queued = store.queue('file', {}, '隔离快照测试')
    with store.connect() as db:
        frozen = snapshot(db, (prompt_id,), composition_policy=WRITING_COMPOSITION_POLICY if compiled else None)
        freeze(db, queued['id'], frozen)
    original = _WRITING_COMPOSITION_DEFAULTS[prompt_id]
    library.update(prompt_id, '后续有效指令。', 1, content_view='effective')

    class Reader:
        def __init__(self, store):
            self.store = store

        @job_prompts
        def read(self, job):
            return prompt_text(prompt_id, composition_policy=WRITING_COMPOSITION_POLICY)

    assert Reader(store).read(queued) == original
    with store.connect() as db:
        assert json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                     (queued['id'],)).fetchone()[0]) == frozen


def test_raw_effective_snapshot_carries_format_and_is_not_recompiled(tmp_path):
    store = Store(tmp_path / 'raw-effective')
    library = PromptLibrary(store)
    saved = library.update('writing_section_strict', '已显式保存的当前指令。', 1, content_view='effective')
    queued = store.queue('file', {}, '隔离当前快照')
    with store.connect() as db:
        frozen = snapshot(db, ('writing_section_strict',))
        freeze(db, queued['id'], frozen)
    assert frozen['writing_section_strict']['contentFormat'] == EFFECTIVE_CONTENT_FORMAT
    library.update('writing_section_strict', '更新的当前指令。', 2, content_view='effective')

    class Reader:
        def __init__(self, store):
            self.store = store

        @job_prompts
        def read(self, job):
            return prompt_text('writing_section_strict', composition_policy=WRITING_COMPOSITION_POLICY)

    assert Reader(store).read(queued) == saved['effectiveContent']


def test_existing_chapter_queue_preserves_actual_messages_when_effective_text_changes(document):
    store, gateway, service, _ = document
    gateway.config['contextChars'] = 64000
    project = ready(document)
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    original = _frozen(store, queued['runId'])
    library = PromptLibrary(store)
    current = library.update('writing_section_strict', '以后正文用这个有效指令。', 1, content_view='effective')
    assert _frozen(store, queued['runId']) == original
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    project = service.get(project['id'])
    next_run = service.queue(project['id'], project['version'], 'section', project['sections'][1]['id'])
    following = _frozen(store, next_run['runId'])
    assert current['effectiveContent'] in following['messages'][0]['content']
    assert _WRITING_COMPOSITION_DEFAULTS['writing_section_strict'] not in following['messages'][0]['content']


def test_long_lived_store_without_format_table_reads_without_migration(tmp_path):
    store = Store(tmp_path / 'old-store')
    library = PromptLibrary(store)
    library.update('writing_section_strict', '旧客户端覆盖。', 1)
    with store.connect() as db:
        db.execute('DROP TABLE prompt_content_formats')
    current = item_for(library, 'writing_section_strict')
    assert 'contentFormat' not in current
    assert library.update(current['id'], current['editableContent'], 2, content_view='effective') == current
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='prompt_content_formats'").fetchone() is None
    saved = library.update(current['id'], '用户显式改成当前指令。', 2, content_view='effective')
    assert saved['contentFormat'] == EFFECTIVE_CONTENT_FORMAT and saved['version'] == 3
