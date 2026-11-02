"""Small synthetic sources and deterministic models, never the real library."""
import copy
import json

import pytest

from server.prompts import PromptLibrary
from server.store import dumps
from server.tests.test_wiki_exploration import ExplorationBench, PlannedGateway, entry, assert_exact_evidence
from server.wiki_canonical import WikiCanonicalService
from server.wiki_resolution import WikiResolutionService, MAX_CANDIDATES, JUDGMENTS


class ResolutionGateway(PlannedGateway):
    def __init__(self):
        super().__init__()
        self.judgment = 'same_as'
        self.damage = None
        self.resolution_callback = None

    def complete(self, config, messages):
        payload = json.loads(messages[1]['content'])
        if payload.get('task') not in ('wiki_resolve', 'wiki_synthesize'):
            return super().complete(config, messages)
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.resolution_callback:
            callback, self.resolution_callback = self.resolution_callback, None
            callback()
        if payload['task'] == 'wiki_resolve':
            result = {'judgments': [{'candidateId': candidate['id'], 'judgment': self.judgment,
                        'reason': '名称、定义及对象范围一致，仍应由人工确认。'}
                       for candidate in payload['candidates']]}
        else:
            evidence = payload['evidence'][:2]
            result = {'sections': [{'title': '来源补充与分歧', 'claims': [
                {'text': '来源记载：' + reference['text'],
                 'evidence': [{'evidenceId': reference['evidenceId']}]} for reference in evidence]}], 'issues': []}
            if payload.get('selectionPolicyVersion'):
                for section in result['sections']:
                    for claim in section['claims']:
                        claim['clear'] = True
        if self.damage:
            result = self.damage(result)
        return {'text': json.dumps(result, ensure_ascii=False)}


@pytest.fixture
def resolution(tmp_path):
    bench = ExplorationBench(tmp_path / 'resolution-data')
    bench.models = ResolutionGateway()
    bench.service.models = bench.models
    a = entry('a', '大语言模型', summary='大语言模型在来源甲中用于生成文本，保留来源甲的适用条件。')
    a['aliases'] = ['LLM']
    b = entry('b', 'LLM', summary='LLM在来源乙中用于处理自然语言任务，保留来源乙的不同条件。')
    b['aliases'] = ['大语言模型']
    source_a, pages_a = bench.build([a], title='来源甲')
    source_b, pages_b = bench.build([b], title='来源乙')
    bench.source_ids = [source_a, source_b]
    bench.entry_ids = [pages_a['大语言模型']['id'], pages_b['LLM']['id']]
    bench.canonical = WikiCanonicalService(bench.store)
    bench.resolution = WikiResolutionService(bench.store, bench.models, bench.canonical)
    return bench


def pages(bench):
    with bench.store.connect() as db:
        return bench.resolution._load(db)[1]


def execute(bench, expected_id=None):
    job = bench.store.claim()
    assert job
    if expected_id:
        assert job['id'] == expected_id
    bench.resolution.process(job)
    return bench.store.job(job['id'])


def merge(bench):
    state = bench.canonical.snapshot(pages(bench))
    result = bench.canonical.merge(bench.entry_ids, '大语言模型', state['snapshotId'], pages(bench))
    return result['entryToCanonical'][bench.entry_ids[0]]


def test_reads_and_service_upgrade_never_schedule_or_call_models(resolution):
    bench = resolution
    before_jobs, before_calls = bench.store.jobs(), len(bench.models.calls)
    assert bench.resolution.results()['proposals'] == []
    restarted = WikiResolutionService(bench.store, bench.models)
    assert restarted.results()['proposals'] == []
    assert bench.store.jobs() == before_jobs
    assert len(bench.models.calls) == before_calls
    assert bench.resolution.refresh_source(bench.source_ids[0])['jobs'] == []


def test_explicit_source_limit_and_current_publication_required(resolution):
    bench = resolution
    for source_ids in ([], ['x'] * 11, 'not-list', [False]):
        with pytest.raises(ValueError):
            bench.resolution.enqueue(source_ids)
    with pytest.raises(KeyError):
        bench.resolution.enqueue(['not-a-source'])
    with bench.store.connect() as db:
        db.execute('DELETE FROM wiki_publications WHERE source_id=?', (bench.source_ids[0],))
    with pytest.raises(ValueError, match='当前版本'):
        bench.resolution.enqueue([bench.source_ids[0]])


