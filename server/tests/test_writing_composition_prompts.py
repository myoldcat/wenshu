"""New composition defaults and contracts are opt-in, compiled and durable."""
import copy

import pytest

from server import prompt_defaults as defaults
from server import prompts
from server.prompts import PromptLibrary, REGISTRY, prompt_text, snapshot
from server.store import Store


POLICY = 'reader-composition-v1'
IDS = tuple('writing_' + kind + '_' + mode
            for kind in ('outline', 'section', 'document') for mode in ('strict', 'extended'))


@pytest.mark.parametrize('id', IDS)
def test_only_explicit_new_policy_selects_composition_defaults(id):
    before = copy.deepcopy(REGISTRY[id])
    original = REGISTRY[id]['content']
    assert prompt_text(id) == original
    composed = prompt_text(id, composition_policy=POLICY)
    assert composed != original
    assert '每个实质段落必须 kind=supported' not in composed
    assert '中心问题' in composed
    if '_outline_' in id:
        assert '不按资料一篇一章' in composed and '不逐条绑定引文' in composed
    else:
        assert '起承转合不是固定四段式' in composed
        assert '允许 evidence=[]' in composed
        assert 'claim 定位正文，两者不必相同' in composed
        assert '不得翻译、改写或拼接 quote' in composed
        if id.endswith('_strict'):
            assert '常识性说明免逐句引文，不等于允许' in composed
            assert '不得推理、推算或扩充' in composed
            assert '没有任何可忠实表达的正文时才返回空 paragraphs' in composed
        else:
            assert 'kind=model_supplement' in composed and '不能把推理伪装为原文直接陈述' in composed
    assert REGISTRY[id] == before
    assert prompt_text(id) == original


@pytest.mark.parametrize('id', ['writing_section_strict', 'writing_document_extended'])
def test_custom_style_is_retained_but_new_fixed_contract_replaces_old_contract(id, tmp_path):
    store = Store(tmp_path / id)
    authored = '请使用短句，段落长短自然变化。每段全部内容必须附引文。'
    library = PromptLibrary(store)
    saved = library.update(id, authored, 1)
    with store.connect() as db:
        old = prompt_text(id, db=db)
        new = prompt_text(id, db=db, composition_policy=POLICY)
        frozen = snapshot(db, [id], composition_policy=POLICY)
    assert old == authored + '\n程序固定约束：' + REGISTRY[id]['readOnlyContract']
    assert new.startswith(authored + '\n程序固定约束：')
    assert '保留自定义指令的行文风格与篇章意图' in new
    assert '不覆盖本次选择性引用规则' in new
    assert '不放宽严格模式的事实、观点与推理边界' in new
    assert REGISTRY[id]['readOnlyContract'] not in new[len(authored):]
    assert frozen[id] == {'content': new, 'version': saved['version'], 'compositionPolicy': POLICY}
    assert next(item for item in library.list()['items'] if item['id'] == id)['content'] == authored


def test_new_snapshot_compiles_six_actual_prompts_without_changing_old_snapshot_shape(tmp_path):
    store = Store(tmp_path / 'snapshots')
    ids = (*IDS, 'summary', 'writing_citation_match')
    with store.connect() as db:
        old = snapshot(db, ids)
        new = snapshot(db, ids, composition_policy=POLICY)
        assert snapshot(db, ids) == old
        assert db.execute('SELECT count(*) FROM prompt_overrides').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
    for id in IDS:
        assert old[id] == {'content': REGISTRY[id]['content'], 'version': 1}
        assert new[id] == {'content': prompt_text(id, composition_policy=POLICY),
                           'version': 1, 'compositionPolicy': POLICY}
    assert new['summary'] == old['summary']
    assert new['writing_citation_match'] == old['writing_citation_match']


def test_compiled_snapshot_survives_changed_defaults_overrides_and_repeated_freezing(tmp_path, monkeypatch):
    store = Store(tmp_path / 'compiled')
    id = 'writing_section_strict'
    library = PromptLibrary(store)
    library.update(id, '按队列当时的简练文风组织段落。', 1)
    with store.connect() as db:
        compiled = snapshot(db, [id], composition_policy=POLICY)
    original = copy.deepcopy(compiled)
    library.update(id, '后来的不同文风不能改写旧任务。', 2)
    monkeypatch.setitem(prompts._WRITING_COMPOSITION_DEFAULTS, id, '后来代码中的新默认，不能渗入旧任务。')
    monkeypatch.setattr(prompts, '_WRITING_COMPOSITION_PRIORITY', '后来的新contract也不能重新附加。')
    token = prompts._ACTIVE.set(compiled)
    try:
        assert prompt_text(id, composition_policy=POLICY) == original[id]['content']
        with store.connect() as db:
            assert snapshot(db, [id], composition_policy=POLICY) == original
    finally:
        prompts._ACTIVE.reset(token)
    assert compiled == original


def test_compiled_snapshot_applies_only_requested_values_without_reassembly():
    id = 'writing_section_strict'
    compiled = {id: {'content': '冻结的写法 {{marker}}', 'version': 7, 'compositionPolicy': POLICY}}
    token = prompts._ACTIVE.set(compiled)
    try:
        assert prompt_text(id, composition_policy=POLICY, values={'marker': '甲'}) == '冻结的写法 甲'
        assert prompt_text(id, composition_policy=POLICY) == '冻结的写法 {{marker}}'
    finally:
        prompts._ACTIVE.reset(token)


