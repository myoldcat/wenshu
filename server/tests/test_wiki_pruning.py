"""Clear, reusable Wiki candidates over isolated originals and fake responses."""
import copy
import json

import pytest

from server.prompts import PromptLibrary
from server.store import Store, dumps, now
from server.tests.test_wiki import Gateway, execute, legacy_selection, publish
from server.wiki import WikiService, _hash
from server.wiki_pruning import POLICY_VERSION, INSTRUCTIONS


TEXT = '资料治理保留原文证据及固定版本，读者能够核对每条记录的原始位置、具体对象和适用范围。'


def candidate(key, label, reference, *, text=TEXT, standalone=True, clear=True):
    return {'localKey': key, 'label': label, 'type': 'concept', 'subtype': '',
            'standalone': standalone, 'aliases': [],
            'summary': {'text': text, 'evidence': [copy.deepcopy(reference)], 'clear': clear},
            'sections': [{'title': '来源记载', 'claims': [
                {'text': text, 'evidence': [copy.deepcopy(reference)], 'clear': True}]}]}


def plan(payload):
    reference = {'evidenceId': payload['blocks'][0]['evidenceId']}
    return {'entries': [candidate('a', '资料治理', reference),
                        candidate('b', '固定资料版本', reference)],
            'relations': [{'from': 'a', 'to': 'b', 'type': 'association',
                           'label': '保留版本', 'evidence': [reference], 'clear': True}], 'issues': []}


class PruningGateway(Gateway):
    """Unlike compatibility fixtures, never add missing quality assessments."""
    def __init__(self):
        super().__init__()
        self.plan = plan

    def complete(self, config, messages):
        assert sum(len(message['content']) for message in messages) <= config['contextChars']
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.fail_at == len(self.calls):
            raise RuntimeError('isolated gateway interruption')
        value = self.plan(json.loads(messages[1]['content']))
        return {'text': json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value,
                'usage': {'total_tokens': 7}}


@pytest.fixture
def pruning(tmp_path):
    store = Store(tmp_path / 'isolated-wiki-pruning')
    gateway = PruningGateway()
    service = WikiService(store, gateway)
    source = publish(store, [TEXT], title='资料治理隔离来源')
    return store, gateway, service, source


def validated(pruning, mutate):
    store, gateway, service, source = pruning
    document = store.detail(source)
    pieces = service._batches(document['blocks'], gateway.config)[0]
    value = plan({'blocks': pieces})
    mutate(value)
    return service._validate(value, pieces, document, {}, selection_policy=POLICY_VERSION)


def pages(service):
    return [service.page(node['id']) for node in service.overview()['nodes'] if node['type'] != 'document']


def publication_snapshot(store):
    with store.connect() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in ('wiki_publications', 'wiki_pages', 'wiki_search_index', 'wiki_unified_outbox')}


@pytest.mark.parametrize('location', ['summary', 'label'])
def test_new_jobs_freeze_selection_and_mixed_unsupported_year_keeps_valid_page_without_repair(pruning, location):
    store, gateway, service, source = pruning
    def mixed(payload):
        value = plan(payload)
        if location == 'summary':
            value['entries'][0]['summary']['text'] = '2026年资料治理保留固定版本。'
        else:
            value['entries'][0]['label'] = '2026年资料治理'
        return value
    gateway.plan = mixed
    job = service.queue([source])['jobs'][0]
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
    assert frozen['selectionPolicyVersion'] == POLICY_VERSION
    assert frozen['selectionInstructions'] == INSTRUCTIONS
    assert execute(store, service)['status'] == 'succeeded'
    saved = pages(service)
    assert [page['label'] for page in saved] == ['固定资料版本']
    assert all('2026' not in text for page in saved for text in [
        page['label'], page['summary'], *page['aliases'],
        *(section['title'] for section in page['sections']),
        *(claim['text'] for section in page['sections'] for claim in section['claims'])])
    assert len(gateway.calls) == 1
    assert INSTRUCTIONS in gateway.calls[0][1][0]['content']
    with store.connect() as db:
        assert [row[0] for row in db.execute('SELECT validation_status FROM wiki_calls')] == ['validated']


