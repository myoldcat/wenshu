"""Reader composition checkpoints with isolated stores and simulated providers."""
import copy
import json

from server.prompts import PromptLibrary
from server.store import Store, dumps
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.writing import WritingService
from server import prompt_defaults
from server.writing_composition import POLICY


def _frozen(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


def _steps(store, run_id):
    with store.connect() as db:
        return [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run_id,))]


def _edit_prompt(store, prompt_id, marker):
    library = PromptLibrary(store)
    item = next(item for item in library.list()['items'] if item['id'] == prompt_id)
    return library.update(prompt_id, item['content'] + '\n' + marker, item['version'])


def _parent_context(service, gateway, parent, job):
    service._automatic_parent_run = parent['runId']
    service._automatic_parent_job = job
    service._automatic_step = 'section'
    service._automatic_model_config = gateway.resolve(gateway.config['id'])


def _queue_parent(document, project):
    _, gateway, service, _ = document
    # The whole-draft provider fixture has one text connection; expose its
    # catalogue for the automatic parent's production model selection path.
    gateway.models = lambda: [{key: copy.deepcopy(value) for key, value in gateway.config.items() if key != 'apiKey'}]
    return service.queue_automatic(project['id'], project['version'])


def test_new_manual_outline_freezes_composition_and_abstract_planning(document):
    store, _, service, _ = document
    project = ready(document)
    queued = service.queue(project['id'], project['version'], 'outline')
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert frozen['compositionSnapshot']['policy'] == POLICY
    assert packet['compositionPolicy'] == POLICY
    assert packet['outlinePolicy'] == 'abstract-outline-v1'
    assert 'chapterContext' not in packet
    assert frozen['compositionSnapshot']['outlineInstructions'] in frozen['messages'][0]['content']
    assert frozen['compositionSnapshot']['strictInstructions'] in frozen['messages'][0]['content']
    with store.connect() as db:
        prompt = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                      (queued['job']['id'],)).fetchone()[0])['writing_outline_strict']
    assert prompt['compositionPolicy'] == POLICY


def test_new_manual_chapter_context_has_saved_tail_and_next_goal_without_evidence(document):
    store, gateway, service, _ = document
    project = ready(document)
    first, current, following = project['sections']
    previous = '这一段是先前已保存的正文，只为本章承接读者的问题。'
    project = service.save_section(project['id'], first['id'], project['version'],
                                   {'content': previous, 'locked': True})
    queued = service.queue(project['id'], project['version'], 'section', current['id'])
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    context = packet['chapterContext']
    assert context['evidenceRole'] == 'none'
    assert context['position']['ordinal'] == 2
    assert context['previousChapter']['written'] is True
    assert previous in context['previousChapter']['tail']
    assert context['nextChapter']['title'] == following['title']
    assert context['nextChapter']['goal'] == following['goal']
    assert len(dumps(context)) <= frozen['compositionSnapshot']['contextLimit']
    assert previous not in dumps(frozen['blocks'])
    assert previous not in dumps(packet['blocks'])
    assert previous not in dumps(packet['sourceContext'])
    before = copy.deepcopy(project['sections'][0])
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 1 and not gateway.matching_calls
    assert service.get(project['id'])['sections'][0] == before


def test_automatic_children_reuse_actual_parent_prompt_content_and_version(document):
    store, gateway, service, _ = document
    project = ready(document)
    _edit_prompt(store, 'writing_section_strict', '本次父任务冻结甲。')
    parent = _queue_parent(document, project)
    parent_frozen = _frozen(store, parent['runId'])
    assert parent_frozen['compositionSnapshot']['policy'] == POLICY
    assert len(parent_frozen['compositionPrompts']) == 6
    original = parent_frozen['compositionPrompts']['writing_section_strict']
    _edit_prompt(store, 'writing_section_strict', '之后队列才使用乙。')
    parent_job = store.claim()
    restarted = WritingService(Store(store.root), gateway)
    _parent_context(restarted, gateway, parent, parent_job)
    child = restarted.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    frozen = _frozen(store, child['runId'])
    assert frozen['compositionSnapshot'] == parent_frozen['compositionSnapshot']
    assert original['content'] in frozen['messages'][0]['content']
    assert '本次父任务冻结甲。' in frozen['messages'][0]['content']
    assert '之后队列才使用乙。' not in frozen['messages'][0]['content']
    with store.connect() as db:
        saved = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                     (child['job']['id'],)).fetchone()[0])
    assert saved['writing_section_strict'] == original
    assert not gateway.calls


