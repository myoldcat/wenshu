"""Exercise persisted Wiki provenance, publication fences and retrieval budgets."""
import copy
import json

import pytest

from server.store import Store, dumps
from server.wiki import WikiService, PROMPT_VERSION


def clear_result(result):
    """Normal deterministic plans explicitly assess their content as reusable."""
    for entry in result.get('entries', []):
        if not isinstance(entry, dict):
            continue
        entry.setdefault('standalone', True)
        if isinstance(entry.get('summary'), dict):
            entry['summary'].setdefault('clear', True)
        for section in entry.get('sections', []):
            if isinstance(section, dict):
                for claim in section.get('claims', []):
                    if isinstance(claim, dict):
                        claim.setdefault('clear', True)
    for relation in result.get('relations', []):
        if isinstance(relation, dict):
            relation.setdefault('clear', True)
    return result


def legacy_selection(store, job_id):
    """Keep strict rejection tests on the pre-selection frozen protocol."""
    with store.connect() as db:
        row = db.execute('SELECT input_json FROM jobs WHERE id=?', (job_id,)).fetchone()
        payload = json.loads(row['input_json'])
        payload.pop('selectionPolicyVersion', None)
        payload.pop('selectionInstructions', None)
        payload.pop('selectionRepairInstructions', None)
        db.execute('UPDATE jobs SET input_json=? WHERE id=?', (dumps(payload), job_id))


class Gateway:
    def __init__(self):
        self.config = {'id': 'model_wiki', 'version': 1, 'name': '测试模型', 'model': 'mock',
                       'contextChars': 24000, 'maxOutputTokens': 8192, 'apiKey': 'never-persist-secret'}
        self.calls = []
        self.callback = None
        self.fail_at = None
        self.rewrite = None

    def get_preferences(self):
        return {'summaryModelId': self.config['id']}

    def resolve(self, model_id=None, version=None):
        assert model_id == self.config['id']
        return {**self.config, 'version': version if version is not None else self.config['version']}

    def complete(self, config, messages):
        assert sum(len(message['content']) for message in messages) <= config['contextChars']
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.fail_at == len(self.calls):
            raise RuntimeError('never-persist-secret provider payload')
        payload = json.loads(messages[1]['content'])
        block = payload['blocks'][0]
        evidence = [{'blockId': block['blockId'], 'quote': block['text'][:80]}]
        result = {'entries': [{'localKey': 'governance', 'label': '知识治理', 'type': 'concept', 'subtype': '', 'aliases': [],
                    'summary': {'text': '知识治理保留原始证据。', 'evidence': evidence},
                    'sections': [{'title': '来源记载', 'claims': [{'text': '资料保留来源与原始版本。', 'evidence': evidence}]}]},
                   {'localKey': 'versions', 'label': '原始版本', 'type': 'entity', 'subtype': '', 'aliases': [],
                    'summary': {'text': '本文记载的原始资料版本。', 'evidence': evidence},
                    'sections': [{'title': '记录', 'claims': [{'text': '来源具有原始版本。', 'evidence': evidence}]}]}],
                  'relations': [{'from': 'governance', 'to': 'versions', 'type': 'association', 'label': '保留版本', 'evidence': evidence}], 'issues': []}
        if self.rewrite:
            result = self.rewrite(result)
        return {'text': json.dumps(clear_result(result), ensure_ascii=False), 'usage': {'total_tokens': 42}}


def publish(store, blocks=None, url='https://example.org/wiki', title='知识治理资料'):
    blocks = blocks or ['知识治理保留原始证据。资料保留来源与原始版本，能够回查每一条事实。']
    queued = store.queue('file', {}, title)
    claimed = store.claim()
    assert claimed['id'] == queued['id']
    raw = ('<html>' + '\n'.join(blocks) + '</html>').encode()
    parsed = {'title': title, 'body': blocks, 'blocks': [{'kind': 'paragraph', 'text': value, 'ordinal': index} for index, value in enumerate(blocks)],
              'language': 'zh', 'parser_version': 'wiki-test-1'}
    store.publish(claimed, {'url': url}, raw, parsed)
    return store.job(queued['id'])['source_id']