def test_model_proposal_is_durable_but_never_silently_merges(resolution):
    bench = resolution
    state = bench.canonical.snapshot(pages(bench))
    assert len(state['groups']) == 2
    response = bench.resolution.enqueue([bench.source_ids[0]])
    job_id = response['jobs'][0]['id']
    assert bench.resolution.enqueue([bench.source_ids[0]])['jobs'][0]['id'] == job_id
    assert execute(bench, job_id)['status'] == 'succeeded'
    proposal = bench.resolution.results(job_id)['proposals'][0]
    assert set(proposal['entryIds']) == set(bench.entry_ids)
    assert proposal['judgment'] == 'same_as' and proposal['status'] == 'pending'
    assert len(bench.canonical.snapshot(pages(bench))['groups']) == 2
    assert WikiResolutionService(bench.store, bench.models).results(job_id)['proposals'] == [proposal]
    with bench.store.connect() as db:
        records = dumps([dict(row) for row in db.execute('SELECT * FROM wiki_resolution_runs')])
    assert 'never-persist-secret' not in records


def test_manual_rejection_prevents_repeated_candidate_requests(resolution):
    bench = resolution
    state = bench.canonical.snapshot(pages(bench))
    bench.canonical.reject(bench.entry_ids, state['snapshotId'], pages(bench), reason='同名异义，人工保持独立')
    count = len(bench.models.calls)
    assert bench.resolution.enqueue([bench.source_ids[0]])['jobs'] == []
    assert len(bench.models.calls) == count


@pytest.mark.parametrize('judgment', sorted(JUDGMENTS))
def test_relation_judgments_remain_reviewable_without_changing_identity(resolution, judgment):
    bench = resolution
    bench.models.judgment = judgment
    job = bench.resolution.enqueue([bench.source_ids[0]])['jobs'][0]
    assert execute(bench, job['id'])['status'] == 'succeeded'
    assert bench.resolution.results(job['id'])['proposals'][0]['judgment'] == judgment
    assert len(bench.canonical.snapshot(pages(bench))['groups']) == 2


def test_candidate_recall_is_bounded_and_skips_incompatible_kinds(resolution):
    bench = resolution
    for index in range(11):
        candidate = entry('x', f'大语言模型候选{index}', summary=f'大语言模型候选{index}有不同的资料定义与具体应用范围。')
        candidate['aliases'] = ['LLM']
        bench.build([candidate])
    bench.build([entry('person', 'LLM', 'entity', summary='LLM是本来源记载的同名人物，身份并非语言模型。')])
    job = bench.resolution.enqueue([bench.source_ids[0]])['jobs'][0]
    with bench.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM wiki_resolution_runs WHERE job_id=?', (job['id'],)).fetchone()[0])
    assert len(frozen['units']) == 1
    candidates = frozen['units'][0]['candidates']
    assert len(candidates) == MAX_CANDIDATES
    assert all(candidate['type'] == 'concept' for candidate in candidates)
    assert execute(bench, job['id'])['status'] == 'succeeded'


def test_queue_freezes_model_and_prompt_and_invalid_ids_fail_closed(resolution):
    bench = resolution
    job = bench.resolution.enqueue([bench.source_ids[0]])['jobs'][0]
    library = PromptLibrary(bench.store)
    item = next(item for item in library.list()['items'] if item['id'] == 'wiki_resolve')
    library.update('wiki_resolve', item['content'] + '\n排队后新增要求', item['version'])
    bench.models.config['version'] = 2
    assert execute(bench, job['id'])['status'] == 'succeeded'
    assert bench.models.calls[-1][0]['version'] == 1
    assert '排队后新增要求' not in bench.models.calls[-1][1][0]['content']
    bench.models.damage = lambda result: {'judgments': [dict(result['judgments'][0], candidateId='invented')]}
    bad = bench.resolution.enqueue([bench.source_ids[0]], force=True)['jobs'][0]
    assert execute(bench, bad['id'])['status'] == 'failed'
    assert bench.resolution.results(bad['id'])['proposals'] == []


@pytest.mark.parametrize('event', ['cancel', 'retry', 'decision', 'republication'])
def test_cancellation_new_attempt_and_changed_inputs_block_late_results(resolution, event):
    bench = resolution
    job = bench.resolution.enqueue([bench.source_ids[0]])['jobs'][0]

    def mutate():
        if event == 'cancel':
            bench.store.cancel(job['id'])
        elif event == 'retry':
            bench.store.cancel(job['id'])
            bench.store.retry(job['id'])
            # Claim the newer attempt before the old provider response returns.
            assert bench.store.claim()['id'] == job['id']
        elif event == 'decision':
            state = bench.canonical.snapshot(pages(bench))
            bench.canonical.reject(bench.entry_ids, state['snapshotId'], pages(bench))
        else:
            with bench.store.connect() as db:
                db.execute('DELETE FROM wiki_publications WHERE source_id=?', (bench.source_ids[0],))

    bench.models.resolution_callback = mutate
    status = execute(bench, job['id'])['status']
    assert status == ('running' if event == 'retry' else 'cancelled')
    assert bench.resolution.results(job['id'])['proposals'] == []


