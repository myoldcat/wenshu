"""Display-only Wiki topic scopes derived from complete stored classification paths.

The index retains hidden paths and their exact source scopes. Compaction never
rewrites classifications and never depends on search hits or the canvas budget.
"""
from __future__ import annotations

import hashlib
import json

from .store import dumps


def _digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()[:24]


def build_topics(sources, pages, entry_to_canonical=None):
    entry_to_canonical = entry_to_canonical or {p['id']: p['id'] for p in pages}
    built = {p['sourceId'] for p in pages}
    roots, index = {}, {}
    by_source = {}
    for page in pages:
        by_source.setdefault(page['sourceId'], set()).add(entry_to_canonical[page['id']])

    def new_topic(path):
        topic_id = 'path:' + _digest([n['id'] for n in path])
        return {'id': topic_id, 'label': path[-1]['name'], 'path': path,
                'pathLabel': ' / '.join(n['name'] for n in path), 'sourceIds': set(), 'children': {}}

    for source_id in sorted(built):
        source = sources.get(source_id)
        if not source:
            continue
        raw = source.get('classification_paths_json', '[]')
        paths = json.loads(raw) if isinstance(raw, str) else raw
        valid = [p['nodes'] for p in paths if isinstance(p, dict) and isinstance(p.get('nodes'), list)
                 and len(p['nodes']) == 3 and all(isinstance(n, dict) and isinstance(n.get('id'), str)
                 and n['id'] and isinstance(n.get('name'), str) and n['name'] for n in p['nodes'])]
        if not valid:
            # A historical flat tag does not imply a valid three-level path.
            path = [{'id': 'unclassified', 'name': '未归类'}]
            node = roots.setdefault('unclassified', new_topic(path))
            node['sourceIds'].add(source_id)
            continue
        for path in valid:
            siblings = roots
            for depth, item in enumerate(path):
                node = siblings.setdefault(item['id'], new_topic(path[:depth + 1]))
                node['sourceIds'].add(source_id)
                siblings = node['children']

    def annotate(node):
        node['entryIds'] = set().union(*(by_source.get(s, set()) for s in node['sourceIds']))
        node['sourceCount'] = len(node['sourceIds'])
        node['count'] = len(node['entryIds'])
        # Scope is complete here; it is independent of node/page limits.
        node['viewId'] = 'view:' + _digest([sorted(node['sourceIds']), sorted(node['entryIds'])])
        index[node['id']] = node
        for child in node['children'].values():
            annotate(child)

    for root in roots.values():
        annotate(root)

    def compact(node, first=False):
        children = []
        for child in sorted(node['children'].values(), key=lambda n: (n['label'], n['id'])):
            if child['label'] == '未归类' or child['sourceCount'] < 2:
                continue
            kept = compact(child)
            if kept:
                children.append(kept)
        if not first and node['sourceCount'] < 2:
            return None
        if not first and len(children) == 1 and children[0]['sourceIds'] == node['sourceIds']:
            only = children[0]
            only.setdefault('collapsedPaths', []).append(node['pathLabel'])
            return only
        node['displayChildren'] = children
        return node

    topics = []

    def flatten(node, depth=0, ancestor_views=None):
        ancestor_views = ancestor_views or {}
        # Equal scopes in the same branch share one selector entry; different
        # first-level anchors always retain their independent classification.
        if node['viewId'] in ancestor_views:
            target = ancestor_views[node['viewId']]
            target.setdefault('collapsedPaths', []).append(node['pathLabel'])
            node['displayId'] = target['id']
            for child in node.get('displayChildren', []):
                flatten(child, depth, ancestor_views)
            return
        node['depth'] = depth
        node['displayId'] = node['id']
        topics.append(node)
        branch = {**ancestor_views, node['viewId']: node}
        sibling_views = {}
        for child in node.get('displayChildren', []):
            if child['viewId'] in sibling_views:
                representative = sibling_views[child['viewId']]
                representative.setdefault('collapsedPaths', []).append(child['pathLabel'])
                child['displayId'] = representative['id']
                continue
            sibling_views[child['viewId']] = child
            flatten(child, depth + 1, branch)

    for root in sorted(roots.values(), key=lambda n: (n['label'], n['id'])):
        flatten(compact(root, first=True))
    visible = []
    for node in topics:
        visible.append({key: node[key] for key in ('id', 'label', 'pathLabel', 'sourceCount', 'count', 'viewId', 'depth')}
                       | {'root': min(node['entryIds'], default=None), 'sourceIds': sorted(node['sourceIds']),
                          'collapsedPaths': sorted(set(node.get('collapsedPaths', [])))})
    return visible, index