@pytest.fixture
def environment(tmp_path):
    store = Store(tmp_path / 'data')
    models = Gateway()
    service = WikiService(store, models)
    source = publish(store)
    return store, models, service, source


def execute(store, service):
    job = store.claim()
    assert job
    service.process(job)
    return store.job(job['id'])


def compile_page(environment, *, legacy=False):
    store, models, service, source = environment
    job = service.queue([source])['jobs'][0]
    if legacy:
        legacy_selection(store, job['id'])
    assert execute(store, service)['status'] == 'succeeded'
    overview = service.overview()
    node = next(node for node in overview['nodes'] if node['type'] == 'concept')
    return service.page(node['id'])


def test_compilation_persists_grounded_pages_relations_and_model_lineage(environment):
    store, models, service, source = environment
    page = compile_page(environment)
    original = store.detail(source)
    ref = page['evidence'][0]
    block = original['blocks'][0]
    assert ref['blockId'] == block['id']
    assert block['text'][ref['start']:ref['end']] == ref['quote']
    assert ref['revisionId'] == original['revisionId']
    assert page['sections'][0]['title'] == '定义与范围'
    assert page['relations'][0]['targetLabel'] == '原始版本'
    assert page['provenance']['promptVersion'] == PROMPT_VERSION
    assert page['provenance']['evidenceType'] == 'original_blocks'
    assert page['provenance']['telemetry']['calls'] == 1
    assert page['provenance']['telemetry']['reportedUsage'] == {'total_tokens': 42}
    restarted = WikiService(Store(store.root), models)
    assert restarted.page(page['id']) == page
    assert len(restarted.overview()['edges']) == 3
    with store.connect() as db:
        records = str([tuple(row) for row in db.execute('SELECT * FROM wiki_runs')])
    assert models.config['apiKey'] not in records


