"""Retire demonstration categories without deleting documents or AI history.

Default is a read-only dry run. Stop the API and worker before using --apply so
an already-running importer cannot publish its stale in-memory payload later.
The SQLite backup includes committed WAL content; its destination directory
must not already exist. This script uses only Python's standard library.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


DEMO_NAMES = frozenset((
    '网络安全 · 香港', '网络安全 · 国际', 'AI · 软件', 'AI · 硬件',
    'AI · 底层算法', 'AI · 应用', '香港社会民生', '中东局势', '经济投资',
    '人机协作', '知识管理', '研究方法', '未分类', '生成式 AI', '知识工作',
    '未来工作', '个人知识库', '深度阅读', '深度工作', '学术写作',
    '批判性思维', '证据管理', '数据表达',
))
OLD_VERSIONS = frozenset(('wenshu-categories-1.0', 'wenshu-categories-1.1'))
UNFINISHED = frozenset(('queued', 'running', 'failed', 'cancelled'))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def rows(db, table):
    return [dict(row) for row in db.execute('SELECT * FROM ' + identifier(table))]


def json_object(value, context):
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError(context + ' must be a JSON object')
    return result


def tags(value, context):
    if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
        raise ValueError(context + ' must be a JSON array of strings')
    return value


def filtered(value):
    # Exact matches only: a similar-looking custom label is not ours to remove.
    return [tag for tag in value if tag not in DEMO_NAMES]


def inventory(db):
    tables = {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'sources', 'jobs'}.issubset(tables):
        raise ValueError('Not a supported Wenshu database: sources/jobs missing')
    if ('ai_runs' in tables) != ('ai_outputs' in tables):
        raise ValueError('Incomplete AI schema: ai_runs and ai_outputs must coexist')
    return tables


def build_plan(db, tables):
    plan = {'sources': {}, 'jobs': {}, 'ai_outputs': {}}
    timestamp = datetime.now(timezone.utc).isoformat()
    for row in rows(db, 'sources'):
        current = tags(json.loads(row['tags_json']), 'sources.tags_json')
        remaining = filtered(current)
        if current != remaining:
            plan['sources'][row['id']] = {
                'tags_json': dumps(remaining),
                'tags_manual': row['tags_manual'] if remaining else 0,
            }
    for row in rows(db, 'jobs'):
        if row['status'] not in UNFINISHED:
            continue
        if row['kind'] == 'ai_classification':
            payload = json_object(row['input_json'], 'classification input')
            # Legacy jobs did not store the taxonomy version in their payload.
            if payload.get('taxonomyVersion') in OLD_VERSIONS | {None}:
                if row['status'] != 'cancelled' or row['stage'] != 'superseded':
                    plan['jobs'][row['id']] = {
                        'status': 'cancelled', 'stage': 'superseded',
                        'error': '示例分类体系已退役，请在配置真实分类后重新生成',
                        'updated_at': timestamp,
                    }
        elif row['kind'] == 'corpus':
            payload = json_object(row['input_json'], 'corpus input')
            if 'tags' in payload:
                current = tags(payload['tags'], 'corpus input tags')
                remaining = filtered(current)
                if current != remaining:
                    payload['tags'] = remaining
                    plan['jobs'][row['id']] = {'input_json': dumps(payload)}
    if 'ai_outputs' in tables:
        kinds = {row['id']: row['kind'] for row in rows(db, 'ai_runs')}
        for row in rows(db, 'ai_outputs'):
            if kinds.get(row['run_id']) == 'classification' and row['published']:
                result = json_object(row['result_json'], 'classification output')
                if result.get('taxonomyVersion') in OLD_VERSIONS:
                    plan['ai_outputs'][row['id']] = {'published': 0}
    return plan


def counts(db, tables, overrides=None):
    overrides = overrides or {}

    def adjusted(table):
        for row in rows(db, table):
            yield row | overrides.get(table, {}).get(row.get('id'), {})

    sources = list(adjusted('sources'))
    source_tags = [tags(json.loads(row['tags_json']), 'sources.tags_json') for row in sources]
    result = {
        'sources': len(sources),
        'sources_with_demo_tags': sum(any(tag in DEMO_NAMES for tag in value) for value in source_tags),
        'demo_tag_assignments': sum(tag in DEMO_NAMES for value in source_tags for tag in value),
        'sources_with_custom_tags': sum(any(tag not in DEMO_NAMES for tag in value) for value in source_tags),
        'sources_without_tags': sum(not value for value in source_tags),
        'manual_sources': sum(bool(row['tags_manual']) for row in sources),
        'published_demo_classification_outputs': 0,
        'obsolete_classification_jobs_not_superseded': 0,
        'unfinished_corpus_jobs_with_demo_tags': 0,
        'unfinished_corpus_demo_tag_assignments': 0,
    }
    for row in adjusted('jobs'):
        if row['status'] not in UNFINISHED or row['kind'] not in ('ai_classification', 'corpus'):
            continue
        payload = json_object(row['input_json'], 'job input')
        if row['kind'] == 'ai_classification':
            if payload.get('taxonomyVersion') in OLD_VERSIONS | {None}:
                result['obsolete_classification_jobs_not_superseded'] += row['status'] != 'cancelled' or row['stage'] != 'superseded'
        elif 'tags' in payload:
            number = sum(tag in DEMO_NAMES for tag in tags(payload['tags'], 'corpus input tags'))
            result['unfinished_corpus_jobs_with_demo_tags'] += bool(number)
            result['unfinished_corpus_demo_tag_assignments'] += number
    if 'ai_outputs' in tables:
        kinds = {row['id']: row['kind'] for row in rows(db, 'ai_runs')}
        for row in adjusted('ai_outputs'):
            if kinds.get(row['run_id']) == 'classification' and row['published']:
                value = json_object(row['result_json'], 'classification output')
                result['published_demo_classification_outputs'] += value.get('taxonomyVersion') in OLD_VERSIONS
    return result


def protected_fingerprints(db, tables, plan):
    """Hash every table, excluding only the exact cells authorized by the plan.

    This covers source identity/flags, revisions, original blocks, non-demo
    tags, non-classification outputs, translations, search and all other tables.
    Row multiplicity and the schema are included; unexpected trigger effects
    therefore fail verification and roll back along with our intended changes.
    """
    result = {}
    for table in sorted(tables):
        encoded_rows = []
        for row in rows(db, table):
            excluded = plan.get(table, {}).get(row.get('id'), {})
            encoded = []
            for key, value in sorted(row.items()):
                if key in excluded:
                    continue
                if isinstance(value, bytes):
                    value = {'sqlite_blob': base64.b64encode(value).decode('ascii')}
                encoded.append((key, value))
            encoded_rows.append(dumps(encoded))
        result[table] = hashlib.sha256(dumps(sorted(encoded_rows)).encode('utf-8')).hexdigest()
    schema = [tuple(row) for row in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    result['__schema__'] = hashlib.sha256(dumps(schema).encode('utf-8')).hexdigest()
    return result


def cleanup(database, backup_dir=None, *, apply=False):
    database = Path(database)
    if not database.is_absolute() or not database.is_file():
        raise ValueError('--database must be an absolute path to an existing SQLite file')
    database = database.resolve()
    backup_dir = Path(backup_dir).resolve() if backup_dir is not None else None
    if apply and backup_dir is None:
        raise ValueError('--apply requires --backup-dir pointing to a new directory')
    if backup_dir is not None and backup_dir.exists():
        raise ValueError('--backup-dir must not already exist; backups are never overwritten')
    uri = database.as_uri() + ('?mode=rw' if apply else '?mode=ro')
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    backup_path = None
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite quick_check failed; no changes made')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('SQLite foreign_key_check failed; no changes made')
        tables = inventory(db)
        plan = build_plan(db, tables)
        before = counts(db, tables)
        protected_before = protected_fingerprints(db, tables, plan)
        projected_after = counts(db, tables, plan)
        if apply:
            backup_dir.mkdir(parents=True, exist_ok=False)
            backup_path = backup_dir / database.name
            # BEGIN IMMEDIATE fences writers. A second read connection allows
            # backup() to copy the committed snapshot without backing up a
            # connection that itself owns an open write transaction.
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as destination:
                    source.backup(destination)
                    if destination.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('Backup verification failed; no changes made')
            for table, changes in plan.items():
                for row_id, fields in changes.items():
                    assignments = ','.join(identifier(key) + '=?' for key in fields)
                    db.execute('UPDATE ' + identifier(table) + ' SET ' + assignments + ' WHERE id=?',
                               (*fields.values(), row_id))
            for table, changes in plan.items():
                for row_id, fields in changes.items():
                    actual = db.execute('SELECT * FROM ' + identifier(table) + ' WHERE id=?', (row_id,)).fetchone()
                    if actual is None or any(actual[key] != value for key, value in fields.items()):
                        raise ValueError('An intended category update failed verification')
            protected_after = protected_fingerprints(db, inventory(db), plan)
            if protected_before != protected_after:
                raise ValueError('Protected data changed unexpectedly; transaction rolled back')
            after = counts(db, tables)
            if after != projected_after:
                raise ValueError('Post-cleanup counts differ from plan; transaction rolled back')
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('Foreign key verification failed; transaction rolled back')
            db.commit()
        else:
            after = projected_after
            db.rollback()
        return {
            'mode': 'applied' if apply else 'dry_run', 'database': str(database),
            'backup_path': str(backup_path) if backup_path else None,
            'demo_category_name_count': len(DEMO_NAMES),
            'changed_rows': {table: len(changes) for table, changes in plan.items()},
            'before': before, 'after' if apply else 'projected_after': after,
            'protected_data_verified': bool(apply),
            'protected_fingerprints': protected_before,
        }
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, help='Absolute path to the existing SQLite database')
    parser.add_argument('--backup-dir', help='New directory for the SQLite backup; required with --apply')
    parser.add_argument('--apply', action='store_true', help='Apply the verified cleanup; default is read-only dry run')
    arguments = parser.parse_args(argv)
    try:
        result = cleanup(arguments.database, arguments.backup_dir, apply=arguments.apply)
    except Exception as exc:
        print(dumps({'status': 'failed', 'error': str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
