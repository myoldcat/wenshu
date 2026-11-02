"""Frozen, chapter-local aliases for model-visible original block references.

Aliases clarify chapter ownership; they never add sources or change the frozen
evidence registry. Legacy frozen requests continue to use their original IDs.
This module has no persistence, model, or source-loading side effects.
"""
from __future__ import annotations

import copy
import json


PROTOCOL = 'chapter-block-alias-v1'
OUTLINE_SCOPE_PROTOCOL = 'outline-block-alias-v1'
_REQUIREMENT = ('本章引用仅可使用 allowedBlockIds 中的完整编号；不得使用其他章节编号、'
                '永久原文块编号或自行编造编号。即使其他章节包含相同资料，也必须使用本章编号。'
                '编号须逐字复制，不得修改大小写、增加空白或省略前缀。')
_OUTLINE_REQUIREMENT = ('本次纲要 planningEvidence.blockId 仅可从 allowedBlockIds 逐字复制完整短编号；'
                        '不得从 ordinal 推算编号，不得使用永久原文块编号、章节编号或自行编造编号。'
                        '短编号只定位本次冻结原文，不改变来源、版本和逐字摘录要求。')


def _chapter_number(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError('冻结引用范围的章节序号无效')
    return value


def _original_ids(frozen):
    blocks = frozen.get('blocks') if isinstance(frozen, dict) else None
    if not isinstance(blocks, list):
        raise ValueError('冻结原文块列表无效')
    result, known = [], {}
    for block in blocks:
        value = block.get('blockId') if isinstance(block, dict) else None
        if not isinstance(value, str) or not value:
            raise ValueError('冻结原文块编号无效')
        if value in known and any(known[value].get(key) != block.get(key)
                                  for key in ('sourceId', 'revisionId', 'text')):
            raise ValueError('冻结资料包含不明确的重复原文块标识')
        known[value] = block
        result.append(value)
    return result


def _scope(frozen):
    scope = frozen.get('citationScope')
    if not isinstance(scope, dict) or scope.get('protocol') not in (PROTOCOL, OUTLINE_SCOPE_PROTOCOL):
        raise ValueError('冻结引用范围协议无效')
    if scope['protocol'] == OUTLINE_SCOPE_PROTOCOL:
        if set(scope) != {'protocol', 'namespace', 'aliases'}:
            raise ValueError('冻结纲要引用范围字段无效')
        namespace = 'O'
    else:
        number = _chapter_number(scope.get('chapterNumber'))
        namespace = 'C%02d' % number
    if scope.get('namespace') != namespace:
        raise ValueError('冻结引用范围的章节命名空间无效')
    expected = {'%s-B%03d' % (namespace, index): value
                for index, value in enumerate(_original_ids(frozen), 1)}
    aliases = scope.get('aliases')
    if not isinstance(aliases, dict) or aliases != expected:
        raise ValueError('冻结引用编号映射与本章原文块不一致')
    return scope


def citation_scope_payload(item):
    """Return only the chapter ownership contract that the model may see.

    None means a legacy frozen request, whose protocol must remain unchanged.
    Never expose the permanent-ID mapping or infer a replacement scope.
    """
    if 'citationScope' not in item:
        return None
    scope = _scope(item)
    return {'protocol': scope['protocol'], 'namespace': scope['namespace'],
            'allowedBlockIds': list(scope['aliases']),
            'requirement': _OUTLINE_REQUIREMENT if scope['protocol'] == OUTLINE_SCOPE_PROTOCOL else _REQUIREMENT}


def _describe_schema(value, namespace):
    if isinstance(value, dict):
        for key, entry in value.items():
            if key in ('evidence', 'planningEvidence') and isinstance(entry, list):
                for evidence in entry:
                    if isinstance(evidence, dict) and 'blockId' in evidence:
                        evidence['blockId'] = '从%s citationScope.allowedBlockIds 逐字复制完整短编号（如 %s-B001）' % (
                            '本次纲要' if namespace == 'O' else '本章', namespace)
            else:
                _describe_schema(entry, namespace)
    elif isinstance(value, list):
        for entry in value:
            _describe_schema(entry, namespace)


def scope_prepared(item, chapter_number):
    """Copy a newly prepared chapter and replace only model-visible block IDs.

    An already scoped frozen chapter keeps its namespace even when resumed as
    the only remaining chapter. Re-numbering checkpoints would break evidence.
    """
    result = copy.deepcopy(item)
    if 'citationScope' in result:
        if _scope(result)['protocol'] != PROTOCOL:
            raise ValueError('章节不能沿用纲要引用范围')
        return result
    number = _chapter_number(chapter_number)
    namespace = 'C%02d' % number
    original_ids = _original_ids(result)
    aliases = {'%s-B%03d' % (namespace, index): value
               for index, value in enumerate(original_ids, 1)}
    messages = result.get('messages')
    if not isinstance(messages, list) or len(messages) < 2 or not isinstance(messages[1], dict):
        raise ValueError('冻结章节消息无效')
    try:
        payload = json.loads(messages[1]['content'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('冻结章节消息不是有效 JSON') from None
    payload_blocks = payload.get('blocks') if isinstance(payload, dict) else None
    if (not isinstance(payload_blocks, list) or len(payload_blocks) != len(original_ids)
            or any(not isinstance(block, dict) or block.get('blockId') != original
                   for block, original in zip(payload_blocks, original_ids))):
        raise ValueError('模型章节材料与冻结原文块范围不一致')
    result['citationScope'] = {'protocol': PROTOCOL, 'chapterNumber': number,
                               'namespace': namespace, 'aliases': aliases}
    for block, alias in zip(payload_blocks, aliases):
        block['blockId'] = alias
    if 'plannedContent' in payload:
        plan = payload['plannedContent']
        ids = plan.get('evidenceBlockIds') if isinstance(plan, dict) else None
        reverse = {original: alias for alias, original in aliases.items()}
        if not isinstance(ids, list) or any(not isinstance(block_id, str) or block_id not in reverse for block_id in ids):
            raise ValueError('章节规划依据不在本章冻结原文块范围内')
        plan['evidenceBlockIds'] = [reverse[block_id] for block_id in ids]
    payload['citationScope'] = citation_scope_payload(result)
    _describe_schema(payload.get('outputSchema'), namespace)
    messages[1]['content'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return result


def scope_outline_prepared(item):
    """Scope a newly queued outline without inventing a chapter namespace.

    The permanent original registry stays intact. Only the generated request's
    model-visible IDs and schema change; old frozen requests are never upgraded
    by the resolver or validator.
    """
    result = copy.deepcopy(item)
    if 'citationScope' in result:
        if _scope(result)['protocol'] != OUTLINE_SCOPE_PROTOCOL:
            raise ValueError('纲要不能沿用章节引用范围')
        return result
    original_ids = _original_ids(result)
    messages = result.get('messages')
    if not isinstance(messages, list) or len(messages) < 2 or not isinstance(messages[1], dict):
        raise ValueError('冻结纲要消息无效')
    try:
        payload = json.loads(messages[1]['content'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('冻结纲要消息不是有效 JSON') from None
    if not isinstance(payload, dict) or payload.get('task') != 'writing_outline':
        raise ValueError('纲要引用范围只能用于纲要请求')
    payload_blocks = payload.get('blocks') if isinstance(payload, dict) else None
    if (not isinstance(payload_blocks, list) or len(payload_blocks) != len(original_ids)
            or any(not isinstance(block, dict) or block.get('blockId') != original
                   for block, original in zip(payload_blocks, original_ids))):
        raise ValueError('模型纲要材料与冻结原文块范围不一致')
    result['citationScope'] = {'protocol': OUTLINE_SCOPE_PROTOCOL, 'namespace': 'O',
        'aliases': {'O-B%03d' % index: value for index, value in enumerate(original_ids, 1)}}
    for block, alias in zip(payload_blocks, result['citationScope']['aliases']):
        block['blockId'] = alias
        # Ordinals are storage positions, not suffixes from which an ID can be
        # reconstructed. Keep them in the program's original registry only.
        block.pop('ordinal', None)
        if isinstance(block.get('locator'), dict):
            block['locator'].pop('ordinal', None)
    payload['citationScope'] = citation_scope_payload(result)
    _describe_schema(payload.get('outputSchema'), 'O')
    messages[1]['content'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return result


def resolve_reference_block(frozen, value):
    """Resolve exactly one ID within this frozen chapter; never repair an ID."""
    if not isinstance(value, str):
        raise ValueError('原文块编号须为字符串')
    if not value:
        raise ValueError('原文块编号为空')
    if 'citationScope' not in frozen:
        if value not in _original_ids(frozen):
            raise ValueError('原文块编号不在本章冻结资料范围内')
        return value
    scope = _scope(frozen)
    if value not in scope['aliases']:
        raise ValueError('原文块编号不在%s允许的引用编号中' % (
            '本次纲要' if scope['protocol'] == OUTLINE_SCOPE_PROTOCOL else '本章'))
    return scope['aliases'][value]


def _display(value, maximum=160):
    """Bound diagnostics and escape controls, including Unicode line controls."""
    if not isinstance(value, str):
        return '（非字符串：%s）' % type(value).__name__
    pieces, length = [], 0
    for char in value:
        piece = ('\\u%04x' % ord(char)) if ord(char) < 32 or 127 <= ord(char) <= 159 or char in '\u2028\u2029\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069' else char
        if length + len(piece) > maximum:
            return ''.join(pieces) + '…'
        pieces.append(piece)
        length += len(piece)
    return ''.join(pieces)


class CitationScopeError(ValueError):
    """A safe, human-readable rejection with machine-readable location fields."""


def citation_error(frozen, paragraph_index, evidence_index, block_value, reason, *,
                   message_prefix='模型引用了本次资料范围之外的原文块'):
    """Build a chapter/paragraph/reference diagnostic (indices are zero-based).

    Pass the exported MISSING_BLOCK_ID sentinel when the model omitted blockId;
    explicit JSON null is kept distinct from an absent field.
    """
    section = frozen.get('section') or {}
    section = section if isinstance(section, dict) else {}
    scope = frozen.get('citationScope') or {}
    number = scope.get('chapterNumber') if isinstance(scope, dict) else None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        number = frozen.get('chapterNumber')
    chapter = '第%d章' % number if isinstance(number, int) and not isinstance(number, bool) and number > 0 else '章节'
    title, section_id = _display(section.get('title', '（未提供标题）')), _display(section.get('id', '（未提供 ID）'))
    block = '（缺失 blockId）' if block_value is MISSING_BLOCK_ID else _display(block_value)
    if isinstance(block_value, str) and not block_value:
        block = '（空字符串）'
    error = CitationScopeError('%s“%s”（ID：%s）第%d段、第%d引用：%s；'
                               '错误原文块编号：%s；原因：%s' %
                               (chapter, title, section_id, paragraph_index + 1, evidence_index + 1,
                                _display(str(message_prefix), 160), block, _display(str(reason), 240)))
    error.chapter_number = number
    error.section_id = section.get('id')
    error.paragraph_index = paragraph_index
    error.evidence_index = evidence_index
    error.block_value = block_value
    error.reason = reason
    error.message_prefix = message_prefix
    return error


MISSING_BLOCK_ID = object()
