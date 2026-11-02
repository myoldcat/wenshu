"""Versioned structure and voice instructions, compiled only for new requests."""
from __future__ import annotations

import copy
import re

from .writing_style_defaults import STYLES, OFFICIAL_PROSE_POLICY, OFFICIAL_PROSE_INSTRUCTIONS

POLICY = 'writing-preferences-v1'
INTRODUCTION_POLICY = 'research-report-lead-v1'
TEMPLATE_TITLE_POLICY = 'content-specific-template-titles-v1'
AUTHOR_STRUCTURE_POLICY = 'author-structure-first-v1'
AUTHOR_INTRODUCTION_POLICY = 'author-introduction-placement-v1'
AUTHOR_STRUCTURE_INSTRUCTIONS = (
    '结构：写作要求中明确的章数、标题、顺序、层级、分组和引言安排优先于模板；'
    '模板仅补未指定部分，不加回排除项，明确标题原样保留。'
    '正文按已确认或人工调整的纲要；冲突记issues并提示调整纲要，不擅自重排；选区只改selectedText。')
AUTHOR_OUTLINE_REQUIREMENT = (
    '作者明确一级章数，或完整章节列表、层级可确定一级章数时据此确定章数；否则严格遵循模板有效参考部分的数量，'
    '仅模板无有效参考部分时参考2至8章；系统支持1至20章。'
    '一级章节写入sections，下级标题、层级及顺序写入goal。'
    '纲要是待确认计划，不写正文；各章保持请求mode。')
TEMPLATE_DEFAULT_PREFIX = '未指定的模板默认：'
AUTHOR_INTRODUCTION_REQUIREMENT = (
    '作者要求不要章外引言或把引言列为正式章节时，introductionParagraphCount=0；未指定则按模板默认。')
NONREPORT_INTRODUCTION_REQUIREMENT = (
    '模板默认无章外引言，填0；作者要求章外引言时先写引言再写本章，填正整数引言段数（<paragraphs总数）。')
RESEARCH_INTRODUCTION_REQUIREMENT = ('先写无标题背景，再写本章正文；introductionParagraphCount填背景段落数，'
    '≥1且<paragraphs总数，首章标题由程序后置。')
LEGACY_RESEARCH_OUTLINE_REQUIREMENT = '本次调研报告仅规划当前态势、存在问题隐患、对策建议三章；背景介绍记入首章goal，不单列章节。'
RESEARCH_OUTLINE_REQUIREMENT = '调研报告按实际内容拟定章标题，模板提示不作标题；背景写入首章goal，不单列章节。'
VARIANT_OUTLINE_REQUIREMENTS = {
    'research-report-problem': '引言不单列章，写入首章goal；按五部分组织纲要：首章为调研背景与方法，随后是基本情况、存在问题、原因分析、对策建议；正式章标题结合实际主题拟定，不照抄提示。',
    'research-report-experience': '引言不单列章，写入首章goal；参考主要做法、取得成效、经验启示、推广建议组织纲要；正式章标题结合实际内容拟定，不照抄提示。',
    'research-report-decision': '引言不单列章，写入首章goal；参考现状与趋势、风险研判、意见建议组织纲要；建议分近期、中期，正式章标题结合实际内容拟定。',
}
VARIANT_INTRODUCTION_REQUIREMENT = ('先写无标题、不编号、不单列章的简短引言，交代主题与目的，再写本章正文；引言属正文，不写备稿或生成说明。'
    'introductionParagraphCount填引言段数，≥1且<paragraphs总数，首章标题由程序后置。')
VARIANT_INTRODUCTION_INSTRUCTIONS = '引言不单独计章；仅按首章introductionPolicy/Requirement输出开篇引言，其余章不重复。'
TEMPLATE_TITLE_REQUIREMENT = ('模板仅提示结构，正式章标题须结合主题及本章内容补充完善，不照抄模板短提示；'
    '保留作者明确指定、已确认或人工调整的章节标题。')
