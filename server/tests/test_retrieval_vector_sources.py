"""Four semantic retrieval sources, original-block provenance and visible budgets.

These are isolated behavioral checks. The deterministic HTTP embedding provider
recognizes fixture markers; it does not assess a real model's language quality.
No configured provider, real article, or writing project is changed.
"""
import copy
import json
import math

import httpx
import pytest

from server.model_gateway import ModelService
from server.retrieval import CHANNELS, QWEN3_QUERY_PREFIX, RetrievalService
from server.store import Store, dumps
from server.tests.test_model_gateway import TestCipher
from server.tests.test_wiki import Gateway, execute, publish
from server.wiki import WikiService


QUERY = '不含样本文字的独立检索主题'
HIT = 'SEMANTIC_FIXTURE_HIT'
WEAK = 'SEMANTIC_FIXTURE_WEAK'
NEUTRAL = ('This isolated background concerns archival cataloguing and preserves '
           'each original record with an immutable revision. ')


class EmbeddingProvider:
    def __init__(self):
        self.requests = []
        self.callback = None

    def __call__(self, request):
        assert request.url.path == '/v1/embeddings'
        body = json.loads(request.content)
        self.requests.append(copy.deepcopy(body))
        if self.callback:
            self.callback(len(self.requests), body)
        values = []
        for index, value in enumerate(body['input']):
            # Context accompanies a passage to resolve its meaning. It must not
            # cause a neighboring neutral paragraph to become the matched unit.
            core = value
            if '【匹配段落】' in core:
                core = core.split('【匹配段落】', 1)[1].split('【后文上下文】', 1)[0]
            if value == QUERY or value == QWEN3_QUERY_PREFIX + QUERY:
                vector = [1.0, 0.0]
            elif HIT in core:
                vector = [.99, math.sqrt(1 - .99 ** 2)]
            elif WEAK in core:
                vector = [.9, math.sqrt(1 - .9 ** 2)]
            else:
                vector = [0.0, 1.0]
            values.append({'index': index, 'embedding': vector})
        # The provider's array order is deliberately different from its input.
        return httpx.Response(200, json={'data': list(reversed(values))})

    def document_inputs(self, start=0):
        return [text for request in self.requests[start:] for text in request['input']
                if text not in (QUERY, QWEN3_QUERY_PREFIX + QUERY)]


@pytest.fixture
def vector_bench(tmp_path):
    store = Store(tmp_path / 'isolated-four-vector-sources')
    provider = EmbeddingProvider()
    models = ModelService(store, cipher=TestCipher(), transport=httpx.MockTransport(provider))
    model = models.save_config({'name': '隔离分源向量模型', 'baseUrl': 'http://semantic.test/v1',
                                'model': 'qwen3-embedding:fixture', 'apiKey': 'private-fixture-value'})
    service = RetrievalService(store, models)
    service.save_settings({'queryExpansion': False, 'rerankEnabled': False, 'embeddingModelId': model['id'],
                           'passagesPerSource': 5,
                           'channels': {key: key == 'vector' for key in CHANNELS}})
    return store, provider, models, service, model


def channel(result):
    return next(value for value in result['channels'] if value['id'] == 'vector')


def by_source(result):
    return {item['sourceId']: item for item in result['items']}


def assert_matches(item, expected_kind):
    matches = item['vectorMatches']
    assert matches and {match['kind'] for match in matches} == {expected_kind}
    assert all(isinstance(match['unitId'], str) and match['unitId'] for match in matches)
    assert all(.9 <= match['cosine'] <= 1 for match in matches)
    assert all(isinstance(match['blockIds'], list) for match in matches)
    return matches


def assert_original_passages(store, item):
    document = store.detail(item['sourceId'], item['revisionId'])
    originals = {block['id']: block['text'] for block in document['blocks']}
    assert all(passage['blockId'] in originals and passage['quote'] in originals[passage['blockId']]
               for passage in item['passages'])


def published_derived(store, *, kind='claim', semantic=HIT, source=None, force=False):
    """A paraphrase marker exists in the derived statement only, never its quote."""
    source = source or publish(store, [NEUTRAL * 5], title='Independent archived source',
                               url=f'https://example.test/vector-derived/{kind}')
    gateway = Gateway()

    def rewrite(result):
        entry = result['entries'][0]
        result['entries'] = [entry]
        result['relations'] = []
        entry.update(type=kind, label='Extracted statement')
        entry['summary']['text'] = semantic + ' is a fixture paraphrase of the original record.'
        entry['sections'][0]['claims'][0]['text'] = semantic + ' is a fixture paraphrase of the original record.'
        return result

    gateway.rewrite = rewrite
    wiki = WikiService(store, gateway)
    wiki.queue([source], force=force)
    job = execute(store, wiki)
    assert job['status'] == 'succeeded', job
    with store.connect() as db:
        row = db.execute('''SELECT p.content_json FROM wiki_pages p
            JOIN wiki_publications u ON u.source_id=p.source_id AND u.run_id=p.run_id
            WHERE p.source_id=?''', (source,)).fetchone()
    return source, json.loads(row['content_json'])


