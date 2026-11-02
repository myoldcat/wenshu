"""Refresh existing language metadata offline, without touching source or AI data.

Run with ``python -m server.language_refresh --data-dir ... --report-dir ...``.
The default is a read-only dry run. ``--apply`` changes only the two derived
language tables, after saving an exclusive, private rollback snapshot.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import uuid

from .language import (LANGUAGE_POLICY_VERSION, SOURCE_LANGUAGE_CODES,
                       detect_language, document_language_text,
                       normalize_language_code, source_language_from_shares)


LANGUAGE_TABLES = ('document_languages', 'document_language_analysis')


def _identifier(name):
    return '"' + name.replace('"', '""') + '"'


def _json_value(value):
    if isinstance(value, bytes):
        return {'$blob': base64.b64encode(value).decode('ascii')}
    return value


def _tables(db):
    return {row['name']: row['sql'] for row in db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")}


def _ordered_rows(db, table):
    """Include rowids for FTS and ordinary tables; support WITHOUT ROWID too."""
    name = _identifier(table)
    try:
        return db.execute(f'SELECT rowid,* FROM {name} ORDER BY rowid')
    except sqlite3.OperationalError as error:
        if 'no such column: rowid' not in str(error):
            raise
        columns = [row['name'] for row in db.execute(f'PRAGMA table_info({name})')]
        order = ','.join(_identifier(column) for column in columns)
        return db.execute(f'SELECT * FROM {name} ORDER BY {order}')


def _protected_hashes(db):
    """Stream every non-language table, including AI, translations and FTS."""
    hashes = {}
    for table, schema in _tables(db).items():
        if table in LANGUAGE_TABLES:
            continue
        digest = hashlib.sha256()
        digest.update(json.dumps(schema, ensure_ascii=False).encode('utf-8'))
        count = 0
        for row in _ordered_rows(db, table):
            encoded = json.dumps([_json_value(value) for value in row],
                                 ensure_ascii=False, separators=(',', ':'))
            digest.update(b'\n' + encoded.encode('utf-8'))
            count += 1
        hashes[table] = {'sha256': digest.hexdigest(), 'rows': count}
    return hashes


def _write_private_json(directory, prefix, value):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = directory / f'{prefix}-{stamp}-{uuid.uuid4().hex[:10]}.json'
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    return str(path)


def _language_snapshot(db):
    result = {}
    for table in LANGUAGE_TABLES:
        cursor = db.execute(f'SELECT * FROM {_identifier(table)} ORDER BY revision_id')
        result[table] = {'columns': [item[0] for item in cursor.description],
                         'rows': [[_json_value(value) for value in row] for row in cursor]}
    return {'format': 'wenshu-language-metadata-rollback-v1',
            'policyVersion': LANGUAGE_POLICY_VERSION, 'tables': result}


def _validated_shares(value):
    # Keep this independent of enrichment/model imports: the refresh cannot
    # initialize a provider or schedule a capability by importing its service.
    if not isinstance(value, dict) or not value:
        return None
    shares = Counter()
    for raw_code, fraction in value.items():
        code = normalize_language_code(raw_code, default='')
        if (code not in SOURCE_LANGUAGE_CODES or code in ('mixed', 'und')
                or type(fraction) not in (int, float) or not math.isfinite(fraction)
                or not 0 <= fraction <= 1):
            return None
        shares[code] += fraction
    total = sum(shares.values())
    if not .98 <= total <= 1.02:
        return None
    return {code: fraction / total for code, fraction in shares.items() if fraction > 0}


def _reusable_semantic_code(db, tables, revision, identified):
    if (not identified or identified['identification'] != 'model'
            or identified['policy_version'] != LANGUAGE_POLICY_VERSION
            or not {'ai_runs', 'ai_outputs'} <= tables):
        return None
    output = db.execute(
        'SELECT o.result_json FROM ai_runs r JOIN ai_outputs o ON o.run_id=r.id '
        "WHERE r.revision_id=? AND r.source_id=? AND r.kind='classification' AND o.published=1 "
        'ORDER BY r.seq DESC LIMIT 1', (revision['id'], revision['source_id'])).fetchone()
    if not output:
        return None
    try:
        result = json.loads(output['result_json'])
    except (TypeError, ValueError):
        return None
    if (not isinstance(result, dict) or result.get('languagePolicyVersion') != LANGUAGE_POLICY_VERSION
            or ('sourceRevisionId' in result and result['sourceRevisionId'] != revision['id'])):
        return None
    shares = _validated_shares(result.get('sourceLanguageShares'))
    code = normalize_language_code(identified['code'])
    if (shares and source_language_from_shares(shares) == code
            and normalize_language_code(result.get('sourceLanguage')) == code):
        return code
    return None


def _plan(db):
    tables = set(_tables(db))
    required = {'revisions', 'blocks', *LANGUAGE_TABLES}
    if not required <= tables:
        raise ValueError('The existing database must contain the current language metadata schema')
    identified = {row['revision_id']: dict(row) for row in db.execute('SELECT * FROM document_languages')}
    analyses = {row['revision_id']: dict(row) for row in db.execute('SELECT * FROM document_language_analysis')}
    current = {row['current_revision'] for row in db.execute('SELECT current_revision FROM sources')} if 'sources' in tables else set()
    before, after = Counter(), Counter()
    language_rows, analysis_rows, changes = [], [], []
    total = reused = 0
    for revision in db.execute('SELECT id,source_id,title,language FROM revisions ORDER BY id'):
        total += 1
        old = identified.get(revision['id'])
        analysis = analyses.get(revision['id'])
        hint = normalize_language_code(old['code'] if old else revision['language'])
        code = _reusable_semantic_code(db, tables, revision, old)
        if code is not None:
            identification = 'model'
            reused += 1
        else:
            blocks = [dict(row) for row in db.execute(
                'SELECT kind,text FROM blocks WHERE revision_id=? ORDER BY ordinal', (revision['id'],))]
            text = document_language_text({'blocks': blocks}) or revision['title']
            code = normalize_language_code(detect_language(text, hint))
            identification = 'fallback'
        needed = int(code not in ('zh', 'und'))
        language = {'revision_id': revision['id'], 'code': code,
                    'identification': identification, 'policy_version': LANGUAGE_POLICY_VERSION}
        computed = {'revision_id': revision['id'], 'hint': code, 'version': LANGUAGE_POLICY_VERSION,
                    'needs_translation': needed, 'detected_code': code}
        language_changed = old != language
        analysis_changed = analysis != computed
        if language_changed:
            language_rows.append(language)
        if analysis_changed:
            analysis_rows.append(computed)
        before[old['code'] if old else normalize_language_code(revision['language'])] += 1
        after[code] += 1
        if language_changed or analysis_changed:
            changes.append({'sourceId': revision['source_id'], 'revisionId': revision['id'],
                            'current': revision['id'] in current,
                            'beforeCode': old['code'] if old else normalize_language_code(revision['language']),
                            'afterCode': code, 'identification': identification,
                            'beforeNeedsTranslation': bool(analysis['needs_translation']) if analysis else None,
                            'afterNeedsTranslation': bool(needed)})
    return ({'totalRevisions': total, 'currentRevisions': len(current),
             'changedRevisions': len(changes), 'languageRowsChanged': len(language_rows),
             'analysisRowsChanged': len(analysis_rows), 'reusedSemanticRecords': reused,
             'beforeCounts': dict(sorted(before.items())), 'afterCounts': dict(sorted(after.items())),
             'changes': changes}, language_rows, analysis_rows)


def refresh_languages(data_dir, report_dir, *, apply=False):
    """Refresh all immutable revisions' derived language labels; never use a model."""
    database = Path(data_dir).resolve() / 'library.sqlite3'
    if not database.is_file():
        raise ValueError('The existing library.sqlite3 was not found')
    report_dir = Path(report_dir).resolve()
    mode = 'rw' if apply else 'ro'
    db = sqlite3.connect(database.as_uri() + '?mode=' + mode, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        if apply and 'jobs' in _tables(db):
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if active:
                raise ValueError(f'Refresh requires no queued or running jobs; found {active}')
        protected_before = _protected_hashes(db)
        summary, language_rows, analysis_rows = _plan(db)
        summary.update(policyVersion=LANGUAGE_POLICY_VERSION, apply=bool(apply), rollbackSnapshot=None)
        if apply and summary['changedRevisions']:
            summary['rollbackSnapshot'] = _write_private_json(report_dir, 'language-rollback', _language_snapshot(db))
            db.executemany(
                'INSERT INTO document_languages(revision_id,code,identification,policy_version) '
                'VALUES(:revision_id,:code,:identification,:policy_version) ON CONFLICT(revision_id) DO UPDATE SET '
                'code=excluded.code,identification=excluded.identification,policy_version=excluded.policy_version',
                language_rows)
            db.executemany(
                'INSERT INTO document_language_analysis(revision_id,hint,version,needs_translation,detected_code) '
                'VALUES(:revision_id,:hint,:version,:needs_translation,:detected_code) '
                'ON CONFLICT(revision_id) DO UPDATE SET hint=excluded.hint,version=excluded.version,'
                'needs_translation=excluded.needs_translation,detected_code=excluded.detected_code', analysis_rows)
        protected_after = _protected_hashes(db)
        if protected_before != protected_after:
            raise RuntimeError('Protected non-language tables changed; transaction rolled back')
        summary.update(protectedBefore=protected_before, protectedAfter=protected_after, protectedUnchanged=True)
        # Persist the reviewable report before commit too, so report failures
        # cannot leave an applied change without its corresponding evidence.
        summary['reportPath'] = _write_private_json(report_dir, 'language-refresh', summary)
        db.commit() if apply else db.rollback()
        return summary
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--report-dir', required=True)
    parser.add_argument('--apply', action='store_true', help='Apply metadata-only changes; otherwise dry run')
    options = parser.parse_args(argv)
    try:
        result = refresh_languages(options.data_dir, options.report_dir, apply=options.apply)
    except (OSError, ValueError, sqlite3.Error, RuntimeError) as error:
        parser.exit(2, f'Language refresh failed: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
