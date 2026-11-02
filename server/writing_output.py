"""Recover usable writing output under an explicitly frozen request policy.

This only repairs the transport shape. Evidence still has to pass the writing
service's source and support checks before it may become a registered citation.
"""
from __future__ import annotations

import copy
import json
import re

from . import enrichment
from .writing_preferences import AUTHOR_STRUCTURE_POLICY, INTRODUCTION_POLICY


OUTPUT_POLICY = 'preserve-usable-v1'
SECTION_ENVELOPE_POLICY = 'section-json-object-content-v1'
AUTOMATIC_CITATION_POLICY = 'report-and-continue-v1'
CITATION_RETENTION_POLICY = 'preserve-generated-citations-v1'
_BREAKS = re.compile(r'(?:\r?\n[\t ]*){2,}|\u2029+')
_MODEL_MARKERS = re.compile(r'\[E\d+\]')
_PREFIXED_RESULT = re.compile(
    r'\A[^\r\n{}]{1,200}[:：][\t ]*\r?\n[\t ]*'
    r'(?:```(?:json)?[\t ]*\r?\n[\t ]*)?'
    r'(?P<object>\{\s*"(?:paragraphs|sections|replacementText)"\s*:)', re.IGNORECASE)
_INTERNAL_ISSUE = re.compile(
    r'\b(?:sourceIds?|revisionIds?|blockIds?|sectionIds?|graphSnapshotId|'
    r'citationPlacement|generationOutputPolicy|referenceBlockIds?)\b', re.IGNORECASE)
_INTERRUPTED = {'length', 'max_tokens', 'max_output_tokens', 'content_filter',
                'interrupted', 'error', 'cancelled', 'canceled'}
_MAX_NOTICES = 12
_MAX_NOTICE_CHARS = 500


def automatic_citations_report_only(frozen):
    """Only newly delegated automatic runs may save an unverified draft."""
    return frozen.get('automaticCitationPolicy') == AUTOMATIC_CITATION_POLICY


def enabled(frozen):
    """Only the exact request-packet policy opts a job into these repairs."""
    try:
        packet = json.loads(frozen['messages'][1]['content'])
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return isinstance(packet, dict) and packet.get('generationOutputPolicy') == OUTPUT_POLICY


def _notice(notices, message):
    message = re.sub(r'[\x00-\x1f\x7f]', ' ', message).strip()
    if len(message) > _MAX_NOTICE_CHARS:
        message = message[:_MAX_NOTICE_CHARS - 1] + '…'
    if message and message not in notices and len(notices) < _MAX_NOTICES:
        notices.append(message)


def _issues(result, notices, *, preserve=False):
    if 'issues' not in result:
        return
    issues = result['issues']
    if isinstance(issues, str):
        issues = [issues]
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, str):
                continue
            if _INTERNAL_ISSUE.search(issue):
                issue = '生成结果存在参考材料对应问题，请结合参考材料检查。'
            _notice(notices, issue)
    # Strict outline planning must review every model-declared scope gap. The
    # original compliant list remains available to that independent reviewer;
    # publishing a sanitized author notice cannot erase a planning blocker.
    result['issues'] = issues if preserve else []


def _json_shaped(text):
    text = text.lstrip()
    if re.match(r'```json\b', text, re.IGNORECASE):
        return True
    if text.startswith('```'):
        text = text[3:].lstrip()
    return text.startswith(('{', '['))