STRUCTURES = {
    'research-report-problem': ('调研报告（问题对策型）', ['调研背景与方法', '基本情况', '存在问题', '原因分析', '对策建议'],
        '按调研背景与方法、基本情况、存在问题、原因分析、对策建议五部分展开，背景与方法作为第一章。'
        '只说明实际提供的调研方法、样本与过程；原因须有依据，建议回应问题，不虚构调研或因果。'),
    'research-report-experience': ('调研报告（经验总结型）', ['主要做法', '取得成效', '经验启示', '推广建议'],
        '参考主要做法、取得成效、经验启示、推广建议展开。'
        '用有据做法和成效归纳经验，说明推广条件与局限，不夸大成果或将相关性写成因果。'),
    'research-report-decision': ('调研报告（决策参考型）', ['现状与趋势', '风险研判', '意见建议（分近期、中期）'],
        '参考现状与趋势、风险研判、意见建议展开，建议区分近期、中期及适用条件。'
        '区分已有事实、趋势判断与风险研判，不将可能性写成确定事实，不臆定日期或虚构风险、已批准部署。'),
    'research-report': ('调研报告', ['当前态势', '存在问题隐患', '对策建议'],
        '开头以不编号自然段介绍背景、范围和关注问题，写入首章goal，背景介绍不算独立章节。'
        '参考态势分析、问题隐患、对策建议组织内容，结合实际主题细化调整；区分已有问题与有据风险，建议回应问题。'),
    'opinion': ('观点文章', ['从一个具体问题开始', '提出核心观点', '用证据展开论证', '回应另一种声音', '回到问题，留下思考'],
        '从具体问题提出核心判断，以证据展开论证，公平回应不同观点，形成有边界的结论。'),
    'review': ('主题综述', ['主题与讨论范围', '关键概念与发展脉络', '主要观点与代表材料', '共识、分歧与证据缺口', '总结与后续问题'],
        '界定主题和概念，梳理脉络，比较主要观点，区分共识、分歧及证据缺口后收束。'),
    'report': ('研究报告', ['摘要与核心结论', '研究问题与范围', '资料来源与分析方法', '主要发现与证据', '讨论、限制与建议'],
        '围绕研究问题组织发现、分析与建议；不虚构调查方法、样本或数据，方法与限制仅在确有内容时说明。'),
    'book': ('长篇论著', ['导论：核心问题与论证路线', '第一部分：概念与理论基础', '第二部分：材料与案例分析', '第三部分：比较、讨论与综合', '结语：发现、限制与展望'],
        '围绕稳定主线逐章递进，明确各章分工，保持概念与术语一致，避免重复开题。'),
    'explainer': ('知识解读', [], '按读者理解顺序解释概念、原理与应用，先交代必要背景，再逐步展开。'),
    'custom': ('自定章节结构', [], '按作者已保存的结构要求组织内容。'),
}
BOUNDARY = ('结构／文风只管组织表达，服从实际模式、固定资料、引用及事实边界；'
    '无据的事实、因果、风险判断及建议写issues，不编造填空。正文保留已确认或人工调整的章节，不自行增删。')


def validate(brief):
    for key, choices, label in (('structureTemplate', STRUCTURES, '结构模板'), ('writingStyle', STYLES, '文风')):
        if key in brief and (not isinstance(brief[key], str) or brief[key] not in choices):
            raise ValueError(label + '选项无效')


