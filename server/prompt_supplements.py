"""Editable organization presets and read-only rules used beside task prompts.

This module exposes the existing text; it neither changes a validation policy
nor assembles a model request. Historical rules are explicitly scoped.
"""
from __future__ import annotations

from . import writing_composition as composition
from . import writing_preferences as preferences


ORGANIZATION_CONTRACT = (
    preferences.BOUNDARY + preferences.AUTHOR_STRUCTURE_INSTRUCTIONS +
    '本条仅调整篇章组织表达，不改变实际任务、严格／拓展模式、固定资料、引用或JSON输出协议。'
    '编辑正文仍按已确认纲要，只改本次请求范围；缺口写issues，不把作者说明写进正文。'
)
ROLE_NAMES = {'whole': '独立成篇', 'opening': '开篇章节',
              'development': '展开章节', 'closing': '收束章节'}


def _entry(id, title, category, content, stage, usage, input_description,
           output_description, contract, **metadata):
    return dict(id=id, title=title, category=category, content=content, stage=stage,
                description=usage, usage=usage, inputDescription=input_description,
                outputDescription=output_description, readOnlyContract=contract,
                **metadata)


def extra_catalog_entries():
    """Return ten existing structures and six organization/role instructions."""
    entries = []
    for key, (name, _parts, instructions) in preferences.STRUCTURES.items():
        legacy = key == 'research-report'
        entries.append(_entry(
            'writing_structure_' + key,
            name + ('（旧模板兼容）' if legacy else ''), 'structures', instructions,
            '写作准备与文稿编辑 · 结构模板',
            '选择此结构模板时使用其论述方向；作者明确结构优先，保存后用于后续新任务。' +
            ('此项保留给已选择旧调研报告模板的文稿，不替换新三型默认。' if legacy else ''),
            '实际所选结构、作者写作要求和已确认纲要；参考部分及默认章数由程序提供。',
            '并入当前任务指令；不直接修改已保存标题、纲要、正文或历史任务。',
            ORGANIZATION_CONTRACT,
            scope='legacy-template' if legacy else 'current'))
    for id, name, content, stage in (
            ('writing_composition_outline', '纲要篇章组织', composition.OUTLINE_INSTRUCTIONS, '写作准备 · 纲要组织'),
            ('writing_composition_prose', '正文篇章组织', composition.PROSE_INSTRUCTIONS, '文稿编辑 · 正文组织')):
        entries.append(_entry(
            id, name, 'composition', content, stage,
            '围绕全文主线组织章节或段落，减少素材堆砌、重复开题和固定导航句。',
            '主题、写作要求、本章分工及有界篇章上下文；上下文不是事实证据。',
            '并入新任务冻结的篇章组织指令，原结果格式和事实边界保持。',
            ORGANIZATION_CONTRACT))
    for role, name in ROLE_NAMES.items():
        entries.append(_entry(
            'writing_role_' + role, name + '的组织要求', 'composition', composition._ROLES[role],
            '文稿编辑 · 章节角色',
            '章节实际承担此角色时提供组织提示；由当前章位置派生角色，不改变章节结构。',
            '本章位置、相邻章规划和已保存片段；只作行文衔接，不能作为证据。',
            '进入本章有界上下文的roleInstruction，最多180字符；不扩大原上下文容量。',
            ORGANIZATION_CONTRACT + composition.CONTEXT_BOUNDARY + '本条最多180字符。',
            maxContentLength=180))
    return entries