@pytest.mark.parametrize('position', ['late_paragraph', 'late_within_long_block'])
def test_paragraph_vectors_find_content_beyond_old_article_prefix(vector_bench, position):
    store, provider, _, service, _ = vector_bench
    target = HIT + ' This passage is the only semantically relevant fixture text.'
    if position == 'late_paragraph':
        paragraphs = [NEUTRAL * 25, target, NEUTRAL * 4]
        ordinal = 1
    else:
        paragraphs = [NEUTRAL * 25 + target + NEUTRAL * 4]
        ordinal = 0
    assert '\n'.join(paragraphs).index(HIT) > 2400
    source = publish(store, paragraphs, title='Long archive', url='https://example.test/deep-vector')
    target_block = store.detail(source)['blocks'][ordinal]['id']
    result = service.recommend(QUERY)
    item = by_source(result)[source]
    matches = assert_matches(item, 'passage')
    assert {block for match in matches for block in match['blockIds']} == {target_block}
    assert {passage['blockId'] for passage in item['passages']} == {target_block}
    assert all('vector' in passage['channels'] for passage in item['passages'])
    assert any(HIT in passage['quote'] for passage in item['passages'])
    assert any(HIT in value for value in provider.document_inputs())
    assert_original_passages(store, item)


def test_semantic_passages_do_not_include_unmatched_neighbor_blocks(vector_bench):
    store, _, _, service, _ = vector_bench
    source = publish(store, [NEUTRAL * 5, HIT + ' Specific meaningful paragraph.', NEUTRAL * 3],
                     title='Unmatched surrounding paragraphs', url='https://example.test/vector-neighbors')
    blocks = store.detail(source)['blocks']
    result = service.recommend(QUERY)
    item = by_source(result)[source]
    assert {passage['blockId'] for passage in item['passages']} == {blocks[1]['id']}
    assert all(passage['channels'] == ['vector'] for passage in item['passages'])
    assert_original_passages(store, item)


def test_semantic_quote_keeps_the_matched_content_after_character_700(vector_bench):
    store, _, _, service, _ = vector_bench
    paragraph = (NEUTRAL * 6)[:708] + HIT + ' This is the late semantic subject. ' + NEUTRAL[:50]
    assert paragraph.index(HIT) == 708
    assert 700 < len(paragraph) < 1000
    source = publish(store, [paragraph], title='Late subject in one passage',
                     url='https://example.test/vector-quote-tail')
    item = by_source(service.recommend(QUERY))[source]
    assert_matches(item, 'passage')
    assert any(HIT in passage['quote'] for passage in item['passages']), (
        'The actual vector-matched subject must survive the selectable original quote.')
    assert all(passage['channels'] == ['vector'] for passage in item['passages'])
    assert_original_passages(store, item)


@pytest.mark.parametrize('field', ['title', 'summary'])
def test_title_and_summary_are_an_independent_vector_source(vector_bench, field):
    store, provider, _, service, _ = vector_bench
    source = publish(store, [NEUTRAL * 5], title=HIT if field == 'title' else 'Neutral title',
                     url=f'https://example.test/vector-metadata/{field}')
    if field == 'summary':
        with store.connect() as db:
            db.execute('UPDATE revisions SET summary=? WHERE id=?',
                       (HIT + ' appears only in the summary.', store.detail(source)['revisionId']))
    result = service.recommend(QUERY)
    item = by_source(result)[source]
    matches = assert_matches(item, 'title_summary')
    assert all(match['blockIds'] == [] for match in matches)
    assert any('【标题与摘要】' in text and HIT in text for text in provider.document_inputs())
    # An article-level metadata hit cannot claim its preview was vector-matched.
    assert all('vector' not in passage['channels'] for passage in item['passages'])
    assert_original_passages(store, item)


