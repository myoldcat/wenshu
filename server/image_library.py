"""Small, model-free manifests for reproducible whole-library image discovery."""
from __future__ import annotations

import hashlib
import json


LIBRARY_SCOPE_VERSION = 'writing-image-library-1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _selected_ids(project):
    # Exclude every selected source, including narrowed and historical sources.
    return {item['sourceId'] for item in project.get('sources', [])
            if isinstance(item, dict) and isinstance(item.get('sourceId'), str)}


def freeze_library_scope(db, project):
    """Use the caller's transaction to freeze current owned revisions/assets.

    No original/asset files are read, no derived records written, and no model
    calls made. An empty library is a valid frozen scope, not a live fallback.
    """
    excluded = _selected_ids(project)
    sources = {}
    for row in db.execute(
            'SELECT s.id source_id,r.id revision_id,a.id asset_id FROM sources s '
            'JOIN revisions r ON r.id=s.current_revision AND r.source_id=s.id '
            'JOIN assets a ON a.revision_id=r.id ORDER BY s.id,r.id,a.id'):
        if row['source_id'] in excluded:
            continue
        item = sources.setdefault(row['source_id'], {'sourceId': row['source_id'],
                                  'revisionId': row['revision_id'], 'assetIds': []})
        item['assetIds'].append(row['asset_id'])
    scope = {'version': LIBRARY_SCOPE_VERSION, 'sources': list(sources.values()),
             'excludedSourceIds': sorted(excluded), 'sourceCount': len(sources),
             'assetCount': sum(len(item['assetIds']) for item in sources.values())}
    return {**scope, 'scopeHash': _digest(scope)}


def library_scope_sources(scope, project):
    """Validate a stored manifest, then independently enforce selected scope.

    The digest detects accidental corruption; it is not an authorization token.
    Revision ownership and asset membership are also checked by retrieval.
    """
    if not isinstance(scope, dict) or scope.get('version') != LIBRARY_SCOPE_VERSION:
        raise ValueError('图库检索范围无效，请重新查找配图')
    expected = {'version', 'sources', 'excludedSourceIds', 'sourceCount', 'assetCount', 'scopeHash'}
    if set(scope) != expected or scope['scopeHash'] != _digest({k: v for k, v in scope.items() if k != 'scopeHash'}):
        raise ValueError('图库检索范围已变化，请重新查找配图')
    if not isinstance(scope['sources'], list) or not isinstance(scope['excludedSourceIds'], list):
        raise ValueError('图库检索范围无效，请重新查找配图')
    excluded = _selected_ids(project)
    seen, assets, result = set(), set(), []
    for item in scope['sources']:
        if (not isinstance(item, dict) or set(item) != {'sourceId', 'revisionId', 'assetIds'}
                or any(not isinstance(item.get(k), str) or not item[k] for k in ('sourceId', 'revisionId'))
                or not isinstance(item['assetIds'], list) or item['sourceId'] in seen
                or any(not isinstance(value, str) or not value or value in assets for value in item['assetIds'])
                or len(set(item['assetIds'])) != len(item['assetIds'])):
            raise ValueError('图库检索范围无效，请重新查找配图')
        seen.add(item['sourceId'])
        assets.update(item['assetIds'])
        if item['sourceId'] not in excluded:
            result.append(item)
    if (type(scope['sourceCount']) is not int or type(scope['assetCount']) is not int
            or scope['sourceCount'] != len(seen) or scope['assetCount'] != len(assets)):
        raise ValueError('图库检索范围不完整，请重新查找配图')
    return result
