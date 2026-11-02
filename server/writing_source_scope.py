"""Program-owned scopes for citations preserved when a new task changes inputs.

These snapshots authorize only an existing chapter's immutable evidence
registry. Draft-generation packets continue to use project.sources exclusively.
"""
from __future__ import annotations

import copy

from .writing_sentence_citations import evidence_hash, content_hash, strip_citations


PRESERVED_CITATION_SCOPE = 'preserved-chapter-evidence-scope-v1'


def preserved_citation_scope_valid(section):
    saved = section.get('citationSourceScope')
    digest = evidence_hash(section.get('evidence', []))
    if (not isinstance(saved, dict) or saved.get('policy') != PRESERVED_CITATION_SCOPE
            or saved.get('sectionId') != section.get('id')
            or digest is None or saved.get('evidenceHash') != digest
            or saved.get('textHash') != content_hash(strip_citations(section.get('content', '')))):
        return False
    sources = saved.get('sources')
    if (not isinstance(sources, list) or len(sources) > 100
            or any(not isinstance(source, dict) or any(
                not isinstance(source.get(key), str) or not source[key] for key in ('sourceId', 'revisionId'))
                or 'blockIds' in source and (not isinstance(source['blockIds'], list)
                    or any(not isinstance(value, str) or not value for value in source['blockIds']))
                for source in sources)
            or len({(source['sourceId'], source['revisionId']) for source in sources}) != len(sources)):
        return False
    return True


def citation_sources(project, section):
    return section['citationSourceScope']['sources'] if preserved_citation_scope_valid(section) else project.get('sources', [])


def preserve_citation_sources(project):
    for section in project.get('sections', []):
        if not section.get('content', '').strip() or not section.get('evidence'):
            continue
        section['citationSourceScope'] = {'policy': PRESERVED_CITATION_SCOPE,
            'sectionId': section['id'],
            'sources': copy.deepcopy(citation_sources(project, section)),
            'evidenceHash': evidence_hash(section['evidence']),
            'textHash': content_hash(strip_citations(section['content']))}
