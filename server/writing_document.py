"""Whole-draft planning and durable, fenced chapter checkpoints.

Output estimates are conservative planning heuristics, not a tokenizer or a
promise of exact length. Each completed call and its validated candidates are
committed together before any chapter is applied.
"""
from __future__ import annotations

import copy
import json
import math
import re
import time

from .enrichment import _json
from .writing_output import SECTION_ENVELOPE_POLICY, parse_output, automatic_citations_report_only
from .writing_automatic_retry import retry_call, AutomaticRetryCancelled
from .model_gateway import ModelError
from .store import dumps, ident, now
from .prompts import snapshot, freeze, job_prompts, prompt_text, writing_prompt_id, legacy_prompt_text
from .writing_reference_scope import CitationScopeError, citation_scope_payload, scope_prepared
from .writing_citation_matching import (matching_policy, freeze_generation_temporal_review,
                                       DEFERRED_CITATION_REVIEW_POLICY)
from .writing_composition import POLICY as COMPOSITION_POLICY, snapshot as composition_snapshot_for_task, chapter_context
from .writing_preferences import snapshot as preference_snapshot_for_task
from .writing_preferences import AUTHOR_STRUCTURE_POLICY
from . import prompt_defaults

DOCUMENT_PROMPT_VERSION = 'wenshu-writing-document-1.0'
DOCUMENT_MODE_PROMPT_PROTOCOL = 'document-mode-prompts-v1'
AUTOMATIC_DOCUMENT_POLICY = 'automatic-single-document-v1'
INTERACTIVE_DOCUMENT_POLICY = 'interactive-single-document-v1'
INTERACTIVE_GENERATION_CHOICE_POLICY = 'interactive-generation-choice-v1'
REMAINING_GENERATION_POLICY = 'remaining-sequential-document-v1'
COMPOSITION_INSTRUCTION_FORMAT = 'shared-catalog-v1'
COMPOSITION_CATALOG_REQUIREMENT = (
    'task=writing_document且提供instructionCatalog时，每章writingInstructionRef与documentInstructionRef'
    '指向目录中的完整指令；只读取本章引用的目录项，结合本章实际mode执行，不能借其他章节或模式的能力。'
    'task=writing_section时，本次完整指令已经内联，不使用指令目录。'
)
DOCUMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_document_steps(
 run_id TEXT NOT NULL REFERENCES writing_runs(id), section_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, input_json TEXT NOT NULL, candidate_json TEXT,
 status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(run_id,section_id)
);
CREATE TABLE IF NOT EXISTS writing_document_calls(
 run_id TEXT NOT NULL REFERENCES writing_runs(id), call_key TEXT NOT NULL,
 attempt INTEGER NOT NULL, duration_ms INTEGER NOT NULL, usage_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(run_id,call_key)
);
"""


def _outline(project):
    return [{key: section[key] for key in ('id', 'title', 'goal', 'mode', 'targetWords')}
            for section in project['sections']]


def _remaining_scope(project, remaining_from_section_id):
    if remaining_from_section_id is None:
        return None
    from .writing_chapter_confirmation import can_enable_sequential
    if (not isinstance(remaining_from_section_id, str)
            or not remaining_from_section_id.strip() or len(remaining_from_section_id) > 100):
        raise ValueError('剩余章节的起始章节标识不正确')
    if not can_enable_sequential(project):
        raise ValueError('只有交互式逐章生成可以依次生成剩余章节')
    ids = [section['id'] for section in project['sections']]
    if remaining_from_section_id not in ids:
        raise ValueError('剩余章节的起始章节不存在，请重新选择章节')
    return ids[ids.index(remaining_from_section_id):]


def _word_count(content):
    value = re.sub(r'\[E\d+\]|【模型补充·待核实】', '', content)
    return len(re.findall(r'[\u3400-\u9fff]|[A-Za-z0-9]+', value))


def _output_tokens_per_word(messages):
    # The sentence-anchor protocol repeats supported body text as claim in
    # addition to the body and original quote. Historical packets keep their
    # original estimate, just as their persisted generation plans do.
    packet = json.loads(messages[1]['content'])
    return 4.2 if packet.get('citationPlacement') in ('claim-exact-v1', 'sentence-evidence-reviewed-v2', 'sentence-evidence-reviewed-v3') else 2.8


def _document_prompt_snapshot(db, mode, composition=None, inherited_prompts=None):
    prompt_id = writing_prompt_id('document', mode)
    if composition is not None:
        if inherited_prompts is not None:
            item = inherited_prompts.get(prompt_id)
            if not item:
                raise ValueError('自动写作缺少已冻结的一次成文组织指令')
        else:
            item = snapshot(db, (prompt_id,), composition_policy=COMPOSITION_POLICY)[prompt_id]
        return {'id': prompt_id, 'mode': mode, 'version': item['version'], 'content': item['content']}
    return {'id': prompt_id, 'mode': mode, 'version': snapshot(db, (prompt_id,))[prompt_id]['version'],
            'content': prompt_text(prompt_id, db=db)}


def _document_prompt_overhead(document_prompt, orchestration):
    metadata = {'documentPromptProtocol': DOCUMENT_MODE_PROMPT_PROTOCOL,
                'documentPromptId': document_prompt['id'],
                'documentPromptVersion': document_prompt['version']}
    # Adding these keys to a nonempty JSON object replaces its closing brace
    # with a comma and the serialized metadata without its opening brace.
    return (len(orchestration + '\n本章一次成文指令：\n' + document_prompt['content'] + '\n本章正文指令：\n') +
            len(dumps(metadata)) - 1)


def _freeze_document_prompt(item, document_prompt, orchestration):
    """Freeze the applicable whole-draft instructions for either strategy.

    The section packet remains the source of its citation/schema protocol.
    Only newly planned chapters receive this envelope; historical checkpoints
    must never be upgraded by reconstructing them from current defaults.
    """
    result = copy.deepcopy(item)
    prompt_id, version = document_prompt['id'], document_prompt['version']
    instructions = document_prompt['content']
    section_instructions = result['messages'][0]['content']
    result.update({'documentPromptProtocol': DOCUMENT_MODE_PROMPT_PROTOCOL,
                   'documentPrompt': copy.deepcopy(document_prompt),
                   'documentOrchestration': orchestration,
                   'sectionInstructions': section_instructions})
    # Sequential generation is still an execution of this whole-draft task.
    # Its request freezes and uses the same mode-specific whole-draft prompt.
    result['messages'][0]['content'] = (orchestration + '\n本章一次成文指令：\n' + instructions +
                                         '\n本章正文指令：\n' + section_instructions)
    value = json.loads(result['messages'][1]['content'])
    value.update({'documentPromptProtocol': DOCUMENT_MODE_PROMPT_PROTOCOL,
                  'documentPromptId': prompt_id, 'documentPromptVersion': version})
    result['messages'][1]['content'] = dumps(value)
    return result


def _has_document_mode_prompts(prepared):
    return bool(prepared) and all(item.get('documentPromptProtocol') == DOCUMENT_MODE_PROMPT_PROTOCOL
                                  for item in prepared)


class WritingDocumentMixin:
    def _document_config(self, project):
        return self._writing_config(project)

    def _document_plan(self, db, project, config, remaining_from_section_id=None):
        sections = project['sections']
        if remaining_from_section_id is not None and self._automatic_active_run_id():
            raise ValueError('只有交互式逐章生成可以依次生成剩余章节')
        remaining_scope = _remaining_scope(project, remaining_from_section_id)
        outline_words = sum(section['targetWords'] for section in sections)
        requested = project['brief'].get('targetWords')
        if isinstance(requested, str) and re.fullmatch(r'\s*\d+\s*', requested):
            requested = int(requested)
        requested = requested if isinstance(requested, int) and not isinstance(requested, bool) and 0 < requested <= 100000 else 0
        target_words = max(requested, outline_words)
        skipped, eligible, prepared, errors, output_word_tokens = [], [], [], [], []
        maximum = int(config.get('maxOutputTokens', 2048))
        capacity = math.floor(maximum * .8)
        context_chars = int(config.get('contextChars', 24000))
        orchestration = prompt_defaults.WRITING_DOCUMENT_ORCHESTRATION
        document_prompts = {}
        review_policy = DEFERRED_CITATION_REVIEW_POLICY
        envelope_policy = SECTION_ENVELOPE_POLICY
        composition = (None if self._automatic_active_run_id() else composition_snapshot_for_task(db=db))
        preferences = (None if self._automatic_active_run_id() else
                       preference_snapshot_for_task(project['brief'], db=db))
        inherited_prompts = None
        instruction_format = COMPOSITION_INSTRUCTION_FORMAT
        inherited_orchestration = None
        parent_frozen = {}
        if self._automatic_active_run_id():
            parent = db.execute('SELECT input_json FROM writing_runs WHERE id=?',
                                (self._automatic_active_run_id(),)).fetchone()
            parent_frozen = json.loads(parent['input_json']) if parent else {}
            review_policy = parent_frozen.get('citationReviewPolicy')
            envelope_policy = parent_frozen.get('sectionOutputEnvelopePolicy')
            composition = parent_frozen.get('compositionSnapshot')
            preferences = parent_frozen.get('preferenceSnapshot')
            inherited_prompts = parent_frozen.get('compositionPrompts', {})
            instruction_format = parent_frozen.get('compositionInstructionFormat')
            inherited_orchestration = parent_frozen.get('compositionDocumentOrchestration')
        if inherited_orchestration is not None:
            if not isinstance(inherited_orchestration, str) or not inherited_orchestration.strip():
                raise ValueError('自动写作缺少有效的冻结编排指令')
            orchestration = inherited_orchestration
        elif composition is not None:
            orchestration += '\n' + composition['proseInstructions']
            if instruction_format == COMPOSITION_INSTRUCTION_FORMAT:
                orchestration += '\n' + COMPOSITION_CATALOG_REQUIREMENT
        for chapter_number, section in enumerate(sections, 1):
            reason = ('不在本次剩余章节范围内' if remaining_scope is not None and section['id'] not in remaining_scope else
                      '章节已锁定' if section['locked'] else '保留人工修改' if section['manual'] else
                      '保留已有正文' if section['content'] else '')
            words = math.ceil(section['targetWords'] * target_words / outline_words) if outline_words else 0
            entry = {'sectionId': section['id'], 'title': section['title'], 'targetWords': words}
            if reason:
                skipped.append({**entry, 'reason': reason})
                continue
            # Keep a complete read-only plan even if input construction fails;
            # replace this legacy allowance once the actual protocol is known.
            entry['estimatedOutputTokens'] = math.ceil(words * 2.8 + 416)
            output_word_tokens.append(words * 2.8)
            eligible.append(entry)
            generated_section = copy.deepcopy(section)
            generated_section['targetWords'] = words
            actual_mode = project['mode'] if section['mode'] == 'inherit' else section['mode']
            if actual_mode not in document_prompts:
                document_prompts[actual_mode] = _document_prompt_snapshot(db, actual_mode, composition, inherited_prompts)
            document_prompt = document_prompts[actual_mode]
            # Select source blocks only after reserving the exact frozen
            # whole-draft instructions and their added request metadata.
            section_budget = context_chars - _document_prompt_overhead(document_prompt, orchestration)
            context_reserve = 0
            section_prompt = None
            section_prompt_version = None
            if composition is not None:
                # A later sequential chapter will see the preceding saved
                # prose. Reserve its bounded growth before selecting originals.
                if chapter_number > 1:
                    context_reserve = max(0, int(composition['contextLimit']) -
                                          len(dumps(chapter_context(project, generated_section, composition))))
                section_budget -= context_reserve
                if inherited_prompts is not None:
                    item = inherited_prompts.get(writing_prompt_id('section', actual_mode))
                    if not item:
                        raise ValueError('自动写作缺少已冻结的章节组织指令')
                    section_prompt = item['content']
                    section_prompt_version = item['version']
                else:
                    item = snapshot(db, (writing_prompt_id('section', actual_mode),), composition_policy=COMPOSITION_POLICY)
                    item = item[writing_prompt_id('section', actual_mode)]
                    section_prompt, section_prompt_version = item['content'], item['version']
            try:
                while True:
                    section_config = {**config, 'contextChars': section_budget}
                    messages, blocks = self._messages(project, 'section', generated_section, section_config, db,
                                                      citation_review_policy=review_policy, composition_snapshot=composition,
                                                      section_envelope_policy=envelope_policy,
                                                      preference_snapshot=preferences,
                                                      composition_prompt=section_prompt)
                    scoped = scope_prepared(
                        {'section': copy.deepcopy(section), 'targetWords': words, 'messages': messages,
                         'promptId': writing_prompt_id('section', json.loads(messages[1]['content'])['mode']),
                         'blocks': blocks, 'effectiveMode': actual_mode}, chapter_number)
                    scoped = _freeze_document_prompt(scoped, document_prompt, orchestration)
                    excess = sum(len(message['content']) for message in scoped['messages']) + context_reserve - context_chars
                    if excess <= 0:
                        break
                    # Alias/ownership instructions depend on the actual
                    # selected block count. Re-select under the measured
                    # shortfall rather than exceed the real model budget.
                    section_budget -= excess + 1
            except ValueError as exc:
                errors.append((f'“{section["title"]}”的一次成文指令及章节引用范围说明超出模型输入预算，请缩短需求或提高模型字符预算'
                               if '输入预算' in str(exc) else str(exc)))
                continue
            word_tokens = words * _output_tokens_per_word(messages)
            output_word_tokens[-1] = word_tokens
            entry['estimatedOutputTokens'] = math.ceil(word_tokens + 416)
            if entry['estimatedOutputTokens'] > capacity:
                errors.append(f'“{section["title"]}”目标 {words} 字超出单章输出预算，请减少字数或提高模型最大输出 Token')
            if json.loads(scoped['messages'][1]['content']).get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY:
                scoped['citationReviewPolicy'] = DEFERRED_CITATION_REVIEW_POLICY
            else:
                scoped['citationMatchInstructions'] = prompt_text('writing_citation_match', db=db)
                scoped['citationMatchMode'] = scoped['effectiveMode']
                scoped['citationMatchPolicy'] = matching_policy(scoped['effectiveMode'])
            if envelope_policy == SECTION_ENVELOPE_POLICY:
                scoped['sectionOutputEnvelopePolicy'] = envelope_policy
            if parent_frozen.get('automaticCitationPolicy'):
                scoped['automaticCitationPolicy'] = parent_frozen['automaticCitationPolicy']
            scoped = freeze_generation_temporal_review(scoped)
            if composition is not None:
                scoped['compositionSnapshot'] = copy.deepcopy(composition)
                if instruction_format == COMPOSITION_INSTRUCTION_FORMAT:
                    scoped['compositionInstructionFormat'] = instruction_format
                    scoped['writingPromptVersion'] = section_prompt_version
            if preferences is not None:
                scoped['preferenceSnapshot'] = copy.deepcopy(preferences)
            prepared.append(scoped)
        if not sections:
            errors.append('请先保存至少一个章节的纲要')
        if not eligible:
            errors.append('没有可生成的空白章节；已有正文、人工修改和锁定章节均保留')
        total = sum(item['targetWords'] for item in eligible)
        estimated = math.ceil(sum(output_word_tokens) + 256 + 160 * len(eligible))
        whole_messages = self._document_messages(project, prepared, db=db) if prepared else []
        input_chars = sum(len(message['content']) for message in whole_messages)
        single = estimated <= capacity and input_chars <= int(config.get('contextChars', 24000))
        reason = ('全文预计输出及材料均在模型预算内，使用一次请求生成' if single else
                  '全文预计输出超出单次预算，按章节顺序生成' if estimated > capacity else
                  '全文材料超出单次输入预算，按章节顺序生成')
        automatic_single = (parent_frozen.get('automaticBodyPolicy') == 'automatic-body-length-v1'
                            and parent_frozen.get('automaticBodyStrategy') == 'single')
        interactive_short = (not self._automatic_active_run_id()
                             and project['brief'].get('automation', 'manual') == 'manual'
                             and 0 < requested < 10000)
        interactive_choice = project['brief'].get('interactiveGeneration') if interactive_short else None
        interactive_chapter = interactive_choice == 'chapter'
        interactive_single = interactive_short and not interactive_chapter
        if interactive_chapter:
            single = False
            reason = '本次已选择逐章生成，按章节顺序生成'
        required_single = automatic_single or interactive_single
        if remaining_scope is not None:
            single = False
            reason = '本次从选定章节起依次生成剩余空白章节'
        if required_single and not single:
            errors.append('本次目标低于10000字，须一次生成全文；当前模型的输入或输出容量不足，请提高模型容量、减少资料或降低目标字数后重新开始。')
            reason = '本次目标低于10000字，须一次生成全文；当前模型容量不足'
        plan = {'strategy': 'single' if single or required_single else 'sequential', 'strategyReason': reason,
                'targetWords': target_words, 'requestedWords': requested, 'outlineWords': outline_words,
                'generationWords': total, 'estimatedOutputTokens': estimated, 'maxOutputTokens': maximum,
                'safeOutputTokens': capacity, 'estimatedInputChars': input_chars,
                'contextChars': int(config.get('contextChars', 24000)),
                'estimateNote': '已为中文正文、原文摘录和引用位置预留输出空间；实际字数需预览核对。总目标取需求字数与纲要合计较大值，按章比例分配。',
                'eligibleSections': eligible, 'skippedSections': skipped, 'canGenerate': not errors,
                'blockingReasons': list(dict.fromkeys(errors))}
        if automatic_single:
            plan['automaticDocumentPolicy'] = AUTOMATIC_DOCUMENT_POLICY
        if interactive_single:
            plan['interactiveDocumentPolicy'] = INTERACTIVE_DOCUMENT_POLICY
        if interactive_choice in ('chapter', 'single'):
            plan['interactiveGenerationChoicePolicy'] = INTERACTIVE_GENERATION_CHOICE_POLICY
            plan['interactiveGenerationChoice'] = interactive_choice
        if remaining_scope is not None:
            plan.update({'remainingGenerationPolicy': REMAINING_GENERATION_POLICY,
                         'remainingFromSectionId': remaining_from_section_id,
                         'remainingSectionIds': remaining_scope})
        return plan, prepared

    @staticmethod
    def _document_messages(project, prepared, db=None):
        if not prepared:
            return []
        document_mode_prompts = _has_document_mode_prompts(prepared)
        formats = {item.get('compositionInstructionFormat') for item in prepared}
        if len(formats) != 1 or next(iter(formats)) not in (None, COMPOSITION_INSTRUCTION_FORMAT):
            raise ValueError('一次成文冻结指令格式不一致')
        shared_instructions = formats == {COMPOSITION_INSTRUCTION_FORMAT}
        # Persisted requests keep their original assembly and contract. New
        # whole-draft mode prompts apply only to their associated chapter.
        mode_prompts = all('promptId' in item for item in prepared)
        if document_mode_prompts:
            requirements = prepared[0]['documentOrchestration']
            if any(item['documentOrchestration'] != requirements for item in prepared):
                raise ValueError('一次成文冻结编排规则不一致')
        elif mode_prompts:
            requirements = project.get('documentRequirements')
            if not isinstance(requirements, str):
                requirements = (prompt_text('writing_document', db=db) if db is not None
                                else legacy_prompt_text('writing_document_mode'))
        else:
            requirements = legacy_prompt_text('writing_document')
        system = requirements if mode_prompts or document_mode_prompts else prepared[0]['messages'][0]['content']
        # New requests freeze the exact common suffix. Lift that one identical
        # suffix from chapter catalog entries into the whole-draft system once;
        # sequential requests retain their full, persisted chapter messages.
        preference_suffix = None
        if document_mode_prompts and shared_instructions:
            suffixes = [(item.get('preferenceSnapshot') or {}).get('sectionSystemSuffix') for item in prepared]
            if (suffixes and isinstance(suffixes[0], str) and suffixes[0]
                    and all(value == suffixes[0] for value in suffixes)
                    and all(item['sectionInstructions'].endswith(suffixes[0]) for item in prepared)):
                preference_suffix = suffixes[0]
                system += preference_suffix
        payload = {'task': 'writing_document', 'promptVersion': DOCUMENT_PROMPT_VERSION,
                   'title': project['title'], 'brief': project['brief'], 'sections': [],
                   'requirements': requirements,
                   'outputSchema': {'sections': [{'sectionId': '从 sections.id 逐字复制',
                       'paragraphs': [{'text': '正文段落', 'kind': 'supported 或 model_supplement',
                                       'evidence': [{'blockId': '从本章 blocks 复制', 'quote': '逐字原文'}]}]}]}}
        shared_preferences = None
        if preference_suffix:
            selections = [json.loads(item['messages'][1]['content']).get('writingPreferences') for item in prepared]
            if selections and selections[0] is not None and all(value == selections[0] for value in selections):
                shared_preferences = selections[0]
                payload['writingPreferences'] = {'policy': shared_preferences['policy']}
                for key, field in (('structure', 'structureTemplate'), ('style', 'writingStyle')):
                    if key in shared_preferences:
                        payload['writingPreferences'][field] = shared_preferences[key]['id']
        output_policies = {json.loads(item['messages'][1]['content']).get('generationOutputPolicy') for item in prepared}
        if len(output_policies) == 1 and None not in output_policies:
            payload['generationOutputPolicy'] = next(iter(output_policies))
            payload['outputSchema']['contentStatus'] = 'usable；仅完全偏题填 unrelated，异常中断填 interrupted'
        envelope_policies = {json.loads(item['messages'][1]['content']).get('sectionOutputEnvelopePolicy') for item in prepared}
        if envelope_policies == {SECTION_ENVELOPE_POLICY}:
            payload['sectionOutputEnvelopePolicy'] = SECTION_ENVELOPE_POLICY
        if document_mode_prompts:
            payload['documentPromptProtocol'] = DOCUMENT_MODE_PROMPT_PROTOCOL
        instruction_refs = {}
        if shared_instructions:
            payload['compositionInstructionFormat'] = COMPOSITION_INSTRUCTION_FORMAT
            payload['instructionCatalog'] = {}

        def instruction_ref(prompt_id, mode, kind, version, content):
            identity = (prompt_id, mode, kind, version, content)
            if identity not in instruction_refs:
                reference = 'I%03d' % (len(instruction_refs) + 1)
                instruction_refs[identity] = reference
                payload['instructionCatalog'][reference] = {
                    'promptId': prompt_id, 'mode': mode, 'kind': kind, 'version': version, 'content': content}
            return instruction_refs[identity]
        for item in prepared:
            value = json.loads(item['messages'][1]['content'])
            section = {**value['section'], 'blocks': value['blocks']}
            for key in ('unmatchedCitationPolicy', 'unmatchedCitationRequirement', 'generationOutputPolicy', 'generationOutputRequirements',
                        'citationReviewPolicy', 'citationReviewScope', 'citationReviewRequirement', 'sectionOutputEnvelopePolicy'):
                if key in value:
                    section[key] = value[key]
            # Copy only fields present in this chapter's persisted packet.
            # Reassembling an old whole draft must not consult live source
            # metadata or acquire today's temporal/coverage instructions.
            for key in ('originalInputPolicy', 'sourceContext', 'temporalAndAttributionRequirements', 'inputCoverage', 'citationTemporalReview'):
                if key in value:
                    section[key] = copy.deepcopy(value[key])
            for key in ('compositionPolicy', 'chapterContext', 'writingPreferences', 'introductionPolicy', 'introductionRequirement',
                        'authorStructurePolicy', 'authorIntroductionPolicy'):
                if key == 'writingPreferences' and shared_preferences is not None:
                    continue
                if key in value:
                    section[key] = copy.deepcopy(value[key])
            if 'plannedContent' in value:
                section['plannedContent'] = copy.deepcopy(value['plannedContent'])
            if 'citationPlacement' in value:
                section['citationPlacement'] = value['citationPlacement']
                # Derive the output shape from the frozen chapter packet. A
                # resumed legacy whole draft must keep its original schema.
                payload['outputSchema']['sections'][0]['paragraphs'] = copy.deepcopy(value['outputSchema']['paragraphs'])
                if 'contentStatus' in value['outputSchema']:
                    payload['outputSchema']['sections'][0]['contentStatus'] = value['outputSchema']['contentStatus']
                if 'introductionParagraphCount' in value['outputSchema']:
                    payload['outputSchema']['sections'][0]['introductionParagraphCount'] = (
                        '首章按作者安排：无章外引言填0，有则填段数；其余章填0或省略'
                        if value.get('authorStructurePolicy') == AUTHOR_STRUCTURE_POLICY else
                        '首章背景段落数；其余章填0或省略')
                for paragraph in payload['outputSchema']['sections'][0]['paragraphs']:
                    for reference in paragraph.get('evidence', []):
                        reference['blockId'] = '从本章 blocks 复制'
            if document_mode_prompts:
                document_prompt = item['documentPrompt']
                section_instructions = item['sectionInstructions']
                if preference_suffix:
                    section_instructions = section_instructions[:-len(preference_suffix)]
                if (document_prompt['id'] != writing_prompt_id('document', value['mode']) or
                        document_prompt['mode'] != value['mode'] or item['effectiveMode'] != value['mode']):
                    raise ValueError('一次成文冻结提示词与章节实际模式不一致')
                section.update({'promptId': item['promptId'],
                                'documentPromptId': document_prompt['id'],
                                'documentPromptVersion': document_prompt['version']})
                if shared_instructions:
                    section['writingInstructionRef'] = instruction_ref(item['promptId'], value['mode'], 'section',
                                                                      item['writingPromptVersion'], section_instructions)
                    section['documentInstructionRef'] = instruction_ref(document_prompt['id'], value['mode'], 'document',
                                                                       document_prompt['version'], document_prompt['content'])
                else:
                    section.update(writingInstructions=section_instructions, documentInstructions=document_prompt['content'])
            elif mode_prompts:
                section.update({'promptId': item['promptId'], 'writingInstructions': item['messages'][0]['content']})
            visible_scope = citation_scope_payload(item)
            if visible_scope is not None:
                section['citationScope'] = visible_scope
            payload['sections'].append(section)
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': dumps(payload)}]

    def generation_plan(self, project_id):
        project = self.get(project_id)
        config = self._document_config(project)
        with self.store.connect() as db:
            return self._document_plan(db, project, config)[0]

    def queue_document(self, project_id, expected, confirm_outline=False, remaining_from_section_id=None):
        from .writing import WritingConflict, _version
        if not isinstance(confirm_outline, bool):
            raise ValueError('确认纲要值须为布尔值')
        initial = self.get(project_id)
        _version(initial, expected)
        config = self._document_config(initial)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._automatic_child_guard(db)
            if remaining_from_section_id is not None and self._automatic_active_run_id():
                raise ValueError('只有交互式逐章生成可以依次生成剩余章节')
            remaining_scope = _remaining_scope(project, remaining_from_section_id)
            active = db.execute("SELECT r.* FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND r.id<>? AND j.status IN ('queued','running') LIMIT 1", (project_id, self._automatic_active_run_id() or '')).fetchone()
            if active:
                if active['kind'] == 'document' and active['base_version'] == expected:
                    prior = json.loads(active['input_json'])
                    same_remaining = (prior.get('remainingFromSectionId') == remaining_from_section_id
                                      and prior.get('remainingSectionIds') == remaining_scope
                                      and prior.get('remainingGenerationPolicy') ==
                                      (REMAINING_GENERATION_POLICY if remaining_scope is not None else None))
                    if same_remaining:
                        return {'project': self.get(project_id), 'runId': active['id'], 'job': self.store.job(active['job_id'])}
                raise WritingConflict('当前项目已有写作任务，请等待完成或取消后继续')
            if not project['outlineConfirmed'] and not confirm_outline:
                raise ValueError('请先确认最新纲要，再自动生成全文')
            plan, prepared = self._document_plan(db, project, config, remaining_from_section_id)
            if not plan['canGenerate']:
                raise ValueError('；'.join(plan['blockingReasons']))
            from .writing_chapter_confirmation import POLICY as CHAPTER_CONFIRMATION_POLICY
            activate_chapter_confirmation = (plan.get('interactiveDocumentPolicy') == INTERACTIVE_DOCUMENT_POLICY
                                             and project.get('chapterConfirmationPolicy') != CHAPTER_CONFIRMATION_POLICY)
            if activate_chapter_confirmation:
                project['chapterConfirmationPolicy'] = CHAPTER_CONFIRMATION_POLICY
            if not project['outlineConfirmed']:
                project['outlineConfirmed'] = True
                self._save(db, project, 'outline_confirmed')
            elif activate_chapter_confirmation:
                self._save_chapter_confirmation(db, project, expected)
            frozen = {key: copy.deepcopy(project[key]) for key in ('title', 'brief', 'mode', 'sources', 'modelId')}
            frozen.update({'projectId': project_id, 'outline': _outline(project), 'plan': plan,
                           'documentPromptProtocol': DOCUMENT_MODE_PROMPT_PROTOCOL,
                           'documentRequirements': self._document_messages(project, prepared)[0]['content']})
            if remaining_scope is not None:
                frozen.update({key: copy.deepcopy(plan[key]) for key in
                               ('remainingGenerationPolicy', 'remainingFromSectionId', 'remainingSectionIds')})
            if prepared[0].get('compositionSnapshot') is not None:
                frozen['compositionSnapshot'] = copy.deepcopy(prepared[0]['compositionSnapshot'])
            if prepared[0].get('preferenceSnapshot') is not None:
                frozen['preferenceSnapshot'] = copy.deepcopy(prepared[0]['preferenceSnapshot'])
            if prepared[0].get('compositionInstructionFormat') is not None:
                frozen['compositionInstructionFormat'] = prepared[0]['compositionInstructionFormat']
            if all(item.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY for item in prepared):
                frozen['citationReviewPolicy'] = DEFERRED_CITATION_REVIEW_POLICY
            if all(item.get('sectionOutputEnvelopePolicy') == SECTION_ENVELOPE_POLICY for item in prepared):
                frozen['sectionOutputEnvelopePolicy'] = SECTION_ENVELOPE_POLICY
            if plan.get('automaticDocumentPolicy') == AUTOMATIC_DOCUMENT_POLICY:
                frozen['automaticDocumentPolicy'] = AUTOMATIC_DOCUMENT_POLICY
                frozen['automaticParentRunId'] = self._automatic_active_run_id()
                if all(item.get('automaticCitationPolicy') for item in prepared):
                    frozen['automaticCitationPolicy'] = prepared[0]['automaticCitationPolicy']
            if plan.get('interactiveDocumentPolicy') == INTERACTIVE_DOCUMENT_POLICY:
                frozen['interactiveDocumentPolicy'] = INTERACTIVE_DOCUMENT_POLICY
                frozen['chapterConfirmationPolicy'] = CHAPTER_CONFIRMATION_POLICY
            run_id, job_id, timestamp = ident('writing_run'), ident('job'), now()
            public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
            db.execute('INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (job_id, ident('writing_batch'), ('剩余章节生成 · ' if remaining_scope is not None else '一次成文 · ') + project['title'], 'writing_document', dumps(self._automatic_child_payload(run_id)), 'queued', 'queued', timestamp, timestamp))
            db.execute('INSERT INTO writing_runs(id,job_id,project_id,section_id,kind,base_version,model_id,model_version,model_json,prompt_version,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, job_id, project_id, None, 'document', project['version'], config['id'], config['version'], dumps(public), DOCUMENT_PROMPT_VERSION, dumps(frozen), timestamp))
            prompt_ids = tuple(dict.fromkeys([item['promptId'] for item in prepared] +
                                             [item['documentPrompt']['id'] for item in prepared] +
                                             (['writing_citation_match'] if any('citationMatchInstructions' in item for item in prepared) else [])))
            prompt_snapshots = (snapshot(db, prompt_ids, composition_policy=COMPOSITION_POLICY)
                                if 'compositionSnapshot' in frozen else snapshot(db, prompt_ids))
            # A document delegated by an automatic parent must retain the
            # parent's actual prompt content and versions, independent of edits.
            if 'compositionSnapshot' in frozen and self._automatic_active_run_id():
                parent = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (self._automatic_active_run_id(),)).fetchone()
                inherited = json.loads(parent['input_json']).get('compositionPrompts', {}) if parent else {}
                for prompt_id in prompt_ids:
                    if prompt_id in inherited:
                        prompt_snapshots[prompt_id] = copy.deepcopy(inherited[prompt_id])
            prompt_snapshots.update(copy.deepcopy((frozen.get('preferenceSnapshot') or {}).get('stylePromptSnapshot', {})))
            prompt_snapshots.update(copy.deepcopy((frozen.get('preferenceSnapshot') or {}).get('structurePromptSnapshot', {})))
            prompt_snapshots.update(copy.deepcopy((frozen.get('compositionSnapshot') or {}).get('compositionPromptSnapshot', {})))
            freeze(db, job_id, prompt_snapshots)
            by_id = {item['section']['id']: item for item in prepared}
            skipped = {item['sectionId']: item for item in plan['skippedSections']}
            for index, section in enumerate(project['sections']):
                sid = section['id']
                item = by_id.get(sid, {'section': section, 'targetWords': section['targetWords']})
                db.execute('INSERT INTO writing_document_steps VALUES(?,?,?,?,?,?,?)',
                           (run_id, sid, index, dumps(item), None, 'skipped' if sid in skipped else 'pending', skipped[sid]['reason'] if sid in skipped else ''))
            self._document_publish(db, run_id, plan)
        return {'project': self.get(project_id), 'runId': run_id, 'job': self.store.job(job_id)}

    def _document_publish(self, db, run_id, plan):
        steps = []
        for row in db.execute('SELECT * FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run_id,)):
            frozen = json.loads(row['input_json'])
            item = {'sectionId': row['section_id'], 'title': frozen['section']['title'], 'status': row['status'],
                    'reason': row['reason'], 'targetWords': frozen['targetWords']}
            if row['candidate_json']:
                item['section'] = json.loads(row['candidate_json'])['section']
                item['actualWords'] = _word_count(item['section']['content'])
                item['lengthWarning'] = item['actualWords'] < frozen['targetWords'] * .6 or item['actualWords'] > frozen['targetWords'] * 1.5
            steps.append(item)
        result = {'document': {'strategy': plan['strategy'], 'steps': steps, 'totalSections': len(steps),
                               'completedSections': sum(item['status'] in ('applied', 'candidate', 'skipped') for item in steps)}}
        db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps(result), run_id))
        return result

    @staticmethod
    def _document_safe(project, frozen, item):
        if not project['outlineConfirmed'] or any(project.get(key) != frozen.get(key) for key in ('title', 'brief', 'mode', 'sources', 'modelId')) or _outline(project) != frozen['outline']:
            return '纲要、需求、模式或资料范围已变化，保留候选，请根据最新要求重新生成'
        section = next((section for section in project['sections'] if section['id'] == item['section']['id']), None)
        if not section:
            return '章节已移除'
        if section['locked']:
            return '章节已锁定，原稿未修改'
        if section != item['section']:
            return '章节已发生人工修改或版本变化，原稿未覆盖'
        if section['manual'] or section['content']:
            return '保留已有正文和人工修改'
        return ''

    def _document_apply_step(self, db, run, frozen, row, *, allow_issues=False):
        item = json.loads(row['input_json'])
        project = self._load(db, run['project_id'])
        parent_state = None
        if frozen.get('automaticDocumentPolicy') == AUTOMATIC_DOCUMENT_POLICY and not allow_issues:
            from .writing import WritingConflict
            parent = db.execute('SELECT r.input_json,r.result_json,j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?',
                                (frozen['automaticParentRunId'],)).fetchone()
            if not parent or parent['status'] != 'running':
                raise WritingConflict('全自动写作已停止，未采纳迟到结果')
            parent_state = json.loads(parent['result_json'])['automatic']
            if parent_state['expectedVersion'] != project['version']:
                raise WritingConflict('项目版本已变化，全文候选未覆盖当前文稿')
        reason = self._document_safe(project, frozen, item)
        candidate = json.loads(row['candidate_json'])['section']
        if (not reason and not allow_issues and not automatic_citations_report_only(frozen)
                and item['effectiveMode'] == 'strict' and candidate.get('citationBindingState') == 'needs_review'):
            reason = '部分句子的资料依据仍需核对，已保留候选及匹配说明'
        if reason:
            db.execute("UPDATE writing_document_steps SET status='candidate',reason=? WHERE run_id=? AND section_id=?", (reason, run['id'], row['section_id']))
            return False
        section = self._section(project, row['section_id'])
        section.pop('citationSourceScope', None)
        section.pop('citationBindings', None)
        section.pop('citationBindingState', None)
        section.pop('citationIssues', None)
        section.pop('extendedContentPolicy', None)
        section.pop('generationNotices', None)
        section.pop('introductionParagraphCount', None)
        section.update(copy.deepcopy(candidate))
        section['manual'] = False
        section['version'] += 1
        self._save(db, project, 'generated_document_section')
        if parent_state is not None:
            parent_state['expectedVersion'] = project['version']
            parent_state['generatedSectionIds'] = list(dict.fromkeys([
                *parent_state['generatedSectionIds'], row['section_id']]))
            db.execute('UPDATE writing_runs SET result_json=? WHERE id=?',
                       (dumps({'automatic': parent_state}), frozen['automaticParentRunId']))
        db.execute("UPDATE writing_document_steps SET status='applied',reason='' WHERE run_id=? AND section_id=?", (run['id'], row['section_id']))
        return True

    @job_prompts
    def process_document(self, job):
        attempt = job['attempts'] + 1
        run = None
        try:
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM writing_runs WHERE id=? AND job_id=?', (json.loads(job['input_json'])['runId'], job['id'])).fetchone()
                if not row or not self._active(db, job, attempt):
                    return
                run = dict(row)
            frozen = json.loads(run['input_json'])
            plan = frozen['plan']
            config = self.models.resolve(run['model_id'], run['model_version'])
            while True:
                # Generation and sentence matching have separate durable
                # checkpoints. A matching retry must never reissue a completed
                # generation request or apply a provisional marker placement.
                with self.store.connect() as db:
                    checkpoints = db.execute('SELECT * FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['id'],)).fetchall()
                for checkpoint in checkpoints:
                    candidate = json.loads(checkpoint['candidate_json']) if checkpoint['candidate_json'] else None
                    if not candidate or not candidate.get('citationMatchingPending'):
                        continue
                    candidate = self._match_candidate_citations(run, job, attempt, config, json.loads(checkpoint['input_json']), candidate)
                    with self.store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        if not self._active(db, job, attempt):
                            return
                        db.execute("UPDATE writing_document_steps SET candidate_json=?,status='validated' WHERE run_id=? AND section_id=?", (dumps(candidate), run['id'], checkpoint['section_id']))
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        return
                    rows = db.execute('SELECT * FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['id'],)).fetchall()
                    # A saved model result is never requested a second time.
                    for row in rows:
                        if row['candidate_json'] and row['status'] not in ('applied', 'candidate', 'skipped'):
                            self._document_apply_step(db, run, frozen, row)
                    pending = []
                    project = self._load(db, run['project_id'])
                    for row in rows:
                        if row['candidate_json'] or row['status'] in ('applied', 'candidate', 'skipped'):
                            continue
                        reason = self._document_safe(project, frozen, json.loads(row['input_json']))
                        if reason:
                            db.execute("UPDATE writing_document_steps SET status='skipped',reason=? WHERE run_id=? AND section_id=?", (reason, run['id'], row['section_id']))
                        else:
                            pending.append(dict(row))
                    if not pending:
                        result = self._document_publish(db, run['id'], plan)
                        waiting = any(step['status'] == 'candidate' for step in result['document']['steps'])
                        db.execute('UPDATE writing_runs SET applied=? WHERE id=?', (int(not waiting), run['id']))
                        db.execute("UPDATE jobs SET status='succeeded',stage=?,error=NULL,updated_at=? WHERE id=? AND attempts=?", ('candidate' if waiting else 'ready', now(), job['id'], attempt))
                        return
                    selected = pending if plan['strategy'] == 'single' else pending[:1]
                    for row in selected:
                        prepared_step = json.loads(row['input_json'])
                        composition = prepared_step.get('compositionSnapshot')
                        if (plan['strategy'] == 'sequential' and composition is not None and
                                not prepared_step.get('compositionContextPrepared')):
                            if composition.get('policy') != COMPOSITION_POLICY:
                                raise ValueError('写作组织规则版本不受支持')
                            packet = json.loads(prepared_step['messages'][1]['content'])
                            packet['chapterContext'] = chapter_context(project, prepared_step['section'], composition)
                            prepared_step['messages'][1]['content'] = dumps(packet)
                            if sum(len(message['content']) for message in prepared_step['messages']) > int(config.get('contextChars', 24000)):
                                raise ValueError('章节衔接内容超出模型输入预算，请提高模型字符预算')
                            prepared_step['compositionContextPrepared'] = True
                            # Commit the final request before HTTP. Failed or
                            # interrupted requests reuse this exact context.
                            row['input_json'] = dumps(prepared_step)
                            db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                                       (row['input_json'], run['id'], row['section_id']))
                        db.execute("UPDATE writing_document_steps SET status='generating',reason='' WHERE run_id=? AND section_id=?", (run['id'], row['section_id']))
                    self._document_publish(db, run['id'], plan)
                    db.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=? AND attempts=?', ('generating', now(), job['id'], attempt))
                prepared = [json.loads(row['input_json']) for row in selected]
                messages = self._document_messages(frozen, prepared) if plan['strategy'] == 'single' else prepared[0]['messages']
                started = time.monotonic()
                def generation_guard():
                    from .writing import WritingConflict
                    with self.store.connect() as db:
                        if not self._active(db, job, attempt):
                            raise AutomaticRetryCancelled('全文任务已停止，未继续请求')
                        current = self._load(db, run['project_id'])
                        if any(self._document_safe(current, frozen, item) for item in prepared):
                            raise WritingConflict('章节已有正文、人工修改、锁定或版本变化，未继续请求')

                def result_guard():
                    with self.store.connect() as db:
                        if not self._generation_result_active(db, job, attempt):
                            raise AutomaticRetryCancelled('全文任务已停止，未采纳迟到结果')

                def generate():
                    with self.operations.capture(run, job, attempt, config, messages, [row['section_id'] for row in selected]) as capture:
                        response = capture.complete(self.models)
                        candidates = {}
                        if plan['strategy'] == 'single':
                            output_result, output_notices = parse_output({'messages': messages}, response, 'document')
                            output = output_result.get('sections')
                            expected_ids = {row['section_id'] for row in selected}
                            if not isinstance(output, list) or len(output) != len(selected) or any(not isinstance(item, dict) or not isinstance(item.get('sectionId'), str) for item in output) or {item['sectionId'] for item in output} != expected_ids:
                                error = ValueError('全文输出必须准确包含本次请求的全部章节，不能遗漏、重复或添加章节')
                                if frozen.get('automaticDocumentPolicy') == AUTOMATIC_DOCUMENT_POLICY:
                                    error.automaticRetryCategory = 'format_error'
                                raise error
                            by_id = {item['sectionId']: item for item in output}
                            for row, item in zip(selected, prepared):
                                candidates[row['section_id']] = self._candidate({'kind': 'section'}, item, {'text': dumps(by_id[row['section_id']])})
                                if output_notices:
                                    chapter = candidates[row['section_id']]['section']
                                    chapter['generationNotices'] = list(dict.fromkeys([*chapter.get('generationNotices', []), *output_notices]))[:24]
                        else:
                            candidates[selected[0]['section_id']] = self._candidate({'kind': 'section'}, prepared[0], response)
                        capture.validated()
                        usage = {key: value for key, value in (response.get('usage') or {}).items() if key in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(value, (int, float)) and not isinstance(value, bool)}
                        return {'candidates': candidates, 'usage': usage}
                generated = retry_call(self.store, frozen, 'document-generation:' + run['id'] + ':' +
                    ('single' if plan['strategy'] == 'single' else selected[0]['section_id']), generate,
                    guard=generation_guard, result_guard=result_guard, secret=config.get('apiKey', ''),
                    details={'runId': run['id'], 'stage': 'chapters'})
                candidates = generated['candidates']
                response = {'usage': generated['usage']}
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        return
                    for sid, candidate in candidates.items():
                        status = 'matching' if candidate.get('citationMatchingPending') else 'validated'
                        db.execute('UPDATE writing_document_steps SET candidate_json=?,status=? WHERE run_id=? AND section_id=?', (dumps(candidate), status, run['id'], sid))
                    usage = {key: value for key, value in (response.get('usage') or {}).items() if key in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(value, (int, float)) and not isinstance(value, bool)}
                    call_key = 'single' if plan['strategy'] == 'single' else selected[0]['section_id']
                    db.execute('INSERT INTO writing_document_calls VALUES(?,?,?,?,?,?)', (run['id'], call_key, attempt, round((time.monotonic() - started) * 1000), dumps(usage), now()))
                    self._document_publish(db, run['id'], plan)
                # Separate checkpoint transaction intentionally makes restart
                # after the model call reuse the persisted, validated result.
        except Exception as exc:
            message = str(exc) if type(exc) is ValueError or isinstance(exc, (CitationScopeError, ModelError)) else '全文生成任务执行异常，请检查模型配置后重试'
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self._active(db, job, attempt):
                    return
                if run:
                    db.execute("UPDATE writing_document_steps SET status='failed',reason=? WHERE run_id=? AND status='generating'", (message[:1500], run['id']))
                    self._document_publish(db, run['id'], json.loads(run['input_json'])['plan'])
                db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated_at=? WHERE id=? AND attempts=?", (message[:1500], now(), job['id'], attempt))

    def apply_document_section(self, project_id, run_id, section_id, expected):
        from .writing import WritingConflict, _version
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            run = db.execute('SELECT r.*,j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=? AND r.project_id=?', (run_id, project_id)).fetchone()
            if not run or run['kind'] != 'document':
                raise KeyError(run_id)
            row = db.execute('SELECT * FROM writing_document_steps WHERE run_id=? AND section_id=?', (run_id, section_id)).fetchone()
            if not row or not row['candidate_json'] or row['status'] != 'candidate' or run['status'] not in ('succeeded', 'failed', 'cancelled'):
                raise ValueError('该章节没有可采纳的完成候选')
            frozen = json.loads(run['input_json'])
            reason = self._document_safe(project, frozen, json.loads(row['input_json']))
            if reason:
                raise WritingConflict(reason)
            self._document_apply_step(db, dict(run), frozen, row, allow_issues=True)
            result = self._document_publish(db, run_id, frozen['plan'])
            if all(item['status'] in ('applied', 'skipped') for item in result['document']['steps']):
                db.execute('UPDATE writing_runs SET applied=1 WHERE id=?', (run_id,))
        return self.get(project_id)