@pytest.mark.parametrize('policy', ['', 'reader-composition-v2', True])
def test_unknown_policy_is_rejected_instead_of_silently_changing_protocol(tmp_path, policy):
    with pytest.raises(ValueError, match='策略版本'):
        prompt_text('writing_section_strict', composition_policy=policy)
    with Store(tmp_path / 'unknown').connect() as db:
        with pytest.raises(ValueError, match='策略版本'):
            snapshot(db, IDS, composition_policy=policy)


def test_mismatched_compiled_policy_cannot_be_reinterpreted_as_a_custom_prompt():
    id = 'writing_document_strict'
    token = prompts._ACTIVE.set({id: {'content': '另一个冻结协议', 'version': 2, 'compositionPolicy': 'future'}})
    try:
        with pytest.raises(ValueError, match='版本不一致'):
            prompt_text(id, composition_policy=POLICY)
    finally:
        prompts._ACTIVE.reset(token)


def test_unrelated_and_legacy_prompts_keep_their_original_mode_and_contract():
    for id in ('summary', 'writing_citation_match', 'writing_section'):
        assert prompt_text(id, composition_policy=POLICY) == prompt_text(id)
    assert defaults.WRITING_SECTION_STRICT == REGISTRY['writing_section_strict']['content']
    assert '每个实质段落必须 kind=supported' in defaults.WRITING_SECTION_STRICT


@pytest.mark.parametrize('id', IDS)
def test_exact_unmodified_builtin_prefix_is_replaced_without_changing_authored_suffix_or_storage(id, tmp_path):
    store = Store(tmp_path / id)
    builtin = REGISTRY[id]['content']
    suffix = '\n\n自定义偏好：用短句自然承接，不要强行增加结论。\n保留这行的空白。  \n'
    raw = builtin + suffix
    library = PromptLibrary(store)
    saved = library.update(id, raw, 1)
    with store.connect() as db:
        legacy = prompt_text(id, db=db)
        composed = prompt_text(id, db=db, composition_policy=POLICY)
        compiled = snapshot(db, [id], composition_policy=POLICY)[id]
        row = db.execute('SELECT content,version FROM prompt_overrides WHERE id=?', (id,)).fetchone()
    assert row['content'] == raw and row['version'] == saved['version'] == 2
    assert composed.startswith(suffix + '\n程序固定约束：')
    assert builtin not in composed
    assert composed.endswith(prompts._WRITING_COMPOSITION_DEFAULTS[id])
    unreduced = (raw + '\n程序固定约束：' + prompts._WRITING_COMPOSITION_PRIORITY
                 + prompts.BASE_CONTRACT + prompts.CHINESE_OUTPUT_CONTRACT
                 + prompts._WRITING_COMPOSITION_DEFAULTS[id])
    assert len(composed) == len(unreduced) - len(builtin)
    assert compiled == {'content': composed, 'version': 2, 'compositionPolicy': POLICY}
    assert legacy == raw + '\n程序固定约束：' + REGISTRY[id]['readOnlyContract']
    if id.endswith('_strict') and '_outline_' not in id:
        assert '不得推理、推算或扩充' in composed
        assert '常识性说明免逐句引文，不等于允许' in composed
        assert '不得翻译、改写或拼接 quote' in composed
    assert next(item for item in library.list()['items'] if item['id'] == id)['content'] == raw


@pytest.mark.parametrize('change', ['first', 'middle', 'last', 'embedded', 'leading_space'])
def test_real_custom_rewrites_are_never_mistaken_for_an_unmodified_builtin_prefix(tmp_path, change):
    id = 'writing_section_strict'
    builtin = REGISTRY[id]['content']
    suffix = '\n保持用户真正改写的整份内容。'
    middle = len(builtin) // 2
    raw = {
        'first': '我' + builtin[1:] + suffix,
        'middle': builtin[:middle] + '【用户改写】' + builtin[middle + 1:] + suffix,
        'last': builtin[:-1] + '【用户改写的结尾】' + suffix,
        'embedded': '这是我重新编写的提示词，以下文字仅供参考：\n' + builtin + suffix,
        'leading_space': ' ' + builtin + suffix,
    }[change]
    store = Store(tmp_path / change)
    PromptLibrary(store).update(id, raw, 1)
    with store.connect() as db:
        actual = prompt_text(id, db=db, composition_policy=POLICY)
    assert actual.startswith(raw + '\n程序固定约束：')
    assert '不放宽严格模式的事实、观点与推理边界' in actual


def test_prefix_replacement_is_frozen_once_and_does_not_change_old_raw_or_compiled_jobs(tmp_path, monkeypatch):
    store = Store(tmp_path / 'restart-prefix')
    id = 'writing_document_strict'
    builtin = REGISTRY[id]['content']
    raw = builtin + '\n用户原样后缀甲。  '
    PromptLibrary(store).update(id, raw, 1)
    with store.connect() as db:
        old_raw = snapshot(db, [id])
        compiled = snapshot(db, [id], composition_policy=POLICY)
    original = copy.deepcopy(compiled)
    PromptLibrary(store).update(id, builtin + '\n之后编辑的后缀乙。', 2)
    restarted = Store(store.root)
    monkeypatch.setitem(prompts._WRITING_COMPOSITION_DEFAULTS, id, '后来代码里的新默认不能替换旧编译结果。')
    token = prompts._ACTIVE.set(compiled)
    try:
        assert prompt_text(id, composition_policy=POLICY) == original[id]['content']
        with restarted.connect() as db:
            assert snapshot(db, [id], composition_policy=POLICY) == original
    finally:
        prompts._ACTIVE.reset(token)
    token = prompts._ACTIVE.set(old_raw)
    try:
        assert prompt_text(id) == raw + '\n程序固定约束：' + REGISTRY[id]['readOnlyContract']
    finally:
        prompts._ACTIVE.reset(token)
    assert compiled == original
