"""Offline, reviewed and CAS-guarded publication-date enrichment of a frozen scope.

Run explicitly against an operator-selected database. Does not initialize Store, parse new revisions, schedule
work, fetch URLs or change any field other than approved revisions.published_at.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=lambda v: {'bytes': v.hex()}).encode()).hexdigest()


def read_snapshot(data_dir, document):
    base = Path(data_dir).resolve() / 'blobs'
    path = (Path(data_dir) / document['blob_path']).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Snapshot path outside blob storage')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != document['blob_hash']:
        raise ValueError('Original snapshot changed: ' + document['source_id'])
    return raw


def inspect_snapshot(raw, document):
    from lxml import html
    from server.parser import _decode
    from server.publication_date_extraction import extract_publication_date
    text = _decode(raw, [])
    tree = html.fromstring(text, parser=html.HTMLParser(no_network=True))
    # The saved collection wrapper identifies the original URL only. Its generic
    # "日期" field is deliberately never used as publication-date evidence.
    source_url = document.get('url') or ''
    wrappers = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," dsh-meta ")]')
    links = [a.get('href') for wrapper in wrappers if '原文' in wrapper.text_content()
             for a in wrapper.xpath('./a[@href]') if a.get('href', '').startswith(('http://', 'https://'))]
    if not source_url and len(set(links)) == 1:
        source_url = links[0]
    return extract_publication_date(text, title=document['title'], source_url=source_url)


def connect(data_dir, readonly=False):
    path = Path(data_dir).resolve() / 'library.sqlite3'
    db = sqlite3.connect(path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    if readonly:
        db.execute('PRAGMA query_only=ON')
    return db


def current_document(db, source_id):
    row = db.execute('''SELECT s.id source_id,s.current_revision revision_id,s.url,s.created_at imported_at,
        r.title,r.blob_path,r.blob_hash,r.published_at,r.parser_version
        FROM sources s JOIN revisions r ON r.id=s.current_revision AND r.source_id=s.id WHERE s.id=?''',
                     (source_id,)).fetchone()
    return dict(row) if row else None


def make_plan(data_dir, scope, inspector=inspect_snapshot):
    documents = scope['documents']
    if len(documents) != scope['missing'] or len({d['source_id'] for d in documents}) != len(documents):
        raise ValueError('Invalid frozen document scope')
    results = []
    with closing(connect(data_dir, readonly=True)) as db:
        for document in documents:
            if current_document(db, document['source_id']) != document:
                raise ValueError('Frozen source/revision metadata changed: ' + document['source_id'])
            if document['published_at'] and document['published_at'].strip():
                raise ValueError('Scope contains an existing publication date')
            extracted = inspector(read_snapshot(data_dir, document), document)
            results.append({'document': document, 'extraction': extracted})
    return {'scope_hash': digest(scope), 'documents': results,
            'total': len(results), 'proposed': sum(bool(item['extraction']['date']) for item in results)}


def protected_fingerprints(db, approved_revisions):
    names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
             if row[0] in ('sources', 'revisions', 'blocks', 'assets', 'jobs', 'ai_runs', 'ai_outputs')
             or row[0].startswith(('writing_', 'wiki_', 'document_', 'revision_'))]
    result = {}
    for name in names:
        rows = []
        for row in db.execute('SELECT * FROM "' + name.replace('"', '""') + '"'):
            record = dict(row)
            if name == 'revisions' and record['id'] in approved_revisions:
                record.pop('published_at')
            rows.append(digest(record))
        result[name] = {'count': len(rows), 'sha256': digest(sorted(rows))}
    return result


def apply_plan(data_dir, plan, review, backup_dir, inspector=inspect_snapshot):
    if review['plan_hash'] != digest(plan):
        raise ValueError('Reviewed plan hash changed')
    approved = review['approved_source_ids']
    if len(set(approved)) != len(approved):
        raise ValueError('Duplicate approval')
    by_id = {item['document']['source_id']: item for item in plan['documents']}
    if len(by_id) != plan['total'] or len(by_id) != len(plan['documents']):
        raise ValueError('Invalid plan scope')
    if any(source_id not in by_id or not by_id[source_id]['extraction']['date'] for source_id in approved):
        raise ValueError('Approval contains no explicit extracted date')
    backup_dir = Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / 'before.json'
    receipt = backup_dir / 'receipt.json'
    if backup.exists() or receipt.exists():
        raise ValueError('Operation directory already used; inspect its receipt instead of rerunning')
    db = connect(data_dir)
    try:
        db.execute('BEGIN IMMEDIATE')
        # Recheck the entire frozen scope, not only the accepted records.
        for item in plan['documents']:
            document = item['document']
            if current_document(db, document['source_id']) != document:
                raise ValueError('Concurrent source/revision change: ' + document['source_id'])
            extracted = inspector(read_snapshot(data_dir, document), document)
            if extracted != item['extraction']:
                raise ValueError('Extraction or evidence changed: ' + document['source_id'])
        if approved:
            placeholders = ','.join('?' for _ in approved)
            active = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running') AND source_id IN (" + placeholders + ')', approved).fetchone()[0]
            if active:
                raise ValueError('Target documents have queued/running work; no dates changed')
        revisions = {by_id[source_id]['document']['revision_id'] for source_id in approved}
        before = protected_fingerprints(db, revisions)
        previous = [{'source_id': source_id, 'revision_id': by_id[source_id]['document']['revision_id'],
                     'blob_hash': by_id[source_id]['document']['blob_hash'],
                     'old_published_at': by_id[source_id]['document']['published_at'],
                     'new_published_at': by_id[source_id]['extraction']['date'],
                     'evidence': by_id[source_id]['extraction']['evidence']} for source_id in approved]
        with backup.open('x', encoding='utf-8') as stream:
            json.dump({'plan_hash': digest(plan), 'records': previous, 'protected': before}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        for source_id in approved:
            item = by_id[source_id]
            document = item['document']
            changed = db.execute('''UPDATE revisions SET published_at=?
                WHERE id=? AND source_id=? AND blob_hash=? AND (published_at IS NULL OR trim(published_at)='')
                AND EXISTS (SELECT 1 FROM sources s WHERE s.id=revisions.source_id AND s.current_revision=revisions.id)''',
                (item['extraction']['date'], document['revision_id'], source_id, document['blob_hash'])).rowcount
            if changed != 1:
                raise ValueError('Publication date CAS failed: ' + source_id)
        after = protected_fingerprints(db, revisions)
        if before != after:
            raise ValueError('Protected data changed; transaction rolled back')
        for item in plan['documents']:
            expected = dict(item['document'])
            if expected['source_id'] in approved:
                expected['published_at'] = item['extraction']['date']
            if current_document(db, expected['source_id']) != expected:
                raise ValueError('Post-update validation failed')
            read_snapshot(data_dir, expected)
        db.commit()
        result = {'status': 'committed', 'completed_at': datetime.now(timezone.utc).isoformat(),
                  'plan_hash': digest(plan), 'checked': plan['total'], 'updated': len(approved),
                  'kept_import_fallback': plan['total'] - len(approved), 'protected_unchanged': before == after,
                  'snapshot_hashes_unchanged': True, 'records': previous}
        # A receipt failure after commit cannot be described as a rollback.
        # The fsynced before.json retains exact old/new values for recovery.
        try:
            with receipt.open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            result.update(receipt_saved=False, receipt_error=str(exc))
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['plan', 'apply'])
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--scope', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    if args.mode == 'plan':
        result = make_plan(args.data_dir, json.loads(args.scope.read_text(encoding='utf-8')))
        args.plan.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'checked': result['total'], 'proposed': result['proposed'], 'plan_hash': digest(result)}))
    else:
        if args.review is None or args.backup_dir is None:
            parser.error('apply requires a reviewed plan and a fresh backup directory')
        result = apply_plan(args.data_dir, json.loads(args.plan.read_text(encoding='utf-8')),
                            json.loads(args.review.read_text(encoding='utf-8')), args.backup_dir)
        print(json.dumps({key: value for key, value in result.items() if key != 'records'}))


if __name__ == '__main__':
    main()
