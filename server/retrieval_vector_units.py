"""Read-only, revision-bound vector inputs with separate original evidence.

There are four independent sources: original passages, title plus summary,
published Wiki claims, and published Wiki facts.  ``text`` is the embedding
input; ``matchText`` is its main subject.  Only ``evidence`` contains verbatim
original quotations.  Derived titles, summaries and Wiki prose are never
promoted to quotations.

Every nonempty original block is covered, including the end of long articles.
The per-input character limit splits long inputs rather than dropping their
tail. ``iter_units`` has no hidden article/block/unit-count cap: callers that
apply a model-request budget must report the incomplete coverage themselves.
The module neither writes the database nor calls a model.
"""
from __future__ import annotations

import hashlib
import json
import re


UNIT_VERSION = 'wenshu-vector-units-1'
KINDS = ('passage', 'title_summary', 'claim', 'fact')
PASSAGE_CHARS = 1000
CONTEXT_CHARS = 120
MIN_PASSAGE_CHARS = 250


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _rows(db, sql, parameters=()):
    """Work with both sqlite3.Row and ordinary SQLite connections."""
    cursor = db.execute(sql, parameters)
    columns = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(columns, row))


def _tables(db):
    return {row['name'] for row in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}


def _limits(passage_chars, context_chars):
    if (isinstance(passage_chars, bool) or not isinstance(passage_chars, int)
            or not 64 <= passage_chars <= 4000):
        raise ValueError('passage_chars must be between 64 and 4000')
    if (isinstance(context_chars, bool) or not isinstance(context_chars, int)
            or not 0 <= context_chars <= 500):
        raise ValueError('context_chars must be between 0 and 500')