@pytest.mark.parametrize('assessment', [False, None, 'true', 1], ids=['false', 'missing', 'string', 'number'])
def test_context_dependent_entries_require_explicit_boolean_standalone(pruning, assessment):
    def damage(value):
        target = value['entries'][0]
        target['label'] = '香港三大机遇'
        if assessment is None:
            target.pop('standalone')
        else:
            target['standalone'] = assessment
    clean = validated(pruning, damage)
    assert [entry['localKey'] for entry in clean['entries']] == ['b']
    assert clean['relations'] == []


def test_semantic_assessment_is_not_a_hong_kong_or_numbered_label_blacklist(pruning):
    def clear_numbered(value):
        value['entries'][0]['label'] = '资料治理三层架构'
    assert any(entry['label'] == '资料治理三层架构' for entry in validated(pruning, clear_numbered)['entries'])
    def unclear_other(value):
        value['entries'][0].update(label='上述重点工作', standalone=False)
    assert [entry['localKey'] for entry in validated(pruning, unclear_other)['entries']] == ['b']


@pytest.mark.parametrize('assessment', [False, None, 'true', 1], ids=['false', 'missing', 'string', 'number'])
def test_unclear_definition_discards_only_its_entry(pruning, assessment):
    def damage(value):
        if assessment is None:
            value['entries'][0]['summary'].pop('clear')
        else:
            value['entries'][0]['summary']['clear'] = assessment
    assert [entry['localKey'] for entry in validated(pruning, damage)['entries']] == ['b']


def test_claims_aliases_sections_and_relations_are_pruned_at_their_own_boundaries(pruning):
    def damage(value):
        entry = value['entries'][0]
        supported = copy.deepcopy(entry['sections'][0]['claims'][0])
        unclear = {**copy.deepcopy(supported), 'text': '这几个方面形成了新机会。', 'clear': False}
        unsupported = {**copy.deepcopy(supported), 'text': '2026年增加了新记录。'}
        entry['sections'][0]['claims'].extend([unclear, unsupported])
        entry['sections'].append({'title': '2026年新增事项', 'claims': [copy.deepcopy(supported)]})
        entry['aliases'] = ['资料治理原文名称', '2026年治理方案']
        value['relations'].extend([
            {**copy.deepcopy(value['relations'][0]), 'label': '2026年新增关系'},
            {**copy.deepcopy(value['relations'][0]), 'label': '可能相关', 'clear': False},
            {**copy.deepcopy(value['relations'][0]), 'to': 'not-registered'},
        ])
    clean = validated(pruning, damage)
    assert len(clean['entries']) == 2
    entry = next(entry for entry in clean['entries'] if entry['localKey'] == 'a')
    assert entry['aliases'] == ['资料治理原文名称']
    assert all('2026' not in section['title'] for section in entry['sections'])
    assert [claim['text'] for section in entry['sections'] for claim in section['claims']] == [TEXT]
    assert [relation['label'] for relation in clean['relations']] == ['保留版本']


@pytest.mark.parametrize('damage', ['unknown_evidence', 'changed_quote', 'malformed_entry', 'unknown_type'])
def test_unusable_individual_items_do_not_poison_valid_siblings(pruning, damage):
    def mutate(value):
        entry = value['entries'][0]
        if damage == 'unknown_evidence':
            entry['summary']['evidence'] = [{'evidenceId': 'unregistered'}]
        elif damage == 'changed_quote':
            entry['summary']['evidence'] = [{'evidenceId': 'e1', 'quote': '不是原文的引文'}]
        elif damage == 'malformed_entry':
            value['entries'][0] = 'not-an-entry'
        else:
            entry['type'] = 'unsupported-type'
    clean = validated(pruning, mutate)
    assert [entry['localKey'] for entry in clean['entries']] == ['b']
    assert clean['relations'] == []


