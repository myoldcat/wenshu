"""Frozen Wiki year rules over synthetic originals and deterministic gateways."""

import copy
import json

import pytest

from server.prompts import intent_signature, snapshot
from server.store import Store, dumps, now
from server.tests.test_wiki import Gateway, execute, publish, clear_result, legacy_selection
from server.tests.test_wiki_canonical import source_page
from server.tests.test_wiki_exploration import ExplorationBench, entry, assert_exact_evidence
from server.tests.test_wiki_resolution import (
    ResolutionGateway, execute as execute_resolution, pages as resolution_pages,
)
from server.wiki import WikiService, PROMPT_VERSION, _hash
from server.wiki_canonical import WikiCanonicalService
from server.wiki_resolution import WikiResolutionService
from server.year_context import POLICY_VERSION, INSTRUCTIONS, build_contexts


QUOTE = '受理网络安全事件共四十宗。'
CLAIM = '2026年受理网络安全事件共四十宗。'
BACKGROUND = '相关部门保存了统计与处置工作的原始记录，便于按资料版本核对每条陈述的适用范围。'


def model_result(block, text=CLAIM):
    reference = {'blockId': block['blockId'], 'quote': QUOTE}
    return {
        'entries': [{
            'localKey': 'statistics', 'label': '网络安全统计', 'type': 'concept',
            'subtype': '', 'aliases': [],
            'summary': {'text': text, 'evidence': [copy.deepcopy(reference)]},
            'sections': [{'title': '事件数量', 'claims': [
                {'text': text, 'evidence': [copy.deepcopy(reference)]},
            ]}],
        }],
        'relations': [], 'issues': [],
    }


class YearGateway(Gateway):
    def __init__(self):
        super().__init__()
        self.text = CLAIM

    def complete(self, config, messages):
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        payload = json.loads(messages[1]['content'])
        block = next(block for block in payload['blocks'] if QUOTE in block['text'])
        result = model_result(block, self.text)
        if self.rewrite:
            result = self.rewrite(result)
        return {'text': json.dumps(clear_result(result), ensure_ascii=False)}


def compilation(tmp_path, *, title='2026年香港网络安全事件统计', prefix=''):
    store = Store(tmp_path / 'isolated-wiki')
    gateway = YearGateway()
    service = WikiService(store, gateway)
    source = publish(store, [prefix + QUOTE + BACKGROUND], title=title)
    return store, gateway, service, source


def make_legacy_job(store, job_id):
    """Represent a pre-policy queued task without rewriting its prompt snapshot."""
    with store.connect() as db:
        row = db.execute('SELECT input_json FROM jobs WHERE id=?', (job_id,)).fetchone()
        payload = json.loads(row['input_json'])
        payload.pop('yearContextPolicyVersion')
        payload.pop('yearContextInstructions')
        payload.pop('selectionPolicyVersion', None)
        payload.pop('selectionInstructions', None)
        payload.pop('selectionRepairInstructions', None)
        run = db.execute('SELECT * FROM wiki_runs WHERE job_id=?', (job_id,)).fetchone()
        legacy_fingerprint = _hash(intent_signature([
            run['source_id'], run['revision_id'], run['model_id'], run['model_version'],
            PROMPT_VERSION,
        ], snapshot(db, ('wiki_compile', 'wiki_repair'))))
        db.execute('UPDATE jobs SET input_json=? WHERE id=?', (dumps(payload), job_id))
        db.execute('UPDATE wiki_runs SET fingerprint=? WHERE job_id=?',
                   (legacy_fingerprint, job_id))
    return payload


@pytest.mark.parametrize('origin', ['title', 'paragraph'])
def test_new_compilation_uses_explicit_original_context_and_preserves_exact_quote(tmp_path, origin):
    title = '2026年香港网络安全事件统计' if origin == 'title' else '香港网络安全事件统计'
    prefix = '' if origin == 'title' else '2026年统计显示，'
    store, gateway, service, source = compilation(tmp_path, title=title, prefix=prefix)
    service.queue([source])
    job = execute(store, service)
    assert job['status'] == 'succeeded', job
    node = next(node for node in service.overview()['nodes'] if node['type'] == 'concept')
    page = service.page(node['id'])
    assert page['summary'] == CLAIM
    assert page['evidence'][0]['quote'] == QUOTE
    assert '2026' not in page['evidence'][0]['quote']
    assert_exact_evidence(store, page['evidence'])
    assert len(gateway.calls) == 1