def snapshot(brief, *, db=None):
    """Missing historical selections stay missing; old free-text choices survive."""
    value = {'policy': POLICY, 'boundary': BOUNDARY, 'templateTitlePolicy': TEMPLATE_TITLE_POLICY,
             'outlineTitleRequirement': TEMPLATE_TITLE_REQUIREMENT,
             'authorStructurePolicy': AUTHOR_STRUCTURE_POLICY,
             'authorStructureInstructions': AUTHOR_STRUCTURE_INSTRUCTIONS,
             'authorOutlineRequirement': AUTHOR_OUTLINE_REQUIREMENT,
             'templateDefaultPrefix': TEMPLATE_DEFAULT_PREFIX,
             'authorIntroductionRequirement': AUTHOR_INTRODUCTION_REQUIREMENT}
    structure_id = brief.get('structureTemplate')
    if structure_id in STRUCTURES:
        name, sections, instructions = STRUCTURES[structure_id]
        from .prompts import snapshot as prompt_snapshot, compiled_snapshot_text
        prompt_id = 'writing_structure_' + structure_id
        if db is not None and not db.in_transaction:
            db.execute('BEGIN')
        structure_prompts = (prompt_snapshot(db, (prompt_id,)) if db is not None else
                             {prompt_id: {'content': instructions, 'version': 1}})
        instructions = compiled_snapshot_text(prompt_id, structure_prompts[prompt_id])
        value['structurePromptSnapshot'] = structure_prompts
        if structure_id == 'custom':
            sections = brief.get('templateSections', [])
        value['structure'] = {'id': structure_id, 'name': name, 'sections': copy.deepcopy(sections), 'instructions': instructions}
        if structure_id == 'research-report':
            value['outlineTemplateRequirement'] = RESEARCH_OUTLINE_REQUIREMENT
        elif structure_id in VARIANT_OUTLINE_REQUIREMENTS:
            value['outlineTemplateRequirement'] = VARIANT_OUTLINE_REQUIREMENTS[structure_id]
            value['introductionPolicy'] = INTRODUCTION_POLICY
            value['introductionRequirement'] = VARIANT_INTRODUCTION_REQUIREMENT
            if structure_id == 'research-report-problem':
                value['introductionRequirement'] += '引言不替代或重复第一章调研背景与方法。'
            value['introductionInstructions'] = VARIANT_INTRODUCTION_INSTRUCTIONS
    elif brief.get('template'):
        value['structure'] = {'id': 'legacy', 'name': str(brief['template'])[:200],
            'sections': copy.deepcopy(brief.get('templateSections', [])), 'instructions': '遵循作者已保存的结构模板与章节安排。'}
    parts = value.get('structure', {}).get('sections', [])
    if structure_id == 'custom' and not parts:
        parts = brief.get('templateSections', [])
    if (isinstance(parts, list) and 1 <= len(parts) <= 20
            and all(isinstance(part, str) and part.strip() for part in parts)):
        default = (f'作者未明确一级章数且未给出可确定一级章数的完整章节列表或层级时，严格生成{len(parts)}个一级章节；'
            '只调整标题或顺序不改变默认章数；仅规定章内小节数量也不改变一级章数。采用默认章数且作者未另定分工或顺序时，'
            '各章与以下参考部分依次一一对应：' + '→'.join(parts) +
            '；不自行增删、合并或拆分一级章节。章外引言不计章。')
        if structure_id == 'research-report-decision':
            default += ('采用默认三章分工时，近期、中期建议均写入意见建议所在章的goal内，不另拆章；'
                        '默认顺序下该章为第三章。')
        value['outlineTemplateRequirement'] = default + value.get('outlineTemplateRequirement', '')
    value['authorIntroductionPolicy'] = AUTHOR_INTRODUCTION_POLICY
    value['authorIntroductionDefaultRequirement'] = (RESEARCH_INTRODUCTION_REQUIREMENT
        if structure_id == 'research-report' else value.get('introductionRequirement', NONREPORT_INTRODUCTION_REQUIREMENT))
    style_id = brief.get('writingStyle')
    if style_id in STYLES:
        from .prompts import (REGISTRY, STYLE_PROMPT_IDS, OFFICIAL_BODY_PROMPT_ID,
                              snapshot as prompt_snapshot, compiled_snapshot_text)
        prompt_id = STYLE_PROMPT_IDS[style_id]
        ids = (prompt_id, OFFICIAL_BODY_PROMPT_ID) if style_id == 'official' else (prompt_id,)
        # All values are read once inside the caller's transaction. Compiling
        # this map never re-reads a newer general/body pair midway through it.
        if db is not None and not db.in_transaction:
            db.execute('BEGIN')
        style_prompts = (prompt_snapshot(db, ids) if db is not None else
                         {id: {'content': REGISTRY[id]['content'], 'version': 1} for id in ids})
        name = STYLES[style_id][0]
        instructions = compiled_snapshot_text(prompt_id, style_prompts[prompt_id])
        value['stylePromptSnapshot'] = style_prompts
        value['style'] = {'id': style_id, 'name': name, 'instructions': instructions}
        if style_id == 'official':
            value['officialProsePolicy'] = OFFICIAL_PROSE_POLICY
            value['officialProseInstructions'] = compiled_snapshot_text(OFFICIAL_BODY_PROMPT_ID,
                                                                        style_prompts[OFFICIAL_BODY_PROMPT_ID])
    elif brief.get('style'):
        value['style'] = {'id': 'legacy', 'name': '已保存文风', 'instructions': str(brief['style'])[:3000]}
    value['sectionSystemSuffix'] = _system_suffix(value, section=True)
    return value


