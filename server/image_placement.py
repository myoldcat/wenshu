"""Bounded, chapter-wide image placement; model output never owns an anchor.

Callbacks own model execution and operation capture. This module neither reads
the library nor writes a manuscript. Full paragraphs are the unit of review;
lexical/vector ranking is only a way to shortlist an oversized chapter.
"""
from __future__ import annotations

import base64
import copy
import json
import math
import re

from .writing_images import content_hash, paragraphs

LEGACY_PLACEMENT_PROTOCOL = 'writing-image-placement-1'
PLACEMENT_PROTOCOL = 'writing-image-placement-2'
QUALITY_POLICY = 'writing-image-quality-1'
MAX_VISUAL_CANDIDATES = 12
MAX_SHORTLIST_PARAGRAPHS = 8
MAX_EMBED_PARAGRAPHS = 128
OUTPUT_OVERHEAD = 192
OUTPUT_PER_CANDIDATE = 900

from .prompt_task_defaults import PLACEMENT_SYSTEM


class ImagePlacementInterrupted(ValueError):
    """Cancellation/input changes must not be converted to a manual fallback."""


def _guard(callback):
    if callback is not None:
        try:
            callback()
        except Exception:
            raise ImagePlacementInterrupted('配图位置任务已取消或输入已变化') from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('配图位置返回了重复字段')
        result[key] = value
    return result