def test_all_discarded_is_successful_empty_and_not_reselected_as_pending(pruning):
    store, gateway, service, source = pruning
    gateway.plan = lambda payload: {'entries': [], 'relations': [], 'issues': []}
    job = service.queue([source])['jobs'][0]
    completed = execute(store, service)
    assert completed['id'] == job['id'] and completed['status'] == 'succeeded' and completed['stage'] == 'empty'
    assert pages(service) == [] and len(gateway.calls) == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_publications').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM wiki_search_index').fetchone()[0] == 0
    scope = service.build_scope([source])
    assert scope['documents'] == [] and scope['eligible'] == 0 and scope['excluded']['unclear'] == 1
    repeated = service.queue([source])
    assert repeated == {'jobs': [], 'skipped': [{'sourceId': source, 'reason': 'no_clear_entries'}]}
    assert gateway.calls and len(gateway.calls) == 1 and store.claim() is None
    assert service.queue_all([source]) == {'run': None, 'reused': False}


def test_empty_rebuild_keeps_previous_publication_index_and_outbox(pruning):
    store, gateway, service, source = pruning
    service.queue([source])
    assert execute(store, service)['status'] == 'succeeded'
    original = pages(service)
    before = publication_snapshot(store)
    gateway.plan = lambda payload: {'entries': [], 'relations': [], 'issues': []}
    service.queue([source], force=True)
    result = execute(store, service)
    assert result['status'] == 'succeeded' and result['stage'] == 'empty'
    assert publication_snapshot(store) == before
    assert [(page['id'], page['version']) for page in pages(service)] == [(page['id'], page['version']) for page in original]
    assert len(gateway.calls) == 2


def test_new_original_revision_reopens_an_empty_processed_source(pruning):
    store, gateway, service, source = pruning
    gateway.plan = lambda payload: {'entries': [], 'relations': [], 'issues': []}
    old = service.queue([source])['jobs'][0]
    assert execute(store, service)['stage'] == 'empty'
    assert service.build_scope([source])['eligible'] == 0
    publish(store, [TEXT + '更新后的版本新增了明确的资料归档职责与记录核对方式。'], title='资料治理隔离来源')
    assert service.build_scope([source])['eligible'] == 1
    gateway.plan = plan
    current = service.queue([source])['jobs'][0]
    assert current['id'] != old['id']
    assert execute(store, service)['status'] == 'succeeded' and pages(service)
    assert len(gateway.calls) == 2


def test_json_damage_still_repairs_once_using_the_same_original_request(pruning):
    store, gateway, service, source = pruning
    gateway.plan = lambda payload: '{"entries":' if len(gateway.calls) == 1 else plan(payload)
    service.queue([source])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(gateway.calls) == 2 and gateway.calls[0][1][1] == gateway.calls[1][1][1]
    assert pages(service)


def test_new_policy_checkpoint_reuses_filtered_results_after_interruption(pruning, monkeypatch):
    store, gateway, service, source = pruning
    publish(store, [TEXT, '固定版本登记保留原文引用，并将实际来源归属与明确的适用范围一同保存以便复核。'])
    pieces = [piece for batch in service._batches(store.detail(source)['blocks'], gateway.config) for piece in batch]
    monkeypatch.setattr(service, '_batches', lambda blocks, config: [[pieces[0]], [pieces[-1]]])
    gateway.fail_at = 2
    job = service.queue([source])['jobs'][0]
    assert execute(store, service)['status'] == 'failed'
    with store.connect() as db:
        checkpoint = dict(db.execute('SELECT * FROM wiki_checkpoints').fetchone())
    assert checkpoint
    store.retry(job['id'])
    assert execute(store, service)['status'] == 'succeeded'
    starts = [json.loads(messages[1]['content'])['blocks'][0]['evidenceId'] for _, messages in gateway.calls]
    assert starts == [pieces[0]['evidenceId'], pieces[-1]['evidenceId'], pieces[-1]['evidenceId']]
    assert pages(service)
    with store.connect() as db:
        assert dict(db.execute('SELECT * FROM wiki_checkpoints WHERE checkpoint_key=?', (checkpoint['checkpoint_key'],)).fetchone()) == checkpoint


