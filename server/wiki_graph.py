"""Read-only graph projections over immutable, evidence-bound Wiki pages.

Navigation is a group membership operation, never an entity identity merge.
These helpers only decorate copies loaded by WikiService; they do not publish
pages, change stored evidence or rewrite historical versions.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .store import dumps


EDGE_FIELDS = ('layer', 'origin', 'supportKind', 'derivedBy', 'stale')


def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def normalized(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip().casefold()


def page_order(page):
    return (normalized(page['label']), page['type'], page.get('subtype', ''), page['sourceId'], page['id'])


def edge_metadata(edge, stale=False):
    if edge.get('derivedBy') == 'exact_name_and_definition' or edge.get('layer') == 'navigation':
        layer, origin, support = 'navigation', 'program_equivalence', 'wording_equivalence'
    elif edge.get('type') == 'source' or edge.get('layer') == 'provenance':
        layer, origin, support = 'provenance', 'program_source', 'source_reference'
    else:
        layer, origin, support = 'semantic', 'model_extraction', 'cited_original'
    return {'layer': layer, 'origin': origin, 'supportKind': support,
            'derivedBy': edge.get('derivedBy'),
            'stale': bool(stale or edge.get('stale') or any(ref.get('stale') for ref in edge.get('evidence', [])))}


def relation_endpoints(owner_id, relation):
    target_id = relation['targetId']
    return (owner_id, target_id) if relation.get('direction') == 'outgoing' else (target_id, owner_id)


def decorate_relation(owner, relation, target=None):
    source, target_id = relation_endpoints(owner['id'], relation)
    return {**relation, 'from': source, 'to': target_id,
            **edge_metadata(relation, owner.get('stale', False) or bool(target and target.get('stale')))}


def unique_evidence(items):
    result, seen = [], set()
    for item in items:
        key = (item['revisionId'], item['blockId'], item['start'], item['end'])
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def member(page, memberships):
    return {key: page[key] for key in ('id', 'label', 'type', 'sourceId', 'revisionId')} | {
        'sourceTitle': page['evidence'][0]['title'] if page.get('evidence') else page['sourceId'],
        'topicId': next(iter(memberships.get(page['id'], [])), None)}


def navigation_groups(pages, memberships):
    definitions = {}
    for page in pages:
        if page['type'] == 'concept' and not page.get('stale'):
            key = (normalized(page['label']), page.get('subtype', ''), normalized(page['summary']))
            definitions.setdefault(key, []).append(page)
    result = {}
    for key, values in definitions.items():
        if len({page['sourceId'] for page in values}) < 2:
            continue
        values.sort(key=page_order)
        group_id = 'nav_' + digest(['exact_concept_definition', *key])[:32]
        result[group_id] = {'id': group_id, 'label': values[0]['label'], 'kind': 'exact_concept_definition',
                            'memberCount': len(values), 'members': [member(page, memberships) for page in values]}
    return dict(sorted(result.items()))


def group_page(group, offset=0, limit=20):
    total = group['memberCount']
    members = group['members'][offset:offset + limit]
    next_offset = offset + len(members)
    return {**group, 'members': members, 'offset': offset, 'limit': limit,
            'hasMore': next_offset < total, 'nextOffset': next_offset if next_offset < total else None}


def navigation_relation(owner, target):
    left, right = sorted((owner['id'], target['id']))
    first, second = (owner, target) if owner['id'] == left else (target, owner)
    evidence = unique_evidence(first['sections'][0]['claims'][0]['evidence'] + second['sections'][0]['claims'][0]['evidence'])
    relation = {'targetId': target['id'], 'targetLabel': target['label'], 'targetType': target['type'],
                'label': '跨来源同定义概念', 'type': 'association',
                'direction': 'outgoing' if owner['id'] == left else 'incoming',
                'edgeId': 'edge_' + digest([left, right, 'exact_concept_definition'])[:32],
                'evidence': evidence, 'derivedBy': 'exact_name_and_definition'}
    return decorate_relation(owner, relation, target)


class GraphProjection:
    def __init__(self, pages, memberships):
        self.pages = {page['id']: page for page in pages}
        self.memberships = memberships
        self.groups = navigation_groups(pages, memberships)
        self.group_ids = {}
        for group_id, group in self.groups.items():
            for item in group['members']:
                self.group_ids.setdefault(item['id'], []).append(group_id)

    def semantic_relations(self, entry_id):
        page = self.pages[entry_id]
        return [decorate_relation(page, relation, self.pages.get(relation['targetId']))
                for relation in page['relations']
                if relation.get('derivedBy') != 'exact_name_and_definition'
                and relation['targetId'] in self.pages]

    def relations(self, entry_id):
        page = self.pages[entry_id]
        values = self.semantic_relations(entry_id)
        for group_id in self.group_ids.get(entry_id, []):
            for item in self.groups[group_id]['members']:
                target = self.pages[item['id']]
                if target['id'] != entry_id and target['sourceId'] != page['sourceId']:
                    values.append(navigation_relation(page, target))
        return sorted(values, key=lambda value: (value['layer'] == 'navigation',
                      page_order(self.pages[value['targetId']]), value['type'], value['label'], value['edgeId']))

    def path_edge(self, owner_id, relation):
        source, target = relation_endpoints(owner_id, relation)
        return {'from': source, 'to': target, 'edgeId': relation['edgeId'],
                'fromLabel': self.pages[source]['label'], 'toLabel': self.pages[target]['label'],
                'type': relation['type'], 'label': relation['label'],
                'traversalDirection': 'forward' if relation['direction'] == 'outgoing' else 'reverse',
                'evidence': relation['evidence'], **{key: relation[key] for key in EDGE_FIELDS}}

    def walk(self, seed_ids, hops, limit=None):
        seeds = list(dict.fromkeys(seed for seed in seed_ids if seed in self.pages))
        visited = set(seeds)
        frontier = [{'id': seed, 'seedId': seed, 'hopCount': 0, 'pathNodeIds': [seed], 'pathEdges': []} for seed in seeds]
        found, truncated = [], False
        for depth in range(hops):
            following = []
            for path in frontier:
                for relation in self.relations(path['id']):
                    target = relation['targetId']
                    if target in visited:
                        continue
                    if limit is not None and len(found) >= limit:
                        truncated = True
                        continue
                    visited.add(target)
                    step = {'id': target, 'seedId': path['seedId'], 'hopCount': depth + 1,
                            'pathNodeIds': path['pathNodeIds'] + [target],
                            'pathEdges': path['pathEdges'] + [self.path_edge(path['id'], relation)]}
                    found.append(step)
                    following.append(step)
            frontier = following
        return found, truncated

    def decorate_pages(self):
        names = {}
        for page in self.pages.values():
            names.setdefault((page['type'], page.get('subtype', ''), normalized(page['label'])), []).append(page)
        for values in names.values():
            if len({page['sourceId'] for page in values}) > 1:
                for page in values:
                    issue = '其他来源存在同名词条；来源页保持独立，人物或实体未按名称自动合并。'
                    if issue not in page['issues']:
                        page['issues'].append(issue)
        for page in self.pages.values():
            page['navigationGroups'] = [group_page(self.groups[group_id]) for group_id in self.group_ids.get(page['id'], [])]
            # The page is a bounded preview. Traversal uses group membership,
            # so this limit never turns a large group into an arbitrary chain.
            semantic = [relation for relation in page['relations'] if relation.get('derivedBy') != 'exact_name_and_definition']
            navigation = []
            for group_id in self.group_ids.get(page['id'], []):
                for item in self.groups[group_id]['members']:
                    if item['id'] == page['id'] or item['sourceId'] == page['sourceId']:
                        continue
                    navigation.append(navigation_relation(page, self.pages[item['id']]))
                    if len(navigation) >= 20:
                        break
            page['relations'] = semantic + navigation

    def node(self, page):
        return {key: page[key] for key in ('id', 'label', 'type', 'summary', 'aliases', 'stale', 'revisionId')} | {
            'sourceId': page['sourceId'], 'subtype': page.get('subtype', ''),
            'topicId': next(iter(self.memberships.get(page['id'], [])), None)}

    def navigation_anchors(self, entry_ids, focus_id=None):
        visible = set(entry_ids)
        anchors = {}
        for group_id, group in self.groups.items():
            members = [item['id'] for item in group['members'] if item['id'] in visible]
            if len(members) > 1:
                anchors[group_id] = focus_id if focus_id in members else members[0]
        return anchors

    def edges_for(self, entry_ids, allowed_ids, navigation_anchors=None):
        edges = {}
        current_ids = set(entry_ids)
        for entry_id in entry_ids:
            page = self.pages[entry_id]
            for relation in self.semantic_relations(entry_id):
                if relation['targetId'] not in allowed_ids:
                    continue
                source, target = relation_endpoints(entry_id, relation)
                edges[relation['edgeId']] = {'id': relation['edgeId'], 'edgeId': relation['edgeId'],
                    'from': source, 'to': target, 'type': relation['type'], 'label': relation['label'],
                    'evidence': relation['evidence'], **{key: relation[key] for key in EDGE_FIELDS}}
            doc_id = 'doc:' + page['sourceId']
            edge_id = 'edge_' + digest([page['id'], doc_id])[:32]
            edge = {'id': edge_id, 'edgeId': edge_id, 'from': page['id'], 'to': doc_id,
                    'type': 'source', 'label': '原文依据', 'evidence': page['evidence']}
            edges[edge_id] = {**edge, **edge_metadata(edge, page['stale'])}
        # A group is rendered as a sparse star, not a clique. Traversal still
        # visits every member directly via relations(), independent of this
        # visual projection. A neighborhood reuses its full-scope anchors on
        # every page, allowing mergeEdges to complete the same sparse graph.
        anchors = navigation_anchors if navigation_anchors is not None else self.navigation_anchors(allowed_ids)
        for group_id, anchor in anchors.items():
            if anchor not in allowed_ids:
                continue
            for item in self.groups[group_id]['members']:
                target = item['id']
                if target == anchor or target not in allowed_ids or not ({anchor, target} & current_ids):
                    continue
                relation = navigation_relation(self.pages[anchor], self.pages[target])
                source, destination = relation_endpoints(anchor, relation)
                edges[relation['edgeId']] = {'id': relation['edgeId'], 'edgeId': relation['edgeId'],
                    'from': source, 'to': destination, 'type': relation['type'], 'label': relation['label'],
                    'evidence': relation['evidence'], **{key: relation[key] for key in EDGE_FIELDS}}
        return sorted(edges.values(), key=lambda edge: (edge['from'], edge['to'], edge['type'], edge['id']))

    def render(self, entry_ids, navigation_anchors=None):
        ids = list(dict.fromkeys(entry_ids))
        nodes = [self.node(self.pages[entry_id]) for entry_id in ids]
        docs = {}
        for entry_id in ids:
            page = self.pages[entry_id]
            doc_id = 'doc:' + page['sourceId']
            docs.setdefault(doc_id, {'id': doc_id, 'label': page['evidence'][0]['title'], 'type': 'document', 'subtype': '',
                'summary': 'Wiki 的原始来源文档', 'aliases': [], 'documentId': page['sourceId'],
                'sourceId': page['sourceId'], 'revisionId': page['revisionId'], 'stale': page['stale']})
        nodes.extend(docs[key] for key in sorted(docs))
        edges = self.edges_for(ids, set(ids), navigation_anchors)
        return nodes, edges


def neighborhood_plan(root, candidates, pages, limit):
    """Pack stable pages without losing candidates skipped for source cost.

    Every page includes root and its original document. Offsets count emitted
    non-root entries in this deterministic plan, not positions before packing.
    """
    remaining, offset, result = list(candidates), 0, []
    if not remaining:
        return [(0, [])]
    while remaining:
        chosen, deferred = [], []
        sources = {pages[root]['sourceId']}
        for entry_id in remaining:
            future_sources = sources | {pages[entry_id]['sourceId']}
            if 1 + len(chosen) + 1 + len(future_sources) > limit:
                deferred.append(entry_id)
                continue
            chosen.append(entry_id)
            sources = future_sources
        if not chosen:
            raise ValueError('Wiki 邻域节点预算不足，请将 limit 提高至至少 4')
        result.append((offset, chosen))
        offset += len(chosen)
        remaining = deferred
    return result