def test_confirmed_group_synthesis_cites_originals_and_replay_is_idempotent(resolution):
    bench = resolution
    canonical_id = merge(bench)
    job = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    assert execute(bench, job['id'])['status'] == 'succeeded'
    content = bench.canonical.get_synthesis(canonical_id, pages(bench))
    claims = [claim for section in content['sections'] for claim in section['claims']]
    assert len(claims) == 2
    assert_exact_evidence(bench.store, [ref for claim in claims for ref in claim['evidence']])
    assert {ref['sourceId'] for claim in claims for ref in claim['evidence']} == set(bench.source_ids)
    count = len(bench.models.calls)
    assert bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]['id'] == job['id']
    assert bench.resolution.refresh_source(bench.source_ids[0])['jobs'][0]['id'] == job['id']
    assert len(bench.models.calls) == count


@pytest.mark.parametrize('damage', ['invented_evidence', 'changed_quote', 'missing_evidence', 'year', 'two_sentences'])
def test_legacy_bad_synthesis_preserves_previous_success(resolution, damage):
    bench = resolution
    canonical_id = merge(bench)
    good = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    assert execute(bench, good['id'])['status'] == 'succeeded'
    previous = bench.canonical.get_synthesis(canonical_id, pages(bench))

    def corrupt(result):
        claim = result['sections'][0]['claims'][0]
        if damage == 'invented_evidence':
            claim['evidence'] = [{'evidenceId': 'invented'}]
        elif damage == 'changed_quote':
            claim['evidence'][0]['quote'] = '模型自行创建的假引文'
        elif damage == 'missing_evidence':
            claim['evidence'] = []
        elif damage == 'year':
            claim['text'] = '2026年发生了上述事件。'
        else:
            claim['text'] = '原始资料有据。后一句没有独立引用。'
        return result

    bench.models.damage = corrupt
    failed = bench.resolution.enqueue_synthesis(canonical_id, force=True)['jobs'][0]
    # Represent an already-frozen pre-selection task. New requests instead
    # drop only the bad item, covered by test_wiki_pruning_synthesis.py.
    with bench.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM wiki_resolution_runs WHERE job_id=?',
                                      (failed['id'],)).fetchone()[0])
        for field in ('selectionPolicyVersion', 'selectionInstructions', 'selectionRepairInstructions'):
            frozen.pop(field)
        db.execute('UPDATE wiki_resolution_runs SET input_json=? WHERE job_id=?', (dumps(frozen), failed['id']))
    assert execute(bench, failed['id'])['status'] == 'failed'
    assert bench.canonical.get_synthesis(canonical_id, pages(bench)) == previous


def test_synthesis_cancelled_inflight_preserves_previous_version(resolution):
    bench = resolution
    canonical_id = merge(bench)
    job = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    bench.models.resolution_callback = lambda: bench.store.cancel(job['id'])
    assert execute(bench, job['id'])['status'] == 'cancelled'
    assert bench.canonical.get_synthesis(canonical_id, pages(bench)).get('mode') != 'grounded_synthesis'


def test_synthesis_budget_failure_makes_no_provider_call(resolution):
    bench = resolution
    canonical_id = merge(bench)
    bench.models.config['contextChars'] = 100
    job = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    count = len(bench.models.calls)
    assert execute(bench, job['id'])['status'] == 'failed'
    assert len(bench.models.calls) == count


def test_newer_synthesis_task_fences_older_inflight_publication(resolution):
    bench = resolution
    canonical_id = merge(bench)
    first = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    newer = []
    bench.models.resolution_callback = lambda: newer.append(
        bench.resolution.enqueue_synthesis(canonical_id, force=True)['jobs'][0])
    assert execute(bench, first['id'])['status'] == 'cancelled'
    assert bench.canonical.get_synthesis(canonical_id, pages(bench)).get('mode') != 'grounded_synthesis'
    assert execute(bench, newer[0]['id'])['status'] == 'succeeded'