def test_legacy_task_keeps_strict_whole_result_rejection(pruning):
    store, gateway, service, source = pruning
    def unsupported(payload):
        result = plan(payload)
        result['entries'][0]['summary']['text'] = '2026年资料治理保留固定版本。'
        return result
    gateway.plan = unsupported
    job = service.queue([source])['jobs'][0]
    legacy_selection(store, job['id'])
    assert execute(store, service)['status'] == 'failed'
    assert len(gateway.calls) == 2 and not pages(service)
    assert all(INSTRUCTIONS not in messages[0]['content'] for _, messages in gateway.calls)


def test_legacy_checkpoint_is_not_upgraded_or_reassessed(pruning):
    store, gateway, service, source = pruning
    job = service.queue([source])['jobs'][0]
    legacy_selection(store, job['id'])
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
        payload.pop('yearContextPolicyVersion')
        payload.pop('yearContextInstructions')
        db.execute('UPDATE jobs SET input_json=? WHERE id=?', (dumps(payload), job['id']))
    document = store.detail(source)
    pieces = service._batches(document['blocks'], gateway.config)[0]
    value = plan({'blocks': pieces})
    for entry in value['entries']:
        entry.pop('standalone')
        entry['summary'].pop('clear')
        for section in entry['sections']:
            for claim in section['claims']:
                claim.pop('clear')
    checked = service._validate(value, pieces, document)
    request = service._messages(document, pieces)
    with store.connect() as db:
        run = db.execute('SELECT id FROM wiki_runs WHERE job_id=?', (job['id'],)).fetchone()[0]
        db.execute('INSERT INTO wiki_checkpoints VALUES(?,?,?,?,?)', (run, 'wiki:1/1', _hash(request), dumps(checked), now()))
        before = dict(db.execute('SELECT * FROM wiki_checkpoints WHERE run_id=?', (run,)).fetchone())
    assert execute(store, service)['status'] == 'succeeded' and pages(service)
    assert gateway.calls == []
    with store.connect() as db:
        assert dict(db.execute('SELECT * FROM wiki_checkpoints WHERE run_id=?', (run,)).fetchone()) == before


def test_cancellation_still_fences_a_clear_response_before_publication(pruning):
    store, gateway, service, source = pruning
    job = service.queue([source])['jobs'][0]
    gateway.callback = lambda: store.cancel(job['id'])
    assert execute(store, service)['status'] == 'cancelled'
    assert not pages(service)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_checkpoints').fetchone()[0] == 0
    assert len(gateway.calls) == 1


def test_bulk_children_keep_the_parent_selection_policy_and_instructions(pruning):
    store, gateway, service, source = pruning
    parent = service.queue_all([source])['run']
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT frozen_json FROM wiki_all_runs WHERE job_id=?', (parent['id'],)).fetchone()[0])
    assert frozen['selectionPolicyVersion'] == POLICY_VERSION
    assert frozen['selectionInstructions'] == INSTRUCTIONS
    assert service.advance_all()
    with store.connect() as db:
        child = db.execute("SELECT * FROM jobs WHERE kind='wiki_build'").fetchone()
        payload = json.loads(child['input_json'])
    assert payload['selectionPolicyVersion'] == frozen['selectionPolicyVersion']
    assert payload['selectionInstructions'] == frozen['selectionInstructions']
    assert execute(store, service)['status'] == 'succeeded'
    assert len(gateway.calls) == 1


