"""Reader-facing writing style and mode boundaries, with no model requests.

These inspect the submitted instruction contract and saved overrides; they do
not claim that a real model always follows the requested prose style.
"""
import pytest

from server import prompt_defaults as defaults
from server.prompts import PromptLibrary, REGISTRY, prompt_text
from server.store import Store


PROSE_IDS = ('writing_section_strict', 'writing_section_extended',
             'writing_document_strict', 'writing_document_extended',
             'writing_edit_polish', 'writing_edit_rewrite', 'writing_edit_shorten', 'writing_edit_expand')


@pytest.mark.parametrize('prompt_id', PROSE_IDS)
def test_default_and_fixed_contract_address_readers_preserve_attribution_and_route_author_notes(prompt_id):
    entry = REGISTRY[prompt_id]
    for text in (entry['content'], entry['readOnlyContract']):
        assert defaults.WRITING_READER_PROSE in text
        for rule in ('直接论述事实与分析', '引用标号', '材料中说', '材料记载', '材料指出', '材料显示',
                     '本材料', '受访者', '真实人名', '机构名', '文章名称', '不得编造来源身份',
                     '美国官员估计', '不能删成已经证实的事实', '写入 issues', '原文 quote'):
            assert rule in text
    if prompt_id.startswith('writing_edit_'):
        assert '选区外' in entry['readOnlyContract'] and '不扩大编辑范围' in entry['readOnlyContract']


def test_custom_prompt_storage_is_preserved_while_current_requests_keep_fixed_prose_boundary(tmp_path):
    store = Store(tmp_path / 'reader-contract')
    library = PromptLibrary(store)
    customized = '用户已保存的写作习惯：尽量保留作者语气。'
    for prompt_id in PROSE_IDS:
        saved = library.update(prompt_id, customized, 1)
        assert saved['content'] == customized and saved['version'] == 2
        with store.connect() as db:
            request = prompt_text(prompt_id, db=db)
            row = db.execute('SELECT content,version FROM prompt_overrides WHERE id=?', (prompt_id,)).fetchone()
        assert request.startswith(customized + '\n程序固定约束：')
        assert defaults.WRITING_READER_PROSE in request
        assert row['content'] == customized and row['version'] == 2
    reopened = PromptLibrary(Store(store.root))
    assert all(item['content'] == customized and item['version'] == 2
               for item in reopened.list()['items'] if item['id'] in PROSE_IDS)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_strict_default_and_fixed_contract_allow_faithful_paraphrase_without_new_inference():
    for prompt_id in ('writing_outline_strict', 'writing_section_strict', 'writing_document_strict', 'writing_edit_rewrite'):
        for text in (REGISTRY[prompt_id]['content'], REGISTRY[prompt_id]['readOnlyContract']):
            assert defaults.WRITING_STRICT_SOURCE in text
            assert '忠实归纳、总结、表述改写' in text and '不要求正文逐字引用' in text
            assert '并列比较原文明确事实、合并重复信息' in text
            assert '因果、预测、意图、评价、机制' in text
            assert '不得以“有据推理”“逻辑可推导”' in text
            assert '允许材料内的分析、归纳、比较和有据推理' not in text
    assert defaults.WRITING_STRICT_SOURCE not in defaults.WRITING_SECTION_EXTENDED
    assert defaults.WRITING_STRICT_SOURCE not in defaults.WRITING_DOCUMENT_EXTENDED
    assert '材料内有据推理' in defaults.WRITING_SECTION_EXTENDED
    assert defaults.WRITING_READER_PROSE not in defaults.WRITING
    assert defaults.WRITING_READER_PROSE not in defaults.LEGACY_MODE_WRITING_DOCUMENT


