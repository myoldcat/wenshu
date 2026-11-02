"""Conservative year support from the exact, immutable source of a citation.

Context is a date aid, never another selectable quote.  In particular, a file
containing several HTML articles is not one shared pool of dates.
"""

import hashlib
import json
import re
from collections import defaultdict


POLICY_VERSION = 'source-year-context-v1'
INSTRUCTIONS = (
    '年份校验以本规则优先：引文片段未含年份时，可以采用程序提供的同一原文固定版本的'
    '原始标题、父标题及直接承接上下文中有明确依据的年份；最近的具体上下文优先，'
    '存在冲突或不能明确归属时不补年份。上下文不是可替代引文的新证据，不能用于补写其他事实。'
    '不得把发布日期、抓取时间、导入时间当作事件年份，不得用去年等相对日期或当前时间推算年份，'
    'CVE、CWE、RFC等编号中的四位数字不是事件年份，裸日期不能单独给事件定年；'
    '保留编号须使用直接引文中明确记载的同一完整编号。'
    '不得猜测或跨 source、revision/version、article 借用年份。每条合并陈述的年份须在各自独立来源中均有明确依据，'
    '不能因一个引文含年份给另一来源补年；不同来源的年份不能混为共同支持。'
    '引用不同年份的独立事实时，分别陈述并提供各自明确的原文引用。'
)

_REFERENCE_FIELDS = ('sourceId', 'revisionId', 'blockId', 'start', 'end', 'quote')
_INVALID_REFERENCE = 'Wiki 引用上下文无法在固定原文版本中验证定位'
_RELATIVE = re.compile(r'去年|前年|明年|后年|上年|上一年|次年|翌年|今年|本年|'
                       r'\b(?:last|next|previous|this|following)\s+year\b', re.I)
_ADMINISTRATIVE = re.compile(
    r'发布(?:日期|时间|于)|發[佈布](?:日期|時間|於)|发表(?:日期|时间|于)|發表(?:日期|時間|於)|'
    r'抓取|爬取|采集时间|採集時間|导入(?:日期|时间)|導入(?:日期|時間)|'
    r'上传(?:日期|时间)|上傳(?:日期|時間)|更新(?:日期|时间|于)|更新於|'
    r'\b(?:published|publication\s+date|retrieved|accessed|fetched|imported|uploaded|updated|created)\b', re.I)
_CONTINUATION = re.compile(
    r'^\s*[“「\"\s]*(?:其中|这些|這些|上述|前述|该|該|其[中余餘]|同期|当中|當中|'
    r'这[些类項項项]|這[些類項]|此外|另有|而|同时|同時|'
    r'(?:of\s+)?these\b|those\b|among\s+(?:them|these)\b|the\s+(?:remaining|above)\b)', re.I)
_BACK_REFERENCE = re.compile(
    r'^\s*(?:(?:上述|以上|前述|这些|這些|该|該)(?:的)?(?:数据|數據|统计|統計|个案|個案|案件|事故|事件|记录|記錄)|'
    r'(?:these|those|the\s+above)\s+(?:figures|statistics|cases|incidents|data|events)\b)', re.I)
_COLLECTION = re.compile(r'汇编|彙編|汇集|合集|素材收集|资料收集|資料收集|索引|'
                         r'\b(?:compilation|collection|roundup|digest|index)\b', re.I)
_SENTENCES = re.compile(r'[^。！？!?；;\n]+?(?:[。！？!?；;\n]+|\.(?=\s|$)|$)')
_IDENTIFIER = re.compile(
    r'(?<![A-Za-z0-9])(?:CVE|CWE|CAPEC|RFC|ISO(?:\s*/\s*IEC)?|IEC)\s*'
    r'[-‐‑‒–—−:_]?\s*\d+(?:\s*[-‐‑‒–—−:_]\s*\d+)*', re.I)
_BARE_DATE = re.compile(
    r'\s*(?:19|20|21)\d{2}(?:[-/.]\d{1,2}[-/.]\d{1,2}|\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*[日号號])'
    r'(?:[\sT]+\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:\s*(?:Z|[+-]\d{2}:?\d{2}))?)?\s*[。.]?\s*', re.I)