def test_duplicate_force_history_and_stable_identity(environment):
    store, models, service, source = environment
    first = compile_page(environment)
    assert service.queue([source]) == {'jobs': [], 'skipped': [{'sourceId': source, 'reason': 'already_built'}]}
    assert len(models.calls) == 1
    service.queue([source], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    second = service.page(first['id'])
    assert second['version'] > first['version']
    assert len(second['history']) == 2
    assert service.page(first['id'], first['version'])['summary'] == first['summary']
    assert service.page(first['id'], first['version'])['archived']
    assert service.overview()['stats']['pages'] == 2


def test_existing_failed_job_is_returned_until_explicit_retry_or_force(environment):
    store, models, service, source = environment
    models.fail_at = 1
    job = service.queue([source])['jobs'][0]
    assert execute(store, service)['status'] == 'failed'
    assert service.queue([source])['jobs'][0]['id'] == job['id']
    assert store.claim() is None
    assert 'never-persist-secret' not in store.job(job['id'])['error']
    store.retry(job['id'])
    assert execute(store, service)['status'] == 'succeeded'


@pytest.mark.parametrize('damage', ['unknown_block', 'changed_quote', 'empty_quote', 'missing_definition_evidence', 'unknown_relation', 'empty_entries'])
def test_schema_and_exact_quote_failures_do_not_replace_old_success(environment, damage):
    store, models, service, source = environment
    first = compile_page(environment)
    def corrupt(result):
        entry = result['entries'][0]
        if damage == 'unknown_block':
            entry['summary']['evidence'][0]['blockId'] = 'invented'
        elif damage == 'changed_quote':
            entry['summary']['evidence'][0]['quote'] = '原文从未表达的虚构内容'
        elif damage == 'empty_quote':
            entry['summary']['evidence'][0]['quote'] = ''
        elif damage == 'missing_definition_evidence':
            entry['summary']['evidence'] = []
        elif damage == 'unknown_relation':
            result['relations'][0]['from'] = 'invented'
        else:
            result['entries'] = []
        return result
    models.rewrite = corrupt
    legacy_selection(store, service.queue([source], force=True)['jobs'][0]['id'])
    assert execute(store, service)['status'] == 'failed'
    assert len(models.calls) == 3  # Original call plus exactly one repair attempt.
    assert service.page(first['id'])['version'] == first['version']
    assert service.search('知识治理')['results']


def test_one_schema_repair_uses_same_source_never_rejected_response(environment):
    store, models, service, source = environment
    def rewrite(result):
        if len(models.calls) == 1:
            result['entries'][0]['summary']['evidence'] = []
        return result
    models.rewrite = rewrite
    compile_page(environment, legacy=True)
    assert len(models.calls) == 2
    assert models.calls[0][1][1] == models.calls[1][1][1]
    assert len(models.calls[1][1]) == 2


def test_cancel_during_model_call_cannot_publish_or_store_checkpoint(environment):
    store, models, service, source = environment
    job = service.queue([source])['jobs'][0]
    models.callback = lambda: store.cancel(job['id'])
    assert execute(store, service)['status'] == 'cancelled'
    assert service.overview()['stats']['pages'] == 0
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_checkpoints').fetchone()[0] == 0


def test_late_attempt_does_not_fail_or_publish_retry(environment):
    store, models, service, source = environment
    job = service.queue([source])['jobs'][0]
    claimed = store.claim()
    def interrupt():
        store.recover()
        store.claim()  # Same job, a new frozen attempt.
    models.callback = interrupt
    service.process(claimed)
    assert store.job(job['id'])['status'] == 'running'
    assert store.job(job['id'])['attempts'] == 2
    assert service.overview()['stats']['pages'] == 0


def test_new_run_fences_cancelled_inflight_run_but_keeps_published_page(environment):
    store, models, service, source = environment
    first = compile_page(environment)
    old = service.queue([source], force=True)['jobs'][0]
    def replace_cancelled_run():
        store.cancel(old['id'])
        service.queue([source], force=True)
    models.callback = replace_cancelled_run
    assert execute(store, service)['status'] == 'cancelled'
    assert store.job(old['id'])['status'] == 'cancelled'
    assert service.page(first['id'])['version'] == first['version']
    assert execute(store, service)['status'] == 'succeeded'


def test_stale_source_marks_pages_and_old_revision_stays_readable(environment):
    store, models, service, source = environment
    first = compile_page(environment)
    publish(store, ['更新后的知识治理资料明确了新的版本，并补充了来源校验流程，所有内容都保留完整的原始证据记录。'])
    stale = service.page(first['id'])
    assert stale['stale'] and all(ref['stale'] for ref in stale['evidence'])
    assert service.overview()['stats']['stale'] == 1
    assert service.overview()['stats']['pending'] == 1
    old_block = store.detail(source, stale['evidence'][0]['revisionId'])['blocks'][0]['text']
    reference = stale['evidence'][0]
    assert old_block[reference['start']:reference['end']] == reference['quote']
    service.queue([source])
    assert execute(store, service)['status'] == 'succeeded'
    assert service.page(first['id'])['stale'] is False


def test_changed_revision_before_start_supersedes_without_model_call(environment):
    store, models, service, source = environment
    job = service.queue([source])['jobs'][0]
    # Publish an independent revision while keeping Wiki job queued.
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job['id'],))
    publish(store, ['更新后的原文资料记载了新的校验要求，旧版本的加工结果必须保留来源标记，不能覆盖新版本。'])
    store.retry(job['id'])
    assert execute(store, service)['stage'] == 'superseded'
    assert not models.calls


def test_checkpoints_resume_after_failure_and_offsets_cover_later_slices(environment):
    store, models, service, source = environment
    publish(store, ['甲' * 900 + '乙' * 900 + '丙' * 900])
    models.config['contextChars'] = 6800
    models.fail_at = 2
    job = service.queue([source])['jobs'][0]
    assert execute(store, service)['status'] == 'failed'
    with store.connect() as db:
        first = db.execute('SELECT checkpoint_key,result_json FROM wiki_checkpoints').fetchone()
        assert first is not None
    store.retry(job['id'])
    assert execute(store, service)['status'] == 'succeeded'
    # The completed first checkpoint was not sent again after retry.
    starts = [json.loads(call[1][1]['content'])['blocks'][0]['start'] for call in models.calls]
    assert starts.count(0) == 1
    assert starts.count(starts[1]) == 2
    page = service.page(next(node['id'] for node in service.overview()['nodes'] if node['type'] == 'concept'))
    for reference in page['evidence']:
        block = store.detail(source)['blocks'][0]
        assert block['text'][reference['start']:reference['end']] == reference['quote']
    assert any(reference['start'] > 0 for reference in page['evidence'])