def test_matcher_contract_uses_mode_and_only_adds_support_basis_when_schema_requires_it():
    for text in (REGISTRY['writing_citation_match']['content'], REGISTRY['writing_citation_match']['readOnlyContract']):
        for term in ('strict', 'extended', '直接', '忠实', 'direct', 'paraphrase', 'inference', 'none', 'supportBasis'):
            assert term in text
        assert '旧schema' in text and ('不得追加' in text or '不能追加' in text)
        assert '不能' in text and '原文' in text
        assert 'outline_scope' in text and 'unitRoles' in text
        assert 'not_required' in text and '计划' in text
        assert 'direct或paraphrase' in text


def test_strict_outline_plans_only_covered_content_and_keeps_missing_material_out_of_goals():
    entry = REGISTRY['writing_outline_strict']
    for text in (entry['content'], entry['readOnlyContract']):
        assert '严格范围从制定大纲开始' in text
        assert 'goal' in text and '要点' in text and '明示内容' in text
        assert '话题相关' in text and '机制、影响、对策' in text and '预测' in text
        assert 'sections=[]' in text and '非空 issues' in text
        assert '不为满足章节数' in text or '不能为章节数' in text
        assert defaults.WRITING_OUTLINE_EVIDENCE in text
        assert 'planningEvidence' in text and 'blockId' in text and 'quote' in text
        assert '白名单' in text and '可唯一定位' in text and '旧 schema' in text
        assert 'outlinePlanningProtocol' in text and '逐字回显本次输入协议值' in text
        assert 'evidence-plan-v2' in text and '已冻结 evidence-plan-v1 请求' in text
        assert '顶层 protocol' in text and '已冻结请求' in text
        assert 'issues 或章节 goal' not in text and 'issues 或 goal' not in text
    assert '2 至 8 章' not in entry['content']
    extended = REGISTRY['writing_outline_extended']
    assert all('不因超出所选原文而添加“待核实”标签' in text and '拓展设想' in text
               for text in (extended['content'], extended['readOnlyContract']))


@pytest.mark.parametrize('prompt_id', ('writing_section_extended', 'writing_document_extended'))
def test_extended_generation_contract_keeps_prose_unlabelled_without_fabricating_evidence(prompt_id):
    entry = REGISTRY[prompt_id]
    for text in (entry['content'], entry['readOnlyContract']):
        assert defaults.WRITING_EXTENDED_PROSE in text
        assert 'kind=model_supplement' in text
        assert '不能编造或保留无效引用' in text
        assert '由程序标为' not in text
        assert '须保留“模型补充·待核实”' not in text
        assert '保留“模型补充·待核实”语义' not in text


@pytest.mark.parametrize('operation', ('polish', 'rewrite', 'shorten', 'expand'))
def test_extended_edit_contract_does_not_require_labels_and_preserves_selected_text_markers(operation):
    entry = REGISTRY['writing_edit_' + operation]
    for text in (entry['content'], entry['readOnlyContract']):
        assert '不自动给新扩展内容添加此类标记' in text
        assert '不因超出所选原文或缺少依据而添加“待核实”标签' in text
        assert 'supplement=true' in text
        assert '已有' in text and '模型补充标记' in text
        assert defaults.WRITING_STRICT_SOURCE in text
        assert '扩展补充须明确标记' not in text


def test_saved_strict_outline_override_cannot_turn_missing_evidence_into_a_writing_plan(tmp_path):
    store = Store(tmp_path / 'outline-coverage-contract')
    library = PromptLibrary(store)
    custom = '保留用户原有自定义文本，未来讨论的空缺不要擅自写成事实。'
    saved = library.update('writing_outline_strict', custom, 1)
    with store.connect() as db:
        effective = prompt_text('writing_outline_strict', db=db)
        row = db.execute('SELECT content,version FROM prompt_overrides WHERE id=?', ('writing_outline_strict',)).fetchone()
        assert row['content'] == custom and row['version'] == saved['version'] == 2
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    assert effective.startswith(custom + '\n程序固定约束：')
    assert defaults.WRITING_OUTLINE_EVIDENCE in effective
    assert '资料不足只进 issues，不放进 goal 或要点' in effective
