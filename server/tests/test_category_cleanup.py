"""Exercise the one-off cleanup only against temporary SQLite fixtures."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SPEC = importlib.util.spec_from_file_location(
    'category_cleanup', Path(__file__).resolve().parents[2] / 'scripts' / 'clear-demo-categories.py')
cleanup_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup_module)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'library.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE sources(id TEXT PRIMARY KEY,identity TEXT,tags_json TEXT,
                tags_manual INTEGER,favorite INTEGER,read INTEGER,selected INTEGER,current_revision TEXT);
            CREATE TABLE revisions(id TEXT PRIMARY KEY,source_id TEXT,text_content TEXT,summary TEXT,blob_hash TEXT);
            CREATE TABLE blocks(id TEXT PRIMARY KEY,revision_id TEXT,text TEXT,locator_json TEXT);
            CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT,input_json TEXT,status TEXT,stage TEXT,error TEXT,updated_at TEXT);
            CREATE TABLE ai_runs(id TEXT PRIMARY KEY,kind TEXT);
            CREATE TABLE ai_outputs(id TEXT PRIMARY KEY,run_id TEXT,result_json TEXT,published INTEGER);
            CREATE TABLE ai_translation_edits(id TEXT PRIMARY KEY,text TEXT,locked INTEGER);
        ''')
        for identity, tags, manual in (
            ('mixed', ['知识管理', '自定义分类', 'AI·软件'], 1),
            ('demo', ['网络安全 · 香港', '未分类'], 1),
            ('custom', ['我的分类'], 1),
            ('empty', [], 1),
        ):
            db.execute('INSERT INTO sources VALUES(?,?,?,?,1,1,1,?)',
                       (identity, 'url:' + identity, json.dumps(tags, ensure_ascii=False), manual, 'revision-' + identity))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',
                       ('revision-' + identity, identity, '不能改动的原文 ' + identity, '原始摘要', 'blob-sha256'))
            db.execute('INSERT INTO blocks VALUES(?,?,?,?)',
                       ('block-' + identity, 'revision-' + identity, '原文段落 ' + identity, '{"paragraph":1}'))
        for status in ('queued', 'running', 'failed', 'cancelled', 'succeeded'):
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL,?)',
                       ('class-' + status, 'ai_classification', '{}', status, status, 'original-time'))
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL,?)',
                   ('class-new', 'ai_classification', '{"taxonomyVersion":"real-taxonomy-v3"}', 'queued', 'queued', 'original-time'))
        for status in ('queued', 'running', 'failed', 'cancelled', 'succeeded'):
            payload = json.dumps({'tags': ['知识管理', '私人分类'], 'path': '/unchanged/source.html', 'url': 'https://example.org'})
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL,?)',
                       ('corpus-' + status, 'corpus', payload, status, status, 'original-time'))
        for run, kind, version in (
            ('old-1', 'classification', 'wenshu-categories-1.0'),
            ('old-2', 'classification', 'wenshu-categories-1.1'),
            ('new', 'classification', 'real-taxonomy-v3'),
            ('summary', 'summary', 'wenshu-categories-1.0'),
            ('translation', 'translation', 'wenshu-categories-1.1'),
        ):
            db.execute('INSERT INTO ai_runs VALUES(?,?)', (run, kind))
            db.execute('INSERT INTO ai_outputs VALUES(?,?,?,1)',
                       ('output-' + run, run, json.dumps({'taxonomyVersion': version, 'text': '保留历史内容'})))
        db.execute("INSERT INTO ai_translation_edits VALUES('edit','保留人工译文',1)")
    return path


def snapshot(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def test_default_dry_run_is_read_only_and_predicts_cleanup(database, tmp_path):
    before = snapshot(database)
    backup = tmp_path / 'backup'
    result = cleanup_module.cleanup(database, backup)
    assert result['mode'] == 'dry_run'
    assert result['before']['sources_with_demo_tags'] == 2
    assert result['projected_after']['demo_tag_assignments'] == 0
    assert result['projected_after']['published_demo_classification_outputs'] == 0
    assert result['backup_path'] is None
    assert not backup.exists()
    assert snapshot(database) == before


def test_apply_preserves_documents_custom_tags_flags_and_history(database, tmp_path):
    before = snapshot(database)
    result = cleanup_module.cleanup(database, tmp_path / 'backup', apply=True)
    assert result['protected_data_verified'] is True
    assert snapshot(Path(result['backup_path'])) == before
    assert result['after']['demo_tag_assignments'] == 0
    assert result['after']['obsolete_classification_jobs_not_superseded'] == 0
    assert result['after']['unfinished_corpus_demo_tag_assignments'] == 0
    with sqlite3.connect(database) as db:
        sources = {row[0]: row for row in db.execute('SELECT id,tags_json,tags_manual,favorite,read,selected FROM sources')}
        assert json.loads(sources['mixed'][1]) == ['自定义分类', 'AI·软件']
        assert sources['mixed'][2:] == (1, 1, 1, 1)
        assert json.loads(sources['demo'][1]) == []
        assert sources['demo'][2:] == (0, 1, 1, 1)
        assert sources['empty'][2] == 1  # A prior deliberate empty manual set stays protected.
        assert sources['custom'][2] == 1
        assert db.execute('SELECT count(*) FROM revisions').fetchone()[0] == 4
        assert db.execute('SELECT text FROM blocks WHERE id=?', ('block-demo',)).fetchone()[0] == '原文段落 demo'
        assert db.execute('SELECT text FROM ai_translation_edits').fetchone()[0] == '保留人工译文'
        outputs = dict(db.execute('SELECT id,published FROM ai_outputs'))
        assert outputs == {'output-old-1': 0, 'output-old-2': 0, 'output-new': 1, 'output-summary': 1, 'output-translation': 1}
        assert all('保留历史内容' in json.loads(row[0])['text'] for row in db.execute('SELECT result_json FROM ai_outputs'))
        for status in ('queued', 'running', 'failed', 'cancelled'):
            assert db.execute('SELECT status,stage FROM jobs WHERE id=?', ('class-' + status,)).fetchone() == ('cancelled', 'superseded')
            payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', ('corpus-' + status,)).fetchone()[0])
            assert payload == {'tags': ['私人分类'], 'path': '/unchanged/source.html', 'url': 'https://example.org'}
        assert db.execute("SELECT status FROM jobs WHERE id='class-new'").fetchone()[0] == 'queued'
        assert db.execute("SELECT status FROM jobs WHERE id='class-succeeded'").fetchone()[0] == 'succeeded'
        completed = json.loads(db.execute("SELECT input_json FROM jobs WHERE id='corpus-succeeded'").fetchone()[0])
        assert completed['tags'] == ['知识管理', '私人分类']


def test_second_apply_is_idempotent_and_uses_another_backup(database, tmp_path):
    cleanup_module.cleanup(database, tmp_path / 'first', apply=True)
    cleaned = snapshot(database)
    result = cleanup_module.cleanup(database, tmp_path / 'second', apply=True)
    assert result['changed_rows'] == {'sources': 0, 'jobs': 0, 'ai_outputs': 0}
    assert result['before'] == result['after']
    assert snapshot(database) == cleaned


def test_protected_data_trigger_rolls_back_every_change(database, tmp_path):
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TRIGGER corrupt_body AFTER UPDATE OF tags_json ON sources
            BEGIN UPDATE blocks SET text='unexpected change'; END''')
    before = snapshot(database)
    with pytest.raises(ValueError, match='Protected data changed'):
        cleanup_module.cleanup(database, tmp_path / 'backup', apply=True)
    assert snapshot(database) == before
    assert snapshot(tmp_path / 'backup' / database.name) == before


def test_malformed_tags_abort_without_changes(database, tmp_path):
    with sqlite3.connect(database) as db:
        db.execute("UPDATE sources SET tags_json='{}' WHERE id='demo'")
    before = snapshot(database)
    with pytest.raises(ValueError, match='JSON array'):
        cleanup_module.cleanup(database, tmp_path / 'backup', apply=True)
    assert snapshot(database) == before
    assert not (tmp_path / 'backup').exists()


def test_refuses_existing_backup_and_relative_database(database, tmp_path):
    backup = tmp_path / 'backup'
    backup.mkdir()
    with pytest.raises(ValueError, match='must not already exist'):
        cleanup_module.cleanup(database, backup, apply=True)
    with pytest.raises(ValueError, match='absolute path'):
        cleanup_module.cleanup(Path('library.sqlite3'), tmp_path / 'new')
    with pytest.raises(ValueError, match='requires --backup-dir'):
        cleanup_module.cleanup(database, apply=True)