def _plain_body(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('模型未返回可用正文，请重新生成')
    if _json_shaped(raw):
        raise ValueError('生成内容异常中断，未返回完整结果，请重新生成')
    # A complete top-level JSON string is plain prose in a recoverable wrapper.
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(parsed, str):
        return parsed
    raise ValueError('模型未返回可用正文，请重新生成')


def _prefixed_result(raw):
    """Unwrap a narrow protocol object, never prose mentioning JSON inline."""
    if not isinstance(raw, str):
        return None
    match = _PREFIXED_RESULT.match(raw.lstrip())
    if not match:
        return None
    payload = raw.lstrip()[match.start('object'):].strip()
    if payload.endswith('```'):
        payload = payload[:-3].rstrip()
    try:
        return enrichment._json(payload)
    except ValueError as exc:
        # A protocol wrapper that starts but never completes is not a usable
        # prose response, even if the provider reports a normal stop reason.
        raise ValueError('生成内容异常中断，未返回完整结果，请重新生成') from exc


def _paragraphs(result, notices, *, allow_zero_introduction=False):
    paragraphs = result.get('paragraphs')
    if 'paragraphs' not in result:
        for key in ('text', 'content'):
            if isinstance(result.get(key), str) and result[key].strip():
                paragraphs = [{'text': result[key], 'evidence': []}]
                _notice(notices, '已保留生成正文，本次结果未提供可用的引用信息。')
                break
    if not isinstance(paragraphs, list):
        raise ValueError('模型未返回可用正文，请重新生成')
    usable = []
    introduction_count = result.get('introductionParagraphCount')
    introduction_valid = type(introduction_count) is int and 0 < introduction_count < len(paragraphs)
    zero_introduction = allow_zero_introduction and type(introduction_count) is int and introduction_count == 0
    normalized_introduction_count = 0
    skipped = False
    for paragraph_index, paragraph in enumerate(paragraphs):
        if isinstance(paragraph, str):
            paragraph = {'text': paragraph, 'evidence': []}
            _notice(notices, '已保留生成正文，部分段落未提供可用的引用信息。')
        if not isinstance(paragraph, dict) or not isinstance(paragraph.get('text'), str) or not paragraph['text'].strip():
            skipped = True
            if introduction_valid and paragraph_index < introduction_count:
                introduction_valid = False
            continue
        # Preserve all other model fields; only discard references when a
        # repair makes their original paragraph/claim relationship ambiguous.
        paragraph = copy.deepcopy(paragraph)
        text = paragraph['text']
        if _MODEL_MARKERS.search(text):
            text = _MODEL_MARKERS.sub('', text)
            paragraph['evidence'] = []
            _notice(notices, '已移除正文中未登记的引用编号，相关段落的引用信息未采用。')
        if _BREAKS.search(text):
            paragraph['evidence'] = []
            _notice(notices, '已整理正文分段；原分段对应的引用信息未采用。')
            parts = [part.strip() for part in _BREAKS.split(text) if part.strip()]
        else:
            parts = [text] if text.strip() else []
        for part in parts:
            item = copy.deepcopy(paragraph)
            item['text'] = part
            usable.append(item)
        if introduction_valid and paragraph_index < introduction_count:
            normalized_introduction_count += len(parts)
        if not parts:
            skipped = True
    if not usable:
        raise ValueError('模型未返回可用正文，请重新生成')
    if skipped:
        _notice(notices, '已保留可用正文，未包含正文内容的条目已略过。')
    result['paragraphs'] = usable
    if 'introductionParagraphCount' in result:
        result['introductionParagraphCount'] = (normalized_introduction_count
            if introduction_valid else 0 if zero_introduction else None)


def _section_envelope(frozen, result, notices):
    """Unwrap one unambiguous response envelope only for opted-in requests."""
    packet = json.loads(frozen['messages'][1]['content'])
    if packet.get('sectionOutputEnvelopePolicy') != SECTION_ENVELOPE_POLICY:
        return result
    if (result.get('type') != 'json_object' or 'paragraphs' in result
            or not set(result) <= {'type', 'content', 'contentStatus', 'issues', 'sectionId'}):
        return result
    content = result.get('content')
    if not isinstance(content, dict) or 'paragraphs' not in content:
        return result
    if 'sectionId' in result and 'sectionId' in content and result['sectionId'] != content['sectionId']:
        return result
    # Paragraphs and their evidence move together. Never turn the envelope or
    # its author notices into prose, or borrow evidence for another text.
    value = copy.deepcopy(content)
    if 'sectionId' in result:
        value['sectionId'] = result['sectionId']
    issues = []
    for items in (result.get('issues', []), content.get('issues', [])):
        if isinstance(items, str):
            issues.append(items)
        elif isinstance(items, list):
            issues.extend(items)
    if 'issues' in result or 'issues' in content:
        value['issues'] = issues
    if 'contentStatus' not in value and 'contentStatus' in result:
        value['contentStatus'] = result['contentStatus']
    _notice(notices, '已整理生成结果的格式，正文及可用引用信息已保留。')
    return value


def _content_status(result):
    if result.get('contentStatus') == 'unrelated':
        raise ValueError('生成内容与主题完全无关，请重新生成')
    if result.get('contentStatus') == 'interrupted':
        raise ValueError('生成内容异常中断，未返回完整结果，请重新生成')


def parse_output(frozen, response, kind):
    """Return normalized structured output and separate author-facing notices.

    Old jobs retain exactly the JSON parser they were frozen with. A declared
    interruption or wholly unrelated result remains a hard failure; no keyword
    in a paragraph is treated as a semantic relevance judgment.
    """
    if not enabled(frozen):
        return enrichment._json(response['text']), []
    for key in ('finish_reason', 'finishReason'):
        reason = response.get(key)
        if isinstance(reason, str) and reason.lower() in _INTERRUPTED:
            raise ValueError('生成内容异常中断，未返回完整结果，请重新生成')
    notices = []
    try:
        result = enrichment._json(response['text'])
    except ValueError:
        if kind != 'section':
            raise
        result = _prefixed_result(response['text'])
        if result is not None:
            _notice(notices, '已整理生成结果的格式，正文及可用引用信息已保留。')
        else:
            text = _plain_body(response['text'])
            result = {'paragraphs': [{'text': text, 'evidence': []}]}
            _notice(notices, '已保留生成正文，本次结果未提供可用的引用信息。')
    result = copy.deepcopy(result)
    _content_status(result)
    if kind == 'section':
        result = _section_envelope(frozen, result, notices)
        _content_status(result)
    _issues(result, notices, preserve=kind == 'outline')
    if kind == 'section':
        packet = json.loads(frozen['messages'][1]['content'])
        _paragraphs(result, notices, allow_zero_introduction=(
            packet.get('authorStructurePolicy') == AUTHOR_STRUCTURE_POLICY
            and packet.get('introductionPolicy') == INTRODUCTION_POLICY))
    return result, notices
