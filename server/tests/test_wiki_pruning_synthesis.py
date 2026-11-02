"""Item-local synthesis selection over synthetic evidence and fake gateways."""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_wiki_resolution import resolution, execute, merge, pages
from server.wiki_pruning import POLICY_VERSION
from server.wiki_resolution import WikiResolutionService


def frozen_run(bench, job):
    with bench.store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM wiki_resolution_runs WHERE job_id=?',
                                     (job['id'],)).fetchone()[0])


def queue(bench):
    canonical_id = merge(bench)
    job = bench.resolution.enqueue_synthesis(canonical_id)['jobs'][0]
    return canonical_id, job, frozen_run(bench, job)


def raw_claim(reference, text='来源保留原始证据。', clear=True):
    return {'text': text, 'evidence': [{'evidenceId': reference['evidenceId']}], 'clear': clear}


def test_new_synthesis_freezes_selection_and_prunes_only_unsupported_claim(resolution):
    bench = resolution
    canonical_id, job, frozen = queue(bench)
    assert frozen['selectionPolicyVersion'] == POLICY_VERSION
    assert '香港三大机遇' in frozen['selectionInstructions']
    assert '{validationError}' in frozen['selectionRepairInstructions']

    def mixed(result):
        result['sections'][0]['claims'][0]['text'] = '2026年出现上述结果。'
        result['issues'] = ['2027年还需要补充不明确资料。']
        return result

    bench.models.damage = mixed
    before = len(bench.models.calls)
    assert execute(bench, job['id'])['status'] == 'succeeded'
    saved = bench.canonical.get_synthesis(canonical_id, pages(bench))
    claims = [claim for section in saved['sections'] for claim in section['claims']]
    assert len(claims) == 1 and '2026' not in claims[0]['text']
    assert saved['issues'] == []
    assert len(bench.models.calls) == before + 1, 'Rejected content must not trigger model repair'
    assert 'clear' not in claims[0]
    assert frozen['selectionInstructions'] in bench.models.calls[-1][1][0]['content']


@pytest.mark.parametrize('marker', [False, 'true', 1, None, 'missing'])
def test_missing_false_or_nonboolean_clear_is_dropped_without_rejecting_neighbour(resolution, marker):
    _, _, frozen = queue(resolution)
    first, second = frozen['registry'][:2]
    bad = raw_claim(first, clear=marker)
    if marker == 'missing':
        bad.pop('clear')
    value = {'sections': [{'title': '明确记载', 'claims': [bad, raw_claim(second)]}], 'issues': []}
    clean = WikiResolutionService._validate_synthesis(value, frozen['registry'], frozen['yearContexts'], True)
    assert len(clean['sections'][0]['claims']) == 1
    assert clean['sections'][0]['claims'][0]['evidence'][0]['sourceId'] == second['sourceId']


def test_bad_evidence_and_section_title_do_not_remove_other_sections(resolution):
    _, _, frozen = queue(resolution)
    reference = frozen['registry'][0]
    value = {'sections': [
        {'title': '2026年情况', 'claims': [raw_claim(reference)]},
        {'title': '明确记载', 'claims': [dict(raw_claim(reference), evidence=[{'evidenceId': 'invented'}]),
                                       raw_claim(reference)]},
    ], 'issues': []}
    clean = WikiResolutionService._validate_synthesis(value, frozen['registry'], frozen['yearContexts'], True)
    assert [section['title'] for section in clean['sections']] == ['明确记载']
    assert len(clean['sections'][0]['claims']) == 1