@pytest.mark.parametrize('field', ['summary', 'claim', 'section', 'label', 'alias', 'relation'])
def test_every_compilation_year_guard_accepts_the_same_trusted_title(tmp_path, field):
    store, gateway, service, source = compilation(tmp_path)
    document = store.detail(source)
    pieces = service._batches(document['blocks'], gateway.config)[0]
    block = next(block for block in pieces if QUOTE in block['text'])
    result = model_result(block, QUOTE)
    target = result['entries'][0]
    if field == 'summary':
        target['summary']['text'] = CLAIM
    elif field == 'claim':
        target['sections'][0]['claims'][0]['text'] = CLAIM
    elif field == 'section':
        target['sections'][0]['title'] = '2026年事件数量'
    elif field == 'label':
        target['label'] = '2026年网络安全统计'
    elif field == 'alias':
        target['aliases'] = ['2026年事件统计']
    else:
        other = copy.deepcopy(target)
        other.update(localKey='records', label='原始统计记录')
        result['entries'].append(other)
        result['relations'] = [{
            'from': 'statistics', 'to': 'records', 'type': 'association',
            'label': '2026年统计依据',
            'evidence': copy.deepcopy(target['summary']['evidence']),
        }]
    references = service._evidence(target['summary']['evidence'], pieces, document)
    contexts = build_contexts([document], references)
    assert service._validate(result, pieces, document, contexts)
    with pytest.raises(ValueError, match='2026'):
        service._validate(result, pieces, document)


def test_new_request_freezes_instructions_and_bounds_contexts(tmp_path, monkeypatch):
    store, gateway, service, source = compilation(tmp_path)
    job = service.queue([source])['jobs'][0]
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?',
                                      (job['id'],)).fetchone()[0])
    assert frozen['yearContextPolicyVersion'] == POLICY_VERSION == 'source-year-context-v1'
    assert frozen['yearContextInstructions'] == INSTRUCTIONS
    monkeypatch.setattr('server.wiki.YEAR_CONTEXT_INSTRUCTIONS', '排队后新增的年份规则')
    assert execute(store, service)['status'] == 'succeeded'
    system, user = gateway.calls[0][1]
    payload = json.loads(user['content'])
    assert payload['yearContexts']
    assert len(dumps(payload['yearContexts'])) <= 1800
    assert '2026' in dumps(payload['yearContexts'])
    assert system['content'].endswith(frozen['yearContextInstructions'])
    assert '优先' in system['content']
    assert '排队后新增的年份规则' not in system['content']


def test_old_task_remains_quote_only_and_does_not_deduplicate_new_policy(tmp_path):
    store, gateway, service, source = compilation(tmp_path)
    old = service.queue([source])['jobs'][0]
    legacy_payload = make_legacy_job(store, old['id'])
    failed = execute(store, service)
    assert failed['id'] == old['id'] and failed['status'] == 'failed'
    assert '2026' in failed['error']
    assert len(gateway.calls) == 2  # Initial response and the original repair path.
    assert all('yearContexts' not in json.loads(messages[1]['content'])
               for _, messages in gateway.calls)
    assert all(INSTRUCTIONS not in messages[0]['content'] for _, messages in gateway.calls)
    with store.connect() as db:
        assert json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?',
                                     (old['id'],)).fetchone()[0]) == legacy_payload
    current = service.queue([source])['jobs'][0]
    assert current['id'] != old['id']
    assert execute(store, service)['status'] == 'succeeded'