@pytest.mark.parametrize('kind', ['claim', 'fact'])
def test_extracted_statements_are_independent_vectors_with_original_evidence(vector_bench, kind):
    store, provider, _, service, _ = vector_bench
    source, page = published_derived(store, kind=kind)
    original = store.detail(source)
    assert HIT not in '\n'.join(block['text'] for block in original['blocks'])
    result = service.recommend(QUERY)
    item = by_source(result)[source]
    matches = assert_matches(item, kind)
    expected = {ref['blockId'] for ref in page['evidence']}
    assert {block for match in matches for block in match['blockIds']} == expected
    assert {passage['blockId'] for passage in item['passages']} == expected
    assert all(passage['channels'] == ['vector'] for passage in item['passages'])
    assert any(HIT in text for text in provider.document_inputs())
    assert all(HIT not in passage['quote'] for passage in item['passages'])
    assert_original_passages(store, item)


@pytest.mark.parametrize('kind', ['claim', 'fact'])
def test_current_publication_replaces_previously_cached_derived_matches(vector_bench, kind):
    store, provider, _, service, _ = vector_bench
    source, old_page = published_derived(store, kind=kind)
    first = service.recommend(QUERY)
    assert_matches(by_source(first)[source], kind)
    before = len(provider.requests)
    _, current_page = published_derived(store, kind=kind, semantic='No matching paraphrase', source=source, force=True)
    assert current_page['version'] > old_page['version']
    assert current_page['revisionId'] == old_page['revisionId']
    second = service.recommend(QUERY)
    assert second['items'] == []
    assert provider.document_inputs(before), 'The changed published text needs a new vector.'
    assert all(HIT not in text for text in provider.document_inputs(before))


def test_revision_advance_excludes_old_original_and_derived_unit_caches(vector_bench):
    store, _, _, service, _ = vector_bench
    source, page = published_derived(store)
    assert service.recommend(QUERY)['items']
    publish(store, [NEUTRAL * 5 + 'The replacement revision contains a different record.'],
            title='Replacement source', url='https://example.test/vector-derived/claim')
    assert store.detail(source)['revisionId'] != page['revisionId']
    after = service.recommend(QUERY)
    assert after['items'] == []
    assert all(value['matchedUnits'] == 0 for value in after['vectorSources'])


def test_publication_replaced_during_embedding_cannot_return_old_derived_hit(vector_bench):
    store, provider, _, service, _ = vector_bench
    source, old_page = published_derived(store)
    replacements = []

    def replace_publication(count, body):
        if count == 2:  # Query is first; replace while document vectors are in flight.
            replacements.append(published_derived(store, source=source, force=True,
                                                   semantic='Replacement without a semantic match')[1])

    provider.callback = replace_publication
    result = service.recommend(QUERY)
    assert replacements and replacements[0]['version'] > old_page['version']
    assert replacements[0]['revisionId'] == old_page['revisionId']
    assert result['items'] == []


def test_publication_replaced_after_vector_freshness_check_is_excluded_from_snapshot(vector_bench, monkeypatch):
    store, _, _, service, _ = vector_bench
    source, old_page = published_derived(store)
    vector = service._vector
    replacements = []

    def replace_after_vector(*args, **kwargs):
        result = vector(*args, **kwargs)
        assert source in result['matches'], 'The old publication was valid during vector retrieval.'
        replacements.append(published_derived(store, source=source, force=True,
                                               semantic='Replacement without a semantic match')[1])
        return result

    monkeypatch.setattr(service, '_vector', replace_after_vector)
    result = service.recommend(QUERY)
    assert replacements and replacements[0]['version'] > old_page['version']
    assert replacements[0]['revisionId'] == old_page['revisionId']
    assert result['items'] == []
    assert any('版本更新' in warning for warning in result['warnings'])
    with store.connect() as db:
        snapshot = json.loads(db.execute('SELECT content_json FROM retrieval_snapshots WHERE id=?',
                                         (result['id'],)).fetchone()['content_json'])
    assert snapshot['items'] == []


def test_all_unit_caches_invalidate_when_embedding_model_version_changes(vector_bench):
    store, provider, models, service, model = vector_bench
    source = publish(store, [NEUTRAL * 4, HIT + ' Specific paragraph.'],
                     title='Versioned vector source', url='https://example.test/vector-model-version')
    assert by_source(service.recommend(QUERY))[source]
    before = len(provider.requests)
    service.recommend(QUERY)
    assert not provider.document_inputs(before), 'Unchanged document units must reuse their cached vectors.'
    upgraded = models.save_config({'model': 'qwen3-embedding:fixture-v2'}, id=model['id'])
    assert upgraded['version'] > model['version']
    before = len(provider.requests)
    assert by_source(service.recommend(QUERY))[source]
    assert provider.document_inputs(before), 'A new model version cannot reuse old unit vectors.'
    assert all(request['model'] == 'qwen3-embedding:fixture-v2' for request in provider.requests[before:])


