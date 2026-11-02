"""Bounded image discovery across pinned references and a frozen library.

Assets remain occurrences, even when their content-addressed files are shared.
DOM links, translations and vectors are derived discovery data, never new prose
evidence. Library discovery requires an explicit frozen manifest and never
changes reference scope. Model callbacks own their operation logging.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from lxml import etree, html

from .card_cover import thumbnail
from .image_metadata import library_image_eligible, raster_dimensions
from .store import dumps, now, tokenize


CONTEXT_VERSION = 'writing-image-context-5'
VECTOR_VERSION = 'writing-image-text-1'
QUALITY_POLICY = 'writing-image-quality-1'
MAX_ASSETS = 400
MAX_RESULTS = 24
MAX_EMBEDDINGS = 64
MAX_LIBRARY_CANDIDATES = 64
MAX_VISUAL = 12
VISION_BATCH = 6
# Allow for the exact asset ID/schema, a short reason and provider reasoning.
# The configured output limit includes reasoning on some compatible providers.
VISION_OUTPUT_OVERHEAD = 192
VISION_OUTPUT_PER_IMAGE = 640
SCHEMA = '''
CREATE TABLE IF NOT EXISTS writing_image_contexts(
 asset_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 dependency_hash TEXT NOT NULL, context_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS writing_image_context_revision
 ON writing_image_contexts(source_id,revision_id);
CREATE TABLE IF NOT EXISTS writing_image_vectors(
 cache_key TEXT PRIMARY KEY, asset_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 model_id TEXT NOT NULL, model_version INTEGER NOT NULL,
 dependency_hash TEXT NOT NULL, vector_json TEXT NOT NULL, created_at TEXT NOT NULL
);
'''

from .prompt_task_defaults import VISION_SYSTEM


def _hash(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def _object(text):
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _paragraphs(content):
    # Keep paragraph indexes identical to writing_images.anchor_for/export.
    return [part.strip() for part in re.split(r'\n\s*\n', content or '') if part.strip()]


def _terms(text, limit=40):
    ignored = {'配图', '图片', '資料', '资料', '相关', '相關', '文章', '介绍', '介紹', '分析',
               '事件', '表示', '指出', '目前', '可能', '由于', '由於', '因此', '其中', '以及',
               '进行', '進行', '有关', '有關', '包括', '通过', '透過', '已经', '已經', 'the',
               'and', 'with', 'for', 'from', 'that', 'this', 'are', 'was', 'were', 'have', 'has'}
    return list(dict.fromkeys(word for word in tokenize(text).split()
                             if len(word) > 1 and word not in ignored))[:limit]


def _balanced_text(parts, limit):
    """Share finite text space across the entire chapter, retaining both ends."""
    parts = [part for part in parts if part]
    if len('\n'.join(parts)) <= limit:
        return '\n'.join(parts), len(parts)
    if not parts or limit < 1:
        return '', 0
    # Extremely fragmented documents cannot fit every paragraph; sample evenly
    # including the last, and expose represented counts rather than hide loss.
    selected = parts
    if len(parts) * 12 > limit:
        count = max(1, limit // 12)
        indexes = sorted({round(i * (len(parts) - 1) / max(1, count - 1)) for i in range(count)})
        selected = [parts[i] for i in indexes]
    remaining, output = limit - len(selected) + 1, []
    for index, part in enumerate(selected):
        allowance = max(1, remaining // (len(selected) - index))
        tail = allowance // 2
        fragment = part if len(part) <= allowance else part[:allowance - tail] + (part[-tail:] if tail else '')
        output.append(fragment)
        remaining -= len(fragment)
    return '\n'.join(output), len(selected)


def _rank_key(item):
    return (not item['scopeEligible'], item['status'] == 'unsuitable', -item['_rank'],
            item.get('sourceType') == 'library', item['assetId'])


def _bounded_candidates(items, limit):
    """Reserve a small library discovery lane without exceeding model limits."""
    ordered = sorted(items, key=_rank_key)
    library = [item for item in ordered if item.get('sourceType') == 'library'
               and item['scopeEligible'] and item['status'] != 'unsuitable'][:max(1, limit // 3)]
    selected = ordered[:max(0, limit - len(library))]
    ids = {item['assetId'] for item in selected}
    selected.extend(item for item in library if item['assetId'] not in ids)
    ids = {item['assetId'] for item in selected}
    selected.extend(item for item in ordered if item['assetId'] not in ids)
    return sorted(selected[:limit], key=_rank_key)


def _lexical(text, terms):
    lowered = text.casefold()
    return sum(bool(re.search(r'(?<![a-z0-9])' + re.escape(term.casefold()) + r'(?![a-z0-9])', lowered))
               if re.fullmatch(r'[a-zA-Z0-9_-]+', term) else term.casefold() in lowered
               for term in terms) / max(1, len(terms))


def _library_diverse(items, limit, *, reservoir=False):
    """Give distinct source occurrences a recall lane alongside global score.

    A long gallery must not fill the entire finite semantic pool. Streaming
    keeps both the global leaders and source leaders, then the final shortlist
    reserves half its places for the best occurrence from distinct sources.
    """
    ordered = sorted(items, key=_rank_key)
    leaders, seen = [], set()
    for item in ordered:
        if item['sourceId'] not in seen:
            leaders.append(item)
            seen.add(item['sourceId'])
    reserved = leaders[:limit if reservoir else max(1, limit // 2)]
    if reservoir:
        reserved += ordered[:limit]
    else:
        reserved += ordered
    result, ids = [], set()
    for item in reserved:
        if item['assetId'] not in ids:
            ids.add(item['assetId'])
            result.append(item)
    return sorted(result if reservoir else result[:limit], key=_rank_key)


def _context_metadata(text):
    """Recognize short notices to step over, while keeping them in context."""
    value = text.strip().strip('()（）[]').strip()
    if not value or not _caption_has_description(value):
        return True
    if len(value) > 220:
        return False
    if re.match(r'^(?:最后更新|最後更新|更新日期|发布日期|發佈日期|發布日期|Last updated|Published(?: on)?)', value, re.I):
        return True
    return bool(re.search(r'(?:图片|圖片|图像|圖像|image|illustration)', value, re.I)
                and re.search(r'(?:生成式|人工智能|人工智慧|\bAI\b|generative)', value, re.I)
                and re.search(r'(?:创建|創建|生成|製作|制作|generated|created)', value, re.I))


def _valid_vector(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 8192:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        return None
    norm = math.sqrt(sum(v * v for v in value))
    return [v / norm for v in value] if math.isfinite(norm) and norm else None


def _descendant(path, ancestor):
    return path == ancestor or path.startswith(ancestor + '/')


def _caption_has_description(caption):
    """A byline/credit alone cannot establish the pictured subject or event."""
    credit = re.compile(
        r'^(?:(?:(?:image|photo(?:graph)?|picture|illustration)\s+)?(?:credit|source)s?\s*[:：]'
        r'|(?:image|photo(?:graph)?|picture|illustration)\s*[:：]'
        r'|(?:[图圖]片[来來]源|照片[来來]源|[图圖]像[来來]源|[图圖]源|[图圖]片署名|[摄攝]影|[来來]源)\s*[:：]'
        r'|courtesy\s+of\b|copyright\b|©)', re.I)
    lines = [line.strip().strip('()（）[]').strip() for line in caption.splitlines() if line.strip()]
    return any(line and not credit.match(line) for line in lines)


class ImageSearchInterrupted(ValueError):
    """Safe cancellation signal which must not become a degraded search."""


def _check_guard(guard):
    if guard is not None:
        try:
            guard()
        except ValueError:
            raise ImageSearchInterrupted('图片检索任务已取消或输入已变化') from None


def validate_visual_response(response, asset_ids, *, forbidden_text=None):
    """Validate a gateway response before its operation capture is finalized.

    Returns an assetId -> judgment mapping. Callers may use this within their
    operation context; search repeats it at its own trust boundary. Exceptions
    never echo model output or a credential.
    """
    from .enrichment import _json
    response_text = response.get('text') if isinstance(response, dict) else None
    if not isinstance(response_text, str) or len(response_text) > 24000:
        raise ValueError('图片核对未返回有效文本')
    if forbidden_text and forbidden_text in response_text:
        raise ValueError('图片核对结果包含不应保存的敏感信息')
    result = _json(response_text)
    # JSON escapes must not bypass the credential check at this trust boundary.
    if forbidden_text and forbidden_text in json.dumps(result, ensure_ascii=False):
        raise ValueError('图片核对结果包含不应保存的敏感信息')
    judgments = result.get('items')
    expected = set(asset_ids)
    if set(result) != {'items'} or not isinstance(judgments, list) or len(judgments) != len(expected):
        raise ValueError('图片核对结果未完整对应候选批次')
    accepted = {}
    for judgment in judgments:
        if not isinstance(judgment, dict) or set(judgment) != {'assetId', 'relevance', 'readable', 'contextConsistent', 'reason'}:
            raise ValueError('图片核对结果字段不正确')
        asset_id = judgment['assetId']
        if not isinstance(asset_id, str) or asset_id not in expected or asset_id in accepted:
            raise ValueError('图片核对结果包含候选外或重复图片')
        if judgment['relevance'] not in ('relevant', 'uncertain', 'irrelevant'):
            raise ValueError('图片相关性判断无效')
        if any(judgment[key] is not None and type(judgment[key]) is not bool for key in ('readable', 'contextConsistent')):
            raise ValueError('图片可读性或语境判断无效')
        reason = judgment['reason']
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 400:
            raise ValueError('图片核对理由无效')
        accepted[asset_id] = judgment
    return accepted


class ImageRetrievalService:
    def __init__(self, store, models=None, *, read_only=False):
        self.store = store
        self.models = models
        if not read_only:
            with store.connect() as db:
                db.executescript(SCHEMA)

    def _source_contexts(self, source, *, persist=True):
        """Read one exact owned revision; persist only disposable DOM metadata."""
        source_id, revision_id = source['sourceId'], source['revisionId']
        with self.store.connect() as db:
            revision = db.execute('SELECT * FROM revisions WHERE id=? AND source_id=?',
                                  (revision_id, source_id)).fetchone()
            if not revision:
                return None
            revision = dict(revision)
            assets = [dict(row) for row in db.execute('SELECT * FROM assets WHERE revision_id=? ORDER BY rowid', (revision_id,))]
            blocks = [dict(row) for row in db.execute('SELECT * FROM blocks WHERE revision_id=? ORDER BY ordinal', (revision_id,))]
            translated = {row['block_id']: row['text'] for row in db.execute(
                'SELECT block_id,text FROM translated_blocks WHERE revision_id=?', (revision_id,))}
            has_cache = db.execute("SELECT 1 FROM sqlite_master WHERE name='writing_image_contexts'").fetchone()
            cached = {row['asset_id']: dict(row) for row in db.execute(
                'SELECT * FROM writing_image_contexts WHERE revision_id=? AND source_id=?', (revision_id, source_id))} if has_cache else {}
        block_map = {block['id']: block for block in blocks}
        expected = {asset['id']: _hash([CONTEXT_VERSION, source_id, revision_id, revision['blob_hash'],
                                      asset['id'], asset['alt'], asset['locator_json'], asset['blob_path']]) for asset in assets}
        contexts = {}
        for asset in assets:
            saved = cached.get(asset['id'])
            if saved and saved['dependency_hash'] == expected[asset['id']]:
                context = _object(saved['context_json'])
                if context.get('version') == CONTEXT_VERSION:
                    contexts[asset['id']] = context
        manifest = {'snapshotImageReferences': None, 'unsavedImageReferences': None, 'contextAvailable': True}
        if assets and len(contexts) == len(assets):
            manifest = contexts[assets[0]['id']].get('manifest', manifest)
        else:
            tree = None
            try:
                from .parser import _decode
                original_path, _ = self.store.original(source_id, revision_id)
                original_path = original_path.resolve()
                if not original_path.is_relative_to((self.store.root / 'blobs').resolve()):
                    raise ValueError('snapshot path')
                raw = original_path.read_bytes()
                if len(raw) > 64 * 1024 * 1024:
                    raise ValueError('snapshot size')
                tree = html.document_fromstring(_decode(raw, []), parser=html.HTMLParser(
                    no_network=True, remove_comments=True, huge_tree=False))
            except (OSError, ValueError, etree.LxmlError):
                manifest['contextAvailable'] = False
            if tree is not None:
                image_count = len(tree.xpath('//img'))
                manifest.update(snapshotImageReferences=image_count,
                                unsavedImageReferences=max(0, image_count - len(assets)))
                root_tree = tree.getroottree()
                positions = {root_tree.getpath(node): index for index, node in enumerate(tree.iter()) if isinstance(node.tag, str)}
                context_barriers = [positions[root_tree.getpath(node)] for node in tree.iter()
                                    if isinstance(node.tag, str) and node.tag in ('img', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6')]
                block_paths = {block['id']: _object(block['locator_json']).get('xpath', '') for block in blocks}
            for asset in assets:
                if asset['id'] in contexts:
                    continue
                context = {'version': CONTEXT_VERSION, 'captionBlockIds': [], 'referenceBlockIds': [], 'nearbyBlockIds': [],
                           'headingBlockId': None, 'explicitAssociation': False, 'manifest': manifest,
                           'relation': 'unknown'}
                path = _object(asset['locator_json']).get('xpath')
                if tree is not None and isinstance(path, str) and path.startswith('/html') and len(path) < 2000:
                    # The locator is data: accept only simple parser-owned paths,
                    # never arbitrary XPath expressions from imported metadata.
                    if re.fullmatch(r'/[a-zA-Z0-9_-]+(?:\[\d+\])?(?:/[a-zA-Z0-9_-]+(?:\[\d+\])?)*', path):
                        found = tree.xpath(path)
                        node = found[0] if len(found) == 1 and found[0].tag == 'img' else None
                        if node is not None:
                            captions = []
                            figures = node.xpath('ancestor::figure[1]')
                            if figures:
                                captions.extend(figures[0].xpath('./figcaption'))
                            for described in (node.get('aria-describedby') or '').split()[:8]:
                                matches = tree.xpath('//*[@id=$value]', value=described)
                                if len(matches) == 1:
                                    captions.extend(matches)
                            if not captions:
                                # Explicit publisher component, not a generic
                                # adjacent div or a whole article wrapper. A
                                # multi-image component cannot give one image
                                # another image's descriptive caption.
                                wrappers = node.xpath('ancestor::*[contains(concat(" ",normalize-space(@class)," ")," CaptionedContent ")][1]')
                                if wrappers:
                                    wrapper = wrappers[0]
                                    contents = wrapper.xpath('./*[contains(concat(" ",normalize-space(@class)," ")," CaptionedContent-Content ")]')
                                    labels = wrapper.xpath('./*[contains(concat(" ",normalize-space(@class)," ")," CaptionedContent-Caption ")]')
                                    if (len(contents) == 1 and len(labels) == 1 and len(wrapper.xpath('.//img')) == 1
                                            and node in contents[0].iter('img')):
                                        captions.extend(labels)
                            caption_paths = [root_tree.getpath(caption) for caption in captions]
                            ids = [block['id'] for block in blocks if any(
                                _descendant(block_paths[block['id']], caption_path) for caption_path in caption_paths)]
                            target_ids = [value for value in [node.get('id'), *(figure.get('id') for figure in figures)]
                                          if value and len(tree.xpath('//*[@id=$value]', value=value)) == 1]
                            references = [anchor for target_id in target_ids
                                          for anchor in tree.xpath('//a[@href=$target]', target='#' + target_id)]
                            reference_ids = []
                            for anchor in references:
                                anchor_path = root_tree.getpath(anchor)
                                anchor_text = re.sub(r'\s+', ' ', ''.join(anchor.itertext())).strip()
                                matches = [block for block in blocks if block['id'] not in ids and block['kind'] != 'code'
                                           and block_paths[block['id']] and _descendant(anchor_path, block_paths[block['id']])
                                           and anchor_text and anchor_text in block['text']]
                                # A parent may emit several blocks around <br>.
                                # Ambiguous link text cannot authorize all of them.
                                if len(matches) == 1:
                                    reference_ids.append(matches[0]['id'])
                            context.update(captionBlockIds=list(dict.fromkeys(ids)), explicitAssociation=bool(ids),
                                           referenceBlockIds=list(dict.fromkeys(reference_ids)),
                                           relation='caption' if ids else 'explicit_reference' if reference_ids else 'nearby_context')
                            context['explicitAssociation'] = bool(ids or reference_ids)
                            position = positions.get(path, -1)
                            other = [block for block in blocks if block['id'] not in ids and block['kind'] not in ('code', 'heading')
                                     and block_paths[block['id']] in positions]
                            # A date/credit/AI disclosure can sit between the
                            # image and its nearest substantive paragraph.
                            # Keep that notice, then look a few blocks further,
                            # never crossing another image or heading. This is
                            # discovery context only, not caption/scope proof.
                            lower = max((p for p in context_barriers if p < position), default=-1)
                            upper = min((p for p in context_barriers if p > position), default=math.inf)
                            before = [block for block in other if lower < positions[block_paths[block['id']]] < position]
                            after = [block for block in other if position < positions[block_paths[block['id']]] < upper]
                            def nearest(values):
                                found = []
                                for block in values[:4]:
                                    found.append(block)
                                    if not _context_metadata(block['text']):
                                        break
                                return found
                            context['nearbyBlockIds'] = [block['id'] for block in
                                [*reversed(nearest(list(reversed(before)))), *nearest(after)]]
                            headings = [block for block in blocks if block['kind'] == 'heading'
                                        and positions.get(block_paths[block['id']], math.inf) < position]
                            context['headingBlockId'] = headings[-1]['id'] if headings else None
                contexts[asset['id']] = context
            if assets and persist:
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.executemany('INSERT OR REPLACE INTO writing_image_contexts VALUES(?,?,?,?,?,?)',
                                   [(asset['id'], source_id, revision_id, expected[asset['id']],
                                     dumps(contexts[asset['id']]), now()) for asset in assets
                                    if db.execute('SELECT 1 FROM assets a JOIN revisions r ON r.id=a.revision_id '
                                                  'JOIN sources s ON s.id=r.source_id WHERE a.id=? AND r.id=? AND s.id=?',
                                                  (asset['id'], revision_id, source_id)).fetchone()])
        return revision, assets, block_map, translated, contexts, manifest

    def _asset_facts(self, asset):
        path, _ = self.store.asset(asset['id'])
        path = path.resolve()
        if not path.is_relative_to((self.store.root / 'assets').resolve()) or not path.is_file():
            return '', 0, 0, '图片文件不可用'
        if path.stat().st_size > 16 * 1024 * 1024:
            return '', 0, 0, '图片文件超过可处理大小'
        # The standard Store path is the SHA-256 of these immutable bytes.
        asset_hash = path.stem if re.fullmatch(r'[0-9a-f]{64}', path.stem) else hashlib.sha256(path.read_bytes()).hexdigest()
        width, height = raster_dimensions(path)
        if not width or not height:
            return asset_hash, width, height, '图片格式或实际尺寸无法确认'
        if min(width, height) < 80 or width * height < 16000:
            return asset_hash, width, height, '图片过小，不适合正文展示'
        return asset_hash, width, height, ''

    def _semantic(self, items, query, config, coverage, warnings, request_guard=None, embed_call=None):
        if not config or not query.strip():
            coverage['semanticStatus'] = 'not_configured'
            return
        from .retrieval import RetrievalService, _embedding_query
        selected = _bounded_candidates([item for item in items if item['scopeEligible'] and item['_searchText'].strip()
                                        and item['status'] != 'unsuitable'], MAX_EMBEDDINGS)
        coverage['semanticEligibleCount'] = sum(bool(item['scopeEligible'] and item['_searchText'].strip()
                                                   and item['status'] != 'unsuitable') for item in items)
        coverage['semanticConsideredCount'] = len(selected)
        if not selected:
            coverage['semanticStatus'] = 'no_candidates'
            return
        _check_guard(request_guard)
        def encode(texts):
            return embed_call(config, texts) if embed_call is not None else RetrievalService._embed(self, config, texts)
        try:
            query_vector = encode([_embedding_query(config, [query])])[0]
            pending, vectors = [], {}
            with self.store.connect() as db:
                for item in selected:
                    dependency = _hash([VECTOR_VERSION, item['assetId'], item['revisionId'], item['_searchText']])
                    cache_key = _hash([dependency, config['id'], config['version'], config['model']])
                    item['_vectorKey'], item['_vectorDependency'] = cache_key, dependency
                    row = db.execute('SELECT vector_json FROM writing_image_vectors WHERE cache_key=?', (cache_key,)).fetchone()
                    vector = None
                    if row:
                        try:
                            vector = _valid_vector(json.loads(row['vector_json']))
                        except (ValueError, TypeError):
                            pass
                    if vector is not None and len(vector) == len(query_vector):
                        vectors[item['assetId']] = vector
                    else:
                        pending.append(item)
            for offset in range(0, len(pending), 16):
                _check_guard(request_guard)
                batch = pending[offset:offset + 16]
                encoded = encode([item['_searchText'] for item in batch])
                if any(len(vector) != len(query_vector) for vector in encoded):
                    raise ValueError('embedding dimensions')
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    for item, vector in zip(batch, encoded):
                        if not db.execute('SELECT 1 FROM assets a JOIN revisions r ON r.id=a.revision_id '
                                          'JOIN sources s ON s.id=r.source_id WHERE a.id=? AND r.id=? AND s.id=?',
                                          (item['assetId'], item['revisionId'], item['sourceId'])).fetchone():
                            continue
                        vectors[item['assetId']] = vector
                        db.execute('INSERT OR REPLACE INTO writing_image_vectors VALUES(?,?,?,?,?,?,?,?)',
                                   (item['_vectorKey'], item['assetId'], item['revisionId'], config['id'], config['version'],
                                    item['_vectorDependency'], dumps(vector), now()))
            for item in selected:
                vector = vectors.get(item['assetId'])
                if vector is not None:
                    score = max(-1., min(1., sum(left * right for left, right in zip(query_vector, vector))))
                    item['semanticScore'] = round(score, 6)
                    item['_rank'] += max(0, score) * 2
                    if score > 0:
                        item['rankReasons'].append('图片语境的语义匹配')
            coverage['semanticIndexedCount'] = len(vectors)
            coverage['semanticStatus'] = 'partial' if coverage['semanticEligibleCount'] > len(selected) else 'ready'
            if coverage['semanticStatus'] == 'partial':
                warnings.append('本次仅完成部分图片的语义匹配，其他候选仍可查看。')
        except ImageSearchInterrupted:
            raise
        except Exception:
            _check_guard(request_guard)
            # Provider bodies/credentials must never reach candidates or logs.
            coverage['semanticStatus'] = 'unavailable'
            warnings.append('图片语义检索暂不可用，已保留来源与文字匹配结果。')

    def _visual(self, items, target, config, callback, coverage, warnings, request_guard=None, vision_system=None):
        if not config or callback is None:
            coverage['visualStatus'] = 'not_reviewed'
            return
        selected = _bounded_candidates([item for item in items if item['scopeEligible']
                                        and item['status'] != 'unsuitable'], MAX_VISUAL)
        if not selected:
            coverage['visualStatus'] = 'no_candidates'
            return
        system = VISION_SYSTEM if vision_system is None else vision_system
        output_limit = config.get('maxOutputTokens', 2048)
        batch_limit = (min(VISION_BATCH, max(0, (output_limit - VISION_OUTPUT_OVERHEAD) // VISION_OUTPUT_PER_IMAGE))
                       if type(output_limit) is int else 0)
        if not batch_limit:
            for item in selected:
                item['issues'].append('本次无法完整核对图片，请人工确认或更换模型后重试')
            warnings.append('本次无法完成图片核对，候选图片仍可人工查看。')
            coverage['visualStatus'] = 'partial'
            return
        reviewed, failed = 0, False

        def packet_for(batch):
            # These are the exact text values the reader will see. Discovery
            # snippets may be bounded, but evidence/placement validation may
            # never silently omit a caption or a paragraph's final condition.
            return {'task': 'writing_image_review', 'targetParagraph': target,
                    'candidates': [{'assetId': item['assetId'], 'sourceTitle': item['sourceTitle'],
                                    'originalCaption': item['originalCaption'], 'caption': item['caption'],
                                    'altText': item['altText'], 'contextOnly': item['_contextText'][:600],
                                    'width': item['width'], 'height': item['height']} for item in batch]}

        def within_budget(batch):
            size = len(system) + len(dumps(packet_for(batch)))
            size += sum(len('assetId=' + item['assetId']) for item in batch)
            return size <= config.get('contextChars', 16000)

        batches, pending, skipped = [], [], 0
        for item in selected:
            _check_guard(request_guard)
            if not within_budget([item]):
                item['issues'].append('本段内容或图片说明较长，请查看完整原文后人工核对')
                skipped += 1
                failed = True
                continue
            if pending and (len(pending) >= batch_limit or not within_budget([*pending, item])):
                batches.append(pending)
                pending = []
            pending.append(item)
        if pending:
            batches.append(pending)
        if skipped:
            warnings.append('本段内容或部分图片说明较长，需要人工核对。')
        for planned in batches:
            _check_guard(request_guard)
            batch, images = [], []
            for item in planned:
                try:
                    image = thumbnail(self.store, item)
                except Exception:
                    item.update(status='unsuitable', visualStatus='unavailable')
                    item['issues'].append('图片无法解码或生成核对缩略图')
                    failed = True
                    continue
                batch.append(item)
                images.append(image)
            if not batch:
                continue
            packet = packet_for(batch)
            parts = [{'type': 'text', 'text': dumps(packet)}]
            for item, image in zip(batch, images):
                parts.extend([{'type': 'text', 'text': 'assetId=' + item['assetId']},
                              {'type': 'image_url', 'image_url': {'url': image, 'detail': 'low'}}])
            messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': parts}]
            try:
                _check_guard(request_guard)
                response = callback(config, messages)
                accepted = validate_visual_response(response, [item['assetId'] for item in batch],
                                                    forbidden_text=config.get('apiKey'))
                for item in batch:
                    judgment = accepted[item['assetId']]
                    item['visualStatus'] = 'reviewed'
                    item['visualReason'] = judgment['reason'].strip()
                    if 'qualityGate' in item:
                        passed = (item['scopeEligible'] is True and judgment['relevance'] == 'relevant'
                                  and judgment['readable'] is True and judgment['contextConsistent'] is True)
                        if passed:
                            reason = '画面与本段直接相关，清晰度及图文一致性已核对'
                        elif judgment['relevance'] != 'relevant':
                            reason = '图片与本段的具体关联尚未确认'
                        elif judgment['readable'] is not True:
                            reason = '画面清晰度尚未通过核对'
                        else:
                            reason = '图注、画面与本段的一致性尚未通过核对'
                        item['qualityGate'].update(passed=passed, reason=reason)
                    reviewed += 1
                    if judgment['relevance'] == 'irrelevant' or judgment['readable'] is False or judgment['contextConsistent'] is False:
                        item['status'] = 'unsuitable'
                        item['issues'].append(judgment['reason'].strip())
                    elif (judgment['relevance'] == 'relevant' and judgment['readable'] is True
                          and judgment['contextConsistent'] is True and item['originalCaption'] and not item['issues']
                          and item.get('sourceType') != 'library'):
                        item['status'] = 'recommended'
                        item['rankReasons'].append('画面、图注与正文已通过模型核对')
                    else:
                        item['issues'].append('图片与正文的关系仍需人工确认')
            except ImageSearchInterrupted:
                raise
            except Exception:
                _check_guard(request_guard)
                failed = True
                for item in batch:
                    item['visualStatus'] = 'failed'
                    item['issues'].append('本次图片核对未获得有效结果，请人工确认或重试')
                warnings.append('部分图片核对未完成，未将未核对图片标为推荐。')
        coverage['visualReviewedCount'] = reviewed
        coverage['visualStatus'] = 'partial' if failed or len(selected) < sum(item['scopeEligible'] and item['status'] != 'unsuitable' for item in items) else 'reviewed'

    def search(self, project, section_id, paragraph_index=0, query='', *,
               model_config=None, vision_call=None, embedding_config=None, request_guard=None, embed_call=None,
               vision_system=None, library_scope=None, search_scope='references', placement_mode='paragraph',
               preserve_context=False, quality_policy=None, selected_asset_id=None):
        _check_guard(request_guard)
        if quality_policy is not None and quality_policy != QUALITY_POLICY:
            raise ValueError('配图质量规则无效，请重新创建检索任务')
        if type(preserve_context) is not bool:
            raise ValueError('配图语境选项无效')
        if search_scope not in ('references', 'library') or placement_mode not in ('paragraph', 'chapter'):
            raise ValueError('配图检索范围或位置方式无效')
        library_sources = []
        if search_scope == 'library':
            from .image_library import library_scope_sources
            library_sources = library_scope_sources(library_scope, project)
        if vision_system is not None and (not isinstance(vision_system, str) or not vision_system.strip()):
            raise ValueError('图片核对规则无效，请重新创建检索任务')
        section = next((section for section in project.get('sections', []) if section.get('id') == section_id), None)
        if section is None:
            raise ValueError('配图章节不存在')
        paragraphs = _paragraphs(section.get('content', ''))
        if not paragraphs:
            raise ValueError('请先保存章节正文，再查找配图')
        if isinstance(paragraph_index, bool) or not isinstance(paragraph_index, int) or not 0 <= paragraph_index < len(paragraphs):
            raise ValueError('配图段落不存在，请重新选择位置')
        if not isinstance(query, str) or len(query) > 1000:
            raise ValueError('配图检索文字不能超过 1000 字符')
        target = paragraphs[paragraph_index] if placement_mode == 'paragraph' else '\n\n'.join(paragraphs)
        selected_refs = {source['sourceId']: source for source in project.get('sources', [])
                         if isinstance(source, dict) and isinstance(source.get('sourceId'), str) and isinstance(source.get('revisionId'), str)}
        paragraph_ids = set(re.findall(r'\[(E\d+)\]', target))
        section_ids = set(re.findall(r'\[(E\d+)\]', section.get('content', '')))
        paragraph_sources, section_sources, cited_blocks = set(), set(), set()
        with self.store.connect() as db:
            for evidence in section.get('evidence', []):
                source = selected_refs.get(evidence.get('sourceId')) if isinstance(evidence, dict) else None
                if not source or source['revisionId'] != evidence.get('revisionId'):
                    continue
                if 'blockIds' in source and evidence.get('blockId') not in source['blockIds']:
                    continue
                original = db.execute('SELECT b.text FROM blocks b JOIN revisions r ON r.id=b.revision_id '
                                      'WHERE b.id=? AND b.revision_id=? AND r.source_id=?',
                                      (evidence.get('blockId'), source['revisionId'], source['sourceId'])).fetchone()
                quote, start, end = evidence.get('quote'), evidence.get('start'), evidence.get('end')
                if (not original or not isinstance(quote, str) or not quote or
                        type(start) is not int or type(end) is not int or
                        not 0 <= start < end <= len(original['text']) or original['text'][start:end] != quote):
                    continue
                if evidence.get('id') in section_ids:
                    section_sources.add(source['sourceId'])
                if evidence.get('id') in paragraph_ids:
                    paragraph_sources.add(source['sourceId'])
                    cited_blocks.add(evidence.get('blockId'))
        sources = sorted(selected_refs.values(), key=lambda source: (
            source['sourceId'] not in paragraph_sources, source['sourceId'] not in section_sources, source['sourceId']))
        warnings, items = [], []
        coverage = {'selectedSourceCount': len(sources), 'availableSourceCount': 0, 'savedImageCount': 0,
                    'examinedImageCount': 0, 'candidateCount': 0, 'unsavedImageReferences': 0,
                    'contextUnavailableSourceCount': 0, 'scopeEligibleCount': 0, 'semanticStatus': 'not_configured',
                    'semanticIndexedCount': 0, 'visualReviewedCount': 0, 'visualStatus': 'not_reviewed', 'truncated': False}
        coverage.update(searchScope=search_scope, placementMode=placement_mode,
                        libraryFrozenSourceCount=len(library_sources),
                        libraryFrozenAssetCount=sum(len(s['assetIds']) for s in library_sources),
                        libraryScannedSourceCount=0, libraryScannedAssetCount=0,
                        libraryMissingAssetCount=0, libraryRecalledCount=0, libraryShortlistTruncated=False,
                        librarySizeExcludedCount=0, libraryUnreadableHeaderCount=0,
                        libraryEligibleAssetCount=0, referenceSizeExcludedCount=0)
        clean_target = re.sub(r'\[E\d+\]', '', target)
        query_text = '\n'.join(part for part in (query.strip(), clean_target, section.get('title', '')) if part)[:3000]
        term_groups = [_terms(query_text)]
        if placement_mode == 'chapter':
            clean_parts = [re.sub(r'\[E\d+\]', '', part) for part in paragraphs]
            # Full-chapter lexical recall includes every token. Each paragraph
            # has its own score so a late, short subject is not diluted away.
            term_groups = [_terms(query.strip() + '\n' + section.get('title', ''), None)]
            term_groups.extend(_terms(part, None) for part in clean_parts)
            body_query, represented = _balanced_text(clean_parts, 1800)
            header = (query.strip() + '\n' + section.get('title', '')).strip()[:350]
            query_text = '\n'.join(part for part in (header, body_query) if part)
            coverage.update(lexicalParagraphCount=len(paragraphs), lexicalBodyChars=len(clean_target),
                            semanticQueryParagraphCount=len(paragraphs), semanticQueryParagraphsRepresented=represented,
                            semanticQueryBodyChars=len(body_query), semanticQuerySourceChars=len(clean_target),
                            semanticQueryTruncated=len(body_query) < len('\n'.join(clean_parts)))
            if coverage['semanticQueryTruncated'] and embedding_config:
                warnings.append('章节较长，语义匹配使用各段的部分文字；文字匹配仍覆盖完整章节。')
        asset_rows, library_items = {}, []
        for source in [*sources, *library_sources]:
            _check_guard(request_guard)
            is_library = 'assetIds' in source and source['sourceId'] not in selected_refs
            data = self._source_contexts(source)
            if data is None:
                warnings.append('部分固定版本资料已不可访问，未使用其他版本替代。')
                if is_library:
                    coverage['libraryMissingAssetCount'] += len(source['assetIds'])
                continue
            revision, assets, blocks, translated, contexts, manifest = data
            if selected_asset_id is not None:
                assets = [asset for asset in assets if asset['id'] == selected_asset_id]
            if is_library:
                wanted = set(source['assetIds'])
                assets = [asset for asset in assets if asset['id'] in wanted]
                coverage['libraryScannedSourceCount'] += 1
                coverage['libraryMissingAssetCount'] += len(wanted) - len(assets)
                coverage['libraryScannedAssetCount'] += len(assets)
                # Apply the existing library size rule before the finite text
                # shortlist. Probe only headers when immutable dimensions have
                # not yet been cached; no image decode or model is involved.
                with self.store.connect() as db:
                    rows = db.execute('SELECT a.blob_path,d.width,d.height FROM assets a '
                                      'LEFT JOIN image_dimensions d ON d.blob_path=a.blob_path '
                                      'WHERE a.revision_id=? AND a.id IN (SELECT value FROM json_each(?))',
                                      (source['revisionId'], dumps(source['assetIds']))).fetchall()
                    dimensions = self.store._asset_dimensions(db, rows)
                coverage['libraryUnreadableHeaderCount'] += sum(
                    not all(dimensions[asset['blob_path']]) for asset in assets)
                coverage['librarySizeExcludedCount'] += sum(
                    bool(all(dimensions[asset['blob_path']])) and not library_image_eligible(*dimensions[asset['blob_path']])
                    for asset in assets)
                assets = [asset for asset in assets if library_image_eligible(*dimensions[asset['blob_path']])]
                coverage['libraryEligibleAssetCount'] += len(assets)
            else:
                coverage['availableSourceCount'] += 1
                coverage['savedImageCount'] += len(assets)
            coverage['unsavedImageReferences'] += manifest.get('unsavedImageReferences') or 0
            coverage['contextUnavailableSourceCount'] += not manifest.get('contextAvailable', False)
            allowed = set(source['blockIds']) if 'blockIds' in source else None
            for asset in assets:
                if not is_library and len(items) >= MAX_ASSETS:
                    coverage['truncated'] = True
                    break
                context = contexts[asset['id']]
                caption_ids = [bid for bid in context['captionBlockIds'] if bid in blocks]
                reference_ids = [bid for bid in context.get('referenceBlockIds', []) if bid in blocks]
                near_ids = [bid for bid in context['nearbyBlockIds'] if bid in blocks]
                scope_ids = caption_ids if caption_ids else [bid for bid in reference_ids if allowed is None or bid in allowed]
                eligible = allowed is None or bool(context['explicitAssociation'] and scope_ids and set(scope_ids) <= allowed)
                original_caption = '\n'.join(blocks[bid]['text'] for bid in caption_ids)
                chinese_caption = '\n'.join(translated.get(bid, blocks[bid]['text']) for bid in caption_ids)
                display_caption = chinese_caption or original_caption
                alt_text = asset['alt'] or chinese_caption or ''
                allowed_near = list(dict.fromkeys(bid for bid in [*reference_ids, *near_ids]
                                                 if allowed is None or bid in allowed))
                contextual = '\n'.join(blocks[bid]['text'] for bid in allowed_near)[:1200]
                localized_context = '\n'.join(translated[bid] for bid in allowed_near if bid in translated)[:1200]
                asset_hash, width, height, problem = '', 0, 0, ''
                if not is_library:
                    try:
                        asset_hash, width, height, problem = self._asset_facts(asset)
                    except (OSError, KeyError, ValueError):
                        problem = '图片文件不可用'
                    if search_scope == 'library' and not problem and not library_image_eligible(width, height):
                        problem = '图片尺寸不足以用于正文配图'
                        coverage['referenceSizeExcludedCount'] += 1
                issues = [problem] if problem else []
                if not original_caption:
                    issues.append('缺少可定位的原图注，图片身份与语境需人工确认')
                elif not _caption_has_description(original_caption):
                    issues.append('原图注仅标明图片来源，画面身份与语境需人工确认')
                if len(display_caption) > 2000 or len(alt_text) > 2000:
                    issues.append('图注或替代文字较长，请完整核对后缩写并人工确认')
                if width and height and (min(width, height) < 160 or max(width, height) > min(width, height) * 6):
                    issues.append('图片尺寸或比例可能影响正文可读性，请检查原图')
                if not eligible:
                    issues.append('图片缺少位于已选段落范围内的明确图注或原文指向，不能直接采纳')
                source_rank = 4 if source['sourceId'] in paragraph_sources else 2 if source['sourceId'] in section_sources else 1
                reasons = ['来自本段引用资料' if source_rank == 4 else '来自本章引用资料' if source_rank == 2 else '来自本稿固定参考资料']
                if search_scope == 'library':
                    source_rank /= 10
                if is_library:
                    source_rank, reasons = 0, ['来自图库其他资料']
                    issues.append('来自其他资料，仅可人工确认配图，不能作为正文依据')
                caption_text = '\n'.join([original_caption, chinese_caption, asset['alt']])
                lexical = max((_lexical(caption_text, terms) * 3 + _lexical(contextual + localized_context, terms)
                               for terms in term_groups if terms), default=0)
                if is_library:
                    # A title can help find an uncaptioned occurrence, but it
                    # cannot identify the pictured subject or authorize use.
                    lexical += max((_lexical(revision['title'], terms) * .15
                                    for terms in term_groups if terms), default=0)
                if lexical:
                    reasons.append('图注或邻近原文与目标文字匹配')
                if cited_blocks.intersection(scope_ids):
                    source_rank += .2 if search_scope == 'library' else 2
                    reasons.append('图片图注或明确指向就是本段引用原文')
                # Bounded, occurrence-specific text. Titles are weak context and
                # an unrelated full article is never copied into every image.
                search_text = '\n'.join(part for part in (original_caption, chinese_caption, asset['alt'][:400],
                    contextual, localized_context) if part)[:3600]
                item = {'assetId': asset['id'], 'assetHash': asset_hash, 'sourceId': source['sourceId'],
                        'sourceType': 'library' if is_library else 'reference',
                        'revisionId': source['revisionId'], 'url': '/api/assets/' + asset['id'],
                        'sourceTitle': revision['title'], 'originalCaption': original_caption,
                        'caption': display_caption, 'altText': alt_text,
                        'captionBlockIds': caption_ids, 'scopeBlockIds': scope_ids,
                        'referenceBlockIds': reference_ids, 'contextBlockIds': list(dict.fromkeys([*caption_ids, *allowed_near])),
                        'scopeEligible': eligible, 'scopeReason': '图库固定版本图片，仅供配图参考' if is_library else '完整固定版本资料' if allowed is None else
                        '明确图注或原文指向位于已选范围' if eligible else '图注或明确原文指向不在已选范围',
                        'relation': context['relation'], 'rankReasons': reasons, 'status': 'unsuitable' if problem else 'needs_review',
                        'visualStatus': 'not_reviewed', 'issues': issues, 'width': width, 'height': height,
                        '_rank': source_rank + lexical, '_searchText': search_text, '_contextText': contextual}
                if quality_policy is not None:
                    item['qualityGate'] = {'policy': quality_policy, 'passed': False,
                                           'reason': '尚未完成图片与本段的完整核对' if eligible else
                                                     '图片不在本稿已确认的资料范围内'}
                if is_library:
                    library_items.append(item)
                    asset_rows[asset['id']] = asset
                    # Keep a global top set while still scanning every frozen
                    # occurrence. Large libraries do not retain all captions,
                    # context text and asset rows in memory until the end.
                    if len(library_items) >= MAX_LIBRARY_CANDIDATES * 3:
                        library_items = _library_diverse(library_items, MAX_LIBRARY_CANDIDATES, reservoir=True)
                        asset_rows = {item['assetId']: asset_rows[item['assetId']] for item in library_items}
                else:
                    items.append(item)
        coverage['referenceExaminedImageCount'] = len(items)
        coverage['libraryShortlistTruncated'] = coverage['libraryEligibleAssetCount'] > MAX_LIBRARY_CANDIDATES
        library_items = _library_diverse(library_items, MAX_LIBRARY_CANDIDATES)
        for item in library_items:
            _check_guard(request_guard)
            try:
                asset_hash, width, height, problem = self._asset_facts(asset_rows[item['assetId']])
            except (OSError, KeyError, ValueError):
                asset_hash, width, height, problem = '', 0, 0, '图片文件不可用'
            item.update(assetHash=asset_hash, width=width, height=height)
            if problem:
                item['status'] = 'unsuitable'
                item['issues'].append(problem)
            elif min(width, height) < 160 or max(width, height) > min(width, height) * 6:
                item['issues'].append('图片尺寸或比例可能影响正文可读性，请检查原图')
        coverage['libraryRecalledCount'] = len(library_items)
        coverage['libraryShortlistSourceCount'] = len({item['sourceId'] for item in library_items})
        if coverage['libraryShortlistTruncated']:
            warnings.append('已完成图库文字匹配，仅对部分较相关图片继续核对。')
        if coverage['libraryMissingAssetCount']:
            warnings.append('本次固定图库范围中有图片不可访问，未使用新增图片替代。')
        if coverage['librarySizeExcludedCount'] or coverage['referenceSizeExcludedCount']:
            warnings.append('已排除尺寸过小、不适合正文配图的图片。')
        if coverage['libraryUnreadableHeaderCount']:
            warnings.append('部分图库图片无法读取尺寸，未列入配图候选。')
        items.extend(library_items)
        coverage['examinedImageCount'] = coverage['referenceExaminedImageCount'] + coverage['libraryScannedAssetCount']
        coverage['scopeEligibleCount'] = sum(item['scopeEligible'] for item in items)
        if coverage['unsavedImageReferences']:
            warnings.append('原始快照中有图片引用未形成已保存素材，其中可能包含页面装饰图。')
        if coverage['truncated']:
            warnings.append('本次仅查看了部分参考图片，其余图片尚未检查。')
        items.sort(key=_rank_key)
        self._semantic(items, query_text, embedding_config, coverage, warnings, request_guard, embed_call)
        items.sort(key=_rank_key)
        # Fold only presentation duplicates. The winning occurrence retains its
        # own caption/scope; all other source associations remain inspectable.
        groups, candidates = {}, []
        for item in items:
            identity = item['assetHash'] or item['assetId']
            if identity in groups:
                groups[identity]['otherOccurrences'].append({key: item[key] for key in
                    ('assetId', 'sourceId', 'revisionId', 'sourceType', 'sourceTitle', 'originalCaption', 'scopeEligible', 'scopeReason')})
            else:
                item['otherOccurrences'] = []
                groups[identity] = item
                candidates.append(item)
        coverage['duplicateOccurrencesCollapsed'] = len(items) - len(candidates)
        coverage['resultLimitReached'] = len(candidates) > MAX_RESULTS
        candidates = _bounded_candidates(candidates, MAX_RESULTS)
        if placement_mode == 'chapter':
            coverage['visualStatus'] = 'deferred_to_placement'
        else:
            self._visual(candidates, target, model_config, vision_call, coverage, warnings, request_guard, vision_system)
        candidates.sort(key=lambda item: ({'recommended': 0, 'needs_review': 1, 'unsuitable': 2}[item['status']],
                                          not item['scopeEligible'], -item['_rank'], item['sourceType'] == 'library', item['assetId']))
        coverage['candidateCount'] = len(candidates)
        if quality_policy is not None and placement_mode == 'paragraph':
            coverage['qualityPassedCount'] = sum(item['qualityGate']['passed'] is True for item in candidates)
            if not coverage['qualityPassedCount']:
                warnings.append('本次未找到已确认合适的配图，建议保留纯文字。')
        elif not candidates:
            warnings.append('本次检索范围中没有可用图片，可保留纯文字。')
        for item in candidates:
            for key in [key for key in item if key.startswith('_')]:
                if not preserve_context or key not in ('_contextText', '_searchText'):
                    del item[key]
        return {'candidates': candidates, 'coverage': coverage, 'warnings': list(dict.fromkeys(warnings))}
