"""Bounded discovery packets for reranking; original evidence stays immutable.

Derived summaries help discovery only. Every original excerpt retains an exact
source/revision/block span. This module has no model or benchmark dependency.
"""
from __future__ import annotations

import hashlib
import re

from .store import dumps


PACKET_VERSION = 'wenshu-rerank-packet-1'
INSTRUCTION = 'Given a web search query, retrieve relevant passages that answer the query'
CHUNK_CHARS = 750
SHORT_CHARS = 4500
MAX_BLOCKS = 5000
MAX_ORIGINAL_CHARS = 1000000


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def query_text(topic, queries):
    # Writing-purpose/format instructions are not search topics. The actual
    # planner's queries are sufficient and follow the frozen comparison input.
    return topic + '\n检索条件：\n' + '\n'.join(queries)


def prepare_packet(db, source_id, document, topic, queries, vector_matches, anchors):
    from .retrieval import _match, _tokens

    rows = db.execute('SELECT id,ordinal,text FROM blocks WHERE revision_id=? ORDER BY ordinal LIMIT ?',
                      (document['revisionId'], MAX_BLOCKS + 1)).fetchall()
    if len(rows) > MAX_BLOCKS or sum(len(row['text']) for row in rows) > MAX_ORIGINAL_CHARS:
        raise ValueError('资料内容超过本次排序预算')
    blocks = [dict(row) for row in rows]
    pieces = [{'blockId': block['id'], 'ordinal': block['ordinal'], 'start': start,
               'end': min(start + CHUNK_CHARS, len(block['text'])),
               'quote': block['text'][start:start + CHUNK_CHARS]}
              for block in blocks for start in range(0, len(block['text']), CHUNK_CHARS)
              if block['text'][start:start + CHUNK_CHARS].strip()]
    short = sum(len(row['text']) for row in blocks) <= SHORT_CHARS
    if short:
        selected = [dict(piece, role='full-short-original') for piece in pieces]
    else:
        tokens = _tokens(' '.join([topic, *queries]))

        def score(piece):
            vector = max((match['cosine'] for match in vector_matches if match['kind'] == 'passage'
                and any(ref['blockId'] == piece['blockId'] and ref['start'] < piece['end']
                        and ref['end'] > piece['start'] for ref in match['evidence'])), default=0)
            anchored = any(ref.get('blockId') == piece['blockId'] and ref.get('quote')
                           and ref['quote'] in piece['quote'] for _, ref in anchors)
            return vector, int(anchored), _match(piece['quote'], tokens)

        ranked = sorted(pieces, key=lambda piece: tuple(-value for value in score(piece))
                        + (piece['ordinal'], piece['start']))
        selected = [dict(piece, role='query-match') for piece in ranked if any(score(piece))][:3]
        substantive = [piece for piece in pieces if len(piece['quote'].strip()) >= 120
                       and len(re.findall(r'[A-Za-z\u4e00-\u9fff]', piece['quote'])) >= 60]
        if substantive:
            for index, role in ((0, 'main-head'), (len(substantive) // 2, 'main-middle'),
                                (len(substantive) - 1, 'main-tail')):
                selected.append(dict(substantive[index], role=role))
    unique, seen = [], set()
    for piece in selected:
        key = (piece['blockId'], piece['start'], piece['end'])
        if key not in seen:
            seen.add(key)
            unique.append(piece)
    parts = [{'kind': 'title', 'text': document['title'][:300], 'minTokens': 96}]
    if document['originalTitle'] and document['originalTitle'] != document['title']:
        parts.append({'kind': 'original-title', 'text': document['originalTitle'][:300], 'minTokens': 96})
    if document['summary']:
        parts.append({'kind': 'derived-summary', 'text': document['summary'][:800], 'minTokens': 96})
    spans = [{'sourceId': source_id, 'revisionId': document['revisionId'],
              **{key: piece[key] for key in ('blockId', 'start', 'end')}, 'quote': piece['quote']}
             for piece in unique]
    if short:
        groups, group, size = [], [], 0
        for piece in unique:
            if group and size + len(piece['quote']) > 1500:
                groups.append(group)
                group, size = [], 0
            group.append(piece)
            size += len(piece['quote'])
        if group:
            groups.append(group)
        parts.extend({'kind': 'full-short-original-' + str(index + 1),
                      'text': '\n'.join(piece['quote'] for piece in group), 'minTokens': 96}
                     for index, group in enumerate(groups))
    else:
        parts.extend({'kind': piece['role'], 'text': piece['quote'], 'minTokens': 96} for piece in unique)
    text = '\n\n'.join('[' + part['kind'] + ']\n' + part['text'] for part in parts)
    packet = {'sourceId': source_id, 'revisionId': document['revisionId'], 'text': text,
              'parts': parts, 'originalSpans': spans, 'shortFullText': short,
              'originalCharacters': sum(len(row['text']) for row in blocks)}
    # Full effective metadata is a dependency even when its preview is bounded.
    packet['dependencyHash'] = digest({key: document[key]
                                      for key in ('revisionId', 'title', 'originalTitle', 'summary')})
    packet['packetHash'] = digest([PACKET_VERSION, packet['dependencyHash'], text, spans])
    return packet


def dependencies_current(db, packets):
    """Check original revision and effective metadata after provider IO."""
    rows = db.execute('''SELECT s.id sourceId,r.id revisionId,coalesce(nullif(c.title,''),r.title) title,
        r.title originalTitle,coalesce(nullif(c.summary,''),r.summary) summary
        FROM sources s JOIN revisions r ON r.id=s.current_revision
        LEFT JOIN document_chinese c ON c.revision_id=r.id
        WHERE s.id IN (SELECT value FROM json_each(?))''',
        (dumps([packet['sourceId'] for packet in packets]),)).fetchall()
    metadata = {row['sourceId']: {key: row[key] for key in ('revisionId', 'title', 'originalTitle', 'summary')}
                for row in rows}
    for packet in packets:
        current = metadata.get(packet['sourceId'])
        if current is None or digest(current) != packet['dependencyHash']:
            return False
        # Check spans inside SQLite rather than reloading entire long blocks or
        # issuing a query for every small DOM fragment in the final transaction.
        invalid = db.execute('''SELECT 1 FROM json_each(?) span
            LEFT JOIN blocks b ON b.id=json_extract(span.value,'$.blockId') AND b.revision_id=?
            WHERE b.id IS NULL OR substr(b.text,json_extract(span.value,'$.start')+1,
                json_extract(span.value,'$.end')-json_extract(span.value,'$.start'))
                !=json_extract(span.value,'$.quote') LIMIT 1''',
            (dumps(packet['originalSpans']), packet['revisionId'])).fetchone()
        if invalid:
            return False
    return True