def test_effective_chinese_metadata_change_invalidates_only_its_unit(vector_bench):
    store, provider, _, service, _ = vector_bench
    source = publish(store, [NEUTRAL * 4], title='Untranslated archive',
                     url='https://example.test/vector-chinese-metadata')
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        db.execute('INSERT INTO document_chinese VALUES(?,?,?,?,?)',
                   (revision, '译文标题', HIT + ' Only the effective summary matches.', 'ai_summary', '[]'))
    first = service.recommend(QUERY)
    assert_matches(by_source(first)[source], 'title_summary')
    before = len(provider.requests)
    with store.connect() as db:
        db.execute('UPDATE document_chinese SET summary=? WHERE revision_id=?',
                   ('A replacement summary with no semantic marker.', revision))
    second = service.recommend(QUERY)
    assert second['items'] == []
    # The original passage is unchanged; only the metadata input is re-embedded.
    assert len(provider.document_inputs(before)) == 1
    assert '【标题与摘要】' in provider.document_inputs(before)[0]
    assert HIT not in provider.document_inputs(before)[0]


@pytest.mark.parametrize('damage', ['quote', 'foreign_block', 'foreign_source', 'old_revision'])
def test_corrupt_derived_evidence_cannot_supply_a_vector_hit(vector_bench, damage):
    store, _, _, service, _ = vector_bench
    source, page = published_derived(store)
    foreign = publish(store, [NEUTRAL * 4 + 'A different source owns this block.'],
                      title='Foreign source', url='https://example.test/foreign-vector')
    other = store.detail(foreign)

    def corrupt(value):
        if isinstance(value, dict):
            if 'blockId' in value:
                if damage == 'quote':
                    value['quote'] = 'This quote was never present in any original record.'
                elif damage == 'foreign_block':
                    value['blockId'] = other['blocks'][0]['id']
                elif damage == 'foreign_source':
                    value['sourceId'] = foreign
                else:
                    value['revisionId'] = other['revisionId']
            for child in value.values():
                corrupt(child)
        elif isinstance(value, list):
            for child in value:
                corrupt(child)

    corrupt(page)
    with store.connect() as db:
        db.execute('UPDATE wiki_pages SET content_json=? WHERE entry_id=?', (dumps(page), page['id']))
    result = service.recommend(QUERY)
    assert result['items'] == []
    assert all(value['matchedUnits'] == 0 for value in result['vectorSources'])


def test_repeated_weaker_paragraphs_do_not_outvote_a_stronger_article(vector_bench):
    store, _, _, service, _ = vector_bench
    weak = publish(store, [NEUTRAL * 4] + [WEAK + ' Repeated weaker passage.'] * 24,
                   title='Many weak matches', url='https://example.test/vector-count/weak')
    strong = publish(store, [NEUTRAL * 4, HIT + ' One strong passage.'],
                     title='One strong match', url='https://example.test/vector-count/strong')
    result = service.recommend(QUERY)
    assert set(by_source(result)) == {weak, strong}
    assert result['items'][0]['sourceId'] == strong
    # The per-article rank uses the best unit, rather than adding evidence count.
    assert max(match['cosine'] for match in by_source(result)[strong]['vectorMatches']) > max(
        match['cosine'] for match in by_source(result)[weak]['vectorMatches'])


def test_unindexed_units_are_reported_when_request_budget_is_exhausted(vector_bench):
    store, provider, _, service, _ = vector_bench
    # Consecutive short blocks are legitimately grouped into passage units.
    # Keep each block substantial so this fixture truly exceeds the unit budget.
    publish(store, [NEUTRAL * 4] + [NEUTRAL * 3 + f'Unique archive section {index}. ' + HIT
                                  for index in range(300)],
            title='Budgeted units', url='https://example.test/vector-unit-budget')
    result = service.recommend(QUERY)
    assert channel(result)['status'] == 'degraded'
    sources = result['vectorSources']
    assert {source['id'] for source in sources} == {'passage', 'title_summary', 'claim', 'fact'}
    assert sum(source['totalUnits'] for source in sources) > 300
    assert sum(source['pendingUnits'] for source in sources) > 0
    assert all(source['indexedUnits'] + source['pendingUnits'] == source['totalUnits'] for source in sources)
    assert result['warnings'], 'A bounded request cannot silently imply all units were searched.'
    assert all(len(request['input']) <= 16 for request in provider.requests)
    assert len(provider.document_inputs()) == sum(source['indexedUnits'] for source in sources)
