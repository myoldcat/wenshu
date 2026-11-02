"""Deterministic whole-document checks over an immutable saved snapshot.

These checks deliberately do not claim factual entailment or fix the text.
Evidence identities and original anchors remain program-owned.
"""
from __future__ import annotations

from collections import Counter
import math
import re

from .store import now
from .writing_document import _word_count
from .writing_sentence_citations import codepoint_offset, valid_bindings
from .writing_check_report import finalize_review, review_skipped, six_step
from .writing_citation_matching import DEFERRED_CITATION_REVIEW_POLICY
from .writing_source_scope import citation_sources

CITATIONS = re.compile(r'\[(E\d+)\]')
PENDING = re.compile(r'待核实|待核验|待验证|待查证')
PLACEHOLDERS = re.compile(r'(?<![A-Za-z0-9_])(?:TODO|TBD)(?![A-Za-z0-9_])|待补充|待完善|待填写|待插入', re.I)
INVALID_XML = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')
GENERAL_REMINDER_CODES = frozenset({
    'section_word_count', 'total_word_count', 'source_new_revision',
    'image_caption_missing', 'images_unconfirmed',
})


def review_requires_draft(report):
    """Keep general reminders visible without making them draft-only issues.

    A current explicit skip waives the check without acknowledging findings.
    Otherwise derive this from the issues, including historical reports;
    unknown or malformed issues retain the consent gate.
    """
    if review_skipped(report):
        return False
    issues = report.get('issues', []) if isinstance(report, dict) else None
    if not isinstance(issues, list):
        return True
    for issue in issues:
        if not isinstance(issue, dict):
            return True
        if issue.get('acknowledged') is True:
            continue
        if (issue.get('severity') != 'warning' or not isinstance(issue.get('code'), str)
                or issue['code'] not in GENERAL_REMINDER_CODES):
            return True
    return False


def paragraphs(content):
    """The same non-empty blank-line paragraphs as the writing preview."""
    return [p for p in re.split(r'\n\s*\n', content) if p.strip()]


def _paragraph_index(content, items, position):
    cursor = 0
    for index, item in enumerate(items, 1):
        start = content.find(item, cursor)
        cursor = start + len(item)
        if start <= position < cursor:
            return index
    return None


def _target(value):
    if isinstance(value, str) and re.fullmatch(r'\s*\d+\s*', value):
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 100000 else 0


def _count_result(actual, target):
    minimum, maximum = math.ceil(target * .8), math.floor(target * 1.2)
    status = ('not_set' if not target else 'below_target' if actual < minimum else
              'above_target' if actual > maximum else 'within_range')
    return {'actual': actual, 'target': target, 'min': minimum, 'max': maximum, 'status': status}


