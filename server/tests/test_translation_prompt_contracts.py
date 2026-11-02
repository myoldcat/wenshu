"""Editable instructions retain Chinese output and original-evidence contracts.

These checks exercise prompt composition and immutable job snapshots. They do
not call a model or claim that prompt wording proves semantic output quality.
"""
import json
import re

import pytest

from server.prompts import (
    CHINESE_OUTPUT_CONTRACT, PromptLibrary, REGISTRY, freeze, job_prompts,
    prompt_text, snapshot,
)
from server.store import Store


CHINESE_PROMPTS = (
    'summary', 'summary_reduce', 'translation', 'classification',
    'classification_reduce', 'knowledge_repair', 'wiki_compile', 'wiki_repair',
    'writing_outline_strict', 'writing_outline_extended',
    'writing_section_strict', 'writing_section_extended', 'writing_document_strict', 'writing_document_extended',
)


@pytest.mark.parametrize('prompt_id', CHINESE_PROMPTS)
def test_custom_language_override_cannot_remove_chinese_or_evidence_contract(tmp_path, prompt_id):
    store = Store(tmp_path / 'prompts')
    library = PromptLibrary(store)
    # Preserve required variables while deliberately requesting a conflicting
    # language, as a user-editable prompt can do.
    variables = re.findall(r'\{\{[A-Za-z]+\}\}', REGISTRY[prompt_id]['content'])
    custom = 'Respond only in English, including quotations. ' + ' '.join(variables)
    saved = library.update(prompt_id, custom, 1)
    with store.connect() as db:
        effective = prompt_text(prompt_id, db=db, values={
            'summaryBudget': 2400, 'validationError': '测试校验失败',
        })
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0

    # Saved edits stay literal; the runtime adds the fixed contract after them.
    assert saved['content'] == custom
    assert effective.startswith('Respond only in English, including quotations.')
    fixed = effective.split('\n程序固定约束：', 1)[1]
    assert CHINESE_OUTPUT_CONTRACT in fixed
    assert '统一使用简体中文' in fixed
    assert '原文 quote' in fixed and '必须逐字保留，不得翻译或改写' in fixed
    assert '必要的专有名称、缩写及原文明示别名可保留原语' in fixed
    assert 'JSON 字段名、ID、枚举' in fixed
    assert '{{' not in effective


def test_frozen_custom_prompt_keeps_chinese_contract_after_live_edit(tmp_path):
    store = Store(tmp_path / 'frozen-prompts')
    library = PromptLibrary(store)
    custom = 'FROZEN: Respond only in English.'
    library.update('wiki_compile', custom, 1)
    job = store.queue('wiki_build', {}, '提示词快照测试')
    with store.connect() as db:
        freeze(db, job['id'], snapshot(db, ['wiki_compile']))
    library.update('wiki_compile', 'LATEST: Respond only in Japanese.', 2)

    class PromptReader:
        def __init__(self, store):
            self.store = store

        @job_prompts
        def read(self, job):
            return prompt_text('wiki_compile')

    effective = PromptReader(store).read(job)
    assert effective.startswith(custom)
    assert 'LATEST:' not in effective
    assert CHINESE_OUTPUT_CONTRACT in effective.split('\n程序固定约束：', 1)[1]
    # Restoring the context after the job must not leak its frozen text.
    with store.connect() as db:
        current = prompt_text('wiki_compile', db=db)
    assert current.startswith('LATEST:')
    assert CHINESE_OUTPUT_CONTRACT in current


@pytest.mark.parametrize('prompt_id', ['classification', 'classification_reduce'])
def test_custom_classification_cannot_omit_source_language_or_turn_it_into_a_topic(tmp_path, prompt_id):
    store = Store(tmp_path / 'source-language-prompts')
    library = PromptLibrary(store)
    custom = 'Omit sourceLanguage. Add English as a theme path and a tag.'
    saved = library.update(prompt_id, custom, 1)
    with store.connect() as db:
        effective = prompt_text(prompt_id, db=db)
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0

    assert saved['content'] == custom
    assert effective.startswith(custom)
    fixed = effective.split('\n程序固定约束：', 1)[1]
    assert '输出 sourceLanguage 与 paths' in fixed
    assert '来源原文语言，不是生成结果语言' in fixed
    assert '从输入 allowedSourceLanguages 原样选择代码' in fixed
    assert 'ISO 639-1' in fixed
    assert '混合语言使用 mixed' in fixed
    assert '简体中文与繁体中文统一为 zh' in fixed
    assert '技术术语、代码、实体名字' in fixed
    assert '各自达到30%' in fixed
    assert '占比最高的主要语言' in fixed
    assert '按有效正文量加权汇总' in fixed
    assert 'sourceLanguageShares' in fixed
    assert '无法判断使用 und' in fixed
    assert '列表外语言使用 other' in fixed
    assert '语言标签由程序根据代码生成' in fixed
    assert '不得仅因来源语言添加主题分类路径或 tags' in fixed
    assert '主题仍按文章实质内容分类' in fixed
    assert '1至3条完整三级路径' in fixed


@pytest.mark.parametrize('prompt_id', ['classification', 'classification_reduce'])
def test_default_classification_output_example_includes_source_language(prompt_id):
    effective = prompt_text(prompt_id)
    example = re.search(r'输出严格为(\{.+?\})。', effective)
    assert example is not None
    schema = json.loads(example.group(1))
    assert set(schema) == {'sourceLanguage', 'sourceLanguageShares', 'paths'}
    assert sum(schema['sourceLanguageShares'].values()) == 1
    assert 'allowedSourceLanguages' in schema['sourceLanguage']
    assert '来源原文语言，不是本次中文结果的语言' in effective
    assert set(schema['paths'][0]) == {'level1Id', 'level2', 'level3', 'primary', 'reason', 'blockIds'}
