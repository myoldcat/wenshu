"""Explicit library deletion with a reviewable, transaction-bound impact check.

Manuscripts retain their own frozen citations and captions. Library originals,
derived indexes and picture bytes belong to the deleted source; shared blobs
are collected only after their final library association has gone away.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from .store import dumps, normalized_url, now


class DocumentDeletionConflict(ValueError):
    def __init__(self, message, impact):
        super().__init__(message)
        self.impact = impact


def _contains(value, identities):
    if isinstance(value, dict):
        return any(key in identities or _contains(item, identities) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains(item, identities) for item in value)
    return isinstance(value, str) and (value in identities or value.removeprefix('/api/assets/') in identities)


def _json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


class DocumentDeletionService:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _tables(db):
        return {row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def _impact(self, db, source_id):
        source = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if source is None:
            raise KeyError(source_id)
        revisions = db.execute('SELECT id,title,blob_path FROM revisions WHERE source_id=? ORDER BY id', (source_id,)).fetchall()
        assets = db.execute('SELECT a.id,a.blob_path FROM assets a JOIN revisions r ON r.id=a.revision_id '
                            'WHERE r.source_id=? ORDER BY a.id', (source_id,)).fetchall()
        identities = {source_id, *(row['id'] for row in revisions), *(row['id'] for row in assets)}
        tables = self._tables(db)
        entries = {row['id'] for row in db.execute('SELECT id FROM wiki_entries WHERE source_id=?', (source_id,))} if 'wiki_entries' in tables else set()
        identities.update(entries)
        references, signatures, related_projects = [], [], set()
        if 'writing_projects' in tables:
            for row in db.execute('SELECT id,version,content_json,deleted_at FROM writing_projects ORDER BY id'):
                project = _json(row['content_json']) or {}
                historical = []
                if 'writing_history' in tables:
                    historical = [history for history in db.execute('SELECT version,content_json FROM writing_history WHERE project_id=? ORDER BY version', (row['id'],))
                                  if _contains(_json(history['content_json']), identities)]
                if not _contains(project, identities) and not historical:
                    continue
                related_projects.add(row['id'])
                text = sum(1 for section in project.get('sections', []) for ref in section.get('evidence', []) if _contains(ref, identities))
                pictures = sum(1 for item in project.get('illustrations', []) if _contains(item, identities))
                references.append({'projectId': row['id'], 'title': project.get('title', '未命名文稿'),
                    'textReferences': text, 'imageReferences': pictures,
                    'selectedSource': source_id in project.get('sourceIds', []) or _contains(project.get('sources', []), identities),
                    'historicalReferences': len(historical), 'deleted': bool(row['deleted_at'])})
                signatures.append([row['id'], row['version'], hashlib.sha256(row['content_json'].encode()).hexdigest(),
                    [[history['version'], hashlib.sha256(history['content_json'].encode()).hexdigest()] for history in historical]])
        paths = {(self.store.root / row['blob_path']).resolve() for row in revisions}
        active, active_signatures = [], []
        for job in db.execute("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id"):
            payload = _json(job['input_json']) or {}
            related = job['source_id'] == source_id or job['revision_id'] in identities or _contains(payload, identities)
            if not related and payload.get('url'):
                try:
                    related = source['identity'] == 'url:' + normalized_url(payload['url'])
                except ValueError:
                    pass
            if not related and isinstance(payload.get('path'), str):
                related = Path(payload['path']).resolve() in paths
            if not related and 'writing_runs' in tables:
                run = db.execute('SELECT project_id,input_json FROM writing_runs WHERE job_id=?', (job['id'],)).fetchone()
                related = bool(run and (run['project_id'] in related_projects or _contains(_json(run['input_json']), identities)))
            if not related and 'wiki_resolution_runs' in tables:
                run = db.execute('SELECT input_json FROM wiki_resolution_runs WHERE job_id=?', (job['id'],)).fetchone()
                related = bool(run and _contains(_json(run['input_json']), identities))
            if related:
                active.append({key: job[key] for key in ('id', 'label', 'kind', 'status')})
                active_signatures.append([job['id'], job['status'], job['attempts']])
        current = next((row for row in revisions if row['id'] == source['current_revision']), None)
        token = hashlib.sha256(dumps([dict(source), [dict(row) for row in revisions],
            [dict(row) for row in assets], signatures, active_signatures]).encode()).hexdigest()
        return {'sourceId': source_id, 'title': current['title'] if current else '文档',
            'revisionId': source['current_revision'], 'impactToken': token,
            'references': references, 'referenceCount': len(references), 'hasReferences': bool(references),
            'imageCount': len(assets), 'revisionCount': len(revisions), 'activeJobs': active, 'canDelete': not active}

    def impact(self, source_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._impact(db, source_id)

    @staticmethod
    def _delete_ids(db, tables, table, column, ids, removed):
        if table not in tables or not ids:
            return
        # json_each avoids SQLite parameter-count limits on large source histories.
        count = db.execute(f'DELETE FROM "{table}" WHERE "{column}" IN (SELECT value FROM json_each(?))', (dumps(sorted(ids)),)).rowcount
        removed[table] = removed.get(table, 0) + count

    @staticmethod
    def _delete_json(db, tables, table, columns, identities, removed):
        if table not in tables:
            return
        ids = []
        for row in db.execute(f'SELECT rowid AS deletion_rowid,* FROM "{table}"'):
            if any(_contains(_json(row[column]), identities) for column in columns):
                ids.append(row['deletion_rowid'])
        DocumentDeletionService._delete_ids(db, tables, table, 'rowid', ids, removed)

    def delete(self, source_id, expected_revision, impact_token, confirm_references=False):
        removed, paths = {}, set()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            impact = self._impact(db, source_id)
            if impact['revisionId'] != expected_revision or impact['impactToken'] != impact_token:
                raise DocumentDeletionConflict('文档或引用情况已变化，请核对最新提示后再删除。', impact)
            if not impact['canDelete']:
                raise DocumentDeletionConflict('文档或关联文稿仍有排队／运行任务，请先结束或取消任务后再删除。', impact)
            if impact['hasReferences'] and confirm_references is not True:
                raise DocumentDeletionConflict('有文稿使用此文档或其中图片，请确认引用影响后再删除。', impact)
            tables = self._tables(db)
            revisions = {row['id'] for row in db.execute('SELECT id FROM revisions WHERE source_id=?', (source_id,))}
            assets = {row['id'] for row in db.execute('SELECT a.id FROM assets a JOIN revisions r ON r.id=a.revision_id WHERE r.source_id=?', (source_id,))}
            entries = {row['id'] for row in db.execute('SELECT id FROM wiki_entries WHERE source_id=?', (source_id,))} if 'wiki_entries' in tables else set()
            identities = {source_id, *revisions, *assets, *entries}
            if 'wiki_all_items' in tables:
                # Keep the frozen batch audit; deleting one source must neither
                # delete the parent nor prevent unrelated later batches.
                db.execute("""UPDATE wiki_all_items SET status='skipped',reason='source_deleted',child_job_id=NULL
                    WHERE source_id=?""", (source_id,))
            paths.update(row['blob_path'] for row in db.execute('SELECT blob_path FROM revisions WHERE source_id=?', (source_id,)))
            paths.update(row['blob_path'] for row in db.execute('SELECT blob_path FROM assets WHERE revision_id IN (SELECT value FROM json_each(?))', (dumps(sorted(revisions)),)))
            jobs = {row['id'] for row in db.execute("SELECT id FROM jobs WHERE kind NOT GLOB 'writing_*' AND (source_id=? OR revision_id IN (SELECT value FROM json_each(?)))", (source_id, dumps(sorted(revisions))))}
            source = db.execute('SELECT identity FROM sources WHERE id=?', (source_id,)).fetchone()
            original_paths = {(self.store.root / row['blob_path']).resolve() for row in db.execute('SELECT blob_path FROM revisions WHERE source_id=?', (source_id,))}
            for job in db.execute("SELECT id,input_json FROM jobs WHERE kind NOT GLOB 'writing_*' AND source_id IS NULL"):
                payload = _json(job['input_json']) or {}
                related = _contains(payload, identities)
                if not related and payload.get('url'):
                    try:
                        related = source['identity'] == 'url:' + normalized_url(payload['url'])
                    except ValueError:
                        pass
                if not related and isinstance(payload.get('path'), str):
                    related = Path(payload['path']).resolve() in original_paths
                if related:
                    jobs.add(job['id'])
            canonical_ids = set()
            if 'wiki_canonical_pages' in tables:
                canonical_ids.update(row['canonical_id'] for row in db.execute('SELECT canonical_id,content_json FROM wiki_canonical_pages')
                                     if _contains(_json(row['content_json']), identities))
            if 'wiki_canonical_contributions' in tables:
                canonical_ids.update(row['canonical_id'] for row in db.execute('SELECT canonical_id FROM wiki_canonical_contributions WHERE entry_id IN (SELECT value FROM json_each(?))', (dumps(sorted(entries)),)))
            self._delete_ids(db, tables, 'wiki_canonical_search_index', 'canonical_id', canonical_ids, removed)
            self._delete_ids(db, tables, 'wiki_canonical_memberships', 'canonical_id', canonical_ids, removed)
            # Derived graph identities and multi-source caches may mention the
            # source inside JSON. Invalidate only affected records; originals
            # belonging to other sources and manuscript audit history remain.
            for table, columns in (
                ('wiki_knowledge_links', ('entry_ids_json',)),
                ('wiki_canonical_decisions', ('payload_json',)),
                ('wiki_canonical_snapshots', ('content_json',)),
                ('wiki_canonical_pages', ('content_json',)),
                ('wiki_canonical_syntheses', ('content_json', 'frozen_json')),
                ('retrieval_snapshots', ('content_json',)),
            ):
                self._delete_json(db, tables, table, columns, identities, removed)
            if entries and 'wiki_canonical_entities' in tables:
                affected = {row['id'] for row in db.execute('SELECT id FROM wiki_canonical_entities WHERE anchor_entry_id IN (SELECT value FROM json_each(?))', (dumps(sorted(entries)),))}
                self._delete_ids(db, tables, 'wiki_canonical_search_index', 'canonical_id', affected, removed)
                self._delete_ids(db, tables, 'wiki_canonical_memberships', 'canonical_id', affected, removed)
                self._delete_ids(db, tables, 'wiki_canonical_entities', 'id', affected, removed)
            if 'wiki_canonical_state' in tables:
                db.execute("UPDATE wiki_canonical_state SET snapshot_id='',decision_version=decision_version+1 WHERE id=1")
            self._delete_ids(db, tables, 'wiki_canonical_contributions', 'revision_id', revisions, removed)
            # Resolution requests span multiple sources. Their derived result
            # and checkpoints are invalid once any selected source disappears.
            if 'wiki_resolution_runs' in tables:
                resolution = [dict(row) for row in db.execute('SELECT id,job_id,input_json FROM wiki_resolution_runs') if _contains(_json(row['input_json']), identities)]
                run_ids = {row['id'] for row in resolution}
                jobs.update(row['job_id'] for row in resolution)
                self._delete_ids(db, tables, 'wiki_resolution_checkpoints', 'run_id', run_ids, removed)
                self._delete_ids(db, tables, 'wiki_resolution_results', 'run_id', run_ids, removed)
                self._delete_ids(db, tables, 'wiki_resolution_runs', 'id', run_ids, removed)
            for parent, children in (
                ('ai_runs', ('ai_checkpoints', 'ai_outputs', 'ai_calls')),
                ('wiki_runs', ('wiki_checkpoints', 'wiki_calls')),
            ):
                if parent not in tables:
                    continue
                runs = {row['id'] for row in db.execute(f'SELECT id FROM "{parent}" WHERE source_id=?', (source_id,))}
                for child in children:
                    self._delete_ids(db, tables, child, 'run_id', runs, removed)
            self._delete_ids(db, tables, 'wiki_search_index', 'entry_id', entries, removed)
            for table in ('wiki_publications', 'wiki_pages', 'wiki_entries', 'wiki_unified_outbox',
                          'wiki_runs', 'ai_runs', 'ai_card_covers', 'writing_image_contexts',
                          'retrieval_embeddings', 'retrieval_vector_units', 'search_index'):
                self._delete_ids(db, tables, table, 'source_id', {source_id}, removed)
            for table in ('writing_image_vectors', 'ai_translation_edits', 'translated_blocks',
                          'document_chinese', 'document_languages', 'document_article_types',
                          'document_language_analysis', 'document_body_metrics', 'revision_image_candidates',
                          'assets', 'blocks'):
                self._delete_ids(db, tables, table, 'revision_id', revisions, removed)
            self._delete_ids(db, tables, 'job_prompt_snapshots', 'job_id', jobs, removed)
            self._delete_ids(db, tables, 'jobs', 'id', jobs, removed)
            self._delete_ids(db, tables, 'revisions', 'id', revisions, removed)
            self._delete_ids(db, tables, 'sources', 'id', {source_id}, removed)
            db.executemany('INSERT OR IGNORE INTO document_blob_cleanup(blob_path,created_at) VALUES(?,?)',
                           [(path, now()) for path in sorted(paths)])
            # The canonical FTS projection may contain a mixed group whose
            # anchor belongs to a surviving source. Remove affected cached
            # groups as well; the next graph read derives the current groups.
            if entries and 'wiki_canonical_search_index' in tables:
                live_groups = {row['canonical_id'] for row in db.execute('SELECT DISTINCT canonical_id FROM wiki_canonical_pages')}
                for row in db.execute('SELECT canonical_id FROM wiki_canonical_search_index').fetchall():
                    if row['canonical_id'] not in live_groups:
                        db.execute('DELETE FROM wiki_canonical_search_index WHERE canonical_id=?', (row['canonical_id'],))
        # Do not unlink before the database commit: a rollback must never leave
        # surviving rows pointing at deleted files. publish() rechecks/recreates
        # content-addressed files inside its own write transaction.
        collected, pending = self.collect_pending(limit=128)
        removed['files'] = collected
        return {'deleted': True, 'sourceId': source_id, 'removed': removed, 'pendingCleanup': pending}

    def collect_pending(self, *, limit=32, force=False):
        """Retry bounded physical cleanup independently of model processing."""
        collected, pending = 0, 0
        with self.store.connect() as db:
            due = db.execute('SELECT blob_path FROM document_blob_cleanup WHERE next_attempt<=? '
                             'ORDER BY created_at,blob_path LIMIT ?', (float('inf') if force else time.time(), limit)).fetchall()
            if not due:
                return 0, db.execute('SELECT count(*) FROM document_blob_cleanup').fetchone()[0]
            db.execute('BEGIN IMMEDIATE')
            # Re-read after taking the write lock: another worker/API may have
            # consumed these intents while this connection waited for the lock.
            for candidate in due:
                relative = candidate['blob_path']
                if not db.execute('SELECT 1 FROM document_blob_cleanup WHERE blob_path=?', (relative,)).fetchone():
                    continue
                if db.execute('SELECT 1 FROM assets WHERE blob_path=? UNION ALL SELECT 1 FROM revisions WHERE blob_path=? LIMIT 1', (relative, relative)).fetchone():
                    db.execute('DELETE FROM document_blob_cleanup WHERE blob_path=?', (relative,))
                    continue
                path = (self.store.root / relative).resolve()
                # Only content-addressed managed files are candidates. Never
                # remove directories or caller-supplied filesystem paths.
                if not any(path.is_relative_to(self.store.root / kind) for kind in ('blobs', 'assets')) or not path.is_file():
                    db.execute('DELETE FROM image_dimensions WHERE blob_path=?', (relative,))
                    db.execute('DELETE FROM document_blob_cleanup WHERE blob_path=?', (relative,))
                    continue
                try:
                    path.unlink()
                except OSError:
                    db.execute('UPDATE document_blob_cleanup SET next_attempt=? WHERE blob_path=?',
                               (time.time() + 5, relative))
                    continue
                collected += 1
                db.execute('DELETE FROM image_dimensions WHERE blob_path=?', (relative,))
                db.execute('DELETE FROM document_blob_cleanup WHERE blob_path=?', (relative,))
            pending = db.execute('SELECT count(*) FROM document_blob_cleanup').fetchone()[0]
        return collected, pending