def test_precomposition_parent_does_not_upgrade_new_child(document):
    store, gateway, service, _ = document
    project = ready(document)
    parent = _queue_parent(document, project)
    with store.connect() as db:
        frozen = _frozen(store, parent['runId'])
        frozen.pop('compositionSnapshot')
        frozen.pop('compositionPrompts')
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), parent['runId']))
    _parent_context(service, gateway, parent, store.claim())
    child = service.queue(project['id'], project['version'], 'section', project['sections'][1]['id'])
    frozen = _frozen(store, child['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert 'compositionSnapshot' not in frozen
    assert 'compositionPolicy' not in packet and 'chapterContext' not in packet
    with store.connect() as db:
        prompts = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                       (child['job']['id'],)).fetchone()[0])
    assert 'compositionPolicy' not in prompts['writing_section_strict']


def test_single_document_carries_non_evidence_roles_with_one_generation_call(document):
    store, gateway, service, _ = document
    project = ready(document)
    queued, job = queue_and_claim(document, project)
    assert _frozen(store, queued['runId'])['plan']['strategy'] == 'single'
    service.process(job)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 1 and not gateway.matching_calls
    sections = gateway.calls[0]['payload']['sections']
    assert [s['chapterContext']['position']['ordinal'] for s in sections] == [1, 2, 3]
    assert all(s['compositionPolicy'] == POLICY for s in sections)
    assert all(s['chapterContext']['evidenceRole'] == 'none' and s['blocks'] for s in sections)
    packet = gateway.calls[0]['payload']
    assert packet['compositionInstructionFormat'] == 'shared-catalog-v1'
    assert len(packet['instructionCatalog']) == 2
    for section in sections:
        writing = packet['instructionCatalog'][section['writingInstructionRef']]
        document_prompt = packet['instructionCatalog'][section['documentInstructionRef']]
        assert writing['kind'] == 'section' and writing['mode'] == section['mode']
        assert document_prompt['kind'] == 'document' and document_prompt['mode'] == section['mode']


def test_shared_catalog_retains_different_content_for_same_prompt_identity(document):
    store, _, service, _ = document
    project = ready(document)
    queued = service.queue_document(project['id'], project['version'])
    prepared = _steps(store, queued['runId'])
    prepared[1]['sectionInstructions'] += '\n这一章冻结了不同文字。'
    packet = json.loads(service._document_messages(_frozen(store, queued['runId']), prepared)[1]['content'])
    first, second, third = packet['sections']
    assert first['writingInstructionRef'] != second['writingInstructionRef']
    assert first['writingInstructionRef'] == third['writingInstructionRef']
    assert len(packet['instructionCatalog']) == 3
    assert packet['instructionCatalog'][second['writingInstructionRef']]['content'] == prepared[1]['sectionInstructions']


def test_pre_catalog_composition_checkpoints_keep_frozen_inline_shape(document):
    store, _, service, _ = document
    project = ready(document)
    queued = service.queue_document(project['id'], project['version'])
    prepared = _steps(store, queued['runId'])
    for item in prepared:
        item.pop('compositionInstructionFormat')
        item.pop('writingPromptVersion')
        item['documentOrchestration'] = '这一任务早先冻结了内联编排原文。'
    messages = service._document_messages(_frozen(store, queued['runId']), prepared)
    packet = json.loads(messages[1]['content'])
    assert messages[0]['content'] == '这一任务早先冻结了内联编排原文。'
    assert 'instructionCatalog' not in packet and 'compositionInstructionFormat' not in packet
    for section, item in zip(packet['sections'], prepared):
        assert section['writingInstructions'] == item['sectionInstructions']
        assert section['documentInstructions'] == item['documentPrompt']['content']
        assert 'writingInstructionRef' not in section and 'documentInstructionRef' not in section


