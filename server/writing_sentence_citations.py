"""Exact sentence bindings and citation-only, prose-preserving relocation.

Evidence excerpts remain immutable. Ranges are UTF-16 offsets in the raw saved
section so browser selection and rendering use the same coordinates. This
module validates identities and locations; semantic support is assessed by
the separate matching pass, never guessed from a marker's neighbouring text.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re


MARKER = re.compile(r'\[(E\d+)\]')
STATUSES = {'supported', 'partial', 'unsupported', 'uncertain', 'not_required', 'unreviewed'}
RESOLVED_STATUSES = frozenset(('supported', 'not_required'))
REVIEW_METHODS = frozenset(('model_review', 'assistant_evidence_review'))


def content_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def strip_citations(text):
    """Remove only citation protocol tokens; every prose byte is preserved."""
    return MARKER.sub('', text)


def evidence_hash(evidence):
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        return None
    fields = ('id', 'sourceId', 'revisionId', 'blockId', 'start', 'end', 'quote')
    return content_hash(json.dumps([{key: item.get(key) for key in fields} for item in evidence], ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def utf16_offset(text, offset):
    return len(text[:offset].encode('utf-16-le')) // 2


def codepoint_offset(text, offset):
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError('引用句位置必须为非负整数')
    units = 0
    for index, character in enumerate(text):
        if units == offset:
            return index
        units += 2 if ord(character) > 0xFFFF else 1
        if units > offset:
            raise ValueError('引用句位置不能切开 Unicode 字符')
    if units == offset:
        return len(text)
    raise ValueError('引用句位置超出正文范围')


def sentence_ranges(text):
    # One canonical segmenter is shared with the semantic matching request.
    from .writing_citation_matching import sentence_ranges as split
    return split(text)


def exact_sentence(text, start, end, claim):
    """An excerpt or a multi-sentence paragraph cannot masquerade as a sentence."""
    return any(item['start'] == start and item['end'] == end and item['text'] == claim
               for item in sentence_ranges(text))


def unreviewed_matches(content, evidence, matches):
    """Keep original exact sentence markers after an incomplete review.

    The caller supplies program-validated uncertain decisions for only the
    failed batch. An original marker establishes its location, never semantic
    support. Unknown markers, other sentences' sources and uncited sentences
    cannot acquire new evidence through this recovery path.
    """
    if (not isinstance(content, str) or not isinstance(matches, list)
            or not isinstance(evidence, list) or len(matches) > 4000):
        raise ValueError('未完成引用核对的恢复数据格式无效')
    if any(not isinstance(item, dict) or not isinstance(item.get('id'), str)
           or not re.fullmatch(r'E\d+', item['id']) for item in evidence):
        raise ValueError('引用登记标识不唯一或格式无效')
    registered = {item['id'] for item in evidence}
    if len(registered) != len(evidence):
        raise ValueError('引用登记标识不唯一或格式无效')
    plain, originals, removed = strip_citations(content), {}, 0
    for marker in MARKER.finditer(content):
        position, eid = marker.start() - removed, marker.group(1)
        removed += len(marker.group(0))
        if eid in registered:
            ids = originals.setdefault(position, [])
            if eid not in ids:
                ids.append(eid)
    canonical = {(item['start'], item['end'], item['text']) for item in sentence_ranges(plain)}
    result, converted = [], set()
    for match in matches:
        if not isinstance(match, dict):
            raise ValueError('未完成引用核对的恢复项必须为对象')
        start, end = codepoint_offset(plain, match.get('start')), codepoint_offset(plain, match.get('end'))
        claim = match.get('text')
        if not isinstance(claim, str) or (start, end, claim) not in canonical:
            raise ValueError('未完成引用核对须对应逐字一致的完整正文句子')
        if match.get('status') != 'uncertain':
            result.append(copy.deepcopy(match))
            continue
        key = (start, end, claim)
        if key in converted:
            continue
        converted.add(key)
        for eid in originals.get(end) or [None]:
            result.append({**copy.deepcopy(match), 'status': 'unreviewed',
                           'evidenceId': eid, 'supportBasis': 'none'})
    return result


def relocate_citations(content, evidence, matches, *, method='model_review', mode=None, support_policy=None):
    """Apply validated matching decisions, moving markers and never body text.

    Input offsets address ``strip_citations(content)`` in UTF-16. Incomplete
    reviews may retain original markers with an explicit unreviewed state;
    unsupported decisions never create a source marker.
    """
    if not isinstance(content, str) or not isinstance(matches, list) or len(matches) > 4000:
        raise ValueError('引用重新匹配结果格式无效')
    if not isinstance(method, str) or method not in REVIEW_METHODS:
        raise ValueError('引用审阅来源无效')
    if mode is not None:
        from .writing_citation_matching import matching_policy, validate_matching_policy
        support_policy = copy.deepcopy(matching_policy(mode) if support_policy is None else support_policy)
        validate_matching_policy(mode, support_policy)
    elif support_policy is not None:
        raise ValueError('引用支持规则必须明确写作模式')
    plain = strip_citations(content)
    registered = {item.get('id') for item in evidence if isinstance(item, dict)}
    if len(registered) != len(evidence) or any(not isinstance(eid, str) or not re.fullmatch(r'E\d+', eid) for eid in registered):
        raise ValueError('引用登记标识不唯一或格式无效')
    decisions, seen, insertions, sentence_statuses = [], set(), {}, {}
    for match in matches:
        if not isinstance(match, dict):
            raise ValueError('引用重新匹配项必须为对象')
        status = match.get('status')
        eid = match.get('evidenceId')
        if not isinstance(status, str) or status not in STATUSES or (eid is not None and (not isinstance(eid, str) or eid not in registered)) or (status == 'supported' and eid is None):
            raise ValueError('引用重新匹配的证据或支持状态无效')
        if status == 'not_required' and eid is not None:
            raise ValueError('组织性句子无需来源证据，不能绑定引用')
        if mode is not None:
            from .writing_citation_matching import validate_support_basis
            validate_support_basis(mode, status, [eid] if eid is not None else [], match.get('supportBasis'), support_policy)
        start = codepoint_offset(plain, match.get('start'))
        end = codepoint_offset(plain, match.get('end'))
        claim = match.get('text')
        if not isinstance(claim, str) or start >= end or plain[start:end] != claim or not exact_sentence(plain, start, end, claim):
            raise ValueError('引用必须绑定正文中一个逐字一致的完整句子')
        reason = match.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 3000:
            raise ValueError('引用匹配说明格式无效')
        sentence_key = (start, end, claim)
        if sentence_key in sentence_statuses and sentence_statuses[sentence_key] != status:
            raise ValueError('同一句不能同时登记为不同支持状态')
        sentence_statuses[sentence_key] = status
        key = (start, end, eid)
        if key in seen:
            raise ValueError('同一句与同一证据不能重复登记')
        seen.add(key)
        decision = {'evidenceId': eid, 'startCp': start, 'endCp': end, 'text': claim, 'status': status, 'reason': reason}
        if mode is not None:
            decision['supportBasis'] = match['supportBasis']
        decisions.append(decision)
        if status == 'supported' or status == 'unreviewed' and eid is not None:
            insertions.setdefault(end, []).append(decision)
    expected_sentences = {(item['start'], item['end'], item['text']) for item in sentence_ranges(plain)}
    actual_sentences = {(item['startCp'], item['endCp'], item['text']) for item in decisions}
    if actual_sentences != expected_sentences:
        raise ValueError('重新匹配须逐句判断完整正文，不能遗漏句子或补造句子')
    # Build a deterministic mapping from plain body offsets to the saved raw
    # text. At a sentence's start preceding markers are included; at its end
    # its own markers are excluded from the highlight range.
    chunks, raw_positions, added = [], {}, 0
    previous = 0
    for position, items in sorted(insertions.items()):
        chunks.append(plain[previous:position])
        for item in items:
            item['markerCp'] = position + added
            marker = '[' + item['evidenceId'] + ']'
            chunks.append(marker)
            added += len(marker)
        raw_positions[position] = sum(len('[' + item['evidenceId'] + ']') for item in items)
        previous = position
    chunks.append(plain[previous:])
    updated = ''.join(chunks)
    if len(updated) > 60000:
        raise ValueError('重新匹配后的章节超过正文长度上限')
    items = []
    for item in decisions:
        start = item.pop('startCp')
        end = item.pop('endCp')
        raw_start = start + sum(length for position, length in raw_positions.items() if position <= start)
        raw_end = end + sum(length for position, length in raw_positions.items() if position < end)
        stable = f'{start}:{end}:{item["evidenceId"]}:{item["status"]}:{item["text"]}'
        entry = {'id': 'citation_' + content_hash(stable)[:20],
                 'start': utf16_offset(updated, raw_start), 'end': utf16_offset(updated, raw_end), **item}
        if 'markerCp' in entry:
            entry['markerStart'] = utf16_offset(updated, entry.pop('markerCp'))
        items.append(entry)
    binding = {'version': 1, 'content': updated, 'contentHash': content_hash(updated),
               'textHash': content_hash(plain), 'evidenceHash': evidence_hash(evidence), 'method': method, 'items': items}
    if mode is not None:
        binding.update(mode=mode, supportPolicy=support_policy)
    assert strip_citations(updated) == plain
    return updated, binding


def valid_bindings(content, evidence, bindings, *, mode=None):
    """Reject stale or forged coordinate metadata without inferring a fallback."""
    if not isinstance(bindings, dict) or bindings.get('version') != 1 or bindings.get('content') != content:
        return False
    if not isinstance(bindings.get('method'), str) or bindings['method'] not in REVIEW_METHODS:
        return False
    binding_mode = bindings.get('mode')
    if binding_mode is not None:
        from .writing_citation_matching import validate_matching_policy
        try:
            validate_matching_policy(binding_mode, bindings.get('supportPolicy'))
        except (ValueError, TypeError):
            return False
        if mode is not None and binding_mode != mode:
            return False
    elif 'supportPolicy' in bindings:
        return False
    if not isinstance(evidence, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in evidence):
        return False
    if bindings.get('contentHash') != content_hash(content) or bindings.get('textHash') != content_hash(strip_citations(content)):
        return False
    if bindings.get('evidenceHash') != evidence_hash(evidence) or len({item['id'] for item in evidence}) != len(evidence):
        return False
    items = bindings.get('items')
    if not isinstance(items, list):
        return False
    registered = {item.get('id') for item in evidence if isinstance(item, dict)}
    expected = {(utf16_offset(content, marker.start()), marker.group(1)) for marker in MARKER.finditer(content)}
    actual, identities, actual_sentences, sentence_statuses = set(), set(), set(), {}
    plain = strip_citations(content)
    expected_sentences = {(item['start'], item['end'], item['text']) for item in sentence_ranges(plain)}
    try:
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('status'), str) or item['status'] not in STATUSES:
                return False
            start, end = codepoint_offset(content, item.get('start')), codepoint_offset(content, item.get('end'))
            if start >= end or content[start:end] != item.get('text') or MARKER.search(item['text']):
                return False
            plain_start, plain_end = len(strip_citations(content[:start])), len(strip_citations(content[:end]))
            sentence = (plain_start, plain_end, item['text'])
            if sentence not in expected_sentences:
                return False
            if sentence in sentence_statuses and sentence_statuses[sentence] != item['status']:
                return False
            sentence_statuses[sentence] = item['status']
            actual_sentences.add(sentence)
            eid = item.get('evidenceId')
            if eid is not None and (not isinstance(eid, str) or eid not in registered):
                return False
            if item['status'] == 'not_required' and eid is not None:
                return False
            if binding_mode is not None:
                from .writing_citation_matching import validate_support_basis
                validate_support_basis(binding_mode, item['status'], [eid] if eid is not None else [], item.get('supportBasis'), bindings['supportPolicy'])
            identity = item.get('id')
            if not isinstance(identity, str) or not identity or identity in identities:
                return False
            identities.add(identity)
            if item['status'] == 'supported' or item['status'] == 'unreviewed' and eid is not None:
                position = item.get('markerStart')
                marker = (position, eid)
                if marker not in expected or marker in actual:
                    return False
                marker_cp = codepoint_offset(content, position)
                if marker_cp < end or strip_citations(content[end:marker_cp]):
                    return False
                actual.add(marker)
            elif 'markerStart' in item:
                return False
    except (ValueError, TypeError, KeyError, UnicodeError):
        return False
    return actual == expected and actual_sentences == expected_sentences