def test_all_unclear_synthesis_succeeds_without_erasing_prior_synthesis_or_index(resolution):
    bench = resolution
    canonical_id, good, _ = queue(bench)
    assert execute(bench, good['id'])['status'] == 'succeeded'
    previous = bench.canonical.get_synthesis(canonical_id, pages(bench))
    with bench.store.connect() as db:
        previous_index = [tuple(row) for row in db.execute('SELECT * FROM wiki_canonical_search_index')]
        previous_versions = db.execute('SELECT count(*) FROM wiki_canonical_syntheses').fetchone()[0]

    def unclear(result):
        for section in result['sections']:
            for claim in section['claims']:
                claim['clear'] = False
        return result

    bench.models.damage = unclear
    job = bench.resolution.enqueue_synthesis(canonical_id, force=True)['jobs'][0]
    finished = execute(bench, job['id'])
    assert finished['status'] == 'succeeded' and finished['stage'] == 'empty'
    assert bench.canonical.get_synthesis(canonical_id, pages(bench)) == previous
    with bench.store.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM wiki_canonical_search_index')] == previous_index
        assert db.execute('SELECT count(*) FROM wiki_canonical_syntheses').fetchone()[0] == previous_versions


def test_publisher_rechecks_true_evidence_and_dates_item_by_item(resolution):
    bench = resolution
    canonical_id, _, frozen = queue(bench)
    reference = copy.deepcopy(frozen['evidence'][0])
    content = {'sections': [
        {'title': '明确记载', 'claims': [
            {'text': '2026年出现结果。', 'evidence': [reference]},
            {'text': '伪造引用不能进入结果。', 'evidence': [dict(reference, quote='不存在的引文')]},
            {'text': '来源保留原始证据。', 'evidence': [reference]},
        ]},
        {'title': '2027年情况', 'claims': [{'text': '来源有明确记载。', 'evidence': [reference]}]},
    ], 'issues': ['未明事项不收录']}
    saved = bench.canonical.publish_synthesis(canonical_id, frozen, content, pages(bench))
    assert len(saved['sections']) == 1 and len(saved['sections'][0]['claims']) == 1
    assert saved['sections'][0]['claims'][0]['text'] == '来源保留原始证据。'
    assert saved['issues'] == []


def test_normalized_checkpoint_reuse_does_not_need_model_clear_markers_again(resolution, monkeypatch):
    bench = resolution
    canonical_id, job, _ = queue(bench)
    original = bench.canonical.publish_synthesis
    monkeypatch.setattr(bench.canonical, 'publish_synthesis', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('Wiki 模拟发布中断')))
    assert execute(bench, job['id'])['status'] == 'failed'
    with bench.store.connect() as db:
        checkpoint = json.loads(db.execute('SELECT content_json FROM wiki_resolution_checkpoints WHERE run_id=(SELECT id FROM wiki_resolution_runs WHERE job_id=?)', (job['id'],)).fetchone()[0])
    assert checkpoint['selectionPolicyVersion'] == POLICY_VERSION
    assert 'clear' not in checkpoint['sections'][0]['claims'][0]
    before = len(bench.models.calls)
    monkeypatch.setattr(bench.canonical, 'publish_synthesis', original)
    bench.store.retry(job['id'])
    assert execute(bench, job['id'])['status'] == 'succeeded'
    assert len(bench.models.calls) == before
    assert bench.canonical.get_synthesis(canonical_id, pages(bench))['mode'] == 'grounded_synthesis'


@pytest.mark.parametrize('broken', ['json', 'sections_type'])
def test_only_global_structure_errors_get_one_repair(resolution, broken):
    bench = resolution
    _, job, frozen = queue(bench)
    original = bench.models.complete
    sent = []

    def provider(config, messages):
        sent.append(copy.deepcopy(messages))
        if len(sent) == 1:
            return {'text': 'bad JSON' if broken == 'json' else dumps({'sections': {}, 'issues': []})}
        return original(config, messages)

    bench.models.complete = provider
    assert execute(bench, job['id'])['status'] == 'succeeded'
    assert len(sent) == 2
    assert frozen['selectionRepairInstructions'].split('{validationError}')[0] in sent[1][0]['content']


def test_empty_noop_still_obeys_cancellation_fence(resolution):
    bench = resolution
    canonical_id, job, _ = queue(bench)
    bench.models.damage = lambda result: {'sections': [], 'issues': []}
    bench.models.resolution_callback = lambda: bench.store.cancel(job['id'])
    assert execute(bench, job['id'])['status'] == 'cancelled'
    assert bench.canonical.get_synthesis(canonical_id, pages(bench))['mode'] == 'source_claims'
