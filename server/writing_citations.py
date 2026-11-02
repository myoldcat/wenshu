"""Pure presentation numbering for one already-reviewed writing snapshot.

Stored E-IDs are local to a chapter. Export numbers identify pinned source
versions and are assigned in manuscript reading order. This module never
repairs text or accesses the library; the matching frozen review owns database
anchor validation.
"""
from __future__ import annotations

import copy
import re

from .writing_review import CITATIONS
from .writing_sentence_citations import valid_bindings
from .writing_source_scope import citation_sources


INVALID_REFERENCE_CODES = frozenset({
    'duplicate_citation', 'citation_incomplete', 'citation_id_invalid',
    'citation_out_of_scope', 'citation_anchor_invalid',
})
REFERENCE_INTRO = '正文引用按首次出现顺序统一编号；同一资料的同一固定版本复用编号，条目内保留各处原文定位和逐字摘录。'
HORIZONTAL_WHITESPACE = re.compile(r'[^\S\r\n\v\f\x85\u2028\u2029]*\Z')


def _identity(ref):
    strings = ('sourceId', 'revisionId', 'blockId', 'quote', 'title')
    if not all(isinstance(ref.get(key), str) and ref[key].strip() for key in strings):
        return None
    if not all(isinstance(ref.get(key), int) and not isinstance(ref[key], bool) for key in ('start', 'end')):
        return None
    if not 0 <= ref['start'] < ref['end'] or ref['end'] - ref['start'] != len(ref['quote']):
        return None
    return tuple(ref[key] for key in ('sourceId', 'revisionId', 'blockId', 'start', 'end', 'quote'))


def build_citation_plan(project, report):
    """Return sectionNumbers, references and unresolved without mutating input.

    The renderer must use the same saved project and freshly built review. A
    stale review cannot confer valid numbering. Unresolved markers stay visible
    in the body and their registered details remain available in the appendix.
    """
    bad, stale_sections = {}, {}
    for issue in report.get('issues', []):
        if issue.get('code') in INVALID_REFERENCE_CODES and isinstance(issue.get('evidenceId'), str):
            bad.setdefault((issue.get('sectionId'), issue['evidenceId']), issue['message'])
        if issue.get('code') == 'citation_binding_stale':
            stale_sections.setdefault(issue.get('sectionId'), issue.get('message') or
                                      '正文引用句定位已失效，请重新核对引用')
    references, unresolved, section_numbers, identities, anchors = [], [], [], {}, {}
    for section_number, section in enumerate(project['sections'], 1):
        sources = citation_sources(project, section)
        allowed = {(source['sourceId'], source['revisionId']) for source in sources}
        narrowed = {(source['sourceId'], source['revisionId']): set(source['blockIds'])
                    for source in sources if 'blockIds' in source}
        numbers, records = {}, {}
        evidence = section.get('evidence', [])
        bindings = section.get('citationBindings')
        effective_mode = project.get('mode') if section.get('mode') in (None, 'inherit') else section['mode']
        # Saving edited prose deliberately drops the invalid binding object.
        # Its durable stale state and fresh review must still prevent a draft
        # export from presenting those old markers as valid numbered support.
        stale_message = stale_sections.get(section.get('id')) or stale_sections.get(None)
        bindings_stale = (section.get('citationBindingState') == 'stale' or stale_message is not None
                          or bindings is not None and not valid_bindings(section.get('content', ''), evidence, bindings, mode=effective_mode))
        if isinstance(evidence, list):
            for ref in evidence:
                if isinstance(ref, dict) and isinstance(ref.get('id'), str):
                    records.setdefault(ref['id'], []).append(ref)
        # Dict insertion order follows the first occurrence, never registry order.
        used = dict.fromkeys(CITATIONS.findall(section.get('content', '')))
        for eid in used:
            refs = records.get(eid, [])
            ref = refs[0] if len(refs) == 1 else None
            identity = _identity(ref) if ref else None
            scope = (ref['sourceId'], ref['revisionId']) if identity else None
            reason = None
            if not refs:
                reason = '未登记，未分配参考文献编号'
            elif len(refs) != 1:
                reason = '重复登记，无法唯一定位，未分配参考文献编号'
            elif not re.fullmatch(r'E\d+', eid) or identity is None:
                reason = '来源、版本、定位或原文摘录不完整，未分配参考文献编号'
            elif scope not in allowed or scope in narrowed and ref['blockId'] not in narrowed[scope]:
                reason = '不在当前固定资料范围，未分配参考文献编号'
            elif (section.get('id'), eid) in bad:
                reason = bad[(section.get('id'), eid)]
            elif bindings_stale:
                reason = (stale_message or '正文引用句定位已失效，请重新核对引用') + '；未分配参考文献编号'
            elif report.get('version') != project.get('version'):
                reason = '校验版本不一致，未分配参考文献编号'
            if reason:
                unresolved.append({'sectionNumber': section_number, 'sectionTitle': section['title'],
                                   'evidenceId': eid, 'reason': reason, 'records': copy.deepcopy(refs)})
                continue
            source_identity = (ref['sourceId'], ref['revisionId'])
            if source_identity not in identities:
                identities[source_identity] = len(references) + 1
                references.append({'number': identities[source_identity], 'reference': copy.deepcopy(ref), 'anchors': [], 'uses': []})
            number = identities[source_identity]
            numbers[eid] = number
            use = {'sectionNumber': section_number, 'sectionTitle': section['title'], 'evidenceId': eid}
            entry = references[number - 1]
            entry['uses'].append(use)
            if identity not in anchors:
                anchors[identity] = {'reference': copy.deepcopy(ref), 'uses': []}
                entry['anchors'].append(anchors[identity])
            anchors[identity]['uses'].append(use)
        section_numbers.append(numbers)
    return {'sectionNumbers': section_numbers, 'references': references, 'unresolved': unresolved}


