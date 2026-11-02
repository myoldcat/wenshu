"""Read-only library availability, separate from immutable manuscript scope.

Deleting a library document must not rewrite saved prose, evidence, figure
anchors, approval hashes, or historical model requests.  The reader receives
this independent projection instead of treating its stored excerpts as a live
copy of the original document.
"""
from __future__ import annotations


def library_availability(db, project):
    sources, assets = {}, {}

    def collect(value):
        if isinstance(value, dict):
            source_id, revision_id = value.get('sourceId'), value.get('revisionId')
            if isinstance(source_id, str) and isinstance(revision_id, str):
                key = (source_id, revision_id)
                sources.setdefault(key, None)
                if isinstance(value.get('assetId'), str):
                    assets.setdefault((value['assetId'], source_id, revision_id), None)
            for key, item in value.items():
                if key != 'libraryAvailability':
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(project)
    for source_id, revision_id in sources:
        row = db.execute('SELECT s.id,r.id revision_id FROM sources s LEFT JOIN revisions r '
                         'ON r.source_id=s.id AND r.id=? WHERE s.id=?',
                         (revision_id, source_id)).fetchone()
        sources[(source_id, revision_id)] = ('deleted' if row is None else
                                             'available' if row['revision_id'] else 'unavailable')
    for asset_id, source_id, revision_id in assets:
        status = sources[(source_id, revision_id)]
        if status == 'available' and not db.execute(
                'SELECT 1 FROM assets WHERE id=? AND revision_id=?', (asset_id, revision_id)).fetchone():
            status = 'unavailable'
        assets[(asset_id, source_id, revision_id)] = status
    return {
        'sources': [{'sourceId': key[0], 'revisionId': key[1], 'status': status}
                    for key, status in sources.items()],
        'assets': [{'assetId': key[0], 'sourceId': key[1], 'revisionId': key[2], 'status': status}
                   for key, status in assets.items()],
    }