def test_preexisting_legacy_checkpoint_keeps_request_hash_and_is_reused(tmp_path):
    store, gateway, service, source = compilation(tmp_path)
    job = service.queue([source])['jobs'][0]
    make_legacy_job(store, job['id'])
    document = store.detail(source)
    pieces = service._batches(document['blocks'], gateway.config)[0]
    original_request = service._messages(document, pieces)
    block = next(block for block in pieces if QUOTE in block['text'])
    checked = service._validate(model_result(block, QUOTE), pieces, document)
    with store.connect() as db:
        run_id = db.execute('SELECT id FROM wiki_runs WHERE job_id=?',
                            (job['id'],)).fetchone()[0]
        db.execute('INSERT INTO wiki_checkpoints VALUES(?,?,?,?,?)',
                   (run_id, 'wiki:1/1', _hash(original_request), dumps(checked), now()))
        before = dict(db.execute('SELECT * FROM wiki_checkpoints WHERE run_id=?',
                                 (run_id,)).fetchone())
    assert execute(store, service)['status'] == 'succeeded'
    assert gateway.calls == []
    with store.connect() as db:
        after = dict(db.execute('SELECT * FROM wiki_checkpoints WHERE run_id=?',
                                (run_id,)).fetchone())
    assert after == before
    assert service._messages(document, pieces) == original_request


def test_model_supplied_title_and_context_do_not_authorize_an_unsupported_year(tmp_path):
    store, gateway, service, source = compilation(tmp_path, title='香港网络安全事件统计')

    def inject(result):
        result['yearContexts'] = {'years': ['2026']}
        for entry_result in result['entries']:
            for claim in [entry_result['summary'], *entry_result['sections'][0]['claims']]:
                claim['evidence'][0].update(title='2026年统计', supportedYears=['2026'],
                                          yearContext={'years': ['2026']})
        return result

    gateway.rewrite = inject
    legacy_selection(store, service.queue([source])['jobs'][0]['id'])
    assert execute(store, service)['status'] == 'failed'
    assert service.overview()['stats']['pages'] == 0


def synthesis_bench(tmp_path, *, summaries=None, titles=None):
    bench = ExplorationBench(tmp_path / 'isolated-synthesis')
    bench.models = ResolutionGateway()
    bench.service.models = bench.models
    summaries = summaries or ('网络安全统计记载受理事件四十宗。',
                              '网络安全统计记载勒索软件事件十宗。')
    titles = titles or ('2026年香港网络安全事件统计', '2026年香港勒索软件事件统计')
    source_a, first = bench.build([
        entry('a', '事件统计', summary=summaries[0]),
    ], title=titles[0])
    source_b, second = bench.build([
        entry('b', '勒索统计', summary=summaries[1]),
    ], title=titles[1])
    bench.source_ids = [source_a, source_b]
    bench.entry_ids = [first['事件统计']['id'], second['勒索统计']['id']]
    bench.canonical = WikiCanonicalService(bench.store)
    bench.resolution = WikiResolutionService(bench.store, bench.models, bench.canonical)
    pages = resolution_pages(bench)
    state = bench.canonical.snapshot(pages)
    merged = bench.canonical.merge(bench.entry_ids, '网络安全统计', state['snapshotId'], pages)
    return bench, merged['entryToCanonical'][bench.entry_ids[0]]


def add_synthesis_year(result):
    result['sections'][0]['title'] = '2026年事件数量'
    for claim in result['sections'][0]['claims']:
        claim['text'] = '2026年' + claim['text']
    return result


def test_synthesis_validation_and_canonical_publication_use_the_same_frozen_context(tmp_path):
    bench, canonical_id = synthesis_bench(tmp_path)
    bench.models.damage = add_synthesis_year
    job = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    with bench.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM wiki_resolution_runs WHERE job_id=?',
                                      (job['id'],)).fetchone()[0])
    assert frozen['yearContextPolicyVersion'] == POLICY_VERSION
    assert frozen['yearContextInstructions'] == INSTRUCTIONS
    assert frozen['yearContexts']
    assert execute_resolution(bench, job['id'])['status'] == 'succeeded'
    saved = bench.canonical.get_synthesis(canonical_id, resolution_pages(bench))
    assert saved['mode'] == 'grounded_synthesis'
    assert saved['sections'][0]['title'] == '2026年事件数量'
    references = [ref for section in saved['sections'] for claim in section['claims']
                  for ref in claim['evidence']]
    assert all('2026' not in ref['quote'] for ref in references)
    assert_exact_evidence(bench.store, references)
    system, user = bench.models.calls[-1][1]
    assert json.loads(user['content'])['yearContexts']
    assert frozen['yearContextInstructions'] in system['content']