def test_frozen_model_version_and_prompt_upgrade_rejects_checkpoint_retry(environment):
    store, models, service, source = environment
    job = service.queue([source])['jobs'][0]
    models.config['version'] = 2
    assert execute(store, service)['status'] == 'succeeded'
    assert models.calls[0][0]['version'] == 1
    new_job = service.queue([source], force=True)['jobs'][0]
    with store.connect() as db:
        db.execute("UPDATE wiki_runs SET prompt_version='obsolete' WHERE job_id=?", (new_job['id'],))
    assert execute(store, service)['status'] == 'failed'
    assert len(models.calls) == 1


def test_search_returns_original_evidence_and_bounded_graph_expansion(environment):
    store, models, service, source = environment
    compile_page(environment)
    result = service.search('知识治理', limit=1, hops=1)
    assert result['mode'] == 'fulltext+graph'
    assert len(result['results']) == 1
    assert result['evidence'][0]['sourceId'] == source
    assert result['expanded'][0]['label'] == '原始版本'
    assert result['expanded'][0]['topicId']
    assert result['expanded'][0]['evidence']
    assert not service.search('知识治理', limit=1, hops=0)['expanded']
    assert len(service.overview(limit=2)['nodes']) <= 2
    assert service.overview(limit=2)['truncated']
    assert service.search('" OR * NOT -') == {'query': '" OR * NOT -', 'mode': 'fulltext+graph', 'results': [], 'evidence': [], 'expanded': [], 'truncated': False,
                                             'truncation': {'lexical': False, 'graph': False, 'raw': False}}


def test_original_text_search_works_before_wiki_compilation(environment):
    store, models, service, source = environment
    result = service.search('知识治理')
    assert result['results'] == []
    assert result['evidence'][0]['sourceId'] == source
    assert service.overview()['topics'] == []
    single_character = service.search('识')
    assert single_character['evidence'][0]['sourceId'] == source
    assert '识' in single_character['evidence'][0]['quote']


def test_search_snippet_keeps_its_own_evidence_after_four_other_references(environment):
    store, models, service, source = environment
    publish(store, ['第一份来源材料。', '第二份来源材料。', '第三份来源材料。', '第四份来源材料。', 'NebulaZebra867 是第五份材料中的标记。'])
    blocks = store.detail(source)['blocks']
    def multiple(result):
        result['entries'][0]['sections'] = [{'title': '五项记载', 'claims': [
            {'text': block['text'], 'evidence': [{'blockId': block['id'], 'quote': block['text']}]}
            for block in blocks]}]
        return result
    models.rewrite = multiple
    service.queue([source])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.search('NebulaZebra867')['results'][0]
    assert 'NebulaZebra867' in result['snippet']
    assert result['evidence'][0]['blockId'] == blocks[4]['id']