def explicit_years(text):
    """Keep the legacy guard's treatment of money, counts and identifiers."""
    years = set()
    for match in re.finditer(r'(?<![0-9A-Za-z])(?:19|20|21)\d{2}(?![0-9A-Za-z])', text):
        before, after = text[max(0, match.start() - 20):match.start()], text[match.end():match.end() + 24]
        explicit_date = (re.match(r'\s*年', after) or re.search(r'(?:year|年份)\s*$', before, re.I)
                         or re.match(r'[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])(?:\D|$)', after))
        if not explicit_date:
            money_before = re.search(r'(?:[$¥￥€£]|人民币|美元|港币|欧元|日元|英镑|USD|CNY|RMB|HKD)\s*$', before, re.I)
            unit_after = re.match(r'\s*(?:(?:[亿万千百]?(?:元|美元|港元|港币|欧元|英镑|日元|人|个|项|条|件|次|份|字|页|公斤|千克|吨|米|公里|小时|分钟|秒))|[%‰]|(?:dollars?|USD|CNY|RMB|HKD|EUR|GBP|JPY)\b)', after, re.I)
            decimal_after = re.match(r'[.,]\d', after)
            if money_before or unit_after or decimal_after:
                continue
        years.add(match.group())
    return years


def reference_key(ref):
    """Bind only citation identity; display titles and model metadata are ignored."""
    if not isinstance(ref, dict):
        raise ValueError(_INVALID_REFERENCE)
    values = [ref.get(field) for field in _REFERENCE_FIELDS]
    if any(value is not None and not isinstance(value, (str, int)) for value in values):
        raise ValueError(_INVALID_REFERENCE)
    encoded = json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _event_years(text):
    """Identifier numbers can describe a vulnerability/standard, not its date."""
    return explicit_years(_IDENTIFIER.sub(lambda match: '_' * len(match.group()), text))


def _year_identifiers(text):
    """Only guard identifiers whose numbers the legacy year regex would see."""
    identifiers = {}
    for match in _IDENTIFIER.finditer(text):
        years = explicit_years(match.group())
        if years:
            normalized = re.sub(r'\s+', '', match.group()).upper()
            normalized = re.sub(r'[‐‑‒–—−]', '-', normalized)
            identifiers[normalized] = years
    return identifiers


def _locator(block):
    locator = block.get('locator', {})
    if not isinstance(locator, dict):
        return {}
    return locator


def _article(block):
    xpath = _locator(block).get('xpath', '')
    if not isinstance(xpath, str):
        return ''
    matches = list(re.finditer(r'(?:^|/)article(?:\[\d+\])?(?=/|$)', xpath, re.I))
    return xpath[:matches[-1].end()] if matches else ''


def _level(block):
    level = block.get('level', _locator(block).get('level'))
    if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 6:
        return level
    xpath = _locator(block).get('xpath', '')
    match = re.search(r'/h([1-6])(?:\[\d+\])?$', xpath, re.I) if isinstance(xpath, str) else None
    return int(match.group(1)) if match else 2


def _metadata(text, block=None):
    if block and block.get('kind') in ('metadata', 'date', 'timestamp'):
        return True
    if (_ADMINISTRATIVE.search(text) or _BARE_DATE.fullmatch(text)
            or re.fullmatch(r'\s*(?:https?://|www\.)\S+\s*', text, re.I)):
        return True
    # Common imported publisher/date ribbons have no event proposition.
    return bool(len(text) < 180 and re.search(r'(?:19|20|21)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}', text)
                and re.search(r'(?:官方报告|官方報告|新闻稿|新聞稿|原文|來源|来源|网站|網站)\s*$', text))


def _basis(kind, text, block=None):
    item = {'kind': kind, 'text': text}
    if block:
        item['blockId'] = block['id']
    return item


def _one_year_basis(kind, text, block=None):
    years = _event_years(text)
    if len(years) == 1 and not _metadata(text, block) and not _RELATIVE.search(text):
        return years, [_basis(kind, text, block)]
    return set(), []