def text_spans(text, max_chars=PASSAGE_CHARS):
    """Yield contiguous [start,end) spans without normalizing any character.

    Prefer a nearby sentence/line ending in the latter half of each window.
    A single unbroken block still gets bounded pieces and complete coverage.
    Offsets use Python Unicode character indices, matching stored Wiki quotes.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError('max_chars must be a positive integer')
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundaries = list(re.finditer(r'[。！？!?；;\n]|[.](?=\s)', text[start + max_chars // 2:end]))
            if boundaries:
                end = start + max_chars // 2 + boundaries[-1].end()
        yield start, end
        start = end


def _unit(source_id, revision_id, kind, anchor, text, match_text, evidence=(), **metadata):
    references = [dict(reference) for reference in evidence]
    spans = [{key: reference[key] for key in ('blockId', 'start', 'end')} for reference in references]
    unit = {'unitId': 'vu_' + _hash([UNIT_VERSION, source_id, revision_id, kind, anchor]),
            'sourceId': source_id, 'revisionId': revision_id, 'kind': kind,
            'text': text, 'matchText': match_text,
            'blockIds': list(dict.fromkeys(reference['blockId'] for reference in references)),
            'evidence': references, 'spans': spans, **metadata}
    # Version identity alone is insufficient: translations/summaries and Wiki
    # publications can change while the immutable original revision stays put.
    unit['dependencyHash'] = _hash([UNIT_VERSION, unit])
    return unit


def _reference(source_id, revision_id, block, start, end, title=''):
    return {'sourceId': source_id, 'revisionId': revision_id, 'blockId': block['id'],
            'start': start, 'end': end, 'quote': block['text'][start:end],
            'title': title, 'stale': False}


def _passage_groups(blocks, passage_chars, min_passage_chars):
    """Pack only consecutive short blocks; preserve substantial paragraphs.

    A block at or above the minimum is an independent semantic subject, split
    only when it exceeds the input window. Short headings/menu lines can share
    a vector with adjacent short blocks, but never bridge across a substantive
    paragraph. Every constituent retains a separate original evidence span.
    """
    group, size = [], 0
    for block in blocks:
        length = len(block['text'])
        if length >= min_passage_chars:
            if group:
                yield group
                group, size = [], 0
            for start, end in text_spans(block['text'], passage_chars):
                yield [(block, start, end)]
            continue
        separator = 1 if group else 0
        if group and size + separator + length > passage_chars:
            yield group
            group, size = [], 0
            separator = 0
        group.append((block, 0, length))
        size += separator + length
    if group:
        yield group


def passage_units(source_id, revision_id, blocks, *, title='',
                  passage_chars=PASSAGE_CHARS, context_chars=CONTEXT_CHARS,
                  min_passage_chars=MIN_PASSAGE_CHARS):
    """Yield independent substantial paragraphs and adjacent short-block groups.

    Context comes from the same block where possible, otherwise the immediately
    adjacent nonempty block. Its exact spans are available separately and do
    not expand the selectable quote or the main matched passage.
    """
    _limits(passage_chars, context_chars)
    if (isinstance(min_passage_chars, bool) or not isinstance(min_passage_chars, int)
            or not 1 <= min_passage_chars <= 4000):
        raise ValueError('min_passage_chars must be between 1 and 4000')
    # Small fixture/provider windows can be below the normal 250-char minimum.
    min_passage_chars = min(min_passage_chars, passage_chars)
    blocks = [dict(block) for block in blocks if isinstance(block['text'], str) and block['text'].strip()]
    indices = {block['id']: index for index, block in enumerate(blocks)}
    for group in _passage_groups(blocks, passage_chars, min_passage_chars):
        first, first_start, _ = group[0]
        last, _, last_end = group[-1]
        first_index, last_index = indices[first['id']], indices[last['id']]
        context = []
        before, after = '', ''
        if context_chars:
            previous = first if first_start else (blocks[first_index - 1] if first_index else None)
            following = last if last_end < len(last['text']) else (blocks[last_index + 1] if last_index + 1 < len(blocks) else None)
            if previous is not None:
                stop = first_start if first_start else len(previous['text'])
                begin = max(0, stop - context_chars)
                before = previous['text'][begin:stop]
                if before:
                    context.append({'position': 'before', 'blockId': previous['id'], 'start': begin, 'end': stop})
            if following is not None:
                begin = last_end if last_end < len(last['text']) else 0
                stop = min(len(following['text']), begin + context_chars)
                after = following['text'][begin:stop]
                if after:
                    context.append({'position': 'after', 'blockId': following['id'], 'start': begin, 'end': stop})
        references = [_reference(source_id, revision_id, block, start, end, title) for block, start, end in group]
        match = '\n'.join(reference['quote'] for reference in references)
        text = (('【前文上下文】\n' + before + '\n') if before else '')
        text += '【匹配段落】\n' + match
        text += ('\n【后文上下文】\n' + after) if after else ''
        anchor = [[reference['blockId'], reference['start'], reference['end']] for reference in references]
        yield _unit(source_id, revision_id, 'passage', anchor, text, match, references, contextSpans=context)


def title_summary_units(source_id, revision_id, title, summary, *, passage_chars=PASSAGE_CHARS):
    """Index all effective title/summary text independently of body passages."""
    _limits(passage_chars, 0)
    combined = '\n'.join(part for part in (title, summary) if isinstance(part, str) and part.strip())
    for start, end in text_spans(combined, passage_chars):
        match = combined[start:end]
        yield _unit(source_id, revision_id, 'title_summary', [start, end],
                    '【标题与摘要】\n' + match, match, metadataSpan={'start': start, 'end': end})


def _page_references(page):
    """All references participate in current-page freshness, as in WikiService."""
    references = list(page.get('evidence', []))
    for section in page.get('sections', []):
        for claim in section.get('claims', []):
            references.extend(claim.get('evidence', []))
    for relation in page.get('relations', []):
        references.extend(relation.get('evidence', []))
    return references


def _validated_references(references, source_id, revision_id, blocks, title):
    if not isinstance(references, list) or not references:
        return []
    result, seen = [], set()
    for reference in references:
        if not isinstance(reference, dict):
            return []
        block = blocks.get(reference.get('blockId'))
        quote = reference.get('quote')
        if (reference.get('sourceId') != source_id or reference.get('revisionId') != revision_id
                or not block or not isinstance(quote, str) or not quote.strip()):
            return []
        start, end = reference.get('start'), reference.get('end')
        # Legacy evidence with no offsets can be located only when unambiguous.
        # Invalid supplied offsets never fall back to a coincidental substring.
        if start is None and end is None:
            start = block['text'].find(quote)
            if start < 0 or block['text'].find(quote, start + 1) >= 0:
                return []
            end = start + len(quote)
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, int) or not isinstance(end, int)
                or not 0 <= start < end <= len(block['text']) or block['text'][start:end] != quote):
            return []
        key = (block['id'], start, end)
        if key not in seen:
            result.append(_reference(source_id, revision_id, block, start, end, title))
            seen.add(key)
    return result


def wiki_units(source_id, revision_id, pages, blocks, current, *, title='', passage_chars=PASSAGE_CHARS):
    """Yield only current, published source-page statements with checked quotes.

    ``pages`` must already be selected through wiki_publications.  Page-level
    current-version checks are repeated here for pure-function callers. Fact
    labels are extraction types and do not imply independent fact verification.
    """
    _limits(passage_chars, 0)
    block_map = {block['id']: dict(block) for block in blocks}
    for page in pages:
        if (not isinstance(page, dict) or page.get('sourceId') != source_id
                or page.get('revisionId') != revision_id or current.get(source_id) != revision_id
                or page.get('archived') or page.get('stale')):
            continue
        refs = _page_references(page)
        if any(not isinstance(ref, dict) or current.get(ref.get('sourceId')) != ref.get('revisionId') for ref in refs):
            continue
        kind = 'fact' if page.get('type') == 'fact' else 'claim'
        label = '【提取事实】' if kind == 'fact' else '【提取观点】'
        for section_index, section in enumerate(page.get('sections', [])):
            for claim_index, claim in enumerate(section.get('claims', [])):
                statement = claim.get('text')
                if not isinstance(statement, str) or not statement.strip():
                    continue
                evidence = _validated_references(claim.get('evidence'), source_id, revision_id, block_map, title)
                if not evidence:
                    continue
                for start, end in text_spans(statement, passage_chars):
                    match = statement[start:end]
                    # The entry label is context only; all statement characters
                    # remain represented even when a long statement is split.
                    entry_label = str(page.get('label', ''))
                    text = label + '\n' + (entry_label + '\n' if entry_label else '') + match
                    yield _unit(source_id, revision_id, kind,
                                [page.get('id'), page.get('version'), section_index, claim_index, start, end],
                                text, match, evidence, entryId=page.get('id'), wikiVersion=page.get('version'),
                                statementSpan={'start': start, 'end': end})


def iter_units(db, source_ids=None, *, kinds=None,
               passage_chars=PASSAGE_CHARS, context_chars=CONTEXT_CHARS,
               min_passage_chars=MIN_PASSAGE_CHARS):
    """Stream all requested kinds for selected current revisions, without writes.

    No lexical candidate list is needed: leaving ``source_ids`` as None includes
    the complete current source scope. An empty collection deliberately yields
    nothing. Optional derived tables may be absent in a fresh database.
    """
    _limits(passage_chars, context_chars)
    if isinstance(source_ids, str):
        raise ValueError('source_ids must be a collection, not a string')
    selected = None if source_ids is None else set(source_ids)
    requested = set(KINDS if kinds is None else kinds)
    if requested - set(KINDS):
        raise ValueError('unknown vector unit kind')
    if not requested or selected == set():
        return
    tables = _tables(db)
    current = {row['id']: row['current_revision'] for row in _rows(db, 'SELECT id,current_revision FROM sources')}
    chinese_join = 'LEFT JOIN document_chinese c ON c.revision_id=r.id' if 'document_chinese' in tables else ''
    title_sql = "coalesce(nullif(c.title,''),r.title)" if chinese_join else 'r.title'
    summary_sql = "coalesce(nullif(c.summary,''),r.summary)" if chinese_join else 'r.summary'
    documents = _rows(db, f'''SELECT s.id sourceId,r.id revisionId,{title_sql} title,{summary_sql} summary
        FROM sources s JOIN revisions r ON r.id=s.current_revision AND r.source_id=s.id
        {chinese_join} ORDER BY s.id''')
    for document in documents:
        source_id, revision_id = document['sourceId'], document['revisionId']
        if selected is not None and source_id not in selected:
            continue
        blocks = list(_rows(db, 'SELECT id,text,ordinal FROM blocks WHERE revision_id=? ORDER BY ordinal,id', (revision_id,)))
        if 'passage' in requested:
            yield from passage_units(source_id, revision_id, blocks, title=document['title'],
                                     passage_chars=passage_chars, context_chars=context_chars,
                                     min_passage_chars=min_passage_chars)
        if 'title_summary' in requested:
            yield from title_summary_units(source_id, revision_id, document['title'], document['summary'],
                                           passage_chars=passage_chars)
        if requested & {'claim', 'fact'} and {'wiki_pages', 'wiki_publications'} <= tables:
            pages = []
            for row in _rows(db, '''SELECT p.content_json FROM wiki_pages p
                    JOIN wiki_publications u ON u.source_id=p.source_id AND u.run_id=p.run_id
                    WHERE p.source_id=? AND p.revision_id=? ORDER BY p.entry_id,p.version''', (source_id, revision_id)):
                try:
                    page = json.loads(row['content_json'])
                except (TypeError, ValueError):
                    continue
                pages.append(page)
            for unit in wiki_units(source_id, revision_id, pages, blocks, current, title=document['title'],
                                   passage_chars=passage_chars):
                if unit['kind'] in requested:
                    yield unit


def build_units(db, source_ids=None, **options):
    """Materialize ``iter_units`` for bounded fixtures or caller-owned budgets."""
    return list(iter_units(db, source_ids, **options))