def test_legacy_synthesis_remains_quote_only(tmp_path):
    bench, canonical_id = synthesis_bench(tmp_path)
    bench.models.damage = add_synthesis_year
    job = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    with bench.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM wiki_resolution_runs WHERE job_id=?',
                                      (job['id'],)).fetchone()[0])
        for field in ('yearContextPolicyVersion', 'yearContextInstructions', 'yearContexts',
                      'selectionPolicyVersion', 'selectionInstructions', 'selectionRepairInstructions'):
            frozen.pop(field)
        db.execute('UPDATE wiki_resolution_runs SET input_json=? WHERE job_id=?',
                   (dumps(frozen), job['id']))
    assert execute_resolution(bench, job['id'])['status'] == 'failed'
    assert 'yearContexts' not in json.loads(bench.models.calls[-1][1][1]['content'])
    assert bench.canonical.get_synthesis(canonical_id, resolution_pages(bench))['mode'] == 'source_claims'


@pytest.mark.parametrize('other_year', ['', '2025年'], ids=['undated-source', 'different-year-source'])
def test_direct_quote_year_cannot_date_another_source_in_validation_or_publication(tmp_path, other_year):
    bench, canonical_id = synthesis_bench(
        tmp_path,
        summaries=('2026年受理网络安全事件四十宗。',
                   other_year + '受理勒索软件事件十宗。'),
        titles=('来源甲网络安全事件统计', '来源乙勒索软件事件统计'),
    )
    job = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    with bench.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM wiki_resolution_runs WHERE job_id=?',
                                      (job['id'],)).fetchone()[0])
    assert {ref['sourceId'] for ref in frozen['registry']} == set(bench.source_ids)
    value = {'sections': [{'title': '事件统计', 'claims': [{
        'text': '2026年受理网络安全事件四十宗并受理勒索软件事件十宗。',
        'evidence': [{'evidenceId': ref['evidenceId']} for ref in frozen['registry']],
    }]}], 'issues': []}
    # Historical tasks keep their precise quote-union behavior. New policy
    # cannot use the first source's explicit year to date the second source.
    legacy_checked = bench.resolution._validate_synthesis(value, frozen['registry'])
    with pytest.raises(ValueError, match='2026'):
        bench.resolution._validate_synthesis(value, frozen['registry'], frozen['yearContexts'])
    strict_frozen = copy.deepcopy(frozen)
    for field in ('selectionPolicyVersion', 'selectionInstructions', 'selectionRepairInstructions'):
        strict_frozen.pop(field, None)
    with pytest.raises(ValueError, match='2026'):
        bench.canonical.publish_synthesis(canonical_id, strict_frozen, legacy_checked, resolution_pages(bench))
    with bench.store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_canonical_syntheses').fetchone()[0] == 0


def test_canonical_publisher_rebuilds_context_instead_of_trusting_frozen_injection(tmp_path):
    store = Store(tmp_path / 'isolated-canonical')
    canonical = WikiCanonicalService(store)
    pages = [source_page(store, 1)]
    canonical_id = next(iter(canonical.snapshot(pages)['groups']))
    frozen = canonical.prepare_synthesis(canonical_id, pages)
    original = store.detail(pages[0]['sourceId'], pages[0]['revisionId'])
    forged = copy.deepcopy(original)
    forged['originalTitle'] = '2026年香港网络安全统计'
    forged['title'] = forged['originalTitle']
    frozen.update(yearContextPolicyVersion=POLICY_VERSION,
                  yearContextInstructions=INSTRUCTIONS,
                  yearContexts=build_contexts([forged], frozen['evidence']))
    content = {'sections': [{'title': '事件统计', 'claims': [{
        'text': '2026年保留了原始证据。', 'evidence': copy.deepcopy(frozen['evidence']),
    }]}], 'issues': []}
    with pytest.raises(ValueError, match='2026'):
        canonical.publish_synthesis(canonical_id, frozen, content, pages)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_canonical_syntheses').fetchone()[0] == 0
