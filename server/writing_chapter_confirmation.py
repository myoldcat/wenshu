"""Optional author approval of saved chapters; never citation verification."""
from __future__ import annotations

import hashlib
import json
import math

from .store import now


POLICY = 'chapter-confirmation-v1'


def enabled(project):
    return project.get('chapterConfirmationPolicy') == POLICY


def can_enable_sequential(project):
    """Allow a new author approval only for the current manual chapter flow."""
    brief = project.get('brief') or {}
    if brief.get('automation') == 'automatic':
        return False
    value = brief.get('targetWords', 6000)
    try:
        target = float(6000 if value is None else value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(target) or target <= 0:
        return False
    if target < 10000:
        # Explicit chapter choice overrides a historical oneshot workflow.
        return brief.get('interactiveGeneration') == 'chapter'
    return brief.get('workflow') != 'oneshot'


def fingerprint(project, section):
    selection = project.get('materialSelection') or {}
    source_ids = set(project.get('sourceIds', []))
    scope = {
        'title': project['title'], 'brief': project['brief'], 'sources': project['sources'],
        'materialScope': {
            'userSourceIds': sorted(set(selection.get('userSourceIds', source_ids)) & source_ids),
            'passages': sorted({(item['sourceId'], item['revisionId'], item['blockId'])
                                for item in selection.get('passages', [])}),
        },
        'projectMode': project['mode'],
        'section': {key: section.get(key) for key in
                    ('title', 'goal', 'targetWords', 'content', 'evidence')},
        'mode': project['mode'] if section['mode'] == 'inherit' else section['mode'],
    }
    return hashlib.sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def confirmed(project, section):
    saved = section.get('chapterConfirmation') or {}
    return bool(enabled(project) and section['content'].strip()
                and saved.get('fingerprint') == fingerprint(project, section))


def confirm(project, section):
    if confirmed(project, section):
        return False
    section['chapterConfirmation'] = {'fingerprint': fingerprint(project, section), 'confirmedAt': now()}
    return True


def invalidate_changed(project):
    if enabled(project):
        for section in project['sections']:
            if 'chapterConfirmation' in section and not confirmed(project, section):
                section.pop('chapterConfirmation')
