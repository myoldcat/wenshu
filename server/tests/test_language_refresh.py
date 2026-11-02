"""Offline metadata migration, using isolated SQLite and no model services."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from server.language_refresh import refresh_languages


SCHEMA = '''
CREATE TABLE sources(id TEXT PRIMARY KEY,current_revision TEXT,tags_json TEXT);
CREATE TABLE revisions(id TEXT PRIMARY KEY,source_id TEXT,title TEXT,language TEXT);
CREATE TABLE blocks(id TEXT PRIMARY KEY,revision_id TEXT,ordinal INTEGER,kind TEXT,text TEXT);
CREATE TABLE document_languages(revision_id TEXT PRIMARY KEY,code TEXT,identification TEXT,policy_version INTEGER);
CREATE TABLE document_language_analysis(revision_id TEXT PRIMARY KEY,hint TEXT,version INTEGER,
    needs_translation INTEGER,detected_code TEXT);
CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT);
CREATE TABLE ai_runs(seq INTEGER PRIMARY KEY,id TEXT,source_id TEXT,revision_id TEXT,kind TEXT);
CREATE TABLE ai_outputs(id TEXT PRIMARY KEY,run_id TEXT,result_json TEXT,published INTEGER);
CREATE TABLE ai_translation_edits(id TEXT PRIMARY KEY,text TEXT,locked INTEGER);
CREATE TABLE document_chinese(revision_id TEXT PRIMARY KEY,title TEXT,summary TEXT);
CREATE TABLE translated_blocks(revision_id TEXT,block_id TEXT,text TEXT);
CREATE TABLE assets(id TEXT PRIMARY KEY,data BLOB);
CREATE TABLE ai_calls(id TEXT PRIMARY KEY,result BLOB);
CREATE TABLE ai_checkpoints(id TEXT PRIMARY KEY,result_json TEXT);
CREATE VIRTUAL TABLE search_index USING fts5(source_id UNINDEXED,body_tokens);
'''


@pytest.fixture
def library(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    with sqlite3.connect(data / 'library.sqlite3') as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO ai_translation_edits VALUES('edit','人工譯文原樣保留',1)")
        db.execute("INSERT INTO assets VALUES('asset',?)", (b'\x00\xff\x80',))
        db.execute("INSERT INTO ai_calls VALUES('call',?)", (b'\x80\x00',))
        db.execute("INSERT INTO ai_checkpoints VALUES('checkpoint','{}')")
    return data


def add_revision(data, revision, text, *, code='en', parser='en', version=0,
                 identification='model', source=None, current=True, title='Original source title'):
    source = source or 'source_' + revision
    with sqlite3.connect(data / 'library.sqlite3') as db:
        db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)', (source, revision, '["人工主题"]'))
        if current:
            db.execute('UPDATE sources SET current_revision=? WHERE id=?', (revision, source))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (revision, source, title, parser))
        db.execute('INSERT INTO blocks VALUES(?,?,?,?,?)', ('block_' + revision, revision, 0, 'paragraph', text))
        if code is not None:
            db.execute('INSERT INTO document_languages VALUES(?,?,?,?)', (revision, code, identification, version))
        db.execute('INSERT INTO document_language_analysis VALUES(?,?,?,?,?)', (revision, code or parser, 2, 1, code or parser))
        db.execute('INSERT INTO document_chinese VALUES(?,?,?)', (revision, '现有中文标题', '现有中文摘要'))
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)', (revision, 'block_' + revision, '现有译文保持不变'))
        db.execute('INSERT INTO search_index VALUES(?,?)', (source, '现有检索内容'))
    return source


def metadata(data):
    with sqlite3.connect(data / 'library.sqlite3') as db:
        return tuple(db.execute('SELECT * FROM ' + table + ' ORDER BY revision_id').fetchall()
                     for table in ('document_languages', 'document_language_analysis'))


def classification(data, revision, code, shares, *, version=3, published=1):
    with sqlite3.connect(data / 'library.sqlite3') as db:
        source = db.execute('SELECT source_id FROM revisions WHERE id=?', (revision,)).fetchone()[0]
        db.execute('INSERT INTO ai_runs(id,source_id,revision_id,kind) VALUES(?,?,?,?)',
                   ('run_' + revision, source, revision, 'classification'))
        result = {'sourceLanguage': code, 'sourceLanguageShares': shares, 'languagePolicyVersion': version}
        db.execute('INSERT INTO ai_outputs VALUES(?,?,?,?)',
                   ('out_' + revision, 'run_' + revision, json.dumps(result), published))


@pytest.mark.parametrize('old_code', ['en', 'mixed', 'zh-Hant'])
def test_refresh_stale_chinese_updates_both_tables_without_translation_or_source_changes(library, tmp_path, old_code):
    original = '這份資料記錄資訊治理，保留來源與原始證據，方便後續閱讀與核查。'
    add_revision(library, 'revision', original, code=old_code)
    result = refresh_languages(library, tmp_path / 'reports', apply=True)
    assert result['changedRevisions'] == 1
    assert result['protectedUnchanged']
    assert result['protectedBefore'] == result['protectedAfter']
    assert {'sources', 'revisions', 'blocks', 'assets', 'ai_runs', 'ai_outputs', 'ai_calls',
            'ai_checkpoints', 'ai_translation_edits', 'document_chinese', 'translated_blocks',
            'search_index', 'jobs'} <= set(result['protectedBefore'])
    assert metadata(library) == ([('revision', 'zh', 'fallback', 3)], [('revision', 'zh', 3, 0, 'zh')])
    with sqlite3.connect(library / 'library.sqlite3') as db:
        assert db.execute('SELECT text FROM blocks').fetchone()[0] == original
        assert db.execute('SELECT text,locked FROM ai_translation_edits').fetchone() == ('人工譯文原樣保留', 1)
        assert db.execute('SELECT text FROM translated_blocks').fetchone()[0] == '现有译文保持不变'


def test_dry_run_opens_read_only_and_does_not_modify_existing_database(library, tmp_path, monkeypatch):
    add_revision(library, 'revision', '这份资料保留原文和证据。', code='en')
    before = hashlib.sha256((library / 'library.sqlite3').read_bytes()).hexdigest()
    before_rows = metadata(library)
    original_connect = sqlite3.connect
    observed = []

    def connect(database_uri, **kwargs):
        observed.append((database_uri, kwargs))
        return original_connect(database_uri, **kwargs)

    monkeypatch.setattr('server.language_refresh.sqlite3.connect', connect)
    result = refresh_languages(library, tmp_path / 'reports')
    assert observed[0][0].endswith('?mode=ro')
    assert observed[0][1]['uri'] is True
    assert result['apply'] is False and result['changedRevisions'] == 1
    assert result['rollbackSnapshot'] is None
    assert hashlib.sha256((library / 'library.sqlite3').read_bytes()).hexdigest() == before
    assert metadata(library) == before_rows


def test_every_historical_revision_is_refreshed_and_english_hint_is_preserved(library, tmp_path):
    source = add_revision(library, 'old', '繁體中文資料保留來源及原始證據。', code='mixed')
    add_revision(library, 'current', 'The records remain available for every later review.',
                 code=None, parser='en-US', source=source)
    result = refresh_languages(library, tmp_path / 'reports', apply=True)
    assert result['totalRevisions'] == 2 and result['currentRevisions'] == 1
    assert result['afterCounts'] == {'en': 1, 'zh': 1}
    assert {row['revisionId']: row['current'] for row in result['changes']} == {'old': False, 'current': True}
    assert metadata(library)[0] == [('current', 'en', 'fallback', 3), ('old', 'zh', 'fallback', 3)]


def test_valid_current_model_shares_preserve_same_script_semantic_result(library, tmp_path):
    # Script-only counting combines the two Latin languages above 30%; the
    # validated semantic result has neither language individually reaching 30%.
    body = '資料完整保留原始證據。' * 42 + '\n' + 'We see it all. ' * 29 + 'On garde ici. ' * 29
    add_revision(library, 'semantic', body, code='zh', version=3)
    classification(library, 'semantic', 'zh', {'zh': .42, 'en': .29, 'fr': .29})
    result = refresh_languages(library, tmp_path / 'reports', apply=True)
    assert result['reusedSemanticRecords'] == 1
    assert metadata(library) == ([('semantic', 'zh', 'model', 3)], [('semantic', 'zh', 3, 0, 'zh')])


@pytest.mark.parametrize('shares,version,published', [
    ({'zh': True}, 3, 1),
    ({'zh': float('nan')}, 3, 1),
    ({'zh': 1.5}, 3, 1),
    ({'zh': .42}, 3, 1),
    ({'mixed': 1}, 3, 1),
    ({'und': 1}, 3, 1),
    ({'en': 1}, 3, 1),
    ({'zh': 1}, 2, 1),
    ({'zh': 1}, 3, 0),
])
def test_invalid_or_unpublished_semantic_provenance_is_reidentified_offline(library, tmp_path, shares, version, published):
    add_revision(library, 'revision', 'The evidence remains available for public review.', code='zh', version=3)
    classification(library, 'revision', 'zh', shares, version=version, published=published)
    result = refresh_languages(library, tmp_path / 'reports', apply=True)
    assert result['reusedSemanticRecords'] == 0
    assert metadata(library)[0] == [('revision', 'other', 'fallback', 3)]


def test_headings_code_titles_and_small_foreign_passages_do_not_outweigh_body(library, tmp_path):
    add_revision(library, 'revision', '這份資料保留來源與原始證據，方便後續閱讀與核查。' * 30, code='mixed')
    with sqlite3.connect(library / 'library.sqlite3') as db:
        db.executemany('INSERT INTO blocks VALUES(?,?,?,?,?)', [
            ('heading', 'revision', 1, 'heading', 'The title and names ' * 200),
            ('code', 'revision', 2, 'code', 'print("hello world")' * 200),
            ('foreign', 'revision', 3, 'paragraph', 'The records remain available for review.')])
    refresh_languages(library, tmp_path / 'reports', apply=True)
    assert metadata(library)[0][0][1] == 'zh'


def test_unknown_empty_and_entity_only_sources_never_default_to_english(library, tmp_path):
    add_revision(library, 'empty', '', code='en', title='')
    add_revision(library, 'entities', 'OpenAI Google Nvidia Microsoft ChatGPT GPU API', code='en', title='')
    add_revision(library, 'unknown', 'Public records remain available for later review.', code=None, parser='und', title='')
    result = refresh_languages(library, tmp_path / 'reports', apply=True)
    assert result['afterCounts'] == {'other': 1, 'und': 2}
    assert metadata(library)[1] == [('empty', 'und', 3, 0, 'und'), ('entities', 'und', 3, 0, 'und'),
                                    ('unknown', 'other', 3, 1, 'other')]


def test_snapshot_contains_only_language_metadata_and_second_apply_is_idempotent(library, tmp_path):
    add_revision(library, 'revision', '原始中文资料保留完整证据。', code='en')
    before = metadata(library)
    first = refresh_languages(library, tmp_path / 'reports', apply=True)
    snapshot_path = Path(first['rollbackSnapshot'])
    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
    assert set(snapshot['tables']) == {'document_languages', 'document_language_analysis'}
    if os.name != 'nt':
        assert snapshot_path.stat().st_mode & 0o777 == 0o600
    second = refresh_languages(library, tmp_path / 'reports', apply=True)
    assert second['changedRevisions'] == second['languageRowsChanged'] == second['analysisRowsChanged'] == 0
    assert second['rollbackSnapshot'] is None
    # The two-table snapshot is sufficient to restore every original metadata
    # row, without loading or replacing any source/translation database data.
    with sqlite3.connect(library / 'library.sqlite3') as db:
        for table, content in snapshot['tables'].items():
            db.execute('DELETE FROM ' + table)
            placeholders = ','.join('?' for _ in content['columns'])
            db.executemany('INSERT INTO ' + table + ' VALUES(' + placeholders + ')', content['rows'])
    assert metadata(library) == before


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_apply_rejects_in_flight_jobs_before_snapshot_or_mutation(library, tmp_path, status):
    add_revision(library, 'revision', '中文资料保留原文证据。', code='en')
    with sqlite3.connect(library / 'library.sqlite3') as db:
        db.execute('INSERT INTO jobs VALUES(?,?)', ('active', status))
    before = metadata(library)
    with pytest.raises(ValueError, match='no queued or running jobs'):
        refresh_languages(library, tmp_path / 'reports', apply=True)
    assert metadata(library) == before
    assert not (tmp_path / 'reports').exists()
    assert refresh_languages(library, tmp_path / 'dry-run')['changedRevisions'] == 1


def test_protected_table_mismatch_rolls_back_both_language_tables(library, tmp_path):
    add_revision(library, 'revision', '中文资料保留原文证据。', code='en')
    with sqlite3.connect(library / 'library.sqlite3') as db:
        db.executescript('''CREATE TRIGGER unexpected_side_effect AFTER UPDATE ON document_languages
            BEGIN UPDATE translated_blocks SET text='unexpected translation modification'; END;''')
    before = metadata(library)
    with pytest.raises(RuntimeError, match='Protected non-language tables changed'):
        refresh_languages(library, tmp_path / 'reports', apply=True)
    assert metadata(library) == before
    with sqlite3.connect(library / 'library.sqlite3') as db:
        assert db.execute('SELECT text FROM translated_blocks').fetchone()[0] == '现有译文保持不变'
    assert len(list((tmp_path / 'reports').glob('language-rollback-*.json'))) == 1


def test_cli_default_is_dry_run_and_reports_no_source_or_translated_text(library, tmp_path):
    body_marker = '原始正文不应出现在报告中'
    title_marker = 'Original title must stay private'
    add_revision(library, 'revision', body_marker, title=title_marker)
    result = subprocess.run([sys.executable, '-m', 'server.language_refresh', '--data-dir', str(library),
                             '--report-dir', str(tmp_path / 'reports')], text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    assert body_marker not in result.stdout and title_marker not in result.stdout
    summary = json.loads(result.stdout)
    assert summary['apply'] is False and summary['changes'][0]['afterCode'] == 'zh'
    report = Path(summary['reportPath']).read_text(encoding='utf-8')
    assert body_marker not in report and title_marker not in report
    assert metadata(library)[0] == [('revision', 'en', 'model', 0)]
