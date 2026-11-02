"""Original-body metrics use isolated catalogs and never invoke a model."""
import contextlib
import json

import pytest

from server.document_metrics import BODY_COUNT_VERSION, count_body_words, count_text_words
from server.store import Store, tokenize


def publish(store, blocks, *, url='https://example.org/body-count', title='不计入字数的文章标题'):
    job = store.queue('file', {}, title)
    claimed = store.claim()
    assert claimed['id'] == job['id']
    raw = json.dumps({'blocks': blocks, 'title': title}, ensure_ascii=False).encode('utf-8')
    parsed = {'title': title, 'language': 'en', 'parser_version': 'body-count-test-1',
              'blocks': blocks, 'body': [block['text'] for block in blocks],
              'tables': [[['甲', '42'], ['乙', 'β']]] if any(block['kind'] == 'table' for block in blocks) else []}
    store.publish(claimed, {'url': url}, raw, parsed)
    saved = store.job(job['id'])
    return saved['source_id'], saved['revision_id']


@pytest.mark.parametrize(('text', 'expected'), [
    ('中文AI与café 2026，déjà vu.', 8),
    ('naïve über Straße Ελληνικά Русский العربية हिन्दी', 7),
    ('café cafe\u0301', 2),
    ('\U00020000中文Москва１２３', 4),
    (' --\t\n，。！？🙂👩\u200d💻', 0),
    ('123 abc１２３ 456', 3),
])
def test_unicode_word_units(text, expected):
    assert count_text_words(text) == expected


def test_body_excludes_titles_headings_and_code_but_counts_lists_and_tables_once():
    blocks = [
        {'kind': 'title', 'text': '这是文章标题'},
        {'kind': 'heading', 'text': '这是章节标题'},
        {'kind': 'code', 'text': 'console.log("not prose");'},
        {'kind': 'paragraph', 'text': '正文 café'},
        {'kind': 'list_item', 'text': 'one two'},
        {'kind': 'table', 'text': '甲 | 42\n乙 | β'},
    ]
    assert count_body_words(blocks) == 9


def test_list_search_and_detail_share_original_body_count_despite_localized_metadata(tmp_path):
    store = Store(tmp_path / 'body-count')
    source, revision = publish(store, [
        {'kind': 'heading', 'text': '很长的文章标题不计入正文统计' * 4},
        {'kind': 'paragraph', 'text': '正文 café'},
        {'kind': 'list_item', 'text': 'one two'},
        {'kind': 'table', 'text': '甲 | 42\n乙 | β'},
        {'kind': 'code', 'text': 'excluded_source_code = 123456789'},
    ])
    with store.connect() as db:
        original_revision = tuple(db.execute('SELECT * FROM revisions WHERE id=?', (revision,)).fetchone())
        original_blocks = [tuple(row) for row in db.execute('SELECT * FROM blocks WHERE revision_id=? ORDER BY ordinal', (revision,))]
        block_id = db.execute('SELECT id FROM blocks WHERE revision_id=? AND ordinal=1', (revision,)).fetchone()['id']
        db.execute('INSERT INTO document_chinese VALUES(?,?,?,?,?)',
                   (revision, '译文标题' * 30, '生成摘要内容' * 30, 'ai_summary', '["关键词"]'))
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)',
                   (revision, block_id, '译文关键词' * 30))
        db.execute("UPDATE search_index SET body_tokens=body_tokens || ' ' || ? WHERE source_id=?",
                   (tokenize('译文关键词' * 30), source))
    listed = store.documents()['documents'][0]
    searched = store.search('译文关键词')['results'][0]
    detail = store.detail(source)
    assert [document['bodyWordCount'] for document in (listed, searched, detail)] == [9, 9, 9]
    assert 'blocks' not in listed and 'body' not in listed and 'tables' not in listed
    assert searched['matchLanguage'] == 'zh-CN'
    assert detail['summary'] == '生成摘要内容' * 30
    assert detail['tables'] == [[['甲', '42'], ['乙', 'β']]]
    with store.connect() as db:
        assert tuple(db.execute('SELECT * FROM revisions WHERE id=?', (revision,)).fetchone()) == original_revision
        assert [tuple(row) for row in db.execute('SELECT * FROM blocks WHERE revision_id=? ORDER BY ordinal', (revision,))] == original_blocks