def citation_parts(text, numbers, *, links=None):
    """Yield literal spans and one number per source in each citation cluster.

    Adjacent markers can point to several excerpts of the same fixed source.
    Only their repeated display numbers are omitted; exact excerpt anchors stay
    in the bibliography plan. Authored text and whitespace are unchanged, and
    body text, line breaks or unresolved markers start a new cluster.
    """
    offset = 0
    cluster_numbers = set()
    linked = set(links.values()) if links is not None else None
    for marker in CITATIONS.finditer(text):
        number = numbers.get(marker.group(1))
        if number is None:
            continue
        literal = text[offset:marker.start()]
        unresolved = linked is not None and number not in linked
        if unresolved or not HORIZONTAL_WHITESPACE.fullmatch(literal):
            cluster_numbers.clear()
        if literal:
            yield literal, None
        if number not in cluster_numbers:
            yield '[' + str(number) + ']', number
            cluster_numbers.add(number)
        if unresolved:
            cluster_numbers.clear()
        offset = marker.end()
    if offset < len(text) or not text:
        yield text[offset:], None


def build_export_citation_plan(project, report):
    """Number every complete E-marker for display, independently of validity.

    Only the strict plan's resolved sources receive bibliography entries and
    links. Unresolved markers get chapter-local, collision-free display numbers
    without inventing a source or changing the saved review/author text.
    """
    strict = build_citation_plan(project, report)
    entries = {entry['number']: entry for entry in strict['references']}
    identities, references, section_numbers, section_links = {}, [], [], []
    for index, section in enumerate(project['sections']):
        numbers, links = {}, {}
        for eid in dict.fromkeys(CITATIONS.findall(section.get('content', ''))):
            resolved = strict['sectionNumbers'][index].get(eid)
            # Valid numbers already identify one pinned source version. An
            # unresolved E1 in another chapter must never reuse that identity.
            identity = ('source', resolved) if resolved is not None else ('unresolved', index, eid)
            if identity not in identities:
                identities[identity] = len(identities) + 1
                if resolved is not None:
                    entry = copy.deepcopy(entries[resolved])
                    entry['number'] = identities[identity]
                    references.append(entry)
            number = identities[identity]
            numbers[eid] = number
            if resolved is not None:
                links[eid] = number
        section_numbers.append(numbers)
        section_links.append(links)
    return {'sectionNumbers': section_numbers, 'sectionLinks': section_links,
            'references': references, 'unresolved': strict['unresolved']}
