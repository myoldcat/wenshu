"""Canonical graph views without changing the source-scoped Wiki publication API."""
from __future__ import annotations

from collections import deque
import hashlib
import json

from .store import dumps, ident, now, tokenize
from .wiki_graph import EDGE_FIELDS, edge_metadata, normalized, unique_evidence
from .wiki_topics import build_topics
from .graph_analytics import GraphAnalytics, GraphSnapshotConflict, validate_scope


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


class KnowledgeConflict(ValueError):
    pass


class WikiKnowledgeService:
    def __init__(self, wiki, canonical=None):
        from .wiki_canonical import WikiCanonicalService
        self.wiki, self.store = wiki, wiki.store
        self.canonical = canonical or WikiCanonicalService(self.store)
        self._graph_analytics = GraphAnalytics()
        with self.store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS wiki_knowledge_links(
                id TEXT PRIMARY KEY,entry_ids_json TEXT NOT NULL,decision_id TEXT NOT NULL,
                label TEXT NOT NULL,created_at TEXT NOT NULL);
            ''')

    def _state(self, readonly=False):
        sources, pages, jobs = self.wiki._snapshot()
        canonical = self.canonical.snapshot(pages, readonly=readonly)
        topics, index = build_topics(sources, pages, canonical['entryToCanonical'])
        with self.store.connect() as db:
            links = [dict(r) for r in db.execute('SELECT * FROM wiki_knowledge_links ORDER BY id')]
        token = digest(['knowledge-1', canonical['snapshotId'],
                        sorted((key, group.get('version', 1)) for key, group in canonical['groups'].items()),
                        sorted((s, v.get('classification_paths_json', '[]'), v.get('tags_json', '[]')) for s, v in sources.items()), links])
        return {'sources': sources, 'pages': pages, 'jobs': jobs, 'canonical': canonical,
                'topics': topics, 'topicIndex': index, 'links': links, 'snapshotId': token}

    @staticmethod
    def _scope(state, topic_id):
        if topic_id is None:
            return None
        topic = state['topicIndex'].get(topic_id)
        if topic is None:
            raise KeyError(topic_id)
        return set(topic['sourceIds'])

    @staticmethod
    def _members(group, allowed):
        return [p for p in group['members'] if allowed is None or p['sourceId'] in allowed]

    @staticmethod
    def _base_page(group, members):
        first = members[0]
        evidence = unique_evidence([e for p in members for e in p.get('evidence', [])])
        sections = []
        for page in members:
            title = page.get('evidence', [{}])[0].get('title', page['sourceId']) if page.get('evidence') else page['sourceId']
            for section in page.get('sections', []):
                sections.append({'title': section['title'] if len(members) == 1 else title + ' · ' + section['title'],
                                 'claims': section['claims'], 'sourceEntryId': page['id'], 'sourceId': page['sourceId']})
        return {'id': group['id'], 'label': group['label'], 'type': group['type'], 'subtype': group.get('subtype', ''),
                'summary': first.get('summary', ''), 'aliases': sorted({name for p in members
                for name in [p['label'], *p.get('aliases', [])] if normalized(name) != normalized(group['label'])}),
                'sections': sections, 'evidence': evidence, 'relations': [], 'navigationGroups': [],
                'issues': list(dict.fromkeys(issue for p in members for issue in p.get('issues', [])
                    if '其他来源存在同名词条' not in issue)),
                'sourceId': first['sourceId'], 'sourceIds': sorted({p['sourceId'] for p in members}),
                'revisionId': first['revisionId'], 'stale': any(p.get('stale') for p in members),
                'version': group.get('version', 1), 'updatedAt': max(p.get('updatedAt', '') for p in members),
                'members': [{'id': p['id'], 'sourceEntryId': p['id'], 'label': p['label'], 'type': p['type'],
                    'sourceId': p['sourceId'], 'revisionId': p['revisionId'], 'version': p['version'],
                    'sourceTitle': p['evidence'][0].get('title', p['sourceId']) if p.get('evidence') else p['sourceId'],
                    'stale': bool(p.get('stale'))} for p in members],
                'memberCount': len(members), 'sourceCount': len({p['sourceId'] for p in members}),
                'allMemberCount': len(group['members']), 'history': [], 'archived': False,
                'provenance': {'identityScope': 'knowledge', 'evidenceType': 'original_blocks', 'model': None}}

    def _view(self, state, topic_id=None):
        allowed = self._scope(state, topic_id)
        result = {}
        for key, group in state['canonical']['groups'].items():
            members = self._members(group, allowed)
            if not members:
                continue
            page = self._base_page(group, members)
            # A full-knowledge synthesis may contain out-of-scope statements.
            # A scoped view uses only the contributing source statements.
            if len(members) == len(group['members']):
                synthesis = group.get('synthesis')
                if isinstance(synthesis, dict) and synthesis.get('mode') != 'source_claims' and not synthesis.get('stale', False):
                    content = synthesis.get('content', synthesis)
                    if content.get('sections'):
                        page.update({k: content[k] for k in ('summary', 'sections', 'issues') if k in content})
                        page['evidence'] = unique_evidence([r for s in page['sections'] for c in s['claims'] for r in c['evidence']])
            if len(members) > 1:
                page['issues'].append('多来源陈述保留各自证据，时间、适用范围和结论差异请结合原文核对。')
            page['scopeLimited'] = len(members) != len(group['members'])
            result[key] = page
        return result

    def _edges(self, state, pages, allowed=None):
        mapping = state['canonical']['entryToCanonical']
        edges = {}
        for source_page in state['pages']:
            if allowed is not None and source_page['sourceId'] not in allowed:
                continue
            for relation in source_page.get('relations', []):
                if relation.get('layer') == 'navigation' or relation.get('derivedBy') == 'exact_name_and_definition':
                    continue
                source_id, target_id = source_page['id'], relation['targetId']
                if relation.get('direction') != 'outgoing':
                    source_id, target_id = target_id, source_id
                left, right = mapping.get(source_id), mapping.get(target_id)
                if not left or not right or left == right or left not in pages or right not in pages:
                    continue
                key = (left, right, relation['type'], relation['label'])
                if key not in edges:
                    edge_id = 'knowledge_edge_' + digest(key)[:24]
                    edges[key] = {'id': edge_id, 'edgeId': edge_id, 'from': left, 'to': right,
                        'type': relation['type'], 'label': relation['label'], 'evidence': [], 'contributions': [],
                        **edge_metadata(relation, source_page.get('stale', False))}
                edge = edges[key]
                edge['evidence'] = unique_evidence(edge['evidence'] + relation.get('evidence', []))
                edge['stale'] = edge['stale'] or source_page.get('stale', False)
                contribution = {'sourceEntryId': source_page['id'], 'edgeId': relation.get('edgeId'),
                                'sourceId': source_page['sourceId'], 'revisionId': source_page['revisionId']}
                if contribution not in edge['contributions']:
                    edge['contributions'].append(contribution)
        decisions = {d['id']: d for d in self.canonical.decisions()}
        for link in state['links']:
            decision = decisions.get(link['decision_id'])
            if not decision or decision.get('undone'):
                continue
            ids = sorted({mapping.get(e) for e in json.loads(link['entry_ids_json'])} - {None})
            for i in range(1, len(ids)):
                left, right = ids[0], ids[i]
                if left not in pages or right not in pages:
                    continue
                # Navigation must coexist with an evidence-bound semantic
                # relation with the same endpoints, type and visible label.
                # Keep semantic keys/IDs and persisted evidence unchanged.
                key = ('navigation', left, right, 'association', link['label'])
                edge_id = 'knowledge_link_' + digest([link['id'], left, right])[:24]
                edges[key] = {'id': edge_id, 'edgeId': edge_id, 'from': left, 'to': right,
                    'type': 'association', 'label': link['label'], 'evidence': [],
                    'layer': 'navigation', 'origin': 'manual_decision', 'supportKind': 'manual_related',
                    'derivedBy': 'manual_related', 'stale': False, 'decisionId': decision['id']}
        return sorted(edges.values(), key=lambda e: (e['from'], e['to'], e['type'], e['label']))

    @staticmethod
    def _pack(ids, pages, limit, mandatory=None):
        chosen, docs = [], set()
        if mandatory:
            chosen.append(mandatory)
            docs.update(pages[mandatory]['sourceIds'])
        for key in ids:
            if key in chosen:
                continue
            future = docs | set(pages[key]['sourceIds'])
            if len(chosen) + 1 + len(future) <= limit:
                chosen.append(key)
                docs = future
        return chosen

    @staticmethod
    def _render(chosen, pages, edges, limit):
        ids = set(chosen)
        nodes = [{key: page[key] for key in ('id', 'label', 'type', 'subtype', 'summary', 'aliases',
                  'sourceId', 'sourceIds', 'revisionId', 'stale', 'memberCount', 'sourceCount', 'allMemberCount')}
                 | {'inScope': True} for page in (pages[key] for key in chosen)]
        docs = {}
        provenance = []
        for key in chosen:
            page = pages[key]
            for member in page['members']:
                doc_id = 'doc:' + member['sourceId']
                if doc_id not in docs and len(nodes) + len(docs) < limit:
                    docs[doc_id] = {'id': doc_id, 'label': member['sourceTitle'], 'type': 'document',
                        'subtype': '', 'summary': '统一词条的原始来源文档', 'aliases': [],
                        'documentId': member['sourceId'], 'sourceId': member['sourceId'],
                        'revisionId': member['revisionId'], 'stale': member['stale'], 'inScope': True}
                if doc_id in docs:
                    edge_id = 'knowledge_source_' + digest([key, doc_id])[:24]
                    if any(e['id'] == edge_id for e in provenance):
                        continue
                    refs = [r for r in page['evidence'] if r['sourceId'] == member['sourceId']]
                    provenance.append({'id': edge_id, 'edgeId': edge_id, 'from': key, 'to': doc_id,
                        'type': 'source', 'label': '原文依据', 'evidence': refs,
                        'layer': 'provenance', 'origin': 'program_source', 'supportKind': 'source_reference',
                        'derivedBy': None, 'stale': member['stale']})
        nodes.extend(docs[key] for key in sorted(docs))
        semantic = [e for e in edges if e['from'] in ids and e['to'] in ids]
        all_sources = {s for key in chosen for s in pages[key]['sourceIds']}
        return nodes, semantic + provenance, len(docs) < len(all_sources)

    def overview(self, topic_id=None, limit=100):
        state = self._state()
        selected = topic_id if topic_id in state['topicIndex'] else state['topics'][0]['id'] if state['topics'] else None
        pages = self._view(state, selected)
        ids = sorted(pages, key=lambda key: (normalized(pages[key]['label']), key))
        chosen = self._pack(ids, pages, limit)
        if ids and not chosen:
            chosen = [ids[0]]
        edges = self._edges(state, pages, self._scope(state, selected))
        nodes, edges, provenance_truncated = self._render(chosen, pages, edges, limit)
        built = {p['sourceId'] for p in state['pages']}
        stale = {p['sourceId'] for p in state['pages'] if p.get('stale')}
        unclear = {source_id for source_id, source in state['sources'].items() if source.get('wikiEmpty')}
        return {'topics': state['topics'], 'topicId': selected, 'nodes': nodes, 'edges': edges,
                'snapshotId': state['snapshotId'], 'canonicalSnapshotId': state['canonical']['snapshotId'],
                'truncated': len(chosen) < len(ids) or provenance_truncated,
                'truncation': {'nodeBudget': len(chosen) < len(ids), 'provenance': provenance_truncated},
                'jobs': state['jobs'], 'mode': 'unified', 'stats': {'pages': len(state['canonical']['groups']),
                'sourcePages': len(state['pages']), 'sources': len(built), 'pending': len(set(state['sources']) - built - unclear) + len(stale - unclear),
                'stale': len(stale)}}

    def analytics(self, scope='topic', topic_id=None, snapshot_id=None):
        validate_scope(scope, topic_id)
        try:
            state = self._state(readonly=True)
        except ValueError as exc:
            # A worker can publish between reading source pages and checking
            # canonical bindings. Retry that read once; never return a mixed
            # graph or map an actual snapshot race to a validation error.
            if not str(exc).startswith(('Wiki 来源已更新', 'Wiki 来源版本已更新')):
                raise
            try:
                state = self._state(readonly=True)
            except ValueError as retry_exc:
                if not str(retry_exc).startswith(('Wiki 来源已更新', 'Wiki 来源版本已更新')):
                    raise
                raise GraphSnapshotConflict(None) from None
        if snapshot_id is not None and snapshot_id != state['snapshotId']:
            raise GraphSnapshotConflict(state['snapshotId'])
        if scope == 'topic':
            self._scope(state, topic_id)  # Invalid IDs never silently fall back.
        # Keep fresh contributions to a canonical identity when a different
        # member is stale. Stale members cannot contribute nodes or edges.
        fresh_pages = [page for page in state['pages'] if not page.get('stale') and not page.get('archived')]
        groups = {}
        for key, group in state['canonical']['groups'].items():
            members = [page for page in group['members'] if not page.get('stale') and not page.get('archived')]
            if members:
                # A cached synthesis can include a removed stale contributor;
                # only source claims participate in an analytics projection.
                groups[key] = {**group, 'members': members, 'synthesis': None}
        projection = {**state, 'pages': fresh_pages, 'canonical': {**state['canonical'], 'groups': groups}}
        pages = self._view(projection, topic_id if scope == 'topic' else None)
        for page in pages.values():
            # Global ranking navigation must open a scope that actually owns
            # a fresh contribution. Visible topics have deterministic order.
            page['topicId'] = topic_id if scope == 'topic' else next((topic['id'] for topic in state['topics']
                if set(page['sourceIds']).intersection(topic['sourceIds'])), None)
        edges = self._edges(projection, pages, self._scope(state, topic_id) if scope == 'topic' else None)
        return self._graph_analytics.analyze(mode='unified', snapshot_id=state['snapshotId'],
            nodes=list(pages.values()), edges=edges, scope=scope, topic_id=topic_id, expected_snapshot=snapshot_id)

    def page(self, canonical_id, version=None, topic_id=None):
        state = self._state()
        if version is not None:
            historical = self.canonical.page(canonical_id, state['pages'], version=version)
            members = self._members(historical, self._scope(state, topic_id))
            if not members:
                raise KeyError(canonical_id)
            result = self._base_page(historical, members)
            synthesis = historical.get('synthesis', {})
            if len(members) == len(historical['members']) and synthesis.get('mode') == 'grounded_synthesis':
                result.update({k: synthesis[k] for k in ('summary', 'sections', 'issues') if k in synthesis})
            result.update(history=historical.get('history', []), version=historical.get('version', version),
                          archived=historical.get('archived', True), snapshotId=state['snapshotId'])
            return result
        pages = self._view(state, topic_id)
        if canonical_id not in pages:
            historical = self.canonical.page(canonical_id, state['pages'])
            if historical.get('redirectId'):
                result = self.page(historical['redirectId'], topic_id=topic_id)
                result['redirectedFrom'] = canonical_id
                return result
            members = self._members(historical, self._scope(state, topic_id))
            if not members:
                raise KeyError(canonical_id)
            return {**self._base_page(historical, members), 'archived': True,
                    'history': historical.get('history', []), 'snapshotId': state['snapshotId']}
        result = pages[canonical_id]
        historical = self.canonical.page(canonical_id, state['pages'])
        result['history'] = historical.get('history', [])
        result['version'] = historical.get('version', result['version'])
        result['provenance'].update(historical.get('provenance', {}))
        edges = self._edges(state, pages, self._scope(state, topic_id))
        for edge in edges:
            if canonical_id not in (edge['from'], edge['to']):
                continue
            target = edge['to'] if edge['from'] == canonical_id else edge['from']
            result['relations'].append({**edge, 'targetId': target, 'targetLabel': pages[target]['label'],
                'targetType': pages[target]['type'], 'direction': 'outgoing' if edge['from'] == canonical_id else 'incoming'})
        result['snapshotId'] = state['snapshotId']
        result['decisions'] = self.decisions(state)
        return result

    @staticmethod
    def _walk(root_ids, pages, edges, hops, limit=None):
        adjacency = {}
        for edge in edges:
            adjacency.setdefault(edge['from'], []).append((edge['to'], edge, 'forward'))
            adjacency.setdefault(edge['to'], []).append((edge['from'], edge, 'reverse'))
        visited = set(root_ids)
        queue = deque({'id': key, 'seedId': key, 'hopCount': 0, 'pathNodeIds': [key], 'pathEdges': []}
                      for key in root_ids)
        found, truncated = [], False
        while queue:
            path = queue.popleft()
            if path['hopCount'] >= hops:
                continue
            for target, edge, direction in adjacency.get(path['id'], []):
                if target in visited:
                    continue
                if limit is not None and len(found) >= limit:
                    truncated = True
                    continue
                visited.add(target)
                step = {**edge, 'fromLabel': pages[edge['from']]['label'], 'toLabel': pages[edge['to']]['label'],
                        'traversalDirection': direction}
                candidate = {'id': target, 'seedId': path['seedId'], 'hopCount': path['hopCount'] + 1,
                             'pathNodeIds': path['pathNodeIds'] + [target], 'pathEdges': path['pathEdges'] + [step]}
                found.append(candidate)
                queue.append(candidate)
        return found, truncated

    def neighborhood(self, entry_id, hops=1, limit=100, offset=0, snapshot_id=None, topic_id=None):
        state = self._state()
        pages = self._view(state, topic_id)
        token = digest([state['snapshotId'], entry_id, hops, limit, topic_id])
        base = {'rootId': entry_id, 'focusId': entry_id, 'snapshotId': token, 'graphSnapshotId': state['snapshotId'],
                'offset': offset, 'limit': limit,
                'scope': {'kind': 'neighborhood', 'entryId': entry_id, 'hops': hops, 'topicIds': [topic_id] if topic_id else []}}
        if entry_id not in pages:
            return {**base, 'nodes': [], 'edges': [], 'mergeEdges': [], 'requiredNodeIds': [], 'totalCount': 0,
                    'hasMore': False, 'nextOffset': None, 'reloadRequired': True, 'reason': 'focus_unavailable',
                    'truncated': False, 'truncation': {'nodeBudget': False}}
        if snapshot_id is not None and snapshot_id != token:
            return {**base, 'nodes': [], 'edges': [], 'mergeEdges': [], 'requiredNodeIds': [], 'totalCount': 0,
                    'hasMore': False, 'nextOffset': None, 'reloadRequired': True, 'reason': 'snapshot_changed',
                    'truncated': False, 'truncation': {'nodeBudget': False}}
        if offset and snapshot_id is None:
            raise ValueError('Wiki 邻域继续加载须提供 snapshot_id')
        edges = self._edges(state, pages, self._scope(state, topic_id))
        paths, _ = self._walk([entry_id], pages, edges, hops)
        candidates = [path['id'] for path in paths]
        plan, remaining, start = [], candidates, 0
        while remaining:
            chosen = self._pack(remaining, pages, limit, mandatory=entry_id)[1:]
            if not chosen:
                # A node with many source documents remains navigable; source
                # chips are complete even when its canvas provenance is capped.
                chosen = [remaining[0]]
            plan.append((start, chosen))
            start += len(chosen)
            used = set(chosen)
            remaining = [key for key in remaining if key not in used]
        plan = plan or [(0, [])]
        page_index = next((i for i, (start, _) in enumerate(plan) if start == offset), None)
        if page_index is None:
            raise ValueError('Wiki 邻域分页位置无效，请使用 nextOffset')
        chosen = [entry_id] + plan[page_index][1]
        nodes, visible_edges, provenance_truncated = self._render(chosen, pages, edges, limit)
        previous = {entry_id} | {key for _, ids in plan[:page_index] for key in ids}
        allowed = previous | set(chosen)
        current = {node['id'] for node in nodes}
        merge_edges = [e for e in edges if e['from'] in allowed and e['to'] in allowed
                       and ({e['from'], e['to']} & set(chosen)) and not {e['from'], e['to']} <= current]
        more = page_index + 1 < len(plan)
        return {**base, 'nodes': nodes, 'edges': visible_edges, 'mergeEdges': merge_edges,
                'requiredNodeIds': sorted({n for e in merge_edges for n in (e['from'], e['to']) if n not in current}),
                'totalCount': len(paths) + 1, 'hasMore': more, 'nextOffset': plan[page_index + 1][0] if more else None,
                'reloadRequired': False, 'truncated': len(plan) > 1 or provenance_truncated,
                'truncation': {'nodeBudget': len(plan) > 1, 'provenance': provenance_truncated},
                'counts': {'entries': len(chosen), 'documents': len(nodes) - len(chosen), 'nodes': len(nodes), 'edges': len(visible_edges)}}

    def search(self, q, limit=20, hops=1, topic_id=None):
        q = self.wiki._query(q)
        state = self._state()
        pages = self._view(state, topic_id)
        allowed = self._scope(state, topic_id)
        tokens = list(dict.fromkeys(tokenize(q).split()))[:20]
        ranked = []
        for key, page in pages.items():
            title = normalized(page['label'] + ' ' + ' '.join(page['aliases']))
            body = normalized(' '.join(c['text'] for s in page['sections'] for c in s['claims']))
            title_tokens, body_tokens = set(tokenize(title).split()), set(tokenize(body).split())
            if normalized(q) not in title + ' ' + body and not (tokens and set(tokens) <= title_tokens | body_tokens):
                continue
            score = 10 * int(normalized(q) in title) + 5 * len(set(tokens) & title_tokens) + len(set(tokens) & body_tokens)
            ranked.append((key, score))
        ranked.sort(key=lambda item: (-item[1], normalized(pages[item[0]]['label']), item[0]))
        results = []
        for key, score in ranked[:limit]:
            page = pages[key]
            claims = [c for s in page['sections'] for c in s['claims']]
            claim = max(claims, key=lambda c: sum(t in normalized(c['text']) for t in tokens), default=None)
            results.append({k: page[k] for k in ('id', 'label', 'type', 'subtype', 'summary', 'stale', 'sourceCount', 'memberCount')}
                           | {'score': score, 'reason': '统一词条全文命中', 'snippet': claim['text'][:300] if claim else '',
                              'evidence': claim['evidence'][:4] if claim else [], 'topicId': topic_id})
        raw = self.wiki.search(q, limit=min(limit, 20), hops=0)
        evidence = [r for r in raw['evidence'] if allowed is None or r['sourceId'] in allowed]
        # Also search original documents independently in the actual topic scope.
        if allowed is not None:
            documents = self.store.search(q, limit=min(limit, 20), source_ids=sorted(allowed))
            for doc in documents['results']:
                if not doc.get('matchBlockId'):
                    continue
                detail = self.store.detail(doc['id'], doc['revisionId'])
                block = next((b for b in detail['blocks'] if b['id'] == doc['matchBlockId']), None)
                if block and block['text']:
                    evidence.append({'sourceId': doc['id'], 'revisionId': doc['revisionId'], 'blockId': block['id'],
                        'quote': block['text'][:300], 'start': 0, 'end': min(300, len(block['text'])), 'title': doc['title'], 'stale': False})
        edges = self._edges(state, pages, allowed)
        paths, graph_truncated = self._walk([r['id'] for r in results], pages, edges, hops, min(40, limit * 2))
        expanded = []
        for path in paths:
            page = pages[path['id']]
            edge = path['pathEdges'][-1]
            expanded.append({**path, **{k: page[k] for k in ('label', 'type', 'subtype', 'stale')},
                'evidence': edge['evidence'], 'reason': str(path['hopCount']) + ' 跳关系路径',
                'topicId': topic_id, **{k: edge.get(k) for k in EDGE_FIELDS},
                'pathNodes': [{'id': key, 'label': pages[key]['label'], 'type': pages[key]['type'], 'topicId': topic_id}
                              for key in path['pathNodeIds']]})
        return {'query': q, 'mode': 'unified', 'results': results, 'evidence': unique_evidence(evidence), 'expanded': expanded,
                'snapshotId': state['snapshotId'], 'truncated': len(ranked) > limit or graph_truncated or raw['truncated'],
                'truncation': {'lexical': len(ranked) > limit, 'graph': graph_truncated, 'raw': raw['truncation']['raw']}}

    def decisions(self, state=None):
        return [{**d, 'undoable': d['kind'] != 'undo' and not d.get('undone', False)} for d in self.canonical.decisions()]

    def mutate(self, action, entry_ids, expected_snapshot, label='', reason='', judgment='distinct', decision_id=None):
        state = self._state()
        if expected_snapshot not in (state['snapshotId'], state['canonical']['snapshotId']):
            raise KnowledgeConflict('图谱已更新，请刷新后重新核对归并范围')
        expected = state['canonical']['snapshotId']
        if action == 'merge':
            result = self.canonical.merge(entry_ids, label, expected, state['pages'], reason=reason)
        elif action == 'reject':
            result = self.canonical.reject(entry_ids, expected, state['pages'], reason=reason)
            if judgment == 'related':
                with self.store.connect() as db:
                    db.execute('INSERT INTO wiki_knowledge_links VALUES(?,?,?,?,?)',
                               (ident('knowledge_link'), dumps(entry_ids), result['decisionId'], '人工关联', now()))
        elif action == 'undo':
            result = self.canonical.undo(decision_id, expected, state['pages'])
        else:
            raise ValueError('未知归并操作')
        return {'snapshotId': self._state()['snapshotId'], 'canonicalSnapshotId': result['snapshotId'],
                'decisionId': result.get('decisionId'), 'decisions': self.decisions()}
