"""Sentence-by-sentence review against a frozen original-evidence registry.

This module does not call a model, edit prose or infer support from word overlap.
It builds the review request and validates the model's complete assignment table.
Sentence identities and all offsets are program owned. A support decision is an
auditable model judgment, never a claim that the underlying facts were verified.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re


LEGACY_MATCHING_PROTOCOL = 'sentence-evidence-review-v1'
MATCHING_PROTOCOL = 'sentence-evidence-review-v2'
MATCHING_POLICY_VERSION = 'writing-source-support-v1'
DEFERRED_CITATION_REVIEW_POLICY = 'deferred-to-check-v1'
SELECTIVE_REVIEW_POLICY = 'claims-and-explicit-citations-v1'
SELECTIVE_REVIEW_INSTRUCTION = (
    '本次选择性范围替代较早的“仅组织性过渡句可免核对”限制。'
    '只核对需要资料依据的事实性描述、观点性阐述，以及正文明确带有引文的句子。'
    '忠实归纳前文且没有新增事实或观点的总结、纯粹的内容过渡、重复前文已经核对且没有新增主张的表述、'
    '不涉及具体人物事件数据或争议判断的基础常识介绍，可以判为not_required，evidenceIds为空且supportBasis为none；'
    '不要仅因这些句子没有引用就判为缺少支持。'
    '明确引用的句子必须核对，不能以not_required跳过；包含新增事实、数据、因果、预测或可争议观点的总结仍须核对。'
    '无需引用的判断不代表为任何材料外事实背书，严格模式的事实及观点仍不得超出所选原文明示范围。'
    '本轮只能使用已登记原文摘录，不能从相邻上下文或外部知识新增证据、摘录或引用编号。'
)
TEMPORAL_REVIEW_VERSION = 'source-time-attribution-v1'
TEMPORAL_REVIEW_INSTRUCTION = (
    '逐句独立核对正文的事实时点与历史归属。即使正文逐字照抄引文，也不能仅因文字相同就判为支持；'
    '来源在通报、讲话或文章发表当时所作的调查状态、计划、承诺及相对时间表述，必须明确归属于该来源当时的时点，'
    '不能让读者误读为本篇写作观察时点仍成立的状态或后来已发生的事实。'
    '正文须保留判断者、条件、否定与不确定性；缺少必要历史限定时应判partial或uncertain，不能判supported。'
    'sourceContext中的publishedAt是固定版本已保存的发布日期，绝不是事件日期；时间元数据只解释原文时点与归属，不能独立支持事件事实。'
    '没有可靠日历日期时，可使用证据中已核实的机构、讲话者或文章名配合“曾”“当时”等明确历史归属，或缩小表述；'
    '不能从链接、入库时间、常识或写作日期补出年月或推算事件日。'
    '只有快照明确提供asOf时才将其作为写作观察时点，未提供时不得自行以执行时钟补出“今天”。'
    '复核理由须说明涉及状态、计划或相对时间的句子是否保留来源当时的时点和归属，原文支持哪一历史范围。'
)
SUPPORT_BASES = frozenset(('direct', 'paraphrase', 'inference', 'none'))
SUPPORT_STATUSES = frozenset(('supported', 'partial', 'unsupported', 'uncertain', 'not_required'))
_CITATION = re.compile(r'\[E\d+\]')
_CLOSERS = frozenset('”’\"\')）】》〉」』〕]')
_QUOTE_PAIRS = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"', "'": "'"}
_ALWAYS_ABBREVIATIONS = frozenset(('mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr',
                                 'st', 'vs', 'fig', 'eq', 'no', 'vol', 'pp',
                                 'e.g', 'i.e', 'cf', 'approx', 'dept'))
_COMMON_STARTERS = frozenset(('a', 'an', 'the', 'this', 'that', 'these', 'those',
                             'it', 'its', 'they', 'their', 'he', 'she', 'we', 'our',
                             'however', 'but', 'yet', 'therefore', 'in', 'on',
                             'at', 'after', 'before', 'when', 'while', 'according'))
_URL = re.compile(r'(?:https?://|www\.)[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+')
_QUOTE_CONTINUATION = re.compile(
    r'(?:并|而(?:且|是|非|不|未|要|继续)|但(?:仍|未|不|没有|并|需)|且(?:仍|未|不|需)|'
    r'或(?:者)?(?:仍|需|应|将)|的|地|得|着|了|过|以及|后(?:才|又|继续)|'
    r'(?:and|but|or|nor|yet)\b)', re.IGNORECASE)


def _strip_markers(text):
    return _CITATION.sub('', text)


def _utf16_offset(text, offset):
    return len(text[:offset].encode('utf-16-le')) // 2


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _registry_hash(evidence):
    return _hash(json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _validate_temporal_review(review):
    if (not isinstance(review, dict) or set(review) not in
            ({'version', 'instruction', 'sourceContext'}, {'version', 'instruction', 'sourceContext', 'asOf'})
            or review.get('version') != TEMPORAL_REVIEW_VERSION
            or not isinstance(review.get('instruction'), str) or not review['instruction'].strip()
            or len(review['instruction']) > 10000
            or not isinstance(review.get('sourceContext'), list)):
        raise ValueError('正文时点复核的冻结规则不正确')
    for source in review['sourceContext']:
        if (not isinstance(source, dict) or set(source) != {'sourceId', 'revisionId', 'publishedAt'}
                or any(not isinstance(source.get(key), str) or not source[key] for key in ('sourceId', 'revisionId'))
                or source['publishedAt'] is not None and not isinstance(source['publishedAt'], str)):
            raise ValueError('正文时点复核的固定版本日期不正确')
    if 'asOf' in review and (not isinstance(review['asOf'], str) or not review['asOf'].strip() or len(review['asOf']) > 100):
        raise ValueError('正文时点复核的观察时点不正确')


def temporal_review_snapshot(mode, source_context, *, as_of=None):
    """Only a new strict task acquires these independent review instructions."""
    if mode != 'strict':
        return None
    review = {'version': TEMPORAL_REVIEW_VERSION, 'instruction': TEMPORAL_REVIEW_INSTRUCTION,
              'sourceContext': copy.deepcopy(source_context)}
    if as_of is not None:
        review['asOf'] = as_of
    _validate_temporal_review(review)
    return review


def freeze_generation_temporal_review(prepared):
    """Pair the generation packet with its own saved matcher snapshot."""
    packet = json.loads(prepared['messages'][1]['content'])
    if 'citationTemporalReview' in packet:
        review = packet['citationTemporalReview']
        _validate_temporal_review(review)
        prepared['citationTemporalReview'] = copy.deepcopy(review)
    return prepared


def with_frozen_temporal_review(packet, frozen, *, generation_packet=None):
    """Never attach live wording or dates to an old persisted task."""
    present = 'citationTemporalReview' in frozen
    review = frozen.get('citationTemporalReview')
    if present:
        _validate_temporal_review(review)
        if 'brief' in frozen and review.get('asOf') != frozen['brief'].get('asOf'):
            raise ValueError('正文时点复核与冻结观察时点不一致')
    if generation_packet is not None:
        if (present != ('citationTemporalReview' in generation_packet)
                or present and review != generation_packet['citationTemporalReview']):
            raise ValueError('正文与引用复核的冻结时点规则不一致')
        if present and (review.get('sourceContext') != generation_packet.get('sourceContext')
                        or review.get('asOf') != generation_packet.get('brief', {}).get('asOf')):
            raise ValueError('正文时点复核与冻结来源或观察时点不一致')
    if not present:
        if 'temporalReview' in packet or 'temporalReviewHash' in packet:
            raise ValueError('旧引用任务不能混入新的时点复核规则')
        return packet
    if packet.get('mode') != 'strict':
        raise ValueError('正文时点复核与冻结章节模式不一致')
    if ('temporalReview' in packet or 'temporalReviewHash' in packet) and (
            packet.get('temporalReview') != review or packet.get('temporalReviewHash') != _registry_hash(review)):
        raise ValueError('引用复核的冻结时点规则已变化')
    result = copy.deepcopy(packet)
    result['temporalReview'] = copy.deepcopy(review)
    result['temporalReviewHash'] = _registry_hash(review)
    return result


def matching_policy(mode):
    """Return the policy to freeze when a *new* review is requested.

    Old v1 packets deliberately omit mode and keep their original contract.
    Semantics are reviewed by the model; code enforces its structured basis,
    never guesses whether a reason is an inference from words it contains.
    """
    if mode not in ('strict', 'extended'):
        raise ValueError('引用复核模式须为 strict 或 extended')
    strict = mode == 'strict'
    return {'version': MATCHING_POLICY_VERSION, 'mode': mode,
            'supportedBases': ['direct', 'paraphrase'] if strict else ['direct', 'paraphrase', 'inference'],
            'instruction': ('严格模式允许对原文明示内容忠实归纳、总结、合并同义重复和改写，无需正文逐字照抄；'
                            '须保留原文对象、范围、时间、归属、否定、条件与不确定性。'
                            '禁止通过推理、外部知识或扩充补足原文没有的事实、原因、机制、比较结论或建议；'
                            '含此类新增内容的句子不能判为supported。' if strict else
                            '扩展模式保持既有能力：原文明示内容可直接引用或忠实归纳改写；'
                            '有摘录依据且保留适用条件、否定与不确定性的推论可按inference评估，'
                            '不能把主题相关、外部常识或缺失材料包装成原文事实。'),
            'reasonRequirement': ('逐句说明原文哪项明示内容直接或经忠实归纳支持本句，'
                                  '若存在原文未明示的推理或扩充，须指出缺口并保留待核实状态。' if strict else
                                  '逐句说明直接内容、忠实改写或有条件推论的依据；推论必须说明依据及保留的条件，不能只说主题相关。')}


def validate_matching_policy(mode, policy):
    """Validate frozen policy shape without replacing its saved wording."""
    if mode not in ('strict', 'extended'):
        raise ValueError('引用复核模式须为 strict 或 extended')
    allowed = ['direct', 'paraphrase'] if mode == 'strict' else ['direct', 'paraphrase', 'inference']
    if (not isinstance(policy, dict) or policy.get('version') != MATCHING_POLICY_VERSION
            or policy.get('mode') != mode or policy.get('supportedBases') != allowed
            or any(not isinstance(policy.get(key), str) or not policy[key].strip()
                   for key in ('instruction', 'reasonRequirement'))):
        raise ValueError('引用复核模式或冻结规则已变化，不能应用旧判断')


def validate_support_basis(mode, status, ids, basis, policy):
    """Enforce the reviewed basis, not a keyword heuristic over prose reasons."""
    if not isinstance(basis, str) or basis not in SUPPORT_BASES:
        raise ValueError('引用支持依据类型不正确')
    if status == 'unreviewed':
        # Program-owned reference locations do not assert semantic support.
        # Model result validation never accepts this operational status.
        if basis != 'none':
            raise ValueError('未完成引用核对时不能认定支持依据')
        return
    if (basis == 'none') != (not ids):
        raise ValueError('引用支持依据类型与已登记证据不一致')
    if status in ('unsupported', 'not_required') and basis != 'none':
        raise ValueError('无支持或无需引用的句子必须使用 none 依据')
    if status == 'supported' and basis not in policy['supportedBases']:
        raise ValueError('严格模式不允许把推理或扩充判为原文支持，请保留待核实状态')


def _packet_mode(packet):
    protocol = packet.get('protocol')
    if protocol == LEGACY_MATCHING_PROTOCOL:
        if any(key in packet for key in ('mode', 'policy', 'policyHash', 'temporalReview', 'temporalReviewHash')):
            raise ValueError('旧版引用复核不能混入新版模式规则')
        return None
    if protocol != MATCHING_PROTOCOL or packet.get('mode') not in ('strict', 'extended'):
        raise ValueError('引用复核协议或模式不正确')
    mode, policy = packet['mode'], packet.get('policy')
    validate_matching_policy(mode, policy)
    if packet.get('policyHash') != _registry_hash(policy):
        raise ValueError('引用复核模式或冻结规则已变化，不能应用旧判断')
    if 'temporalReview' in packet or 'temporalReviewHash' in packet:
        _validate_temporal_review(packet.get('temporalReview'))
        if mode != 'strict' or packet.get('temporalReviewHash') != _registry_hash(packet['temporalReview']):
            raise ValueError('引用复核的冻结时点规则已变化')
    return mode


def _dot_ends_sentence(text, position):
    """Distinguish a full stop from decimal, URL, initial and abbreviation dots.

    English initials and abbreviations are inherently ambiguous without a full
    language parser. Prefer retaining a contiguous claim over splitting a name.
    Chinese full stops and explicit line boundaries remain unambiguous.
    """
    previous = text[position - 1] if position else ''
    following = text[position + 1] if position + 1 < len(text) else ''
    if following and previous.isalnum() and following.isalnum():
        return False  # 1.25, v1.2.3, example.org, U.S., e.g.
    if following == '.':
        return False
    token_match = re.search(r'([A-Za-z]+(?:\.[A-Za-z]+)*)$', text[:position])
    token = token_match.group(1) if token_match else ''
    rest = text[position + 1:]
    # Closing quotation/bracket characters belong to the sentence they close.
    rest = rest.lstrip(''.join(_CLOSERS) + ' \t')
    next_word_match = re.match(r'([A-Za-z]+)', rest)
    next_word = next_word_match.group(1) if next_word_match else ''
    if token.lower() in _ALWAYS_ABBREVIATIONS and rest and not rest.startswith(('\r', '\n')):
        return False
    initial_or_acronym = bool(re.fullmatch(r'(?:[A-Z]\.)*[A-Z]', token))
    if initial_or_acronym and next_word and next_word.lower() not in _COMMON_STARTERS:
        return False
    # An ellipsis is a boundary only at its final dot; the preceding dots are
    # included verbatim in the returned span.
    return True


def sentence_ranges(text):
    """Return exact sentence spans with Python codepoint offsets.

    The caller supplies marker-free text. Blank lines and single line breaks
    end a sentence; whitespace is excluded from the span, never rewritten.
    Closing quotes stay with their punctuation. Semicolons remain in the same
    sentence so a single qualified assertion is not split into unrelated facts.
    """
    if not isinstance(text, str):
        raise ValueError('待定位正文必须是文本')
    spans, start, index, quotes = [], 0, 0, []
    protected = set()
    for link in _URL.finditer(text):
        # A period/question mark after the link can still end the sentence.
        # Query punctuation *inside* the URL must never split a body sentence.
        end = link.start() + len(link.group().rstrip('.,;:!?)]'))
        protected.update(range(link.start(), end))

    def append(end):
        nonlocal start
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right and any(char.isalnum() for char in text[left:right]):
            spans.append({'start': left, 'end': right, 'text': text[left:right]})
        start = end

    while index < len(text):
        character = text[index]
        if character in '\r\n':
            # Paragraph boundaries cannot create a cross-paragraph sentence,
            # even when the source contains an unmatched quotation mark.
            append(index)
            quotes.clear()
            index += 1
            start = index
            continue
        if quotes and character == quotes[-1]['close']:
            quote = quotes.pop()
            # A colon/dash introducing a complete quotation can make its final
            # punctuation the enclosing sentence's punctuation. A following
            # comma, outer punctuation or connective still belongs to the same
            # enclosing statement, even when spaces follow the closing quote.
            terminal = index - 1
            while terminal >= start and text[terminal] in _CLOSERS:
                terminal -= 1
            if not quotes and quote['introduced'] and terminal >= start and text[terminal] in '。！？.!?':
                end = index + 1
                while end < len(text) and text[end] in _CLOSERS:
                    end += 1
                after = end
                while after < len(text) and text[after] in ' \t':
                    after += 1
                if (after == len(text) or
                        (text[after] not in '，,；;：:。！？.!?'
                         and not _QUOTE_CONTINUATION.match(text[after:]))):
                    append(end)
                    index = end
                    continue
            index += 1
            continue
        if character in _QUOTE_PAIRS:
            # Apostrophes in English contractions and possessives are not
            # quotation delimiters (don't, source's, U.S.'s).
            apostrophe = character in "'‘’" and index > 0 and index + 1 < len(text) and text[index - 1].isalnum() and text[index + 1].isalnum()
            if not apostrophe:
                preceding = text[start:index].rstrip()
                quotes.append({'close': _QUOTE_PAIRS[character],
                               'introduced': not preceding or preceding.endswith(('：', ':', '——', '—', '--'))})
            index += 1
            continue
        if quotes:
            index += 1
            continue
        if index in protected:
            index += 1
            continue
        boundary = character in '。！？!?'
        if character == '.':
            boundary = _dot_ends_sentence(text, index)
        if character == '…':
            # Ellipsis in the middle of a phrase is not automatically a break.
            after = index + 1
            while after < len(text) and text[after] == '…':
                after += 1
            boundary = after == len(text) or text[after].isspace() or text[after] in _CLOSERS
            if boundary:
                index = after - 1
        if boundary:
            end = index + 1
            while end < len(text) and (text[end] in _CLOSERS or text[end] in '。！？!?'):
                end += 1
            append(end)
            index = end
            continue
        index += 1
    append(len(text))
    return spans


def _bounded_text(value, field, maximum, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ValueError(f'{field}须为{"可空" if allow_empty else "非空"}文本，最多{maximum}字符')
    return value


def build_matching_packet(content, evidence, *, blocks=None, section_id=None, mode=None, policy=None):
    """Freeze every sentence and only the already-registered original excerpts.

    ``blocks`` is an optional list/map of original frozen blocks. When supplied,
    every quote is checked again and adjacent original context is included only
    to resolve attribution, negation and pronouns. Context cannot independently
    substantiate a claim beyond the registered quote.
    """
    _bounded_text(content, '待复核正文', 60000, allow_empty=True)
    original = _strip_markers(content)
    sentences = [dict(sentenceId=f'S{index + 1}', **span)
                 for index, span in enumerate(sentence_ranges(original))]
    if len(sentences) > 600:
        raise ValueError('单次引用复核超过600句，请按章节分批')
    if not isinstance(evidence, list) or len(evidence) > 400:
        raise ValueError('引用复核须提供至多400项已登记原文证据')
    known_blocks = None
    if blocks is not None:
        block_values = list(blocks.values()) if isinstance(blocks, dict) else blocks
        if not isinstance(block_values, list):
            raise ValueError('冻结原文块格式不正确')
        known_blocks = {}
        for block in block_values:
            if not isinstance(block, dict) or not isinstance(block.get('blockId'), str) or not isinstance(block.get('text'), str):
                raise ValueError('冻结原文块须有 blockId 和原文 text')
            key = block['blockId']
            if key in known_blocks and known_blocks[key] != block:
                raise ValueError('冻结原文块标识重复，不能选择性覆盖')
            known_blocks[key] = block
    registry, seen = [], set()
    for reference in evidence:
        if not isinstance(reference, dict):
            raise ValueError('已登记原文证据格式不正确')
        eid = reference.get('id', reference.get('evidenceId'))
        _bounded_text(eid, '已登记证据编号', 80)
        if eid in seen:
            raise ValueError('已登记证据编号重复，不能选择性覆盖')
        seen.add(eid)
        quote = _bounded_text(reference.get('quote'), '已登记原文摘录', 4000)
        entry = {'evidenceId': eid, 'quote': quote}
        for field in ('sourceId', 'revisionId', 'blockId', 'title', 'url', 'locator'):
            value = reference.get(field)
            if value is not None:
                # The registry whitelist excludes summaries, translated text,
                # generated claims and any earlier semantic-support judgments.
                entry[field] = value
        if known_blocks is not None:
            block = known_blocks.get(reference.get('blockId'))
            if block is None:
                raise ValueError('已登记摘录不在本次冻结原文块中')
            for identity in ('sourceId', 'revisionId'):
                if identity in reference and reference[identity] != block.get(identity):
                    raise ValueError('已登记摘录来源版本与冻结原文不一致')
            start = reference.get('start')
            end = reference.get('end')
            if isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int):
                raise ValueError('已登记摘录缺少有效原文偏移')
            if not 0 <= start < end <= len(block['text']) or block['text'][start:end] != quote:
                raise ValueError('已登记摘录与冻结原文逐字定位不一致')
            entry['contextBefore'] = block['text'][max(0, start - 600):start]
            entry['contextAfter'] = block['text'][end:end + 600]
        registry.append(entry)
    packet = {'task': 'writing_citation_match', 'protocol': MATCHING_PROTOCOL if mode is not None else LEGACY_MATCHING_PROTOCOL, 'content': original,
              'contentHash': _hash(original), 'sentences': sentences,
              'evidence': registry, 'registryHash': _registry_hash(registry),
              'outputSchema': {'assignments': [{'sentenceId': '逐字复制输入 sentenceId',
                  'evidenceIds': ['支持该句的已登记 evidenceId；无支持则空数组'],
                  'support': 'supported|partial|unsupported|uncertain|not_required',
                  'reason': '简要说明原文如何支持或缺失哪些限定，不能只说主题相关'}]}}
    if section_id is not None:
        packet['sectionId'] = section_id
    if mode is not None:
        packet['mode'] = mode
        packet['policy'] = copy.deepcopy(matching_policy(mode) if policy is None else policy)
        packet['policyHash'] = _registry_hash(packet['policy'])
        _packet_mode(packet)
        schema = packet['outputSchema']['assignments'][0]
        schema['supportBasis'] = 'direct|paraphrase|inference|none；strict下supported仅允许direct或paraphrase'
        schema['reason'] = packet['policy']['reasonRequirement']
    elif policy is not None:
        raise ValueError('新版引用复核规则必须明确指定模式')
    return packet


def matching_messages(packet, *, db=None, instruction=None):
    """Assemble an inspectable request; the caller owns gateway and call limits."""
    from .prompts import prompt_text
    system = instruction if instruction is not None else prompt_text('writing_citation_match', db=db)
    _packet_mode(packet)
    if 'reviewScope' in packet:
        scope = packet['reviewScope']
        if (not isinstance(scope, dict) or scope.get('version') != SELECTIVE_REVIEW_POLICY
                or not isinstance(scope.get('instruction'), str) or not scope['instruction'].strip()
                or not isinstance(scope.get('explicitCitationSentenceIds'), list)):
            raise ValueError('引用核对范围的冻结规则不正确')
        system += '\n\n本次冻结的引用核对范围：\n' + scope['instruction']
    if 'temporalReview' in packet:
        system += '\n\n本次冻结的正文时点与历史归属复核要求：\n' + packet['temporalReview']['instruction']
    _bounded_text(system, '引用复核指令', 32000)
    # Sentence entries already contain the exact full body once. Sending the
    # duplicate content string wastes context and defeats bounded batching.
    payload = {key: value for key, value in packet.items() if key != 'content'}
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]


def with_selective_review_scope(packet, content, *, scope=None):
    """Freeze the requested review scope without guessing prose semantics."""
    from .writing_sentence_citations import MARKER
    plain, removed, cited_positions = _strip_markers(content), 0, set()
    for marker in MARKER.finditer(content):
        cited_positions.add(marker.start() - removed)
        removed += len(marker.group(0))
    if plain != packet['content']:
        raise ValueError('引用核对范围与冻结正文不一致')
    cited = [sentence['sentenceId'] for sentence in packet['sentences']
             if any(sentence['start'] < position <= sentence['end'] for position in cited_positions)]
    result = copy.deepcopy(packet)
    frozen = scope if scope is not None else {'version': SELECTIVE_REVIEW_POLICY,
                                              'instruction': SELECTIVE_REVIEW_INSTRUCTION}
    if (not isinstance(frozen, dict) or frozen.get('version') != SELECTIVE_REVIEW_POLICY
            or not isinstance(frozen.get('instruction'), str) or not frozen['instruction'].strip()):
        raise ValueError('引用核对范围的冻结规则不正确')
    result['reviewScope'] = {'version': frozen['version'],
                             'instruction': frozen['instruction'],
                             'explicitCitationSentenceIds': cited}
    return result


def matching_sentence_limit(config):
    """Reserve reasoning/output space without reducing configured output tokens."""
    maximum = int(config.get('maxOutputTokens', 2048))
    reserve = {'': .3, 'none': .1, 'minimal': .2, 'low': .3, 'medium': .5,
               'high': .65, 'xhigh': .75, 'max': .8}.get(config.get('reasoningEffort', ''), .5)
    usable = int(maximum * (1 - reserve)) - 256
    return max(1, min(8, usable // 500))


def subset_matching_packet(packet, sentence_ids):
    """Select a program-owned batch without changing global IDs or offsets.

    Evidence is never narrowed by lexical similarity. Results from every batch
    must be collected before the caller publishes any chapter changes.
    """
    if not isinstance(sentence_ids, list) or not sentence_ids or any(not isinstance(sid, str) for sid in sentence_ids):
        raise ValueError('引用复核分批须提供非空句子编号列表')
    selected_ids = set(sentence_ids)
    if len(selected_ids) != len(sentence_ids):
        raise ValueError('引用复核分批句子编号不能重复')
    current = {item['sentenceId']: item for item in packet['sentences']}
    if not selected_ids.issubset(current):
        raise ValueError('引用复核分批包含不存在的句子编号')
    subset = copy.deepcopy(packet)
    subset['sentences'] = [item for item in subset['sentences'] if item['sentenceId'] in selected_ids]
    subset['sentenceSelection'] = [item['sentenceId'] for item in subset['sentences']]
    return subset


def partition_matching_packets(packet, max_chars, *, instruction=None):
    """Bound request text while retaining the complete evidence-quote registry.

    Adjacent source context is optional and can be removed to fit the request;
    registered quotes cannot be truncated or filtered. If even one sentence and
    all quotes cannot fit, fail explicitly so the caller can choose a larger
    model/context rather than silently discard evidence candidates.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1000:
        raise ValueError('引用复核输入预算须为至少1000字符的整数')
    prepared = copy.deepcopy(packet)

    def size(candidate):
        return sum(len(item['content']) for item in matching_messages(candidate, instruction=instruction))

    if size(prepared) <= max_chars:
        return [prepared]
    for entry in prepared['evidence']:
        entry.pop('contextBefore', None)
        entry.pop('contextAfter', None)
    prepared['registryHash'] = _registry_hash(prepared['evidence'])
    if size(prepared) <= max_chars:
        return [prepared]
    batches, ids = [], []
    for sentence in prepared['sentences']:
        next_ids = [*ids, sentence['sentenceId']]
        candidate = subset_matching_packet(prepared, next_ids)
        if size(candidate) <= max_chars:
            ids = next_ids
            continue
        if not ids:
            raise ValueError('全部原文摘录及单句正文超过引用复核输入预算，不能省略证据')
        batches.append(subset_matching_packet(prepared, ids))
        ids = [sentence['sentenceId']]
        if size(subset_matching_packet(prepared, ids)) > max_chars:
            raise ValueError('全部原文摘录及单句正文超过引用复核输入预算，不能省略证据')
    if ids:
        batches.append(subset_matching_packet(prepared, ids))
    return batches