def test_strict_transition_without_reference_is_saved_without_claiming_review(document):
    store, gateway, service, _ = document
    project = ready(document)
    transition = '接下来讨论版本保存与回查的具体安排。'

    def add_transition(result, _):
        result['paragraphs'].insert(1, {'text': transition, 'kind': 'supported', 'evidence': []})
        return result

    gateway.transform = add_transition
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    current = service.get(project['id'])['sections'][0]
    assert transition in current['content']
    assert '【模型补充' not in current['content']
    assert current['citationBindingState'] == 'unreviewed'
    assert not current.get('citationBindings')
    assert next(paragraph for paragraph in current['content'].split('\n\n') if transition in paragraph) == transition
    assert current['evidence'] and all(reference['quote'] != transition for reference in current['evidence'])
    assert len(gateway.calls) == 1 and not gateway.matching_calls


def test_sequential_context_is_frozen_before_http_and_retry_reuses_exact_request(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 5000
    gateway.fail_calls = {2}
    project = ready(document, words=(800, 800, 800),
                    brief={'topic': '固定版本与人工修订', 'targetWords': 2400, 'interactiveGeneration': 'chapter'})
    queued, job = queue_and_claim(document, project)
    initial = _steps(store, queued['runId'])
    assert _frozen(store, queued['runId'])['plan']['strategy'] == 'sequential'
    assert initial[1]['compositionSnapshot']['policy'] == POLICY
    assert not json.loads(initial[1]['messages'][1]['content'])['chapterContext']['previousChapter']['written']
    assert 'compositionContextPrepared' not in initial[1]
    seen_before_http = []

    def before_http(index, packet):
        current_steps = _steps(store, queued['runId'])
        persisted = current_steps[index - 1]
        assert persisted['compositionContextPrepared'] is True
        assert json.loads(persisted['messages'][1]['content']) == packet
        seen_before_http.append(copy.deepcopy(persisted['messages']))

    gateway.callback = before_http
    service.process(job)
    assert store.job(queued['job']['id'])['status'] == 'failed'
    saved = _steps(store, queued['runId'])
    second_packet = json.loads(saved[1]['messages'][1]['content'])
    assert second_packet['chapterContext']['previousChapter']['written'] is True
    first = service.get(project['id'])['sections'][0]
    assert '原始资料要求保存固定版本' in second_packet['chapterContext']['previousChapter']['tail']
    assert '[E' not in dumps(second_packet['chapterContext'])
    assert saved[1]['blocks'] == initial[1]['blocks']
    assert second_packet['blocks'] == json.loads(initial[1]['messages'][1]['content'])['blocks']
    assert all(call['config']['maxOutputTokens'] == 5000 for call in gateway.calls)
    # Change the preceding saved prose after failure. It must not silently
    # alter the already-dispatched second request on restart/retry.
    current = service.get(project['id'])
    service.save_section(project['id'], first['id'], current['version'], {'content': '作者后来修改的前章正文，不得进入重试请求。'})
    gateway.callback = None
    restarted_store = Store(store.root)
    restarted = WritingService(restarted_store, gateway)
    restarted_store.retry(queued['job']['id'])
    restarted.process(restarted_store.claim())
    assert restarted_store.job(queued['job']['id'])['status'] == 'succeeded'
    after = _steps(store, queued['runId'])
    assert after[1]['messages'] == seen_before_http[1] == saved[1]['messages']
    assert gateway.calls[2]['payload'] == second_packet
    assert len(gateway.calls) == 4 and not gateway.matching_calls
    assert all(section['content'] for section in restarted.get(project['id'])['sections'])


def test_sequential_request_keeps_original_input_and_configured_output_capacity(document):
    store, gateway, service, _ = document
    gateway.config.update(maxOutputTokens=5000, contextChars=16000)
    project = ready(document, words=(800, 800, 800),
                    brief={'topic': '固定版本与人工修订', 'targetWords': 2400, 'interactiveGeneration': 'chapter'})
    queued, job = queue_and_claim(document, project)
    service.process(job)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 3
    assert all(call['payload']['blocks'] and call['payload']['sourceContext'] for call in gateway.calls)
    assert all(call['config']['maxOutputTokens'] == 5000 for call in gateway.calls)
    assert all(call['config']['contextChars'] == 16000 for call in gateway.calls)
    for step in _steps(store, queued['runId']):
        assert sum(len(message['content']) for message in step['messages']) <= 16000


def test_dense_original_packets_reserve_tail_and_opening_growth_without_dropping_frozen_blocks(document):
    store, gateway, service, _ = document
    texts = [f'记录{i:03d}：知识治理应保存固定版本和原始段落，读者能够逐段回查依据、数据口径与适用范围。'
             '修订应保留人工修改记录，不得悄然改变引用的来源或当前资料范围。' for i in range(80)]
    queued_source = store.queue('file', {}, '密集原文输入边界')
    source_job = store.claim()
    url = 'https://example.test/composition-dense-originals'
    store.publish(source_job, {'url': url}, ''.join(texts).encode(),
                  {'title': '密集原文输入边界', 'body': texts, 'language': 'zh', 'parser_version': 'composition-test-1',
                   'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': index} for index, text in enumerate(texts)]}, url)
    source_id = store.job(queued_source['id'])['source_id']
    # Reserve the expanded structure/voice contracts, while still filling the
    # remaining source allowance and exercising exact context-budget fencing.
    context_chars = 16000
    gateway.config.update(maxOutputTokens=5000, contextChars=context_chars)
    project = ready((store, gateway, service, source_id), words=(800, 800, 800),
                    brief={'topic': '固定版本与人工修订', 'targetWords': 2400, 'interactiveGeneration': 'chapter'})

    def long_saved_tail(result, _):
        result['paragraphs'].append({'text': ''.join(f'第{i}处继续交代本文的讨论次序。' for i in range(48)),
                                     'kind': 'supported', 'evidence': []})
        return result

    gateway.transform = long_saved_tail
    queued, job = queue_and_claim(document, project)
    before = _steps(store, queued['runId'])
    assert _frozen(store, queued['runId'])['plan']['strategy'] == 'sequential'
    for step in before[1:]:
        packet = json.loads(step['messages'][1]['content'])
        reserve = step['compositionSnapshot']['contextLimit'] - len(dumps(packet['chapterContext']))
        # Many short originals fill the remaining evidence allowance. This
        # exercises growth beyond the normal small-packet safety slack.
        assert packet['inputCoverage']['includedBlocks'] < packet['inputCoverage']['availableBlocks']
        assert packet['inputCoverage']['includedBlocks'] > 1
        assert 0 <= context_chars - (sum(len(message['content']) for message in step['messages']) + reserve) < 1200
    service.process(job)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    after = _steps(store, queued['runId'])
    for original, dispatched in zip(before[1:], after[1:]):
        old_packet = json.loads(original['messages'][1]['content'])
        new_packet = json.loads(dispatched['messages'][1]['content'])
        assert len(dumps(new_packet['chapterContext'])) - len(dumps(old_packet['chapterContext'])) > 200
        assert new_packet['blocks'] == old_packet['blocks']
        assert dispatched['blocks'] == original['blocks']
        assert dispatched['citationScope'] == original['citationScope']
        assert sum(len(message['content']) for message in dispatched['messages']) <= context_chars
    assert 'articleOpening' in json.loads(after[-1]['messages'][1]['content'])['chapterContext']
    assert len(gateway.calls) == 3 and not gateway.matching_calls
    assert all(call['config']['maxOutputTokens'] == 5000 for call in gateway.calls)


def test_delegated_document_uses_parent_whole_draft_prompt_version(document, monkeypatch):
    store, gateway, service, _ = document
    project = ready(document)
    _edit_prompt(store, 'writing_document_strict', '一次成文冻结甲。')
    parent = _queue_parent(document, project)
    original = _frozen(store, parent['runId'])['compositionPrompts']['writing_document_strict']
    orchestration = _frozen(store, parent['runId'])['compositionDocumentOrchestration']
    _edit_prompt(store, 'writing_document_strict', '一次成文后续乙。')
    monkeypatch.setattr(prompt_defaults, 'WRITING_DOCUMENT_ORCHESTRATION', '后续代码编排不得覆盖本父任务。')
    _parent_context(service, gateway, parent, store.claim())
    child = service.queue_document(project['id'], project['version'])
    for step in _steps(store, child['runId']):
        assert step['documentPrompt']['content'] == original['content']
        assert step['documentPrompt']['version'] == original['version']
        assert step['documentOrchestration'] == orchestration
        assert '一次成文后续乙。' not in step['messages'][0]['content']