def test_only_requested_revisions_are_cached_and_historical_counts_remain_isolated(tmp_path):
    store = Store(tmp_path / 'history')
    source, previous = publish(store, [
        {'kind': 'heading', 'text': '标题不计入正文统计' * 4},
        {'kind': 'paragraph', 'text': 'one two three four five six seven eight'},
    ])
    _, current = publish(store, [
        {'kind': 'heading', 'text': '不同标题也不计入正文统计' * 4},
        {'kind': 'paragraph', 'text': 'one two three four five'},
    ])
    _, other = publish(store, [
        {'kind': 'paragraph', 'text': 'a different source has its own original body count'},
    ], url='https://example.org/other')
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM document_body_metrics').fetchone()[0] == 0
    assert store.detail(source)['bodyWordCount'] == 5
    with store.connect() as db:
        assert [row['revision_id'] for row in db.execute('SELECT revision_id FROM document_body_metrics')] == [current]
    assert store.detail(source, previous)['bodyWordCount'] == 8
    documents = {document['id']: document for document in store.documents()['documents']}
    assert documents[source]['revisionId'] == current
    assert documents[source]['bodyWordCount'] == 5
    assert store.search('one')['results'][0]['bodyWordCount'] == 5
    with store.connect() as db:
        assert {row['revision_id'] for row in db.execute('SELECT revision_id FROM document_body_metrics')} == {previous, current, other}


def test_warm_lists_reuse_persistent_counts_without_reading_original_blocks(tmp_path, monkeypatch):
    store = Store(tmp_path / 'cache')
    _, revision = publish(store, [{'kind': 'paragraph', 'text': 'original body content is counted once and cached durably'}])
    first = store.documents()['documents'][0]['bodyWordCount']
    reopened = Store(store.root)
    queries = []
    original_connect = reopened.connect

    @contextlib.contextmanager
    def traced_connect():
        with original_connect() as db:
            db.set_trace_callback(queries.append)
            yield db

    monkeypatch.setattr(reopened, 'connect', traced_connect)
    assert reopened.documents()['documents'][0]['bodyWordCount'] == first
    assert not any('FROM BLOCKS' in ' '.join(query.upper().split()) for query in queries), queries
    with reopened.connect() as db:
        saved = db.execute('SELECT * FROM document_body_metrics WHERE revision_id=?', (revision,)).fetchone()
        assert saved['algorithm_version'] == BODY_COUNT_VERSION
        assert saved['body_word_count'] == first


def test_cache_version_changes_recompute_only_requested_revision(tmp_path):
    store = Store(tmp_path / 'version')
    source, revision = publish(store, [{'kind': 'paragraph', 'text': 'original words stay independent of earlier counting versions'}])
    with store.connect() as db:
        db.execute('INSERT INTO document_body_metrics VALUES(?,?,?)', (revision, BODY_COUNT_VERSION - 1, 999))
    assert store.detail(source)['bodyWordCount'] == 8
    with store.connect() as db:
        assert [(row['algorithm_version'], row['body_word_count']) for row in db.execute(
            'SELECT * FROM document_body_metrics WHERE revision_id=? ORDER BY algorithm_version', (revision,))] == [
                (BODY_COUNT_VERSION - 1, 999), (BODY_COUNT_VERSION, 8)]


def test_zero_is_a_valid_body_count(tmp_path):
    store = Store(tmp_path / 'zero')
    source, _ = publish(store, [
        {'kind': 'heading', 'text': '只有标题和代码而无实质正文的隔离验收资料' * 3},
        {'kind': 'code', 'text': 'zero_body_count = True'},
        {'kind': 'paragraph', 'text': '，。！？ -- 🙂'},
    ])
    assert store.documents()['documents'][0]['bodyWordCount'] == 0
    assert store.detail(source)['bodyWordCount'] == 0