def _reason(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 60 and bool(re.search(r'[\u3400-\u9fff]', value))


def validate_placement_response(response, packet, *, forbidden_text=None):
    """Strict schema/complete IDs, reusable inside an operation capture.

    Only program-supplied paragraph IDs are accepted. No returned offsets,
    paragraphs or hashes can reach a placement. Error messages omit model text.
    """
    text = response.get('text') if isinstance(response, dict) else None
    if not isinstance(text, str) or not 0 < len(text) <= 24000:
        raise ValueError('配图位置核对未返回有效文本')
    if forbidden_text and forbidden_text in text:
        raise ValueError('配图位置结果包含不应保存的信息')
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (TypeError, ValueError):
        raise ValueError('配图位置结果不是完整有效的JSON') from None
    if forbidden_text and forbidden_text in _json(value):
        raise ValueError('配图位置结果包含不应保存的信息')
    try:
        paragraph_ids = {item['paragraphId'] for item in packet['paragraphs']}
        allowed = {item['assetId']: set(item['paragraphIds']) for item in packet['candidates']}
        if len(allowed) != len(packet['candidates']) or any(not ids or not ids <= paragraph_ids for ids in allowed.values()):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('配图位置输入名单无效') from None
    items = value.get('items') if isinstance(value, dict) else None
    if not isinstance(value, dict) or set(value) != {'items'} or not isinstance(items, list) or len(items) != len(allowed):
        raise ValueError('配图位置结果未完整对应候选批次')
    result = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {'assetId', 'matches', 'reason'}:
            raise ValueError('配图位置结果字段不正确')
        asset_id = item['assetId']
        if not isinstance(asset_id, str) or asset_id not in allowed or asset_id in result:
            raise ValueError('配图位置包含候选外或重复图片')
        if not _reason(item['reason']) or not isinstance(item['matches'], list) or len(item['matches']) > 2:
            raise ValueError('配图位置数量或理由无效')
        seen = set()
        for match in item['matches']:
            if not isinstance(match, dict) or set(match) != {'paragraphId', 'relevance', 'readable', 'contextConsistent', 'reason'}:
                raise ValueError('配图位置匹配字段不正确')
            paragraph_id = match['paragraphId']
            if not isinstance(paragraph_id, str) or paragraph_id not in allowed[asset_id] or paragraph_id in seen:
                raise ValueError('配图位置包含未核对或重复段落')
            seen.add(paragraph_id)
            if match['relevance'] not in ('relevant', 'uncertain') or not _reason(match['reason']):
                raise ValueError('配图位置相关性或理由无效')
            if any(match[key] is not None and type(match[key]) is not bool for key in ('readable', 'contextConsistent')):
                raise ValueError('配图位置画面核对状态无效')
        result[asset_id] = item
    return result


def _descriptive(caption):
    credit = re.compile(r'^(?:(?:(?:image|photo(?:graph)?|picture|illustration)\s+)?(?:credit|source)s?\s*[:：]'
                        r'|(?:image|photo(?:graph)?|picture|illustration)\s*[:：]'
                        r'|(?:图片来源|照片来源|图像来源|图源|图片署名|摄影|来源)\s*[:：]'
                        r'|courtesy\s+of\b|copyright\b|©)', re.I)
    for line in (line.strip().strip('()（）[]').strip() for line in caption.splitlines()):
        if not line or credit.match(line):
            continue
        # An isolated name/byline is not a description of the pictured subject.
        # Short labels remain available for manual use; no brand dictionary is
        # used to invent whether a name identifies a product, person or source.
        if re.fullmatch(r"[A-Za-z0-9&'-]+(?:\s+[A-Za-z0-9&'-]+)?\.?", line) or re.fullmatch(r'[\u3400-\u9fff]{1,3}', line):
            continue
        return True
    return False


def _terms(text):
    words = set(re.findall(r'[a-z0-9]+(?:[-.][a-z0-9]+)*', text.casefold()))
    for part in re.findall(r'[\u3400-\u9fff]+', text):
        words.update(part[index:index+2] for index in range(len(part)-1))
    return words - {'the', 'and', 'image', 'photo', 'credit', '来源', '图片', '配图'}


def _spread_order(size):
    """Tie breaks cover the chapter, instead of silently keeping its prefix."""
    if not size:
        return []
    order, pending = [], [(0, size-1)]
    order.append(0)
    if size > 1:
        order.append(size-1)
    while pending:
        left, right = pending.pop(0)
        if right-left > 1:
            mid = (left+right)//2
            order.append(mid)
            pending.extend(((left, mid), (mid, right)))
    return order


def _vector(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 8192:
        raise ValueError('配图段落语义向量无效')
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('配图段落语义向量无效')
    norm = math.sqrt(sum(x*x for x in value))
    if not math.isfinite(norm) or not norm:
        raise ValueError('配图段落语义向量无效')
    return [x/norm for x in value]


def recommend_placements(section, candidates, *, model_config=None, vision_call=None,
                         image_loader=None, embedding_config=None, embed_call=None,
                         request_guard=None, placement_system,
                         placement_protocol=LEGACY_PLACEMENT_PROTOCOL, quality_policy=None):
    """Return copied candidates, per-candidate coverage and exact suggestions.

    ``image_loader(candidate)`` supplies a bounded local raster data URI, normally
    card_cover.thumbnail. ``vision_call(config, messages)`` and
    ``embed_call(config, texts)`` preserve caller-owned capture and frozen models.
    The frozen placement_system is mandatory, including on a manual fallback.
    Missing placement_protocol deliberately retains v1 for old frozen tasks.
    V2 exposes only batch-local i0/i1 image aliases to the model; the program
    maps exact validated aliases back to original asset IDs after each call.
    A missing quality_policy retains the old manual fallback. New frozen tasks
    may require positive direct matching before any position becomes actionable.
    """
    _guard(request_guard)
    if not isinstance(placement_system, str) or not placement_system.strip() or len(placement_system) > 20000:
        raise ValueError('配图位置核对规则无效，请重新创建任务')
    if placement_protocol not in (LEGACY_PLACEMENT_PROTOCOL, PLACEMENT_PROTOCOL):
        raise ValueError('配图位置核对规则版本无效，请重新创建任务')
    if quality_policy not in (None, QUALITY_POLICY):
        raise ValueError('配图质量规则版本无效，请重新创建任务')
    quality_first = quality_policy == QUALITY_POLICY
    content = section.get('content') if isinstance(section, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError('请先保存完整章节正文，再推荐配图位置')
    if not isinstance(candidates, list) or len(candidates) > 400:
        raise ValueError('配图候选列表无效')
    ids = [item.get('assetId') if isinstance(item, dict) else None for item in candidates]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError('配图候选标识无效或重复')
    records = paragraphs(content)
    by_id = {'p'+str(p['index']): p for p in records}
    all_ids = list(by_id)
    digest = content_hash(content)
    items, warnings, vectors = copy.deepcopy(candidates), [], {}
    coverage = {'paragraphCount': len(records), 'lexicalParagraphCount': len(records), 'visualCandidateCount': 0,
                'visualReviewedCandidateCount': 0, 'candidateLimitReached': False, 'semanticParagraphCount': 0,
                'semanticStatus': 'not_used', 'complete': False, 'modelBatches': []}

    def issue(item, text):
        if text not in item.setdefault('issues', []):
            item['issues'].append(text)

    for item in items:
        item['placementSuggestions'] = []
        item['placementStatus'] = 'not_reviewed'
        item['placementCoverage'] = {'totalParagraphs': len(records), 'reviewedParagraphIndexes': [], 'complete': False}
        if quality_first:
            item['qualityGate'] = {'policy': QUALITY_POLICY, 'passed': False,
                'reason': '尚未确认画面清晰且与正文直接对应，暂不建议配图；可以不配图。'}
        if item.get('status') != 'unsuitable':
            item['status'] = 'needs_review'
        if item.get('sourceType') == 'library':
            issue(item, '来自其他资料，仅可人工确认配图，不能作为正文依据')
        if not _descriptive(item.get('originalCaption') or ''):
            issue(item, '原图注缺少画面描述；仅在图文直接对应时可另行人工核对来源' if quality_first
                        else '原图注缺少画面描述，图片身份与位置须人工确认')
    eligible = [item for item in items if item.get('scopeEligible') is True and item.get('status') != 'unsuitable']
    references = [item for item in eligible if item.get('sourceType') != 'library']
    library = [item for item in eligible if item.get('sourceType') == 'library']
    # Reference priority still leaves room to assess explicitly requested
    # library candidates. Empty slots are reused, never filled with unsuitable
    # images merely to meet a quota.
    reference_count = min(len(references), MAX_VISUAL_CANDIDATES-min(3, len(library)))
    selected = references[:reference_count] + library[:MAX_VISUAL_CANDIDATES-reference_count]
    coverage['visualCandidateCount'] = len(selected)
    coverage['visualReferenceCandidateCount'] = reference_count
    coverage['visualLibraryCandidateCount'] = len(selected)-reference_count
    coverage['candidateLimitReached'] = len(eligible) > len(selected)
    if coverage['candidateLimitReached']:
        warnings.append('本次只核对了部分候选图片的位置，其余图片仍需人工核对。')
    if not model_config or vision_call is None or image_loader is None:
        warnings.append('尚未完成画面与位置核对，暂不建议配图；可以不配图或稍后重试。' if quality_first
                        else '尚未完成画面与位置核对，请查看原图后人工选择位置。')
        return {'candidates': items, 'coverage': coverage, 'warnings': warnings}
    output_limit = model_config.get('maxOutputTokens', 2048)
    batch_limit = min(2, max(0, (output_limit-OUTPUT_OVERHEAD)//OUTPUT_PER_CANDIDATE)) if type(output_limit) is int else 0
    context_limit = model_config.get('contextChars', 16000)
    if type(context_limit) is not int or context_limit < 1 or not batch_limit:
        warnings.append('本次无法完整核对配图位置，暂不建议配图；可以不配图或稍后重试。' if quality_first
                        else '本次无法完整核对配图位置，请人工选择或更换模型后重试。')
        return {'candidates': items, 'coverage': coverage, 'warnings': warnings}

    def model_id(item, index):
        return 'i'+str(index) if placement_protocol == PLACEMENT_PROTOCOL else item['assetId']

    def packet_for(batch):
        used = {pid for item in batch for pid in item['_placementIds']}
        return {'task': 'writing_image_placement', 'sectionTitle': section.get('title', ''),
                'paragraphs': [{'paragraphId': pid, 'text': by_id[pid]['text']} for pid in all_ids if pid in used],
                'candidates': [{'assetId': model_id(item, index), 'paragraphIds': item['_placementIds'],
                    'sourceTitle': item.get('sourceTitle', ''), 'sourceType': item.get('sourceType', 'reference'),
                    'originalCaption': item.get('originalCaption', ''), 'caption': item.get('caption', ''),
                    'altText': item.get('altText', ''), 'contextOnly': item.get('_contextText', ''),
                    'width': item.get('width'), 'height': item.get('height')} for index, item in enumerate(batch)]}

    def fits(batch):
        return len(placement_system) + len(_json(packet_for(batch))) + sum(len('assetId='+model_id(item, index)) for index, item in enumerate(batch)) <= context_limit

    def ranked_ids(item):
        query = '\n'.join(item.get(k, '') or '' for k in ('originalCaption', 'caption', 'altText', '_searchText'))
        terms = _terms(query)
        scores = {pid: len(terms & _terms(p['text']))/max(1, len(terms)) for pid, p in by_id.items()}
        ties = {all_ids[index]: order for order, index in enumerate(_spread_order(len(all_ids)))}
        ranked = sorted(all_ids, key=lambda pid: (-scores[pid], ties[pid]))
        if not embedding_config or embed_call is None or not query.strip():
            return ranked
        embedding_limit = embedding_config.get('contextChars', 16000)
        if type(embedding_limit) is not int or len(query) > embedding_limit:
            coverage['semanticStatus'] = 'unavailable'
            return ranked
        chosen = [pid for pid in ranked if len(by_id[pid]['text']) <= embedding_limit][:MAX_EMBED_PARAGRAPHS]
        def encode(texts):
            _guard(request_guard)
            values = embed_call(embedding_config, texts)
            _guard(request_guard)
            if not isinstance(values, list) or len(values) != len(texts):
                raise ValueError('配图段落语义结果不完整')
            return [_vector(value) for value in values]
        try:
            query_vector = encode([query])[0]
            pending = [pid for pid in chosen if pid not in vectors]
            while pending:
                batch, size = [], 0
                while pending and len(batch) < 16 and size+len(by_id[pending[0]]['text']) <= embedding_limit:
                    pid = pending.pop(0)
                    batch.append(pid)
                    size += len(by_id[pid]['text'])
                if not batch:
                    break
                for pid, vector in zip(batch, encode([by_id[pid]['text'] for pid in batch])):
                    if len(vector) != len(query_vector):
                        raise ValueError('配图段落语义维度不一致')
                    vectors[pid] = vector
            semantic = {pid: sum(a*b for a, b in zip(query_vector, vectors[pid])) for pid in chosen
                        if pid in vectors and len(vectors[pid]) == len(query_vector)}
            coverage['semanticParagraphCount'] = len(vectors)
            coverage['semanticStatus'] = 'ready' if len(semantic) == len(records) else 'partial'
            # Both channels rank every paragraph; vectors are not fact evidence.
            semantic_rank = sorted(semantic, key=lambda pid: (-semantic[pid], ties[pid]))
            fusion = {pid: 1/(30+rank) for rank, pid in enumerate(ranked, 1)}
            for rank, pid in enumerate(semantic_rank, 1):
                fusion[pid] += 1/(30+rank)
            return sorted(all_ids, key=lambda pid: (-fusion[pid], ties[pid]))
        except ImagePlacementInterrupted:
            raise
        except Exception:
            _guard(request_guard)
            coverage['semanticStatus'] = 'unavailable'
            if '段落语义筛选暂不可用，已使用全章文字匹配并保留覆盖不足提示。' not in warnings:
                warnings.append('段落语义筛选暂不可用，已使用全章文字匹配并保留覆盖不足提示。')
            return ranked

    prepared = []
    for item in selected:
        _guard(request_guard)
        item['_placementIds'] = all_ids[:]
        if not fits([item]):
            item['_placementIds'] = []
            for pid in ranked_ids(item):
                if len(item['_placementIds']) >= MAX_SHORTLIST_PARAGRAPHS:
                    break
                item['_placementIds'].append(pid)
                if not fits([item]):
                    item['_placementIds'].pop()
            if len(item['_placementIds']) < len(records):
                issue(item, '章节较长，本次仅核对部分完整段落；其他位置尚未核对')
        item['placementCoverage']['selectedParagraphIndexes'] = sorted(by_id[pid]['index'] for pid in item['_placementIds'])
        if not item['_placementIds']:
            item['placementStatus'] = 'over_budget'
            issue(item, '完整段落或图注过长，本次未能核对位置，暂不建议配图' if quality_first
                        else '完整段落或图注过长，本次未能核对位置，请人工确认')
            continue
        try:
            image = image_loader(item)
            _guard(request_guard)
            if not isinstance(image, str) or len(image) > 3*1024*1024 or not re.match(r'^data:image/(?:png|jpeg|webp|gif);base64,', image):
                raise ValueError()
            if not base64.b64decode(image.split(',', 1)[1], validate=True):
                raise ValueError()
            item['_placementImage'] = image
            prepared.append(item)
        except ImagePlacementInterrupted:
            raise
        except Exception:
            _guard(request_guard)
            item['placementStatus'] = 'unavailable'
            issue(item, '图片暂不可用于画面核对，未提供可采纳位置，请稍后重试' if quality_first
                        else '图片暂不可用于画面核对，请查看原图后人工确认')

    batches, pending = [], []
    for item in prepared:
        if pending and (len(pending) >= batch_limit or not fits([*pending, item])):
            batches.append(pending)
            pending = []
        pending.append(item)
    if pending:
        batches.append(pending)
    for batch in batches:
        packet = packet_for(batch)
        parts = [{'type': 'text', 'text': _json(packet)}]
        for index, item in enumerate(batch):
            parts.extend([{'type': 'text', 'text': 'assetId='+model_id(item, index)},
                          {'type': 'image_url', 'image_url': {'url': item['_placementImage'], 'detail': 'low'}}])
        messages = [{'role': 'system', 'content': placement_system}, {'role': 'user', 'content': parts}]
        try:
            _guard(request_guard)
            # Private audit metadata belongs to the run result, not the model
            # packet. Retain it even if the ensuing response fails validation.
            coverage['modelBatches'].append({'batchIndex': len(coverage['modelBatches']),
                                            'assetIds': {model_id(item, index): item['assetId'] for index, item in enumerate(batch)}})
            response = vision_call(model_config, messages)
            _guard(request_guard)
            judgments = validate_placement_response(response, packet, forbidden_text=model_config.get('apiKey'))
            for index, item in enumerate(batch):
                judgment = judgments[model_id(item, index)]
                complete = len(item['_placementIds']) == len(records)
                item['placementCoverage'].update(reviewedParagraphIndexes=sorted(by_id[pid]['index'] for pid in item['_placementIds']), complete=complete)
                item['placementReason'] = judgment['reason'].strip()
                item['placementStatus'] = 'reviewed' if complete else 'partial'
                item['visualStatus'] = 'reviewed'
                coverage['visualReviewedCandidateCount'] += 1
                for match in judgment['matches']:
                    if quality_first and not (match['relevance'] == 'relevant' and match['readable'] is True
                                              and match['contextConsistent'] is True):
                        continue
                    if match['readable'] is False or match['contextConsistent'] is False:
                        continue
                    record = by_id[match['paragraphId']]
                    automatic = (complete and match['relevance'] == 'relevant' and match['readable'] is True
                                 and match['contextConsistent'] is True and item.get('sourceType', 'reference') == 'reference'
                                 and _descriptive(item.get('originalCaption') or '') and not item.get('issues'))
                    item['placementSuggestions'].append({'paragraphIndex': record['index'], 'paragraphExact': record['text'],
                        'contentHash': digest, 'status': 'recommended' if automatic else 'needs_review', 'reason': match['reason'].strip()})
                    if quality_first:
                        item['placementSuggestions'][-1]['qualityPassed'] = True
                if any(p['status'] == 'recommended' for p in item['placementSuggestions']):
                    item['status'] = 'recommended'
                elif quality_first and not item['placementSuggestions'] and (not complete or any(
                        match['relevance'] == 'uncertain' or match['readable'] is None or match['contextConsistent'] is None
                        for match in judgment['matches'])):
                    item['status'], item['placementStatus'] = 'needs_review', 'unconfirmed'
                elif not item['placementSuggestions'] and complete:
                    item['status'], item['placementStatus'] = 'unsuitable', 'no_match'
                else:
                    item['status'] = 'needs_review'
                if quality_first:
                    passed = bool(item['placementSuggestions'])
                    item['qualityGate'].update(passed=passed, reason=(
                        '画面清晰且与正文直接对应，可作为配图。' if item['status'] == 'recommended' else
                        '画面清晰且与正文直接对应，采纳前仍须人工核对来源与图注。' if passed else
                        '未找到画面清晰且直接对应正文的位置，本章可以不配图。' if item['placementStatus'] == 'no_match' else
                        '尚未确认图片与正文直接对应，暂不建议配图；可以不配图。'))
        except ImagePlacementInterrupted:
            raise
        except Exception:
            _guard(request_guard)
            for item in batch:
                item['placementStatus'] = 'failed'
                if quality_first:
                    item['placementSuggestions'] = []
                    item['status'] = 'needs_review'
                    item['qualityGate'].update(passed=False, reason='尚未完成有效的图文核对，暂不建议配图；可以不配图或重试。')
                issue(item, '本次未获得完整有效的位置核对结果，暂不建议配图，请重试' if quality_first
                            else '本次未获得完整有效的位置核对结果，请人工确认或重试')
            warnings.append('部分图片的位置核对未完成，未将其标为推荐。')
    coverage['complete'] = (not coverage['candidateLimitReached'] and
                            all(item['placementCoverage']['complete'] for item in eligible))
    if any(item['placementStatus'] in ('partial', 'over_budget') for item in selected):
        warnings.append('章节部分完整段落未进入本次画面核对，位置建议仍需人工确认。')
    for item in items:
        for key in ('_placementIds', '_placementImage'):
            item.pop(key, None)
    items.sort(key=lambda item: ({'recommended': 0, 'needs_review': 1, 'unsuitable': 2}.get(item['status'], 1),
                                 not bool(item['placementSuggestions']),
                                 not bool(item['placementCoverage']['reviewedParagraphIndexes']),
                                 not item['placementCoverage']['complete'],
                                 item.get('sourceType') == 'library', ids.index(item['assetId'])))
    return {'candidates': items, 'coverage': coverage, 'warnings': list(dict.fromkeys(warnings))}