def build_review(db, project):
    issues = []
    selective_workflow = six_step(project)
    selective_review = selective_workflow or any(
        section.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY
        for section in project['sections'])

    def add(code, message, section=None, severity='error', paragraph_index=None, evidence_id=None):
        issues.append({'code': code, 'message': message, 'sectionId': section['id'] if section else None,
                       'severity': severity, 'paragraphIndex': paragraph_index, 'evidenceId': evidence_id})

    if not project['outlineConfirmed']:
        add('outline_unconfirmed', '最新纲要尚未确认')
    if not project['sections']:
        add('outline_empty', '项目尚无章节')
    for source in project['sources']:
        row = db.execute('SELECT current_revision FROM sources WHERE id=?', (source['sourceId'],)).fetchone()
        if row and row['current_revision'] != source['revisionId']:
            add('source_new_revision', f'资料《{source["title"]}》已有新版本；本稿继续引用原固定版本', severity='warning')

    outline_target = sum(_target(section.get('targetWords')) for section in project['sections'])
    total_target = max(_target(project.get('brief', {}).get('targetWords')), outline_target)
    word_sections, coverage_sections, review_methods = [], [], set()
    for section in project['sections']:
        sources = citation_sources(project, section)
        allowed = {(source['sourceId'], source['revisionId']) for source in sources}
        narrowed = {(source['sourceId'], source['revisionId']): set(source['blockIds'])
                    for source in sources if 'blockIds' in source}
        selective = selective_workflow or section.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY
        content = section.get('content', '')
        mode = project['mode'] if section['mode'] == 'inherit' else section['mode']
        mode_severity = 'error' if mode == 'strict' else 'warning'
        plain_extended = mode == 'extended' and section.get('extendedContentPolicy') == 'retain-as-prose-v2'
        target = math.ceil(_target(section.get('targetWords')) * total_target / outline_target) if outline_target else 0
        word_result = {'sectionId': section['id'], 'title': section['title'], **_count_result(_word_count(content), target)}
        word_sections.append(word_result)
        if word_result['status'] in ('below_target', 'above_target'):
            add('section_word_count', f'本章 {word_result["actual"]} 字，目标 {target} 字，建议范围 {word_result["min"]}–{word_result["max"]} 字', section, 'warning')

        items = paragraphs(content)
        coverage = {'sectionId': section['id'], 'title': section['title'], 'paragraphs': len(items),
                    'citedParagraphs': 0, 'validlyCitedParagraphs': 0, 'pendingParagraphs': 0,
                    'matchedSentences': 0, 'notRequiredSentences': 0, 'unresolvedSentences': 0}
        coverage_sections.append(coverage)
        if not content.strip():
            add('section_empty', '章节尚无正文', section)

        # Do not collapse duplicate identities into a dict and accidentally
        # accept whichever anchor happened to occur last.
        evidence = section.get('evidence', [])
        if not isinstance(evidence, list):
            evidence = []
            add('citation_incomplete', '章节引用登记不是有效列表', section)
        counts = Counter(ref.get('id') for ref in evidence if isinstance(ref, dict) and isinstance(ref.get('id'), str))
        registered, valid = set(counts), set()
        for eid, count in counts.items():
            if count > 1:
                add('duplicate_citation', f'引用 [{eid}] 重复登记，不能唯一定位材料', section, evidence_id=eid)
        for ref in evidence:
            if not isinstance(ref, dict):
                add('citation_incomplete', '引用登记不完整', section)
                continue
            eid = ref.get('id')
            if not isinstance(eid, str) or not re.fullmatch(r'E\d+', eid):
                add('citation_id_invalid', '引用编号格式无效', section, evidence_id=eid if isinstance(eid, str) else None)
                continue
            strings = ('sourceId', 'revisionId', 'blockId', 'quote', 'title')
            complete = all(isinstance(ref.get(key), str) and ref[key].strip() for key in strings)
            complete = complete and all(isinstance(ref.get(key), int) and not isinstance(ref[key], bool) for key in ('start', 'end'))
            if not complete:
                add('citation_incomplete', f'引用 [{eid}] 缺少来源、版本、定位或原文摘录', section, evidence_id=eid)
                continue
            scope = (ref['sourceId'], ref['revisionId'])
            in_scope = scope in allowed and (scope not in narrowed or ref['blockId'] in narrowed[scope])
            if not in_scope:
                add('citation_out_of_scope', f'引用 [{eid}] 不在当前固定资料范围', section, evidence_id=eid)
            block = db.execute('SELECT b.text,r.source_id FROM blocks b JOIN revisions r ON r.id=b.revision_id WHERE b.id=? AND b.revision_id=?',
                               (ref['blockId'], ref['revisionId'])).fetchone()
            anchored = bool(block and block['source_id'] == ref['sourceId'] and
                            0 <= ref['start'] < ref['end'] <= len(block['text']) and
                            block['text'][ref['start']:ref['end']] == ref['quote'])
            if not anchored:
                add('citation_anchor_invalid', f'引用 [{eid}] 的固定版本原文定位失效', section, evidence_id=eid)
            if in_scope and anchored and counts[eid] == 1:
                valid.add(eid)

        bindings = section.get('citationBindings')
        current_bindings = valid_bindings(content, evidence, bindings, mode=mode)
        if bindings is not None and not current_bindings or section.get('citationBindingState') == 'stale':
            add('citation_binding_stale', '正文已经变化，原有引用句定位失效，请重新匹配引用', section, mode_severity)
        elif current_bindings:
            review_methods.add(bindings['method'])
            supported, not_required, unresolved, diagnostics = set(), set(), set(), {}
            for binding in bindings['items']:
                sentence = (binding['start'], binding['end'])
                if binding['status'] == 'not_required':
                    not_required.add(sentence)
                    continue
                if binding['status'] == 'supported' and binding.get('evidenceId') in valid:
                    supported.add(sentence)
                    continue
                unresolved.add(sentence)
                if plain_extended and not selective and binding.get('evidenceId') is None and binding['status'] != 'unreviewed':
                    continue
                issue = {'partial': ('citation_sentence_partial', '原文只支持本句的一部分'),
                         'unreviewed': ('citation_review_incomplete', '自动引用核对未完成，尚未判断原文支持关系'),
                         'unsupported': ('citation_sentence_unsupported', '当前摘录不能支持本句'),
                         'uncertain': ('citation_sentence_uncertain', '本句与原文的支持关系尚不确定')}.get(binding['status'])
                if issue:
                    key = (*sentence, binding['status'], binding.get('reason', ''))
                    diagnostic = diagnostics.setdefault(key, {'binding': binding, 'issue': issue, 'evidenceIds': []})
                    eid = binding.get('evidenceId')
                    if eid is not None and eid not in diagnostic['evidenceIds']:
                        diagnostic['evidenceIds'].append(eid)
            for diagnostic in diagnostics.values():
                binding, issue, evidence_ids = diagnostic['binding'], diagnostic['issue'], diagnostic['evidenceIds']
                start = codepoint_offset(content, binding['start'])
                paragraph_index = _paragraph_index(content, items, start)
                add(issue[0], issue[1] + '：' + binding['text'][:300] + ('；' + binding['reason'][:300] if binding.get('reason') else ''), section, mode_severity, paragraph_index, evidence_ids[0] if evidence_ids else None)
                issues[-1].update(sentence=binding['text'], reason=binding.get('reason', ''), evidenceIds=evidence_ids,
                                  start=binding['start'], end=binding['end'])
            coverage['matchedSentences'] = len(supported)
            coverage['notRequiredSentences'] = len(not_required)
            coverage['unresolvedSentences'] = len(unresolved)
        elif selective and content.strip() and section.get('citationBindingState') != 'stale':
            add('citation_review_pending', '本章尚未进行引用核对；事实、观点和明确引文仍待检查', section, 'warning')
        elif CITATIONS.search(content):
            add('citation_binding_missing', '现有引用只关联段落，尚未核对具体受支持句子，请重新匹配引用', section, 'warning')

        paragraph_cursor = 0
        for index, paragraph in enumerate(items, 1):
            paragraph_start = content.find(paragraph, paragraph_cursor)
            paragraph_end = paragraph_start + len(paragraph)
            paragraph_cursor = paragraph_end
            neutral_paragraph = False
            if current_bindings:
                paragraph_decisions = [binding for binding in bindings['items']
                                       if paragraph_start <= codepoint_offset(content, binding['start'])
                                       and codepoint_offset(content, binding['end']) <= paragraph_end]
                neutral_paragraph = bool(paragraph_decisions) and all(binding['status'] == 'not_required' for binding in paragraph_decisions)
            used = set(CITATIONS.findall(paragraph))
            for marker in re.findall(r'\[E[^\]\n]*(?:\]|(?=\n|$))', paragraph):
                if not re.fullmatch(r'\[E\d+\]', marker):
                    add('citation_id_invalid', '本段包含格式不完整的引用标记：' + marker[:80], section, paragraph_index=index)
            if used:
                coverage['citedParagraphs'] += 1
            if used & valid:
                coverage['validlyCitedParagraphs'] += 1
            elif not selective and not neutral_paragraph and not plain_extended:
                add('uncited_paragraph', '本段未关联有效的固定版本原文依据', section, mode_severity, index)
            for eid in sorted(used - registered):
                add('unknown_citation', f'本段包含未登记的引用 [{eid}]', section, paragraph_index=index, evidence_id=eid)
            supplement = '【模型补充' in paragraph
            pending, placeholder = bool(PENDING.search(paragraph)), bool(PLACEHOLDERS.search(paragraph))
            if supplement or pending or placeholder:
                coverage['pendingParagraphs'] += 1
            if supplement:
                add('model_supplement', '本段包含待核实的模型补充', section, mode_severity, index)
            elif pending:
                add('pending_content', '本段包含待核实或待核验内容', section, mode_severity, index)
            if placeholder:
                add('placeholder_content', '本段包含 TODO、TBD 或待补充占位内容', section, mode_severity, index)
            if INVALID_XML.search(paragraph):
                add('unsupported_control_character', '本段含 Word 无法保存的控制字符；请人工处理后导出 Word', section, paragraph_index=index)
        if section.get('manual') and not current_bindings and (not plain_extended or CITATIONS.search(content)):
            add('manual_edit_recheck', '正文经人工修改，请重新核对引用是否仍支持相邻论述', section, 'warning')

    total = _count_result(sum(item['actual'] for item in word_sections), total_target)
    if total['status'] in ('below_target', 'above_target'):
        add('total_word_count', f'全文 {total["actual"]} 字，目标 {total_target} 字，建议范围 {total["min"]}–{total["max"]} 字', severity='warning')
    total['total'] = total.pop('actual')
    from .writing_images import image_issues
    issues.extend(image_issues(db, project))
    errors = sum(issue['severity'] == 'error' for issue in issues)
    coverage = {key: sum(section[key] for section in coverage_sections)
                for key in ('paragraphs', 'citedParagraphs', 'validlyCitedParagraphs', 'pendingParagraphs', 'matchedSentences', 'notRequiredSentences', 'unresolvedSentences')}
    review_origin = ('尚未取得正文的引用支持核对结果' if not review_methods else
                     '逐句支持关系来自本次辅助证据审阅' if review_methods == {'assistant_evidence_review'} else
                     '逐句支持关系分别来自模型复核或辅助证据审阅' if 'assistant_evidence_review' in review_methods else
                     '逐句支持关系为模型依据已选摘录作出的判断')
    report = {'version': project['version'], 'checkedAt': now(), 'kind': 'rules', 'semanticVerified': False,
            'status': 'needs_attention' if issues else 'rules_passed',
            'requiresDraftConsent': review_requires_draft({'issues': issues}),
            'counts': {'errors': errors, 'warnings': len(issues) - errors}, 'issues': issues,
            'wordCounts': {**total, 'sections': word_sections, 'tolerance': .2,
                           'method': '汉字逐字计数，连续英文或数字计一词；不计引用编号及模型补充标记。目标与全文生成计划一致，允许上下浮动 20%，超出仅提醒。'},
            'coverage': {**coverage, 'sections': coverage_sections},
            'citationReviewMethods': sorted(review_methods),
            'citationReviewCompleted': any(section.get('content', '').strip() for section in project['sections']) and not any(
                issue['code'] in ('citation_review_pending', 'citation_review_incomplete',
                                  'citation_binding_missing', 'citation_binding_stale')
                for issue in issues),
            'notice': ('检查字数、纲要、引用身份、固定版本定位及已有的引用核对结果。事实、观点和明确引文需要核对；'
                       '忠实总结、过渡、无新增主张的重复与基础常识不要求逐句对应资料。' if selective_review else
                       '检查字数、纲要、引用身份、固定版本定位、逐句引用匹配结果及待核实标记。')
                      + review_origin + '，仍需人工核对，不代表已核验事实。人工修改和锁定内容不会被校验改写。'}
    return finalize_review(project, report)
