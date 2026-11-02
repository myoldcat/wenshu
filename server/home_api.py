"""Read-only homepage totals over current saved manuscripts."""
import json

from fastapi import APIRouter

from .writing_document import _word_count


PHASES = ('brief', 'outline', 'body', 'images', 'review', 'ready')
# Match JavaScript String.trim(), including BOM, without Python-only controls.
JS_TRIM = ('\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005'
           '\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff')


def writing_phase(project):
    """Match writingProjectPhase in frontend/src/writing-projects.js."""
    sections = project.get('sections') or []
    if not sections:
        return 'brief'
    if not project.get('outlineConfirmed'):
        return 'outline'
    if any(not (section.get('content') or '').strip(JS_TRIM) for section in sections):
        return 'body'
    if ((project.get('illustrationReview') or {}).get('status') not in ('complete', 'skipped')
            or any(image.get('status') == 'stale' for image in project.get('illustrations') or [])):
        return 'images'
    review = project.get('review') or {}
    # JavaScript treats an existing empty object as truthy, unlike Python.
    word_counts = review.get('wordCounts')
    has_counts = isinstance(word_counts, (dict, list)) or bool(word_counts)
    if not has_counts or review.get('version') != project.get('version'):
        return 'review'
    return 'ready'


def writing_summary(store):
    counts = dict.fromkeys(PHASES, 0)
    total_words = 0
    total = 0
    with store.connect() as db:
        # One current-table snapshot. Never use the capped manuscript card list,
        # histories, generation candidates, or saved review word-count caches.
        for row in db.execute('SELECT version,content_json FROM writing_projects WHERE deleted_at IS NULL'):
            project = json.loads(row['content_json'])
            project['version'] = row['version']
            total += 1
            counts[writing_phase(project)] += 1
            total_words += sum(_word_count(section.get('content') or '')
                               for section in project.get('sections') or [])
    return {'total': total, 'totalWords': total_words, 'phaseCounts': counts}


def router(store):
    routes = APIRouter()

    @routes.get('/api/home')
    def home():
        return {'writing': writing_summary(store)}

    return routes
