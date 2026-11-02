"""Read-only elapsed times from saved writing task/confirmation records.

These are task times, including queueing, retries and saving, rather than a
measurement of how long an author spent thinking or typing. Missing records
remain unknown. Deriving them never creates a checkpoint or changes history.
"""
from __future__ import annotations

from datetime import datetime
import json


STEPS = ('brief', 'materials', 'outline', 'body', 'images', 'review')


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _elapsed(start, finish):
    if not isinstance(start, str) or not isinstance(finish, str):
        return None
    try:
        first, last = (datetime.fromisoformat(value.replace('Z', '+00:00'))
                       for value in (start, finish))
        if first.tzinfo is None or last.tzinfo is None or last < first:
            return None
        return round((last - first).total_seconds() * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def _duration(row):
    return (_elapsed(row['created_at'], row['updated_at'])
            if row['status'] == 'succeeded' else None)


def _item(value=None, kind='generation'):
    return {'durationMs': value, 'kind': kind}


def _body_time(rows, section_ids):
    """Count a document job once, not once for every saved document step."""
    if not section_ids:
        return None
    covered, values = set(), []
    for row in rows:
        if row['kind'] == 'document':
            document = _object(_object(row['result_json']).get('document'))
            steps = document.get('steps', [])
            steps = steps if isinstance(steps, list) else []
            applied = {step.get('sectionId') for step in steps
                       if isinstance(step, dict) and step.get('status') == 'applied'}
        elif row['kind'] == 'section' and row['applied']:
            applied = {row['section_id']}
        else:
            continue
        current = (applied & section_ids) - covered
        if not current:
            continue
        covered.update(current)
        value = _duration(row)
        # An applied result with a missing/incomplete terminal record must not
        # silently borrow an older job's time for the same saved chapter.
        if value is None:
            return None
        values.append(value)
    return sum(values) if covered == section_ids else None


def _latest_time(rows, kinds, *, applied=False):
    for row in rows:
        if row['kind'] in kinds and (not applied or row['applied']):
            if row['status'] in ('queued', 'running') and not row['applied']:
                continue
            return _duration(row)
    return None


def _image_time(rows, section_ids):
    # A chapter batch owns its searches. Count its saved task once, including
    # queueing/acceptance, rather than adding its child times again.
    owned = {child.get('runId') for row in rows if row['kind'] == 'image_batch'
             for child in _object(_object(row['result_json']).get('imageBatch')).get('children', {}).values()
             if isinstance(child, dict)}
    seen, values = set(), []
    for row in rows:
        if row['id'] in owned or row['kind'] not in ('images', 'image_batch'):
            continue
        if row['status'] in ('queued', 'running'):
            continue
        targets = set(_object(_object(row['result_json']).get('imageBatch')).get('sectionIds', [])) if row['kind'] == 'image_batch' else {row['section_id']}
        current = (targets & section_ids) - seen
        if not current:
            continue
        seen.update(current)
        value = _duration(row)
        if value is None:
            return None
        values.append(value)
    return sum(values) if values else None


def _brief_confirmation(db, project):
    if 'brief' in project.get('workflowPendingSteps', []):
        return None
    started, previous, result = None, False, None
    for row in db.execute("""SELECT reason,created_at,
            CASE WHEN json_valid(content_json) THEN json_extract(content_json,'$.workflowPendingSteps') END pending
            FROM writing_history WHERE project_id=? ORDER BY version""", (project['id'],)):
        try:
            values = json.loads(row['pending']) if row['pending'] else None
        except (TypeError, ValueError):
            values = None
        if not isinstance(values, list):
            started, previous, result = None, False, None
            continue
        pending = isinstance(values, list) and 'brief' in values
        if pending and not previous:
            started, result = row['created_at'], None
        elif previous and not pending and row['reason'] == 'step_confirmed_brief':
            result = _elapsed(started, row['created_at'])
        previous = pending
    return result


def workflow_timings(db, project):
    """Return a small public DTO; all statements in this function are SELECTs."""
    result = {step: _item(kind='confirmation' if step == 'brief' else 'generation') for step in STEPS}
    result['brief'] = _item(_brief_confirmation(db, project), 'confirmation')
    rows = [dict(row) for row in db.execute("""SELECT r.id,r.kind,r.section_id,r.created_at,r.applied,
        CASE WHEN r.kind IN ('document','automatic','image_batch') THEN r.result_json ELSE NULL END result_json,
        j.status,j.updated_at
        FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=?
        ORDER BY r.created_at DESC,r.id DESC""", (project['id'],))]
    ids = {section['id'] for section in project.get('sections', [])}
    written = {section['id'] for section in project.get('sections', []) if section.get('content', '').strip()}
    result['outline'] = _item(_latest_time(rows, {'outline'}, applied=True))
    result['body'] = _item(_body_time(rows, ids) if ids == written else None)
    result['images'] = _item(_image_time(rows, written))
    result['review'] = _item(_latest_time(rows, {'citation_match', 'citations'}, applied=True))

    parent = next((row for row in rows if row['kind'] == 'automatic'), None)
    state = _object(parent['result_json']).get('automatic') if parent else None
    if not isinstance(state, dict):
        return result
    report = _object(state.get('report'))
    current = (parent['status'] == 'succeeded' and report.get('projectVersion') == project['version']
               or parent['status'] in ('queued', 'running') and state.get('expectedVersion') == project['version'])
    if not current:
        return result
    children = state.get('children') or {}
    if not isinstance(children, dict):
        return result
    # A current automatic run may preserve existing prose or skip a stage.
    # Only its own children measure this run; previous tasks are not its cost.
    for step in ('outline', 'body', 'images', 'review'):
        result[step] = _item()
    by_id = {row['id']: row for row in rows}
    child_rows = []
    for value in children.values():
        if isinstance(value, dict) and value.get('runId') in by_id:
            child_rows.append(by_id[value['runId']])
    child_rows.sort(key=lambda row: (row['created_at'] or '', row['id']), reverse=True)
    outline_id = children.get('outline', {}).get('runId') if isinstance(children.get('outline'), dict) else None
    outline = by_id.get(outline_id)
    if outline:
        stages = state.get('stages') if isinstance(state.get('stages'), list) else []
        if state.get('materialsReady') is True or any(isinstance(stage, dict) and stage.get('stage') == 'materials'
                and stage.get('status') == 'complete' for stage in stages):
            result['materials'] = _item(_elapsed(state.get('startedAt'), outline['created_at']), 'automatic-stage')
        result['outline'] = _item(_duration(outline) if outline['applied'] else None)
    if any(row['kind'] in ('document', 'section') for row in child_rows):
        result['body'] = _item(_body_time(child_rows, ids) if ids == written else None)
    if any(row['kind'] == 'images' for row in child_rows):
        result['images'] = _item(_image_time(child_rows, written))
    if any(row['kind'] in ('citation_match', 'citations') for row in child_rows):
        result['review'] = _item(_latest_time(child_rows, {'citation_match', 'citations'}, applied=True))
    # An explicitly skipped new stage cannot inherit a previous run's work.
    stages = report.get('stages', state.get('stages', []))
    for stage in stages if isinstance(stages, list) else []:
        if isinstance(stage, dict) and stage.get('stage') in ('images', 'review') and stage.get('status') == 'skipped':
            result[stage['stage']] = _item()
    return result
