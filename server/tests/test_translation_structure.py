"""Translation publication validates structure, not semantic correctness.

All documents and provider responses below are synthetic. Changed amounts and
untranslated prose intentionally pass the structural gate; this makes no claim
that those outputs are accurate translations.
"""
import json

import pytest

from server.enrichment import EnrichmentService
from server.store import Store
from server.tests.test_enrichment import Gateway, execute, publish, publish_table


def piece(identifier='p:0', text='The price is $1 million.', kind='paragraph'):
    return {'id': identifier, 'kind': kind, 'text': text}


@pytest.mark.parametrize('translated', [
    '价格为100万美元。',
    '价格为 $100万。',
    '价格为9港元。',
    'The price is $1 million.',
    'This response is still entirely in English.',
    '已省略金额。',
])
def test_structural_gate_does_not_score_money_language_or_identical_prose(translated):
    result = EnrichmentService._translation(
        {'segments': [{'id': 'p:0', 'text': translated}]}, [piece()])
    assert result == {'segments': [{'id': 'p:0', 'text': translated}]}


@pytest.mark.parametrize('value', [
    {},
    {'segments': None},
    {'segments': {}},
    {'segments': []},
    {'segments': [{'id': 'p:0', 'text': '正文'}, {'id': 'extra', 'text': '多余'}]},
    {'segments': [{'id': 'unknown', 'text': '正文'}]},
    {'segments': [None]},
    {'segments': [{'text': '缺编号'}]},
    {'segments': [{'id': 'p:0'}]},
    {'segments': [{'id': 'p:0', 'text': None}]},
    {'segments': [{'id': 'p:0', 'text': 12}]},
    {'segments': [{'id': 'p:0', 'text': ''}]},
    {'segments': [{'id': 'p:0', 'text': ' \n\t '}]},
    {'segments': [{'id': 'p:0', 'text': '字' * 30001}]},
])
def test_structural_gate_rejects_incomplete_or_invalid_segments(value):
    with pytest.raises(ValueError):
        EnrichmentService._translation(value, [piece()])


def test_duplicate_id_cannot_substitute_for_missing_paragraph():
    pieces = [piece('a:0'), piece('b:0')]
    with pytest.raises(ValueError):
        EnrichmentService._translation({'segments': [
            {'id': 'a:0', 'text': '第一段'},
            {'id': 'a:0', 'text': '第二段'},
        ]}, pieces)


def test_program_restores_input_order_and_keeps_simplified_normalization():
    pieces = [piece('a:0'), piece('b:0')]
    result = EnrichmentService._translation({'segments': [
        {'id': 'b:0', 'text': '第二段資料'},
        {'id': 'a:0', 'text': '  第一段譯文  '},
    ]}, pieces)
    assert result['segments'] == [
        {'id': 'a:0', 'text': '第一段译文'},
        {'id': 'b:0', 'text': '第二段资料'},
    ]


def test_cell_line_breaks_do_not_become_table_rows():
    result = EnrichmentService._translation(
        {'segments': [{'id': 'cell:0', 'text': '  第一行\n第二行\t末尾  '}]},
        [piece('cell:0', kind='table_cell')])
    assert result['segments'][0]['text'] == '第一行 第二行 末尾'


@pytest.fixture
def workbench(tmp_path):
    store = Store(tmp_path / 'isolated-translation-structure')
    models = Gateway()
    return store, models, EnrichmentService(store, models)


def test_table_publication_restores_original_cells_empty_cells_and_order(workbench):
    store, models, service = workbench
    original = [['Metric', 'Amount', ''], ['Revenue', '$1 million', 'Note']]
    source = publish_table(store, original)
    replacements = {
        'Metric': '指标', 'Amount': '金额', 'Revenue': '收入',
        '$1 million': '9港元', 'Note': '第一行\n第二行',
    }

    def reverse_cells(result):
        payload = json.loads(models.calls[-1][1][1]['content'])
        texts = {item['id']: item['text'] for item in payload['blocks']}
        return {'segments': [dict(segment, text=replacements.get(
            texts[segment['id']], '保持段落位置'))
            for segment in reversed(result['segments'])]}

    models.rewrite = reverse_cells
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['translation']['result']
    table = next(segment for segment in result['segments'] if 'table' in segment)
    assert table['table'] == [['指标', '金额', ''], ['收入', '9港元', '第一行 第二行']]
    assert [len(row) for row in table['table']] == [3, 3]
    assert store.detail(source)['tables'] == [original]
    assert [segment['blockId'] for segment in result['segments']] == [
        block['id'] for block in store.detail(source)['blocks']]


def test_later_batch_structure_failure_never_publishes_partial_translation(workbench):
    store, models, service = workbench
    models.config.update(contextChars=7000, maxOutputTokens=1400)
    source = publish(store, 'The archive retains the original evidence. ' * 150)

    def omit_second_batch(result):
        return result if len(models.calls) == 1 else {'segments': []}

    models.rewrite = omit_second_batch
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'failed'
    assert len(models.calls) == 2
    assert service.detail(source)['translation']['result'] is None
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0


def test_invalid_regeneration_preserves_previous_success_and_original(workbench):
    store, models, service = workbench
    original = 'The reported price is $1 million under this synthetic contract.'
    source = publish(store, original)
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    previous = service.detail(source)['translation']['result']
    models.rewrite = lambda result: {'segments': []}
    service.queue(source, ['translation'], force=True)
    assert execute(store, service)['status'] == 'failed'
    stage = service.detail(source)['translation']
    assert stage['previousSuccess'] is True and stage['result'] == previous
    assert store.detail(source)['body'] == [original]


def test_locked_manual_paragraph_is_preserved_while_other_paragraph_regenerates(workbench):
    store, models, service = workbench
    source = publish(store, [
        'The first synthetic paragraph reports $5.',
        'The second synthetic paragraph reports $6.',
    ])
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    first_id = store.detail(source)['blocks'][0]['id']
    service.edit_translation(source, first_id, '人工锁定金额9港元。', locked=True)
    models.rewrite = lambda result: {'segments': [dict(segment, text='新内容10美元。')
                                               for segment in result['segments']]}
    service.queue(source, ['translation'], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    payload = json.loads(models.calls[-1][1][1]['content'])
    assert all(item.get('blockId') != first_id for item in payload['blocks'])
    result = service.detail(source)['translation']['result']
    assert result['segments'][0] == {
        'blockId': first_id, 'text': '人工锁定金额9港元。', 'locked': True, 'edited': True}
    assert result['segments'][1]['text'] == '新内容10美元。'


def test_cancelled_translation_cannot_publish_late_structurally_valid_response(workbench):
    store, models, service = workbench
    source = publish(store, 'The synthetic price is $5 in this isolated cancellation example.')
    job = service.queue(source, ['translation'])[0]
    models.rewrite = lambda result: {'segments': [dict(segment, text='10美元。')
                                               for segment in result['segments']]}
    models.callback = lambda: store.cancel(job['id'])
    assert execute(store, service)['status'] == 'cancelled'
    assert service.detail(source)['translation']['result'] is None
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 0