def supplemental_rules(id):
    """Show applicable fixed instructions without making them editable.

    Current runtime text comes directly from its owner. Dynamic source/schema
    values are described rather than invented. No historical rule is upgraded.
    """
    from . import prompt_defaults as defaults
    rules = []

    def add(key, title, content, scope='current'):
        if content:
            rules.append({'id': key, 'title': title, 'content': content, 'scope': scope})

    writing_modes = {f'writing_{kind}_{mode}' for kind in ('outline', 'section', 'document')
                     for mode in ('strict', 'extended')}
    is_mode = id in writing_modes
    is_structure = id.startswith('writing_structure_')
    is_composition = id.startswith('writing_composition_') or id.startswith('writing_role_')
    is_edit = id.startswith('writing_edit_')
    is_title = id in ('writing_assist_title', 'writing_assist_title_candidates')
    is_writing = is_mode or is_structure or is_composition or is_edit or is_title or id.startswith('writing_style_')

    if is_writing:
        add('writing_fact_boundary', '结构与文风的固定事实边界', preferences.BOUNDARY)
        add('author_structure_first', '作者结构优先与已确认纲要保护', preferences.AUTHOR_STRUCTURE_INSTRUCTIONS)
    if is_mode or is_structure or id == 'writing_composition_outline':
        add('outline_chapter_counts', '纲要章数及层级协议（仅纲要规划）', preferences.AUTHOR_OUTLINE_REQUIREMENT)
        add('outline_title_policy', '结构提示与正式标题（仅纲要规划）', preferences.TEMPLATE_TITLE_REQUIREMENT)
        add('author_introduction', '作者引言安排优先', preferences.AUTHOR_INTRODUCTION_REQUIREMENT)
    if is_structure:
        structure_id = id[len('writing_structure_'):]
        if structure_id in preferences.STRUCTURES:
            _name, parts, _text = preferences.STRUCTURES[structure_id]
            add('template_sections', '程序提供的参考部分与默认章数',
                ('参考部分：' + ' → '.join(parts) + '。未被作者明确结构覆盖时，默认一级章数为' + str(len(parts)) + '；章外引言不计章。')
                if parts else '无固定有效参考部分时，仅纲要规划参考2至8章；作者明确结构优先，系统范围1至20章。',
                'dynamic')
            if structure_id in preferences.VARIANT_OUTLINE_REQUIREMENTS:
                add('variant_outline', '当前模板的纲要及分工要求', preferences.VARIANT_OUTLINE_REQUIREMENTS[structure_id])
                add('variant_introduction', '当前模板的首章引言协议',
                    preferences.VARIANT_INTRODUCTION_REQUIREMENT +
                    ('引言不替代或重复第一章调研背景与方法。' if structure_id == 'research-report-problem' else '') +
                    preferences.VARIANT_INTRODUCTION_INSTRUCTIONS)
            elif structure_id == 'research-report':
                add('legacy_report_introduction', '旧调研报告模板的引言协议',
                    preferences.RESEARCH_INTRODUCTION_REQUIREMENT, 'legacy-template')
                add('legacy_report_outline', '历史任务兼容：旧调研报告纲要',
                    preferences.LEGACY_RESEARCH_OUTLINE_REQUIREMENT, 'legacy')
            else:
                add('nonreport_introduction', '非调研模板的默认引言协议', preferences.NONREPORT_INTRODUCTION_REQUIREMENT)

    if is_mode or is_composition:
        add('composition_strict', '严谨模式的组织与事实边界', composition.STRICT_INSTRUCTIONS)
        add('composition_context', '篇章上下文与证据隔离', composition.CONTEXT_BOUNDARY)
        if not id.startswith('writing_outline_') and id != 'writing_composition_outline':
            add('reader_content_boundary', '正文面向读者的固定要求', defaults.WRITING_READER_CONTENT_BOUNDARY)
            add('natural_continuity', '章节自然衔接的固定要求', defaults.WRITING_NATURAL_CONTINUITY)
    if is_mode:
        from .writing_material_packet import TEMPORAL_REQUIREMENT
        from .writing_outline_planning import ABSTRACT_OUTLINE_REQUIREMENT, OUTLINE_SCOPE_REVIEW_INSTRUCTIONS
        from .writing_reference_scope import _REQUIREMENT, _OUTLINE_REQUIREMENT
        add('source_time_attribution', '原文时点、发布日期与不确定性', TEMPORAL_REQUIREMENT)
        add('runtime_input_schema', '由本次输入决定的固定格式与范围',
            '实际outputSchema、blocks、sourceContext、chapterContext、当前mode、引用别名和章节范围由程序按本次资料及版本构建；'
            '这些值是任务输入，不是可编辑的通用指令。完整实际请求可在写作运维记录中查看。', 'dynamic')
        if id.startswith('writing_outline_'):
            add('abstract_outline', '当前新任务：抽象纲要与引用分工', ABSTRACT_OUTLINE_REQUIREMENT)
            add('legacy_outline_scope', '历史任务兼容：有据纲要的范围复核', OUTLINE_SCOPE_REVIEW_INSTRUCTIONS, 'legacy')
            add('legacy_outline_aliases', '历史任务兼容：纲要证据编号范围', _OUTLINE_REQUIREMENT, 'legacy')
        else:
            add('chapter_reference_scope', '本章原文引用编号范围', _REQUIREMENT)
            add('composition_citations', '当前新任务：正文表达与原文登记', defaults.WRITING_COMPOSITION_CITATIONS)
            if id.startswith('writing_document_'):
                from .writing_document import COMPOSITION_CATALOG_REQUIREMENT
                add('document_instruction_catalog', '整稿共享指令目录协议', COMPOSITION_CATALOG_REQUIREMENT)
                add('document_orchestration', '整稿及按章执行的固定输出协议', defaults.WRITING_DOCUMENT_ORCHESTRATION)
        if id.endswith('_strict') and not id.startswith('writing_outline_'):
            add('current_strict_scope', '当前新任务：严格模式范围', defaults.WRITING_COMPOSITION_STRICT)
        elif id.endswith('_extended'):
            add('current_extended_scope', '当前新任务：拓展模式事实要求', defaults.WRITING_EXTENDED_COMPLETENESS)
    if is_edit:
        add('selection_range', '选区修改的固定正文边界',
            defaults.WRITING_READER_CONTENT_BOUNDARY + defaults.WRITING_NATURAL_CONTINUITY +
            ' 仅在 selectedText 范围内调整，不补写或改动选区外正文；选区外问题写入 issues。'
            'changeSummary 只向作者说明修改，不复制进 replacementText。')
    if id == 'writing_citation_match':
        from .writing_citation_matching import SELECTIVE_REVIEW_INSTRUCTION, TEMPORAL_REVIEW_INSTRUCTION
        add('selective_citation_review', '当前新任务：选择性引用核对范围', SELECTIVE_REVIEW_INSTRUCTION)
        add('temporal_citation_review', '当前新任务：事实时点与历史归属复核', TEMPORAL_REVIEW_INSTRUCTION)
        from .writing_outline_planning import OUTLINE_SCOPE_REVIEW_INSTRUCTIONS, OUTLINE_ISSUE_REVIEW_INSTRUCTIONS
        add('legacy_outline_scope', '历史任务兼容：严格纲要范围复核', OUTLINE_SCOPE_REVIEW_INSTRUCTIONS, 'legacy')
        add('legacy_outline_issues', '历史任务兼容：严格纲要作者提示复核', OUTLINE_ISSUE_REVIEW_INSTRUCTIONS, 'legacy')
    if id in ('wiki_compile', 'wiki_repair', 'wiki_synthesize'):
        from .year_context import INSTRUCTIONS
        add('source_year_context', '当前新任务：固定原文年份上下文规则', INSTRUCTIONS)
        add('legacy_year_quote', '历史任务兼容：旧年份校验范围',
            '没有source-year-context-v1快照的旧任务继续按其已保存规则校验，不从当前标题或邻文补年份；'
            '此处仅说明旧协议范围，不修改已冻结指令或检查点。', 'legacy')
    if id in ('classification', 'classification_reduce', 'classification_article_type'):
        from .prompts import ARTICLE_TYPE_CONTRACT
        add('article_purpose_schema', '当前新任务：文章性质与固定字段协议', ARTICLE_TYPE_CONTRACT)
    return rules
