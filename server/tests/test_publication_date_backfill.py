"""Synthetic isolation tests for the explicitly reviewed historical date update."""
import copy
from contextlib import closing
import gc
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/backfill-publication-dates.py'
BACKFILL = runpy.run_path(str(SCRIPT))


class PublicationDateBackfillTests(unittest.TestCase):
    def setUp(self):
        temporary_root = SCRIPT.parents[1] / '.test-runs'
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='publication-backfill-', dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.temporary.name)
        self.data = self.root / 'isolated-data'
        (self.data / 'blobs').mkdir(parents=True)
        self.database = self.data / 'library.sqlite3'
        self.backup = self.root / 'operation'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript('''
                CREATE TABLE sources(id TEXT PRIMARY KEY,current_revision TEXT,url TEXT,
                    created_at TEXT,favorite INTEGER,read INTEGER,selected INTEGER,tags_json TEXT);
                CREATE TABLE revisions(id TEXT PRIMARY KEY,source_id TEXT,title TEXT,blob_path TEXT,
                    blob_hash TEXT,published_at TEXT,parser_version TEXT,text_content TEXT);
                CREATE TABLE blocks(id TEXT PRIMARY KEY,revision_id TEXT,text TEXT);
                CREATE TABLE jobs(id TEXT PRIMARY KEY,source_id TEXT,status TEXT,input_json TEXT);
                CREATE TABLE writing_projects(id TEXT PRIMARY KEY,body TEXT,version INTEGER);
                CREATE TABLE writing_runs(id TEXT PRIMARY KEY,snapshot_json TEXT);
                CREATE TABLE document_translations(id TEXT PRIMARY KEY,text TEXT,locked INTEGER);
            ''')
            for source in ['accept-a', 'accept-b', 'unknown'] + [f'existing-{i}' for i in range(8)]:
                raw = f'<html><h1>{source}</h1><p>Immutable original {source}</p></html>'.encode()
                name = f'blobs/{source}.html'
                (self.data / name).write_bytes(raw)
                published = '2020-01-01' if source.startswith('existing-') else None
                db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                           (source, f'rev-{source}', '', '2026-10-27T12:34:56Z', 1, 1, 1, '["manual"]'))
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)',
                           (f'rev-{source}', source, f'Original title {source}', name,
                            hashlib.sha256(raw).hexdigest(), published, 'frozen-parser', f'Original prose {source}'))
                db.execute('INSERT INTO blocks VALUES(?,?,?)', (f'block-{source}', f'rev-{source}', 'Exact quote'))
            db.execute('INSERT INTO writing_projects VALUES(?,?,?)', ('manuscript', 'Saved manual prose', 5))
            db.execute('INSERT INTO writing_runs VALUES(?,?)', ('old-run', '{"publishedAt":null,"frozen":true}'))
            db.execute('INSERT INTO document_translations VALUES(?,?,?)', ('translation', 'Locked translation', 1))
        with closing(BACKFILL['connect'](self.data, readonly=True)) as db:
            documents = [BACKFILL['current_document'](db, source) for source in ('accept-a', 'accept-b', 'unknown')]
        self.scope = {'total': 11, 'missing': 3, 'documents': documents}

    @staticmethod
    def inspector(raw, document):
        if document['source_id'] == 'unknown':
            return {'date': None, 'evidence': [], 'rejected': [], 'reason': 'no_explicit_publication_date'}
        return {'date': '2024-02-29', 'evidence': [{'raw': 'Published: 2024-02-29',
                'locator': '/html/body/header/div', 'kind': 'explicit_publication_label'}],
                'rejected': [], 'reason': 'explicit_publication'}

    def plan_and_review(self, approved=('accept-a',)):
        plan = BACKFILL['make_plan'](self.data, self.scope, inspector=self.inspector)
        return plan, {'plan_hash': BACKFILL['digest'](plan), 'approved_source_ids': list(approved)}

    def apply(self, plan, review, inspector=None):
        return BACKFILL['apply_plan'](self.data, plan, review, self.backup,
                                      inspector=inspector or self.inspector)

    def snapshot(self):
        with closing(sqlite3.connect(self.database)) as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: db.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() for name in names}

    def dates(self):
        with closing(sqlite3.connect(self.database)) as db:
            return dict(db.execute('SELECT source_id,published_at FROM revisions'))

    def test_plan_is_read_only_and_preserves_the_frozen_scope(self):
        before = self.snapshot()
        original = copy.deepcopy(self.scope)
        calls = []

        def inspect(raw, document):
            calls.append((raw, document['source_id']))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), document['blob_hash'])
            return self.inspector(raw, document)

        plan = BACKFILL['make_plan'](self.data, self.scope, inspector=inspect)
        self.assertEqual(plan['total'], 3)
        self.assertEqual(plan['proposed'], 2)
        self.assertEqual(plan['scope_hash'], BACKFILL['digest'](self.scope))
        self.assertEqual([source for _, source in calls], ['accept-a', 'accept-b', 'unknown'])
        self.assertEqual(self.scope, original)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())

    def test_apply_updates_only_approved_date_preserving_originals_existing_dates_and_history(self):
        plan, review = self.plan_and_review()
        before = self.snapshot()
        blobs = {path.name: path.read_bytes() for path in (self.data / 'blobs').iterdir()}
        result = self.apply(plan, review)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['kept_import_fallback'], 2)
        self.assertTrue(result['protected_unchanged'])
        self.assertTrue(result['snapshot_hashes_unchanged'])
        after = self.snapshot()
        expected_revisions = [tuple('2024-02-29' if i == 5 and row[1] == 'accept-a' else value
                                    for i, value in enumerate(row)) for row in before['revisions']]
        self.assertEqual(after['revisions'], expected_revisions)
        self.assertEqual({key: value for key, value in after.items() if key != 'revisions'},
                         {key: value for key, value in before.items() if key != 'revisions'})
        self.assertEqual(self.dates()['accept-b'], None)
        self.assertEqual(self.dates()['unknown'], None)
        self.assertEqual(sum(value == '2020-01-01' for value in self.dates().values()), 8)
        self.assertEqual({path.name: path.read_bytes() for path in (self.data / 'blobs').iterdir()}, blobs)
        backup = json.loads((self.backup / 'before.json').read_text(encoding='utf-8'))
        receipt = json.loads((self.backup / 'receipt.json').read_text(encoding='utf-8'))
        self.assertEqual(backup['plan_hash'], review['plan_hash'])
        self.assertEqual(backup['records'][0]['old_published_at'], None)
        self.assertEqual(backup['records'][0]['new_published_at'], '2024-02-29')
        self.assertEqual(receipt['records'], backup['records'])

    def test_invalid_frozen_scope_is_rejected_without_writes(self):
        before = self.snapshot()
        for scope in ({**self.scope, 'missing': 4},
                      {**self.scope, 'documents': [self.scope['documents'][0]] * 3}):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'Invalid frozen'):
                BACKFILL['make_plan'](self.data, scope, inspector=self.inspector)
        self.assertEqual(self.snapshot(), before)

    def test_plan_rejects_changed_metadata_and_existing_publication(self):
        altered = copy.deepcopy(self.scope)
        altered['documents'][0]['title'] = 'Changed title'
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            BACKFILL['make_plan'](self.data, altered, inspector=self.inspector)
        with closing(BACKFILL['connect'](self.data, readonly=True)) as db:
            existing = BACKFILL['current_document'](db, 'existing-0')
        existing_scope = {'missing': 1, 'documents': [existing]}
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'existing publication'):
            BACKFILL['make_plan'](self.data, existing_scope, inspector=self.inspector)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_change_to_unapproved_source_aborts_entire_apply(self):
        plan, review = self.plan_and_review()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE sources SET current_revision=? WHERE id=?', ('missing-revision', 'unknown'))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Concurrent source/revision change'):
            self.apply(plan, review)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.backup / 'before.json').exists())

    def test_changed_snapshot_aborts_plan_and_apply_without_writes(self):
        plan, review = self.plan_and_review()
        (self.data / 'blobs/accept-b.html').write_bytes(b'Changed snapshot')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Original snapshot changed'):
            BACKFILL['make_plan'](self.data, self.scope, inspector=self.inspector)
        with self.assertRaisesRegex(ValueError, 'Original snapshot changed'):
            self.apply(plan, review)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_publication_date_is_never_overwritten(self):
        plan, review = self.plan_and_review()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE revisions SET published_at=? WHERE source_id=?', ('2021-05-06', 'accept-a'))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Concurrent source/revision change'):
            self.apply(plan, review)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.dates()['accept-a'], '2021-05-06')

    def test_queued_or_running_target_work_aborts_without_writes(self):
        plan, review = self.plan_and_review()
        for status in ('queued', 'running'):
            with self.subTest(status=status):
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute('DELETE FROM jobs')
                    db.execute('INSERT INTO jobs VALUES(?,?,?,?)', ('active', 'accept-a', status, '{"frozen":true}'))
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'queued/running work'):
                    self.apply(plan, review)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.backup / 'before.json').exists())

    def test_unrelated_active_work_and_completed_target_work_are_unchanged(self):
        plan, review = self.plan_and_review()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)', ('unrelated', 'existing-0', 'running', '{"frozen":true}'))
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)', ('completed', 'accept-a', 'succeeded', '{"frozen":true}'))
        before = self.snapshot()['jobs']
        self.apply(plan, review)
        self.assertEqual(self.snapshot()['jobs'], before)

    def test_changed_review_hash_or_plan_aborts_before_backup(self):
        plan, review = self.plan_and_review()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Reviewed plan hash changed'):
            self.apply(plan, {**review, 'plan_hash': 'wrong-hash'})
        plan['documents'][0]['extraction']['date'] = '2099-01-01'
        with self.assertRaisesRegex(ValueError, 'Reviewed plan hash changed'):
            self.apply(plan, review)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())

    def test_changed_extraction_evidence_aborts_apply(self):
        plan, review = self.plan_and_review()
        before = self.snapshot()

        def inspect(raw, document):
            result = self.inspector(raw, document)
            result['reason'] = 'Changed extraction policy'
            return result

        with self.assertRaisesRegex(ValueError, 'Extraction or evidence changed'):
            self.apply(plan, review, inspector=inspect)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_unknown_and_undated_approvals_are_rejected(self):
        plan, review = self.plan_and_review()
        before = self.snapshot()
        for approved in (['accept-a', 'accept-a'], ['not-in-scope'], ['unknown']):
            with self.subTest(approved=approved), self.assertRaises(ValueError):
                self.apply(plan, {**review, 'approved_source_ids': approved})
        self.assertEqual(self.snapshot(), before)

    def test_existing_backup_or_receipt_prevents_reuse(self):
        plan, review = self.plan_and_review()
        self.backup.mkdir()
        before = self.snapshot()
        for filename in ('before.json', 'receipt.json'):
            target = self.backup / filename
            target.write_text('{}', encoding='utf-8')
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'already used'):
                self.apply(plan, review)
            target.unlink()
        self.assertEqual(self.snapshot(), before)

    def test_committed_operation_cannot_be_replayed(self):
        plan, review = self.plan_and_review()
        self.apply(plan, review)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.apply(plan, review)
        self.assertEqual(self.snapshot(), before)

    def test_mid_batch_sql_error_rolls_back_all_updates(self):
        plan, review = self.plan_and_review(('accept-a', 'accept-b'))
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript('''CREATE TRIGGER fail_second BEFORE UPDATE OF published_at ON revisions
                WHEN OLD.source_id='accept-b' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;''')
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic failure'):
            self.apply(plan, review)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue((self.backup / 'before.json').exists())
        self.assertFalse((self.backup / 'receipt.json').exists())
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.apply(plan, review)

    def test_protected_side_effect_is_detected_and_transaction_rolled_back(self):
        plan, review = self.plan_and_review()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript('''CREATE TRIGGER tamper_source AFTER UPDATE OF published_at ON revisions
                BEGIN UPDATE sources SET favorite=0 WHERE id=NEW.source_id; END;''')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Protected data changed'):
            self.apply(plan, review)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue((self.backup / 'before.json').exists())
        self.assertFalse((self.backup / 'receipt.json').exists())

    def test_snapshot_path_cannot_escape_blob_storage(self):
        document = dict(self.scope['documents'][0], blob_path='../outside.html')
        with self.assertRaisesRegex(ValueError, 'outside blob storage'):
            BACKFILL['read_snapshot'](self.data, document)


if __name__ == '__main__':
    unittest.main()