def validate_matching_result(packet, result):
    """Reject invented IDs, omissions, rewrites and model-supplied offsets.

    Returned matches use UTF-16 offsets in the unchanged marker-free body, as
    required by browser selection and the safe citation-rebinding API. A sentence
    judged partial, unsupported or uncertain remains visible as a diagnosis; it
    is never silently promoted to a supported sentence or given a source marker.
    """
    if not isinstance(packet, dict):
        raise ValueError('引用复核协议不正确')
    mode = _packet_mode(packet)
    original = packet.get('content')
    if not isinstance(original, str) or packet.get('contentHash') != _hash(original):
        raise ValueError('复核正文已变化，不能应用旧句子定位')
    canonical = [dict(sentenceId=f'S{index + 1}', **span)
                 for index, span in enumerate(sentence_ranges(original))]
    selection = packet.get('sentenceSelection')
    if selection is not None:
        if (not isinstance(selection, list) or not selection
                or any(not isinstance(sid, str) for sid in selection)
                or len(set(selection)) != len(selection)):
            raise ValueError('复核分批句子登记不正确')
        wanted = set(selection)
        canonical = [item for item in canonical if item['sentenceId'] in wanted]
        if [item['sentenceId'] for item in canonical] != selection:
            raise ValueError('复核分批包含不存在或乱序的句子编号')
    if packet.get('sentences') != canonical:
        raise ValueError('复核句子登记与原始正文不一致')
    registry = packet.get('evidence')
    if not isinstance(registry, list) or packet.get('registryHash') != _registry_hash(registry):
        raise ValueError('复核原文登记已变化，不能应用旧判断')
    known_evidence = {item['evidenceId'] for item in registry}
    if len(known_evidence) != len(registry):
        raise ValueError('复核原文登记编号重复')
    if not isinstance(result, dict) or set(result) != {'assignments'}:
        raise ValueError('引用复核只能返回 assignments，不能改写正文或提交偏移')
    assignments = result['assignments']
    if not isinstance(assignments, list) or len(assignments) != len(canonical):
        raise ValueError('引用复核必须逐句完整判断，每个输入句子恰好一次')
    known_sentences = {item['sentenceId']: item for item in canonical}
    by_id = {}
    for assignment in assignments:
        fields = {'sentenceId', 'evidenceIds', 'support', 'reason'} | ({'supportBasis'} if mode is not None else set())
        if not isinstance(assignment, dict) or set(assignment) != fields:
            raise ValueError('引用复核项须符合本次冻结的逐句判断格式')
        sid = assignment['sentenceId']
        if not isinstance(sid, str) or sid not in known_sentences or sid in by_id:
            raise ValueError('引用复核包含不存在或重复的句子编号')
        status = assignment['support']
        if not isinstance(status, str) or status not in SUPPORT_STATUSES:
            raise ValueError('引用支持状态不正确')
        ids = assignment['evidenceIds']
        if not isinstance(ids, list) or len(ids) > 16 or any(not isinstance(eid, str) or eid not in known_evidence for eid in ids):
            raise ValueError('引用复核只能选择本批已登记的原文证据编号')
        if len(ids) != len(set(ids)):
            raise ValueError('同一句不能重复登记相同原文证据编号')
        if status == 'supported' and not ids:
            raise ValueError('完全支持的句子必须有已登记原文证据')
        if status == 'unsupported' and ids:
            raise ValueError('无原文支持的句子不能绑定来源证据')
        if status == 'not_required' and ids:
            raise ValueError('不含事实主张的组织性句子不得绑定来源证据')
        if (status == 'not_required' and sid in
                packet.get('reviewScope', {}).get('explicitCitationSentenceIds', [])):
            raise ValueError('正文明确标注引文的句子不能跳过引用核对')
        basis = assignment.get('supportBasis') if mode is not None else None
        if mode is not None:
            validate_support_basis(mode, status, ids, basis, packet['policy'])
        reason = _bounded_text(assignment['reason'], '逐句支持理由', 700)
        by_id[sid] = dict(sentenceId=sid, evidenceIds=ids[:], support=status, reason=reason)
        if mode is not None:
            by_id[sid]['supportBasis'] = basis
    validated, matches, issues = [], [], []
    for sentence in canonical:
        judgment = by_id[sentence['sentenceId']]
        start, end = (_utf16_offset(original, sentence[key]) for key in ('start', 'end'))
        row = {**judgment, 'start': start, 'end': end, 'text': sentence['text']}
        validated.append(row)
        for eid in judgment['evidenceIds'] or [None]:
            matches.append({'sentenceId': row['sentenceId'], 'evidenceId': eid,
                            'start': start, 'end': end, 'text': sentence['text'],
                            'status': row['support'], 'reason': row['reason'],
                            **({'supportBasis': row['supportBasis']} if mode is not None else {})})
        if row['support'] not in ('supported', 'not_required'):
            label = {'partial': '仅获部分支持', 'unsupported': '缺少原文支持', 'uncertain': '支持关系待核实'}[row['support']]
            issues.append({'sentenceId': row['sentenceId'], 'status': row['support'],
                           'message': label + '：' + row['reason'], 'text': row['text']})
    return {'protocol': packet['protocol'], 'content': original,
            'contentHash': packet['contentHash'], 'registryHash': packet['registryHash'],
            'assignments': validated, 'matches': matches, 'issues': issues,
            **({'temporalReview': copy.deepcopy(packet['temporalReview']), 'temporalReviewHash': packet['temporalReviewHash']}
               if 'temporalReview' in packet else {}),
            **({'mode': mode, 'policy': copy.deepcopy(packet['policy']), 'policyHash': packet['policyHash']} if mode is not None else {})}