def _documents(documents):
    if isinstance(documents, dict):
        return [documents] if 'blocks' in documents else list(documents.values())
    if isinstance(documents, (list, tuple)):
        return documents
    raise ValueError(_INVALID_REFERENCE)


def _index_document(document):
    if not isinstance(document, dict):
        raise ValueError(_INVALID_REFERENCE)
    source, revision = document.get('id', document.get('sourceId')), document.get('revisionId')
    if not all(isinstance(value, str) and value for value in (source, revision)):
        raise ValueError(_INVALID_REFERENCE)
    raw_blocks = document.get('blocks')
    if not isinstance(raw_blocks, list):
        raise ValueError(_INVALID_REFERENCE)
    if any(not isinstance(block, dict) or not isinstance(block.get('id'), str)
           or not block['id'] or not isinstance(block.get('text'), str) for block in raw_blocks):
        raise ValueError(_INVALID_REFERENCE)
    if len({block['id'] for block in raw_blocks}) != len(raw_blocks):
        raise ValueError(_INVALID_REFERENCE)
    # Store and parser already return ordinal order; sort only verified integer ordinals.
    blocks = list(raw_blocks)
    if all(isinstance(block.get('ordinal'), int) and not isinstance(block['ordinal'], bool) for block in blocks):
        blocks.sort(key=lambda block: block['ordinal'])
    rows, headings = [], []
    for block in blocks:
        article = _article(block)
        if block.get('kind') == 'heading':
            level = _level(block)
            headings = [heading for heading in headings if heading['level'] < level]
            headings.append({'level': level, 'article': article, 'block': block})
        active = [heading['block'] for heading in headings if heading['article'] == article]
        scope = article or ('heading:' + active[-1]['id'] if active else 'document')
        rows.append({'block': block, 'article': article, 'scope': scope, 'headings': active})
    # originalTitle is immutable source text; title may be an AI display translation.
    title = document.get('originalTitle', '')
    if not isinstance(title, str):
        title = ''
    articles = {row['article'] for row in rows if row['article']}
    return {'source': source, 'revision': revision, 'rows': rows,
            'positions': {row['block']['id']: index for index, row in enumerate(rows)},
            'title': title, 'root_title_applies': len(articles) <= 1 and not _COLLECTION.search(title)}


def _position(ref, block):
    quote, start, end = ref.get('quote'), ref.get('start'), ref.get('end')
    if not isinstance(quote, str) or not quote:
        raise ValueError(_INVALID_REFERENCE)
    text = block['text']
    if start is None and end is None:
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            raise ValueError(_INVALID_REFERENCE)
        return start, start + len(quote)
    if (not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int)
            or isinstance(end, bool) or not 0 <= start < end <= len(text)
            or text[start:end] != quote):
        raise ValueError(_INVALID_REFERENCE)
    return start, end