def test_bulk_25_empty_sources_continue_20_then_5_and_finish_as_skipped(pruning):
    store, gateway, service, first = pruning
    selected = [first] + [publish(store, [TEXT], url=f'https://example.org/pruning-empty/{index}',
                                  title=f'无可收录词条隔离来源 {index}') for index in range(1, 25)]
    gateway.plan = lambda payload: {'entries': [], 'relations': [], 'issues': []}
    parent = service.queue_all(selected)['run']
    assert parent['total'] == parent['counts']['pending'] == 25
    assert service.advance_all()
    assert service.all_run(parent['id'])['counts']['queued'] == 20
    for _ in range(20):
        child = execute(store, service)
        assert child['status'] == 'succeeded' and child['stage'] == 'empty'
    assert service.advance_all()
    second = service.all_run(parent['id'])
    assert second['batches'] == 2 and second['counts']['queued'] == 5 and second['counts']['skipped'] == 20
    for _ in range(5):
        child = execute(store, service)
        assert child['status'] == 'succeeded' and child['stage'] == 'empty'
    assert service.advance_all()
    finished = service.all_run(parent['id'])
    assert finished['job']['status'] == 'succeeded' and finished['counts']['skipped'] == 25
    assert all(finished['counts'][field] == 0 for field in ('pending', 'queued', 'running', 'succeeded', 'failed', 'cancelled'))
    assert len(gateway.calls) == 25 and store.claim() is None
    scope = service.build_scope(selected)
    assert scope['eligible'] == 0 and scope['documents'] == [] and scope['excluded']['unclear'] == 25
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_publications').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM wiki_pages').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM wiki_search_index').fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='wiki_build' AND status='succeeded' AND stage='empty'").fetchone()[0] == 25
        rows = db.execute('''SELECT i.batch,count(*) n FROM wiki_all_items i JOIN wiki_all_runs r ON r.id=i.run_id
            WHERE r.job_id=? GROUP BY i.batch ORDER BY i.batch''', (parent['id'],)).fetchall()
        assert [(row['batch'], row['n']) for row in rows] == [(1, 20), (2, 5)]
    assert service.queue_all(selected) == {'run': None, 'reused': False}
    assert not service.advance_all() and len(gateway.calls) == 25


def test_legacy_bulk_keeps_its_strict_child_protocol_and_frozen_prompts(pruning):
    store, gateway, service, source = pruning
    parent = service.queue_all([source])['run']
    with store.connect() as db:
        row = db.execute('SELECT frozen_json FROM wiki_all_runs WHERE job_id=?', (parent['id'],)).fetchone()
        frozen = json.loads(row['frozen_json'])
        for field in ('selectionPolicyVersion', 'selectionInstructions', 'selectionRepairInstructions'):
            frozen.pop(field, None)
        legacy_frozen = dumps(frozen)
        db.execute('UPDATE wiki_all_runs SET frozen_json=? WHERE job_id=?', (legacy_frozen, parent['id']))
    gateway.config['version'] = 9
    library = PromptLibrary(store)
    current = next(item for item in library.list()['items'] if item['id'] == 'wiki_compile')
    library.update('wiki_compile', current['content'] + '\n新筛选指令不得升级旧批次。', current['version'])
    def ambiguous(payload):
        value = plan(payload)
        value['entries'][0]['summary']['text'] = '2026年资料治理保留固定版本。'
        return value
    gateway.plan = ambiguous
    assert service.advance_all()
    with store.connect() as db:
        child = db.execute("SELECT input_json FROM jobs WHERE kind='wiki_build'").fetchone()
        payload = json.loads(child['input_json'])
    assert all(field not in payload for field in ('selectionPolicyVersion', 'selectionInstructions', 'selectionRepairInstructions'))
    assert payload['yearContextPolicyVersion'] == frozen['yearContextPolicyVersion']
    assert payload['yearContextInstructions'] == frozen['yearContextInstructions']
    assert payload['modelVersion'] == frozen['model']['version'] == 1
    failed = execute(store, service)
    assert failed['status'] == 'failed' and '2026' in failed['error']
    assert len(gateway.calls) == 2 and not pages(service)
    for config, messages in gateway.calls:
        assert config['version'] == 1
        assert frozen['yearContextInstructions'] in messages[0]['content']
        assert INSTRUCTIONS not in messages[0]['content']
        assert '新筛选指令不得升级旧批次' not in messages[0]['content']
        assert 'selectionPolicyVersion' not in json.loads(messages[1]['content'])
    assert service.advance_all()
    completed = service.all_run(parent['id'])
    assert completed['job']['status'] == 'failed' and completed['counts']['failed'] == 1
    assert completed['counts']['skipped'] == 0
    with store.connect() as db:
        assert db.execute('SELECT frozen_json FROM wiki_all_runs WHERE job_id=?', (parent['id'],)).fetchone()[0] == legacy_frozen