def test_same_name_entities_stay_separate_exact_concepts_link_across_sources(environment):
    store, models, service, source = environment
    other = publish(store, ['知识治理保留原始证据。另一篇资料保留原始版本，并对每项记录建立可追溯的来源和校验流程。'], url='https://example.org/second')
    service.queue([source, other])
    assert execute(store, service)['status'] == 'succeeded'
    assert execute(store, service)['status'] == 'succeeded'
    overview = service.overview()
    concepts = [node for node in overview['nodes'] if node['type'] == 'concept']
    assert len(concepts) == 2 and concepts[0]['id'] != concepts[1]['id']
    page = service.page(concepts[0]['id'])
    relation = next(relation for relation in page['relations'] if relation.get('derivedBy') == 'exact_name_and_definition')
    assert {ref['sourceId'] for ref in relation['evidence']} == {source, other}
    assert any('保持独立' in issue for issue in page['issues'])
    result = service.search('知识治理', limit=1, hops=1)
    assert any(item['reason'].endswith('跨来源同定义概念') for item in result['expanded'])
    entity = next(node for node in overview['nodes'] if node['type'] == 'entity')
    assert not any(relation.get('derivedBy') for relation in service.page(entity['id'])['relations'])
    # Rebuilding one source does not erase another source's contribution.
    other_pages = [node for node in concepts if node['sourceId'] == other]
    other_version = service.page(other_pages[0]['id'])['version']
    service.queue([source], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    assert service.page(other_pages[0]['id'])['version'] == other_version
    assert service.overview()['stats']['pages'] == 4


def test_same_concept_name_with_different_definition_is_not_linked(environment):
    store, models, service, source = environment
    first = compile_page(environment)
    other = publish(store, ['同名术语在这里表示不同的定义，其适用范围与另一篇资料并不一致，因此需要保留独立的来源和解释。'], url='https://example.org/different')
    def change(result):
        result['entries'][0]['summary']['text'] = '同名概念的另一种定义。'
        return result
    models.rewrite = change
    service.queue([other])
    assert execute(store, service)['status'] == 'succeeded'
    page = service.page(first['id'])
    assert not any(relation.get('derivedBy') for relation in page['relations'])
    assert any('同名词条' in issue for issue in page['issues'])


@pytest.mark.parametrize('call', [lambda service: service.queue([]), lambda service: service.queue(['x'] * 21),
    lambda service: service.search(''), lambda service: service.search('a', hops=3),
    lambda service: service.search('a', limit=100), lambda service: service.overview(limit=301)])
def test_invalid_input_does_not_queue_or_expand(environment, call):
    store, models, service, source = environment
    with pytest.raises(ValueError):
        call(service)
    assert not models.calls


def test_registered_evidence_ids_publish_program_owned_quotes_and_offsets(environment):
    store, models, service, source = environment
    publish(store, ['这是第一句资料，用于介绍背景。 这是第二句原始证据，保留繁體與英文 Original 以及完整标点。最终一句说明原文版本仍然可供回查。'])
    def registered(result):
        payload = json.loads(models.calls[-1][1][1]['content'])
        piece = payload['blocks'][1]
        refs = [{'evidenceId': piece['evidenceId']}]
        for entry in result['entries']:
            entry['summary']['evidence'] = refs
            for section in entry['sections']:
                for claim in section['claims']:
                    claim['evidence'] = refs
        for relation in result['relations']:
            relation['evidence'] = refs
        return result
    models.rewrite = registered
    page = compile_page(environment)
    reference = page['evidence'][0]
    original = store.detail(source)['blocks'][0]['text']
    assert reference['evidenceId'] == 'e2'
    assert reference['start'] > 0
    assert original[reference['start']:reference['end']] == reference['quote']
    assert '繁體與英文 Original' in reference['quote']
    assert '繁体与英文' not in reference['quote']


@pytest.mark.parametrize('reference', [{'evidenceId': 'invented'}, {'evidenceId': []},
    {'evidenceId': 'e1', 'blockId': 'invented'}, {'evidenceId': 'e1', 'quote': '改写的引文'},
    {'evidenceId': 'e1', 'start': 1234}])
def test_registered_evidence_rejects_unknown_or_conflicting_model_fields(environment, reference):
    store, models, service, source = environment
    def corrupted(result):
        result['entries'][0]['summary']['evidence'] = [reference]
        return result
    models.rewrite = corrupted
    legacy_selection(store, service.queue([source])['jobs'][0]['id'])
    assert execute(store, service)['status'] == 'failed'
    assert len(models.calls) == 2
    assert service.overview()['stats']['pages'] == 0


def test_registered_segments_preserve_slices_offsets_and_serialized_context_budget():
    text = ('首句中文含有繁體、英文及表情🙂。\n' + 'x"\\\t' * 280 + '结束。  Next sentence preserves whitespace.\n') * 2
    blocks = [{'id': 'block_long', 'kind': 'paragraph', 'text': text}]
    config = {'contextChars': 6800}
    batches = WikiService._batches(blocks, config)
    pieces = [piece for batch in batches for piece in batch]
    assert ''.join(piece['text'] for piece in pieces) == text
    assert len({piece['evidenceId'] for piece in pieces}) == len(pieces)
    for piece in pieces:
        assert 0 < len(piece['text']) <= 500
        assert text[piece['start']:piece['start'] + len(piece['text'])] == piece['text']
    document = {'id': 'source', 'revisionId': 'revision', 'title': '带转义字符的输入预算验证'}
    for batch in batches:
        assert len(dumps(batch)) <= config['contextChars'] - 6000
        assert sum(len(message['content']) for message in WikiService._messages(document, batch)) <= config['contextChars']
    with pytest.raises(ValueError, match='本批原文注册'):
        WikiService._evidence([{'evidenceId': pieces[-1]['evidenceId']}], batches[0], document)


def test_latest_success_is_kept_before_more_than_fifty_failed_builds(environment):
    store, models, service, source = environment
    compile_page(environment)
    for _ in range(55):
        old = service.queue([source], force=True)['jobs'][0]
        with store.connect() as db:
            db.execute("UPDATE jobs SET status='failed',error='历史测试失败' WHERE id=?", (old['id'],))
    latest = service.queue([source], force=True)['jobs'][0]
    assert execute(store, service)['status'] == 'succeeded'
    jobs = service.overview()['jobs']
    assert len(jobs) == 50
    assert jobs[0]['id'] == latest['id'] and jobs[0]['status'] == 'succeeded'


@pytest.mark.parametrize('location', ['label', 'summary', 'claim', 'relation', 'section', 'alias'])
def test_unsupported_explicit_year_never_publishes_even_after_repair(environment, location):
    store, models, service, source = environment
    original = compile_page(environment)
    def invent_year(result):
        entry = result['entries'][0]
        if location == 'label':
            entry['label'] += '（2025年10月）'
        elif location == 'summary':
            entry['summary']['text'] += '日期是2025年10月13日。'
        elif location == 'claim':
            entry['sections'][0]['claims'][0]['text'] += '日期是2025-10-13。'
        elif location == 'relation':
            result['relations'][0]['label'] = '2025 年获批'
        elif location == 'section':
            entry['sections'][0]['title'] = '2025'
        else:
            entry['aliases'] = ['2025年审批']
        return result
    models.rewrite = invent_year
    legacy_selection(store, service.queue([source], force=True)['jobs'][0]['id'])
    failed = execute(store, service)
    assert failed['status'] == 'failed'
    assert '原文未记载的年份' in failed['error']
    assert len(models.calls) == 3
    assert service.page(original['id'])['version'] == original['version']


def test_supported_year_can_publish_and_ordinary_number_rephrasing_is_not_rejected(environment):
    store, models, service, source = environment
    publish(store, ['2027年10月13日，资料系统完成原始证据记录归档，所有记录均可根据来源版本回查并校验。'])
    def supported(result):
        entry = result['entries'][0]
        entry['label'] = '2027 年归档'
        entry['summary']['text'] = '2027年10月13日完成资料归档。'
        entry['sections'][0]['claims'][0]['text'] = '归档日期为2027-10-13。'
        result['relations'][0]['label'] = '2027年保留版本'
        return result
    models.rewrite = supported
    page = compile_page(environment)
    assert page['label'] == '2027 年归档'
    assert page['summary'].startswith('2027年')
    # The guard only targets explicit four-digit years, not number formatting.
    from server.wiki import _check_years
    _check_years('共有三项记录，增长10%，涉及12345个字符。', [{'quote': '有3项记录。'}])
    _check_years('金额2000元，合计$2025，型号AB2027，记录数15000。', [{'quote': '金额两千元。'}])
    with pytest.raises(ValueError, match='2025'):
        _check_years('2025年获批', [{'quote': '原文金额为2025元。'}])


def test_one_repair_can_remove_invented_year_without_replacing_input_evidence(environment):
    store, models, service, source = environment
    def repair(result):
        if len(models.calls) == 1:
            result['entries'][0]['label'] = '知识治理（2025年10月）'
        return result
    models.rewrite = repair
    page = compile_page(environment, legacy=True)
    assert '2025' not in page['label']
    assert len(models.calls) == 2
    assert models.calls[0][1][1] == models.calls[1][1][1]


def test_year_support_is_specific_to_selected_evidence_not_other_source_segments():
    from server.wiki import _check_years
    _check_years('生效年份2027', [{'quote': 'The new fares take effect in 2027.'}])
    with pytest.raises(ValueError, match='原文未记载的年份'):
        _check_years('2025年10月13日获批', [{'quote': 'The fare change was approved on October 13.'}])


@pytest.mark.parametrize('kind,subtype,expected', [('concept', 'method', 'method'), ('entity', 'person', 'person'),
    ('claim', 'method', ''), ('fact', 'person', ''), ('entity', 'method', ''), ('concept', 'person', ''),
    ('claim', 'person', ''), ('entity', 'unrelated', ''), ('fact', None, '')])
def test_subtype_only_refines_its_owning_base_type(environment, kind, subtype, expected):
    store, models, service, source = environment
    def typed(result):
        result['entries'][0].update(type=kind, subtype=subtype)
        return result
    models.rewrite = typed
    service.queue([source])
    assert execute(store, service)['status'] == 'succeeded'
    node = next(node for node in service.overview()['nodes'] if node['label'] == '知识治理')
    assert node['type'] == kind and node['subtype'] == expected
    page = service.page(node['id'])
    assert page['type'] == kind and page['subtype'] == expected
    result = next(result for result in service.search('知识治理')['results'] if result['id'] == node['id'])
    assert result['type'] == kind and result['subtype'] == expected


def test_normalized_subtype_produces_one_identity_even_for_legacy_checkpoints():
    evidence = [{'sourceId': 'source', 'revisionId': 'revision', 'blockId': 'block', 'quote': '原文记录。', 'start': 0, 'end': 5}]
    entry = {'localKey': 'k1', 'label': '有据主张', 'type': 'claim', 'subtype': 'method', 'aliases': [],
        'summary': {'text': '原文记录。', 'evidence': evidence},
        'sections': [{'title': '来源', 'claims': [{'text': '原文记录。', 'evidence': evidence}]}]}
    legacy = WikiService._compile({'source_id': 'source'}, [{'entries': [entry], 'relations': [], 'issues': []}])
    normalized = copy.deepcopy(entry)
    normalized['subtype'] = ''
    current = WikiService._compile({'source_id': 'source'}, [{'entries': [normalized], 'relations': [], 'issues': []}])
    assert legacy.keys() == current.keys()
    assert next(iter(legacy.values()))['subtype'] == ''


def test_historical_subtype_is_normalized_on_all_reads_without_database_write(environment):
    store, models, service, source = environment
    page = compile_page(environment)
    # Simulate a page persisted by an older build, without touching real data.
    with store.connect() as db:
        row = db.execute('SELECT content_json FROM wiki_pages WHERE entry_id=?', (page['id'],)).fetchone()
        historical = json.loads(row['content_json'])
        historical.update(type='claim', subtype='method')
        raw = dumps(historical)
        db.execute('UPDATE wiki_pages SET content_json=? WHERE entry_id=?', (raw, page['id']))
    for result in (service.page(page['id']), service.page(page['id'], page['version'])):
        assert result['type'] == 'claim' and result['subtype'] == ''
        assert result['id'] == page['id'] and result['version'] == page['version']
    node = next(node for node in service.overview()['nodes'] if node['id'] == page['id'])
    assert node['type'] == 'claim' and node['subtype'] == ''
    result = next(result for result in service.search('知识治理')['results'] if result['id'] == page['id'])
    assert result['type'] == 'claim' and result['subtype'] == ''
    with store.connect() as db:
        assert db.execute('SELECT content_json FROM wiki_pages WHERE entry_id=?', (page['id'],)).fetchone()[0] == raw