def _local_support(index, position, start, end):
    row = index['rows'][position]
    block, text = row['block'], row['block']['text']
    if block.get('kind') in ('metadata', 'date', 'timestamp'):
        return set(), []
    sentences = list(_SENTENCES.finditer(text))
    selected = [number for number, sentence in enumerate(sentences)
                if sentence.start() < end and sentence.end() > start]
    if not selected:
        return set(), []
    first, last = selected[0], selected[-1]
    sentence = text[sentences[first].start():sentences[last].end()]
    # A reporting year cannot date "last year's" event, even in the same sentence.
    if _metadata(sentence, block) or _RELATIVE.search(sentence):
        return set(), []
    if _event_years(sentence):
        return _one_year_basis('sentence', sentence, block)

    nearby, forward = [], None
    if first:
        nearby.append(('paragraph', sentences[first - 1].group(), block))
    elif position:
        previous = index['rows'][position - 1]
        if (previous['article'] == row['article']
                and previous['headings'] == row['headings']
                and previous['block'].get('kind') != 'heading'):
            preceding = list(_SENTENCES.finditer(previous['block']['text']))
            if preceding:
                nearby.append(('continuation', preceding[-1].group(), previous['block']))
    if last + 1 < len(sentences):
        forward = (sentences[last + 1].group(), block)
    elif position + 1 < len(index['rows']):
        following = index['rows'][position + 1]
        if (following['article'] == row['article']
                and following['headings'] == row['headings']
                and following['block'].get('kind') != 'heading'):
            following_sentences = list(_SENTENCES.finditer(following['block']['text']))
            if following_sentences:
                forward = (following_sentences[0].group(), following['block'])

    closest_years = set()
    carried_years, carried_basis = set(), []
    for kind, previous_text, previous_block in nearby:
        if _RELATIVE.search(previous_text) and _CONTINUATION.search(sentence):
            return set(), []
        if _metadata(previous_text, previous_block) or _RELATIVE.search(previous_text):
            continue
        closest_years = _event_years(previous_text)
        if closest_years:
            # Dates do not transfer between independent neighboring propositions.
            if _CONTINUATION.search(sentence):
                carried_years, carried_basis = _one_year_basis(kind, previous_text, previous_block)
            break
    if forward and _BACK_REFERENCE.search(forward[0]):
        if _RELATIVE.search(forward[0]):
            return set(), []
        forward_years, forward_basis = _one_year_basis('continuation', forward[0], forward[1])
        if _event_years(forward[0]) and not _metadata(forward[0], forward[1]):
            if closest_years and forward_years != closest_years:
                return set(), []
            return forward_years, forward_basis
    if carried_years:
        return carried_years, carried_basis
    if closest_years and _CONTINUATION.search(sentence):
        return set(), []

    for heading in reversed(row['headings']):
        heading_text = heading['text']
        if _event_years(heading_text):
            years, basis = _one_year_basis('heading', heading_text, heading)
            if closest_years and years != closest_years:
                return set(), []
            return years, basis
    if index['root_title_applies'] and _event_years(index['title']):
        years, basis = _one_year_basis('title', index['title'])
        if closest_years and years != closest_years:
            return set(), []
        return years, basis
    return set(), []


def build_contexts(documents, references):
    """Rebuild trusted context from original-title DTOs and exact quote locations.

    Documents are a list, a single document, or a mapping of documents. Missing
    offsets are accepted only for a unique exact occurrence in the fixed block.
    Invalid identities/locations raise rather than authorizing a context year.
    """
    if not isinstance(references, (list, tuple)):
        raise ValueError(_INVALID_REFERENCE)
    indexes = {}
    for document in _documents(documents):
        index = _index_document(document)
        identity = (index['source'], index['revision'])
        if identity in indexes:
            raise ValueError(_INVALID_REFERENCE)
        indexes[identity] = index
    contexts = {}
    for ref in references:
        key = reference_key(ref)
        identity = (ref.get('sourceId'), ref.get('revisionId'))
        if not all(isinstance(value, str) and value for value in identity):
            raise ValueError(_INVALID_REFERENCE)
        index = indexes.get(identity)
        block_id = ref.get('blockId')
        if not index or not isinstance(block_id, str) or block_id not in index['positions']:
            raise ValueError(_INVALID_REFERENCE)
        position = index['positions'][block_id]
        row = index['rows'][position]
        start, end = _position(ref, row['block'])
        years, basis = _local_support(index, position, start, end)
        extra = years - _event_years(ref['quote'])
        context = {'origin': [*identity, row['scope']], 'years': sorted(extra),
                   'basis': basis if extra else []}
        if isinstance(ref.get('evidenceId'), str) and ref['evidenceId']:
            context['evidenceId'] = ref['evidenceId']
        contexts[key] = context
    return contexts


def load_contexts(store, references, db=None):
    """Use revisions.title and blocks of pinned revisions, never current versions."""
    if db is None:
        with store.connect() as connection:
            return load_contexts(store, references, db=connection)
    if not isinstance(references, (list, tuple)):
        raise ValueError(_INVALID_REFERENCE)
    documents = []
    identities = set()
    for ref in references:
        reference_key(ref)
        identity = (ref.get('sourceId'), ref.get('revisionId'))
        if not all(isinstance(value, str) and value for value in identity):
            raise ValueError(_INVALID_REFERENCE)
        if identity in identities:
            continue
        identities.add(identity)
        source, revision = identity
        record = db.execute('SELECT title FROM revisions WHERE id=? AND source_id=?',
                            (revision, source)).fetchone()
        if record is None:
            raise ValueError(_INVALID_REFERENCE)
        blocks = []
        for item in db.execute('SELECT id, ordinal, kind, text, locator_json FROM blocks '
                               'WHERE revision_id=? ORDER BY ordinal', (revision,)).fetchall():
            try:
                locator = json.loads(item['locator_json'])
            except (TypeError, ValueError):
                locator = {}
            blocks.append({'id': item['id'], 'ordinal': item['ordinal'], 'kind': item['kind'],
                           'text': item['text'], 'locator': locator})
        documents.append({'id': source, 'revisionId': revision, 'originalTitle': record['title'],
                          'blocks': blocks})
    return build_contexts(documents, references)