def _system_suffix(frozen, *, section=False, edit=False, outline=False):
    lines = [frozen['boundary']]
    if frozen.get('authorStructurePolicy') == AUTHOR_STRUCTURE_POLICY:
        lines.append(frozen['authorStructureInstructions'])
    if outline and frozen.get('outlineTitleRequirement'):
        lines.append(frozen['outlineTitleRequirement'])
    for key, label in (('structure', '结构模板'), ('style', '文风')):
        if key in frozen:
            item = frozen[key]
            prefix = frozen.get('templateDefaultPrefix', '') if key == 'structure' else ''
            lines.append(label + '：' + item['name'] + '。' + prefix + item['instructions'])
    # Compile only a new task's actual text. Historical snapshots without this
    # field retain their original rules, including when rebuilding a suffix.
    if (section or edit) and frozen.get('officialProseInstructions'):
        lines.append(frozen['officialProseInstructions'])
    if section and not edit and frozen.get('structure', {}).get('id') == 'research-report':
        lines.append(frozen.get('templateDefaultPrefix', '') + '仅按首章introductionPolicy/Requirement输出背景及段落数，其余章不重复。')
    elif section and not edit and frozen.get('introductionInstructions'):
        lines.append(frozen.get('templateDefaultPrefix', '') + frozen['introductionInstructions'])
    if edit:
        lines.append('仅修改选区的表达，保留选区外既有结构与背景位置。')
    return '\n本次结构与文风要求：\n' + '\n'.join(lines)


def apply(system, payload, frozen, *, section=None, sections=(), edit=False, title=False):
    if frozen is None:
        return system
    if frozen.get('policy') != POLICY:
        raise ValueError('写作结构与文风规则版本不受支持')
    # Actual instructions are frozen in the system message and task snapshot.
    # The user packet carries only selection metadata, avoiding a second full
    # copy in every chapter of a single-request manuscript.
    payload['writingPreferences'] = {'policy': frozen['policy']}
    author_structure = frozen.get('authorStructurePolicy') == AUTHOR_STRUCTURE_POLICY
    author_introduction = (author_structure and
        frozen.get('authorIntroductionPolicy') == AUTHOR_INTRODUCTION_POLICY)
    if author_structure:
        payload['authorStructurePolicy'] = frozen['authorStructurePolicy']
    for key in ('structure', 'style'):
        if key in frozen:
            metadata = {name: copy.deepcopy(value)
                for name, value in frozen[key].items() if name != 'instructions'}
            if key == 'structure' and frozen.get('templateTitlePolicy') == TEMPLATE_TITLE_POLICY:
                metadata['sectionHints'] = metadata.pop('sections', [])
            payload['writingPreferences'][key] = metadata
    structure = frozen.get('structure', {})
    if not title:
        if section is None and not edit:
            if frozen.get('outlineTemplateRequirement'):
                payload['requirements'] += frozen.get('templateDefaultPrefix', '') + frozen['outlineTemplateRequirement']
            elif structure.get('id') == 'research-report':
                payload['requirements'] += LEGACY_RESEARCH_OUTLINE_REQUIREMENT
        elif (not edit and sections and sections[0]['id'] == section.get('id')
              and (structure.get('id') == 'research-report'
                   or frozen.get('introductionPolicy') == INTRODUCTION_POLICY or author_introduction)):
            payload['introductionPolicy'] = INTRODUCTION_POLICY
            if author_introduction:
                payload['authorIntroductionPolicy'] = frozen['authorIntroductionPolicy']
                payload['introductionRequirement'] = frozen['authorIntroductionDefaultRequirement']
            elif structure.get('id') == 'research-report':
                payload['introductionRequirement'] = RESEARCH_INTRODUCTION_REQUIREMENT
                payload['outputSchema']['introductionParagraphCount'] = '开头背景介绍段落数，正整数，小于paragraphs总数'
            else:
                payload['introductionRequirement'] = frozen['introductionRequirement']
                payload['outputSchema']['introductionParagraphCount'] = '开头引言段落数，正整数，小于paragraphs总数'
            if author_structure:
                payload['introductionRequirement'] = (frozen['authorIntroductionRequirement'] +
                    frozen.get('templateDefaultPrefix', '') + payload['introductionRequirement'])
                payload['outputSchema']['introductionParagraphCount'] = '无章外引言填0；否则填引言段数（正整数且<paragraphs总数）'
    suffix = (frozen.get('sectionSystemSuffix') if section is not None and not edit and not title else None)
    return system + (suffix or _system_suffix(frozen, section=section is not None and not title, edit=edit,
                                             outline=section is None and not edit and not title))


def preserve_introduction(section, content):
    """Keep the boundary only while its saved leading paragraphs remain exact."""
    count = section.get('introductionParagraphCount')
    if type(count) is not int or count <= 0:
        return
    old = re.split(r'\n\s*\n', section.get('content', '').strip())
    new = re.split(r'\n\s*\n', content.strip())
    if len(new) <= count or old[:count] != new[:count]:
        section.pop('introductionParagraphCount', None)
