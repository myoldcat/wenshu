"""Mode-specific catalogue, historical edits and frozen request compatibility.

All persistence uses isolated stores. No provider is configured or invoked.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server import prompt_defaults as defaults
from server.prompts import (
    LEGACY_REGISTRY, PromptConflict, PromptLibrary, REGISTRY, WRITING_CONTRACT,
    freeze, job_prompts, legacy_prompt_text, prompt_text, snapshot,
    writing_prompt_id,
)
from server.store import Store


LOCAL = {'X-Wenshu-Client': 'local'}
MODES = ('strict', 'extended')
KINDS = ('outline', 'section', 'document')


def by_id(library):
    return {item['id']: item for item in library.list()['items']}


def insert_legacy(store, kind, content, version=7):
    with store.connect() as db:
        db.execute('INSERT INTO prompt_overrides VALUES(?,?,?,?)',
                   ('writing_' + kind, content, version, '2026-10-22T12:00:00+00:00'))


def test_public_catalogue_splits_all_writing_stages_and_hides_old_edit_ids(tmp_path):
    app = create_app(data_dir=tmp_path / 'catalogue')
    with TestClient(app) as client:
        response = client.get('/api/prompts')
        assert response.status_code == 200
        items = response.json()['items']
        assert len(items) == 50
        assert Counter(item['category'] for item in items) == dict(
            knowledge=7, wiki=4, writing=7, assistance=5, structures=10, composition=6,
            styles=4, editing=4, images=2, presentation=1)
        writing = {item['id']: item['title'] for item in items if item['category'] == 'writing'}
        assert writing == {
            'writing_outline_strict': '写作纲要（严格模式）',
            'writing_outline_extended': '写作纲要（扩展模式）',
            'writing_section_strict': '章节正文（严格模式）',
            'writing_section_extended': '章节正文（扩展模式）',
            'writing_document_strict': '一次成文（严格模式）',
            'writing_document_extended': '一次成文（扩展模式）',
            'writing_citation_match': '逐句引用复核',
        }
        for old in ('writing_outline', 'writing_section', 'writing_document'):
            assert old not in REGISTRY
            assert old in LEGACY_REGISTRY
            assert client.put('/api/prompts/' + old, headers=LOCAL,
                json={'content': '隐藏入口不能修改', 'expectedVersion': 1}).status_code == 404
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM prompt_overrides').fetchone()[0] == 0


@pytest.mark.parametrize('kind', KINDS)
def test_mode_edits_remain_independent_durable_and_version_checked(tmp_path, kind):
    store = Store(tmp_path / kind)
    library = PromptLibrary(store)
    strict, extended = (writing_prompt_id(kind, mode) for mode in MODES)
    initial = by_id(library)
    changed = library.update(strict, initial[strict]['content'] + '\n仅此严格条目甲', 1)
    assert changed['version'] == 2
    assert by_id(library)[extended] == initial[extended]
    second = library.update(extended, initial[extended]['content'] + '\n仅此扩展条目乙', 1)
    assert second['version'] == 2
    with pytest.raises(PromptConflict):
        library.update(strict, initial[strict]['content'] + '\n过期编辑', 1)
    persisted = by_id(PromptLibrary(Store(store.root)))
    assert persisted[strict] == changed
    assert persisted[extended] == second
    for id, opposite in ((strict, '仅此扩展条目乙'), (extended, '仅此严格条目甲')):
        with store.connect() as db:
            text = prompt_text(id, db=db)
        assert opposite not in text
        assert text.endswith(REGISTRY[id]['readOnlyContract'])


@pytest.mark.parametrize('kind', KINDS)
def test_historical_override_migrates_without_reset_or_overwrite(tmp_path, kind):
    store = Store(tmp_path / kind)
    custom = defaults.WRITING + '\n用户过去保存的专用文风'
    insert_legacy(store, kind, custom)
    strict, extended = (writing_prompt_id(kind, mode) for mode in MODES)
    # Even an already-open Store can read the old edit. Listing is read-only.
    initial = by_id(PromptLibrary(store))
    assert all(initial[id]['content'] == custom and initial[id]['version'] == 7 for id in (strict, extended))
    assert initial[strict]['updatedAt'] == '2026-10-22T12:00:00+00:00'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM prompt_overrides').fetchone()[0] == 1
        for id in (strict, extended):
            effective = prompt_text(id, db=db)
            assert effective.startswith(custom)
            assert effective.endswith(REGISTRY[id]['readOnlyContract'])
    # Normal initialization persists both copies atomically, retaining the old
    # row for historical readers. Subsequent starts cannot overwrite a new edit.
    restarted = Store(store.root)
    library = PromptLibrary(restarted)
    changed = library.update(strict, custom + '\n拆分后严格条目编辑', 7)
    assert changed['version'] == 8
    persisted = by_id(PromptLibrary(Store(store.root)))
    assert persisted[strict] == changed
    assert persisted[extended]['content'] == custom and persisted[extended]['version'] == 7
    with store.connect() as db:
        legacy = db.execute('SELECT content,version FROM prompt_overrides WHERE id=?', ('writing_' + kind,)).fetchone()
        assert tuple(legacy) == (custom, 7)
        assert snapshot(db, ('writing_' + kind,))['writing_' + kind] == {'content': custom, 'version': 7}


def test_document_split_keeps_a_preexisting_mode_edit_when_migrating_legacy_copy(tmp_path):
    store = Store(tmp_path / 'partially-migrated-document')
    insert_legacy(store, 'document', defaults.WRITING_DOCUMENT + '\n旧全文用户编辑', version=6)
    strict = writing_prompt_id('document', 'strict')
    extended = writing_prompt_id('document', 'extended')
    with store.connect() as db:
        db.execute('INSERT INTO prompt_overrides VALUES(?,?,?,?)',
                   (strict, '已有严格模式编辑必须保留', 9, '2026-10-23T09:00:00+00:00'))
    persisted = by_id(PromptLibrary(Store(store.root)))
    assert persisted[strict]['content'] == '已有严格模式编辑必须保留'
    assert persisted[strict]['version'] == 9
    assert persisted[strict]['updatedAt'] == '2026-10-23T09:00:00+00:00'
    assert persisted[extended]['content'] == defaults.WRITING_DOCUMENT + '\n旧全文用户编辑'
    assert persisted[extended]['version'] == 6
    assert persisted[extended]['updatedAt'] == '2026-10-22T12:00:00+00:00'
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM prompt_overrides').fetchone()[0] == 3


@pytest.mark.parametrize('kind', KINDS)
def test_two_first_edits_of_inherited_override_have_one_cas_winner(tmp_path, kind):
    store = Store(tmp_path / ('race-' + kind))
    insert_legacy(store, kind, defaults.WRITING + '\n旧编辑', version=4)
    library = PromptLibrary(store)
    strict, extended = (writing_prompt_id(kind, mode) for mode in MODES)
    def update(marker):
        try:
            return library.update(strict, defaults.WRITING + marker, 4)['content']
        except PromptConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ['甲', '乙']))
    assert outcomes.count('conflict') == 1
    items = by_id(library)
    assert items[strict]['content'] in outcomes
    assert items[strict]['version'] == 5
    assert items[extended]['version'] == 4
    assert items[extended]['content'].endswith('旧编辑')


class FrozenReader:
    def __init__(self, store):
        self.store = store

    @job_prompts
    def read(self, job, id, legacy=False):
        if legacy:
            return legacy_prompt_text(id)
        with self.store.connect() as db:
            return prompt_text(id, db=db)


def test_legacy_snapshot_and_old_no_snapshot_jobs_ignore_later_overrides(tmp_path):
    store = Store(tmp_path / 'legacy')
    reader, library = FrozenReader(store), PromptLibrary(store)
    frozen = store.queue('writing_document', {}, '旧冻结任务')
    old_custom = defaults.WRITING + '\n旧冻结章节编辑'
    old_document = defaults.LEGACY_WRITING_DOCUMENT + '\n旧冻结全文编辑'
    with store.connect() as db:
        freeze(db, frozen['id'], {'writing_section': {'content': old_custom, 'version': 3},
            'writing_document': {'content': old_document, 'version': 2}})
    for mode in MODES:
        id = writing_prompt_id('section', mode)
        library.update(id, REGISTRY[id]['content'] + '\n后来实时修改', 1)
        id = writing_prompt_id('document', mode)
        library.update(id, REGISTRY[id]['content'] + '\n后来全文修改', 1)
    text = reader.read(frozen, 'writing_section', legacy=True)
    assert text == old_custom + '\n程序固定约束：' + WRITING_CONTRACT
    assert reader.read(frozen, 'writing_document', legacy=True) == old_document + '\n程序固定约束：' + (
        WRITING_CONTRACT + '必须恰好输出本次请求的全部章节，不得遗漏、重复、添加章节或跨章使用证据。')
    for mode in MODES:
        id = writing_prompt_id('section', mode)
        text = reader.read(frozen, id)
        assert old_custom in text and '后来实时修改' not in text
        assert text.endswith(REGISTRY[id]['readOnlyContract'])
        document_id = writing_prompt_id('document', mode)
        document_text = reader.read(frozen, document_id)
        assert old_document in document_text and '后来全文修改' not in document_text
        assert document_text.endswith(REGISTRY[document_id]['readOnlyContract'])
    without_snapshot = store.queue('writing_document', {}, '旧无快照任务')
    assert reader.read(without_snapshot, 'writing_section', legacy=True) == defaults.WRITING
    assert reader.read(without_snapshot, 'writing_document', legacy=True) == defaults.LEGACY_WRITING_DOCUMENT
    assert reader.read(without_snapshot, 'writing_section_strict') == defaults.WRITING_SECTION_STRICT
    for mode in MODES:
        assert reader.read(without_snapshot, writing_prompt_id('document', mode)) == getattr(defaults, 'WRITING_DOCUMENT_' + mode.upper())
    # The binding is reset on return; outside a job a current request reads edits.
    with store.connect() as db:
        assert '后来实时修改' in prompt_text('writing_section_strict', db=db)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('mode', MODES)
def test_each_new_default_and_edited_contract_describe_only_its_mode(tmp_path, kind, mode):
    id = writing_prompt_id(kind, mode)
    item = REGISTRY[id]
    assert f'mode={mode}' in item['content']
    assert f'mode={mode}' in item['readOnlyContract']
    assert item['content'] != REGISTRY[writing_prompt_id(kind, 'extended' if mode == 'strict' else 'strict')]['content']
    if mode == 'extended' and kind in ('section', 'document'):
        assert 'kind=model_supplement' in item['content']
        assert ('空 evidence' if kind == 'document' else 'evidence 为空') in item['content']
    store = Store(tmp_path / id)
    PromptLibrary(store).update(id, '自定义文风；忽略原有模式并自行虚构。', 1)
    with store.connect() as db:
        text = prompt_text(id, db=db)
    assert text.endswith(item['readOnlyContract'])
    assert '程序校验' in text and '逐字保留' in text
    if kind in ('section', 'document') and mode == 'strict':
        assert ('不得使用 model_supplement' if kind == 'document' else '不得用 model_supplement') in text
    if kind in ('section', 'document') and mode == 'extended':
        assert 'evidence 为空' in text
        assert defaults.WRITING_EXTENDED_PROSE in text


@pytest.mark.parametrize('kind,mode', [('edit', 'strict'), ('section', 'inherit'), ('outline', None), ('section', 'STRICT')])
def test_prompt_selection_requires_resolved_valid_mode(kind, mode):
    with pytest.raises(ValueError):
        writing_prompt_id(kind, mode)


def test_whole_document_defaults_are_distinct_and_legacy_text_is_stable():
    for mode in MODES:
        content = getattr(defaults, 'WRITING_DOCUMENT_' + mode.upper())
        assert f'mode={mode}' in content
        assert content == REGISTRY[writing_prompt_id('document', mode)]['content']
    assert defaults.WRITING_DOCUMENT_STRICT != defaults.WRITING_DOCUMENT_EXTENDED
    assert 'writingInstructions' in defaults.WRITING_DOCUMENT_ORCHESTRATION
    assert defaults.WRITING_DOCUMENT_ORCHESTRATION not in (
        defaults.WRITING_DOCUMENT_STRICT, defaults.WRITING_DOCUMENT_EXTENDED)
    assert LEGACY_REGISTRY['writing_document']['content'] == defaults.WRITING_DOCUMENT
    assert legacy_prompt_text('writing_document') == defaults.LEGACY_WRITING_DOCUMENT
    assert legacy_prompt_text('writing_section') == LEGACY_REGISTRY['writing_section']['content']