def _valid_context(ref, contexts):
    if not isinstance(contexts, dict):
        return None
    context = contexts.get(reference_key(ref))
    if not isinstance(context, dict):
        return None
    origin, years, basis = context.get('origin'), context.get('years'), context.get('basis')
    if (not isinstance(origin, (list, tuple)) or len(origin) != 3
            or list(origin[:2]) != [ref.get('sourceId'), ref.get('revisionId')]
            or not all(isinstance(value, str) and value for value in origin)
            or not isinstance(years, list) or not all(isinstance(year, str)
                and re.fullmatch(r'(?:19|20|21)\d{2}', year) for year in years)
            or not isinstance(basis, list)):
        return None
    supported = set()
    for item in basis:
        if (not isinstance(item, dict) or item.get('kind') not in ('title', 'heading', 'sentence', 'paragraph', 'continuation')
                or not isinstance(item.get('text'), str)):
            return None
        if not _metadata(item['text']) and not _RELATIVE.search(item['text']):
            supported.update(_event_years(item['text']))
    if not set(years) <= supported:
        return None
    return context


def check_years(text, evidence, contexts=None):
    """Legacy quote-only guard, optionally extended by verified local contexts."""
    unsupported_identifiers = set()
    if contexts is None:
        generated = explicit_years(text)
        supported = {year for reference in evidence for year in explicit_years(reference['quote'])}
    else:
        generated = _event_years(text)
        quoted_identifiers = {identifier for reference in evidence
                              for identifier in _year_identifiers(reference['quote'])}
        for identifier, years in _year_identifiers(text).items():
            if identifier not in quoted_identifiers:
                unsupported_identifiers.update(years)
        groups = defaultdict(set)
        for reference in evidence:
            context = _valid_context(reference, contexts)
            # Missing context must not disappear from the common-support check.
            origin = tuple(context['origin']) if context else ('unverified', reference_key(reference))
            groups[origin].update(_event_years(reference['quote']))
            if context:
                groups[origin].update(context['years'])
        supported = set.intersection(*(set(years) for years in groups.values())) if groups else set()
    unsupported = (generated - supported) | unsupported_identifiers
    if unsupported:
        unsupported = '、'.join(sorted(unsupported))
        raise ValueError(f'Wiki 文案包含所引原文未记载的年份（{unsupported}），请删除补全年份，只保留原文日期，不得改猜其他年份')


def prompt_contexts(contexts, max_chars=1800):
    """Return a bounded date-aid list; original references remain unmodified."""
    if not isinstance(contexts, dict) or not isinstance(max_chars, int) or max_chars < 2:
        return []
    result = []
    for key, context in contexts.items():
        if not isinstance(context, dict) or not context.get('years'):
            continue
        basis = context.get('basis', [])
        if not basis:
            continue
        shortest = min(basis, key=lambda item: len(item.get('text', '')))
        original = shortest.get('text', '')
        matches = [match for match in re.finditer(r'(?:19|20|21)\d{2}', original)
                   if match.group() in context['years']]
        if not matches:
            continue
        match = matches[0]
        snippet = original[max(0, match.start() - 50):max(match.end() + 80, 130)]
        item = {'evidenceId': context.get('evidenceId', key), 'years': context['years'],
                'basis': [{'kind': shortest['kind'], 'text': snippet}]}
        candidate = result + [item]
        if len(json.dumps(candidate, ensure_ascii=False, separators=(',', ':'))) <= max_chars:
            result.append(item)
    return result
