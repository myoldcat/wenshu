"""Whole-document acceptance with controlled providers and isolated stores.

These checks exercise persisted jobs and the HTTP entry points, not a live model.
Citation validation is structural: successful runs still require human review of
whether the cited material supports the generated claims.
"""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store, dumps
from server.worker import run_one
from server.writing import WritingConflict, WritingService
from server.tests.test_writing_integrity import controlled_matching_result, freeze_legacy_generation_output


LOCAL = {'X-Wenshu-Client': 'local'}
PRIVATE = 'document-test-private-provider-token'


class DocumentGateway:
    def __init__(self):
        self.config = {'id': 'document_model', 'version': 1, 'name': '全文验收模型',
                       'model': 'controlled-document', 'contextChars': 24000,
                       'maxOutputTokens': 8000, 'apiKey': PRIVATE}
        self.calls = []
        self.matching_calls = []
        self.generated_sections = {}
        self.callback = None
        self.transform = None
        self.fail_calls = set()

    def get_preferences(self):
        return {'summaryModelId': self.config['id']}

    def resolve(self, model_id=None, version=None):
        assert model_id in (None, self.config['id'])
        return {**self.config, 'version': version if version is not None else self.config['version']}

    @staticmethod
    def paragraphs(payload):
        if payload['mode'] == 'extended':
            return [{'text': '这是模型提出的进一步讨论，尚需补充原文材料核实。',
                     'kind': 'model_supplement', 'evidence': []}]
        block = payload['blocks'][0]
        return [{'text': '原始资料要求保存固定版本，以便逐段回查论述依据。',
                 'kind': 'supported',
                 'evidence': [{'blockId': block['blockId'], 'quote': block['text'],
                               'claim': '原始资料要求保存固定版本，以便逐段回查论述依据。'}]},
                {'text': '修订应保留人工记录，并明确当前资料范围。',
                 'kind': 'supported',
                 'evidence': [{'blockId': block['blockId'], 'quote': block['text'],
                               'claim': '修订应保留人工记录，并明确当前资料范围。'}]}]

    def complete(self, config, messages):
        assert sum(len(message['content']) for message in messages) <= config['contextChars']
        payload = json.loads(messages[1]['content'])
        if payload.get('task') == 'writing_citation_match':
            self.matching_calls.append({'config': copy.deepcopy(config), 'payload': copy.deepcopy(payload)})
            return controlled_matching_result(payload, self.generated_sections.get(payload['sectionId'], []))
        self.calls.append({'config': copy.deepcopy(config), 'payload': copy.deepcopy(payload)})
        if self.callback:
            self.callback(len(self.calls), payload)
        if len(self.calls) in self.fail_calls:
            raise RuntimeError(PRIVATE + ': confidential model response')
        if payload['task'] == 'writing_document':
            result = {'sections': [{'sectionId': item['id'], 'paragraphs': self.paragraphs(item)}
                                   for item in payload['sections']], 'issues': []}
        else:
            assert payload['task'] == 'writing_section'
            result = {'paragraphs': self.paragraphs(payload), 'issues': []}
        if self.transform:
            result = self.transform(result, payload)
        if payload['task'] == 'writing_document':
            self.generated_sections.update({item['sectionId']: copy.deepcopy(item['paragraphs']) for item in result.get('sections', []) if 'sectionId' in item and 'paragraphs' in item})
        else:
            self.generated_sections[payload['section']['id']] = copy.deepcopy(result.get('paragraphs', []))
        return {'text': json.dumps(result, ensure_ascii=False),
                'usage': {'total_tokens': 37, 'completion_tokens': 24, 'provider_secret': PRIVATE}}


def add_document_source(store, text=None):
    text = text or ('知识治理应保存固定版本和原始段落，读者能够逐段回查依据、数据口径与适用范围。'
                    '修订应保留人工修改记录，不得悄然改变引用的来源或当前资料范围。')
    queued = store.queue('file', {}, '全文流程验收资料')
    claimed = store.claim()
    assert claimed['id'] == queued['id']
    url = 'https://example.test/writing-document'
    parsed = {'title': '全文流程验收资料', 'body': [text], 'language': 'zh',
              'parser_version': 'writing-document-test-1',
              'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': 0}]}
    store.publish(claimed, {'url': url}, ('<p>' + text + '</p>').encode(), parsed, url)
    return store.job(queued['id'])['source_id']


@pytest.fixture
def document(tmp_path):
    store = Store(tmp_path / 'isolated-writing-document')
    gateway = DocumentGateway()
    service = WritingService(store, gateway)
    source = add_document_source(store)
    return store, gateway, service, source


def ready(document, *, words=(200, 200, 200), confirmed=True, modes=None, brief=None):
    _, gateway, service, source = document
    project = service.create({'title': '完整写作验收', 'brief': brief or {'topic': '固定版本与人工修订', 'targetWords': sum(words)},
                              'mode': 'strict', 'sourceIds': [source], 'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': f'第 {index + 1} 章', 'goal': '解释原始资料的保存与回查',
         'mode': modes[index] if modes else 'inherit', 'targetWords': target}
        for index, target in enumerate(words)])
    return service.confirm(project['id'], project['version']) if confirmed else project


def queue_and_claim(document, project, **options):
    store, _, service, _ = document
    run = service.queue_document(project['id'], project['version'], **options)
    job = store.claim()
    assert job['id'] == run['job']['id']
    return run, job


def finished(document, project):
    store, _, service, _ = document
    run, job = queue_and_claim(document, project)
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded', store.job(run['job']['id'])
    return service.get(project['id']), run


def composition_context_reserve(item):
    packet = json.loads(item['messages'][1]['content'])
    context = packet.get('chapterContext')
    composition = item.get('compositionSnapshot')
    if not composition or not context or context['position']['ordinal'] == 1:
        return 0
    return max(0, composition['contextLimit'] - len(dumps(context)))


def test_small_document_uses_one_call_and_pins_evidence_for_every_paragraph(document):
    store, gateway, service, source = document
    project = ready(document)
    original = store.detail(source)
    project, run = finished(document, project)
    assert len(gateway.calls) == 1
    assert gateway.calls[0]['payload']['task'] == 'writing_document'
    assert len(project['sections']) == 3 and all(section['content'] for section in project['sections'])
    for section in project['sections']:
        assert not section['manual']
        assert len(section['content'].split('\n\n')) == 2
        assert all('[E1]' in paragraph for paragraph in section['content'].split('\n\n'))
        ref = section['evidence'][0]
        assert ref['revisionId'] == original['revisionId'] and ref['sourceId'] == source
        block = next(block for block in original['blocks'] if block['id'] == ref['blockId'])
        assert block['text'][ref['start']:ref['end']] == ref['quote']
    reviewed = service.review(project['id'], project['version'])['review']
    assert reviewed['semanticVerified'] is False
    assert reviewed['counts']['errors'] == 0
    assert project['runs'][0]['id'] == run['runId']
    assert project['runs'][0]['applied']


def test_large_document_uses_sequential_calls_and_persists_each_finished_chapter(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    project = ready(document, words=(3334, 3333, 3333))
    observed_completed = []

    def observe(index, _):
        current = service.get(project['id'])
        observed_completed.append(sum(bool(section['content']) for section in current['sections']))

    gateway.callback = observe
    project, _ = finished(document, project)
    assert len(gateway.calls) == 3
    assert all(call['payload']['task'] == 'writing_section' for call in gateway.calls)
    assert observed_completed == [0, 1, 2]
    assert all(section['content'] for section in project['sections'])
    assert store.jobs()['counts']['failed'] == 0


def test_plan_is_read_only_and_total_word_budget_can_force_sequential_generation(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    project = ready(document, words=(200, 200, 200), brief={'targetWords': 10000})
    before = store.jobs()['counts']
    plan = service.generation_plan(project['id'])
    assert plan
    assert gateway.calls == [] and store.jobs()['counts'] == before
    current = service.get(project['id'])
    assert current['version'] == project['version'] and current['sections'] == project['sections']
    finished(document, project)
    assert len(gateway.calls) == 3
    assert all(call['payload']['task'] == 'writing_section' for call in gateway.calls)
    assert sum(call['payload']['section']['targetWords'] for call in gateway.calls) >= 10000


def test_too_large_chapter_is_blocked_before_any_provider_call_or_job(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 5000
    project = ready(document, words=(1500, 1500))
    before = store.jobs()['counts']
    with pytest.raises(ValueError):
        service.queue_document(project['id'], project['version'])
    assert gateway.calls == [] and store.jobs()['counts'] == before


def test_input_context_budget_can_split_a_document_at_the_chapter_threshold(document):
    store, gateway, service, _ = document
    add_document_source(store, '原始资料段落保存固定版本并保留人工修订记录。' + '证据范围和条件必须回查。' * 300)
    gateway.config['maxOutputTokens'] = 65536
    project = ready(document, words=(3334, 3333, 3333))
    # Include both queue-time instruction sets and the indivisible original
    # block. The exact fixed contracts may grow without changing this test's
    # boundary: each chapter fits, while their whole-draft request does not.
    gateway.config['contextChars'] = 200000
    with store.connect() as db:
        _, prepared = service._document_plan(db, project, gateway.config)
    gateway.config['contextChars'] = max(sum(len(message['content']) for message in item['messages']) + composition_context_reserve(item)
                                         for item in prepared) + 1
    with store.connect() as db:
        plan, prepared = service._document_plan(db, project, gateway.config)
    assert plan['canGenerate'] and plan['strategy'] == 'sequential'
    assert plan['estimatedInputChars'] > gateway.config['contextChars']
    assert all(sum(len(message['content']) for message in item['messages']) < gateway.config['contextChars']
               for item in prepared)
    finished(document, project)
    assert len(gateway.calls) == 3
    assert all(call['payload']['task'] == 'writing_section' for call in gateway.calls)


def test_confirmation_required_and_confirm_and_generate_is_one_explicit_action(document):
    _, gateway, service, _ = document
    project = ready(document, confirmed=False)
    with pytest.raises(ValueError, match='纲要'):
        service.queue_document(project['id'], project['version'])
    assert not gateway.calls
    run, job = queue_and_claim(document, project, confirm_outline=True)
    assert run['project']['outlineConfirmed']
    service.process(job)
    assert all(section['content'] for section in service.get(project['id'])['sections'])


def test_existing_manual_and_locked_chapters_are_skipped(document):
    _, gateway, service, _ = document
    project = ready(document, words=(200, 200, 200, 200))
    first, second, third, fourth = [section['id'] for section in project['sections']]
    project = service.save_section(project['id'], first, project['version'], {'content': '人工正文必须原样保留。'})
    project = service.save_section(project['id'], second, project['version'], {'content': '作者实际写入后决定清空这章。'})
    project = service.save_section(project['id'], second, project['version'], {'content': ''})
    project = service.save_section(project['id'], third, project['version'], {'locked': True})
    before = copy.deepcopy(project['sections'][:3])
    for section in before:
        section['chapterConfirmed'] = False
    current, _ = finished(document, project)
    assert current['sections'][:3] == before
    assert current['sections'][3]['content']
    request = gateway.calls[0]['payload']
    assert [item['id'] for item in request['sections']] == [fourth]


def test_existing_generated_chapter_is_also_preserved_on_complete_remaining_action(document):
    store, gateway, service, _ = document
    project = ready(document)
    first_id = project['sections'][0]['id']
    service.queue(project['id'], project['version'], 'section', first_id)
    service.process(store.claim())
    project = service.get(project['id'])
    preserved = copy.deepcopy(project['sections'][0])
    preserved['chapterConfirmed'] = False
    assert preserved['content'] and not preserved['manual']
    current, _ = finished(document, project)
    assert current['sections'][0] == preserved
    assert all(section['content'] for section in current['sections'])
    assert all(item['id'] != first_id for item in gateway.calls[-1]['payload']['sections'])


def test_document_queue_is_deduplicated_and_conflicts_with_other_active_work(document):
    store, gateway, service, _ = document
    project = ready(document)
    first = service.queue_document(project['id'], project['version'])
    again = service.queue_document(project['id'], project['version'])
    assert first['runId'] == again['runId'] and first['job']['id'] == again['job']['id']
    with pytest.raises(WritingConflict):
        service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    assert not gateway.calls
    assert len([job for job in store.jobs()['jobs'] if job['kind'].startswith('writing_')]) == 1


@pytest.mark.parametrize('change', ['manual', 'lock', 'lock_unlock'])
def test_inflight_change_protects_that_chapter_and_cannot_be_bypassed_by_candidate_apply(document, change):
    store, gateway, service, _ = document
    project = ready(document)
    section_id = project['sections'][0]['id']
    manual_text = '生成期间新写的人工结论：这些文字不得被任何迟到候选覆盖。'

    def change_during_call(_, __):
        current = service.get(project['id'])
        data = {'content': manual_text} if change == 'manual' else {'locked': True}
        current = service.save_section(project['id'], section_id, current['version'], data)
        if change == 'lock_unlock':
            service.save_section(project['id'], section_id, current['version'], {'locked': False})

    gateway.callback = change_during_call
    current, run = finished(document, project)
    assert current['sections'][0]['content'] == (manual_text if change == 'manual' else '')
    assert all(section['content'] for section in current['sections'][1:])
    assert len(gateway.calls) == 1
    assert current['runs'][0]['candidate'] and not current['runs'][0]['applied']
    before = copy.deepcopy(current['sections'])
    with pytest.raises(WritingConflict):
        service.apply_document_section(project['id'], run['runId'], section_id, current['version'])
    with pytest.raises((WritingConflict, ValueError)):
        service.apply(project['id'], run['runId'], current['version'], replace_manual=True)
    assert service.get(project['id'])['sections'] == before
    assert store.job(run['job']['id'])['status'] == 'succeeded'


def test_future_chapter_changed_during_sequential_call_is_skipped_without_spending_a_model_call(document):
    _, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    project = ready(document, words=(3334, 3333, 3333))
    future = project['sections'][1]['id']

    def protect_future(index, _):
        if index == 1:
            current = service.get(project['id'])
            service.save_section(project['id'], future, current['version'], {'content': '人工提前写好的第二章。'})

    gateway.callback = protect_future
    current, _ = finished(document, project)
    assert len(gateway.calls) == 2
    assert current['sections'][1]['content'] == '人工提前写好的第二章。'
    assert current['sections'][0]['content'] and current['sections'][2]['content']
    assert [call['payload']['section']['id'] for call in gateway.calls] == [
        project['sections'][0]['id'], project['sections'][2]['id']]


def test_cancel_during_second_call_stops_publication_and_next_calls_and_retry_keeps_first_chapter(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    project = ready(document, words=(3334, 3333, 3333))
    run, job = queue_and_claim(document, project)
    gateway.callback = lambda index, _: store.cancel(run['job']['id']) if index == 2 else None
    service.process(job)
    current = service.get(project['id'])
    assert store.job(run['job']['id'])['status'] == 'cancelled'
    assert len(gateway.calls) == 2
    assert current['sections'][0]['content']
    assert not current['sections'][1]['content'] and not current['sections'][2]['content']
    preserved = copy.deepcopy(current['sections'][0])
    gateway.callback = None
    restarted_store = Store(store.root)
    restarted = WritingService(restarted_store, gateway)
    restarted_store.retry(run['job']['id'])
    restarted.process(restarted_store.claim())
    current = restarted.get(project['id'])
    assert restarted_store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 4
    assert current['sections'][0] == preserved
    assert all(section['content'] for section in current['sections'])


def test_provider_failure_after_first_chapter_preserves_progress_and_resumes_remaining_only(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    gateway.fail_calls = {2}
    project = ready(document, words=(3334, 3333, 3333))
    run, job = queue_and_claim(document, project)
    service.process(job)
    current = service.get(project['id'])
    assert store.job(run['job']['id'])['status'] == 'failed'
    assert len(gateway.calls) == 2
    assert current['sections'][0]['content'] and not current['sections'][1]['content']
    first = copy.deepcopy(current['sections'][0])
    assert PRIVATE not in json.dumps(current, ensure_ascii=False)
    assert PRIVATE not in json.dumps(store.jobs(), ensure_ascii=False)
    restarted_store = Store(store.root)
    restarted = WritingService(restarted_store, gateway)
    restarted_store.retry(run['job']['id'])
    restarted.process(restarted_store.claim())
    current = restarted.get(project['id'])
    assert len(gateway.calls) == 4
    assert current['sections'][0] == first
    assert all(section['content'] for section in current['sections'])


def test_validated_candidate_is_reused_after_publication_failure_without_regeneration(document, monkeypatch):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    project = ready(document, words=(3334, 3333, 3333))
    run, job = queue_and_claim(document, project)
    original_save = service._save

    def interrupted_save(db, latest, reason):
        if sum(bool(section['content']) for section in latest['sections']) == 2:
            raise RuntimeError('simulated interruption after a validated candidate')
        return original_save(db, latest, reason)

    monkeypatch.setattr(service, '_save', interrupted_save)
    service.process(job)
    current = service.get(project['id'])
    assert store.job(run['job']['id'])['status'] == 'failed'
    assert len(gateway.calls) == 2
    assert current['sections'][0]['content'] and not current['sections'][1]['content']
    assert current['runs'][0]['candidate']
    monkeypatch.setattr(service, '_save', original_save)
    store.retry(run['job']['id'])
    service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 3
    assert all(section['content'] for section in service.get(project['id'])['sections'])


@pytest.mark.parametrize('old_call_fails', [False, True])
def test_cancelled_old_attempt_cannot_publish_or_fail_reclaimed_document_attempt(document, old_call_fails):
    store, gateway, service, _ = document
    project = ready(document)
    run, old_claim = queue_and_claim(document, project)
    reclaimed = []

    def cancel_and_reclaim(index, _):
        if index == 1:
            store.cancel(run['job']['id'])
            store.retry(run['job']['id'])
            reclaimed.append(store.claim())

    gateway.callback = cancel_and_reclaim
    gateway.fail_calls = {1} if old_call_fails else set()
    service.process(old_claim)
    state = store.job(run['job']['id'])
    assert state['status'] == 'running' and state['attempts'] == 2
    assert not any(section['content'] for section in service.get(project['id'])['sections'])
    service.process(reclaimed[0])
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 2
    assert all(section['content'] for section in service.get(project['id'])['sections'])


def test_frozen_source_and_model_versions_survive_library_update_and_service_restart(document):
    store, gateway, service, source = document
    project = ready(document)
    original = store.detail(source)
    run, job = queue_and_claim(document, project)
    gateway.config['version'] = 2
    updated = add_document_source(store, '这是第二版材料，修改了叙述范围；既有生成任务必须继续依据第一版原始段落，不能混用当前版本。')
    assert updated == source and store.detail(source)['revisionId'] != original['revisionId']
    service.process(job)
    restarted = WritingService(Store(store.root), gateway)
    current = restarted.get(project['id'])
    assert gateway.calls[0]['config']['version'] == 1
    assert all(ref['revisionId'] == original['revisionId'] for section in current['sections'] for ref in section['evidence'])
    assert current['runs'][0]['id'] == run['runId'] and current['runs'][0]['applied']
    with store.connect() as db:
        for table in ('writing_runs', 'writing_calls', 'writing_document_steps', 'writing_document_calls'):
            assert PRIVATE not in str([tuple(row) for row in db.execute('SELECT * FROM ' + table)])


def test_requirement_change_during_call_stops_automatic_writing_and_stale_adoption(document):
    _, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    project = ready(document, words=(3334, 3333, 3333))
    run, job = queue_and_claim(document, project)

    def change_brief(index, _):
        if index == 1:
            current = service.get(project['id'])
            service.patch(project['id'], current['version'], {'brief': {'topic': '人工新确定的任务范围'}})

    gateway.callback = change_brief
    service.process(job)
    current = service.get(project['id'])
    assert len(gateway.calls) == 1
    assert not any(section['content'] for section in current['sections'])
    assert not current['outlineConfirmed']
    with pytest.raises((WritingConflict, ValueError)):
        service.apply_document_section(project['id'], run['runId'], project['sections'][0]['id'], current['version'])


@pytest.mark.parametrize('invalid', ['uncited', 'foreign_block', 'false_quote', 'minted_citation', 'multiline_paragraph',
                                    'supplement', 'missing_section', 'duplicate_section', 'foreign_section'])
def test_legacy_single_request_rejects_invalid_coverage_or_citation_without_publishing_any_chapter(document, invalid):
    store, gateway, service, _ = document
    project = ready(document)

    def corrupt(result, _):
        paragraph = result['sections'][0]['paragraphs'][0]
        if invalid == 'uncited':
            paragraph['evidence'] = []
        elif invalid == 'foreign_block':
            paragraph['evidence'][0]['blockId'] = 'block_from_another_document'
        elif invalid == 'false_quote':
            paragraph['evidence'][0]['quote'] = '原文中不存在的精确摘录'
        elif invalid == 'minted_citation':
            paragraph['text'] += ' [E999]'
        elif invalid == 'multiline_paragraph':
            paragraph['text'] += '\n\n这是另一个实质段落，必须独立附上材料。'
        elif invalid == 'supplement':
            paragraph['kind'] = 'model_supplement'
        elif invalid == 'missing_section':
            result['sections'].pop()
        elif invalid == 'duplicate_section':
            result['sections'][1]['sectionId'] = result['sections'][0]['sectionId']
        elif invalid == 'foreign_section':
            result['sections'][0]['sectionId'] = 'section_not_in_outline'
        return result

    gateway.transform = corrupt
    run, job = queue_and_claim(document, project)
    freeze_legacy_generation_output(store, run['runId'], whole_document=True)
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'failed'
    assert len(gateway.calls) == 1
    assert not any(section['content'] for section in service.get(project['id'])['sections'])


def test_each_chapter_effective_mode_is_respected_and_supplement_is_plain(document):
    _, gateway, service, _ = document
    project = ready(document, words=(200, 200), modes=('strict', 'extended'))
    current, _ = finished(document, project)
    assert current['sections'][0]['effectiveMode'] == 'strict'
    assert '[E1]' in current['sections'][0]['content']
    assert current['sections'][1]['effectiveMode'] == 'extended'
    assert current['sections'][1]['content'] and '【模型补充·待核实】' not in current['sections'][1]['content']
    assert current['sections'][1]['extendedContentPolicy'] == 'retain-as-prose-v2'
    assert current['sections'][1]['evidence'] == []
    assert [section['mode'] for section in gateway.calls[0]['payload']['sections']] == ['strict', 'extended']


def test_worker_restart_reuses_durable_validated_result_and_completed_chapter(document, monkeypatch):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 20000
    project = ready(document, words=(3334, 3333, 3333))
    run, job = queue_and_claim(document, project)
    original_apply = service._document_apply_step

    def simulate_worker_exit(db, current_run, frozen, row):
        if row['section_id'] == project['sections'][1]['id']:
            raise KeyboardInterrupt('simulated process shutdown after persisted provider result')
        return original_apply(db, current_run, frozen, row)

    monkeypatch.setattr(service, '_document_apply_step', simulate_worker_exit)
    with pytest.raises(KeyboardInterrupt):
        service.process(job)
    assert store.job(run['job']['id'])['status'] == 'running'
    assert len(gateway.calls) == 2
    before = service.get(project['id'])['sections'][0]
    assert before['content']
    restarted_store = Store(store.root)
    restarted_store.recover()
    restarted = WritingService(restarted_store, gateway)
    restarted.process(restarted_store.claim())
    assert restarted_store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 3
    current = restarted.get(project['id'])
    assert current['sections'][0] == before and all(section['content'] for section in current['sections'])


def test_retry_does_not_create_two_active_writing_jobs_for_same_project(document):
    store, gateway, service, _ = document
    project = ready(document)
    old = service.queue_document(project['id'], project['version'])
    store.cancel(old['job']['id'])
    new = service.queue_document(project['id'], project['version'])
    assert new['runId'] != old['runId']
    with pytest.raises(ValueError):
        store.retry(old['job']['id'])
    assert not gateway.calls
    assert store.job(old['job']['id'])['status'] == 'cancelled'
    assert store.job(new['job']['id'])['status'] == 'queued'


def test_http_safe_candidate_can_be_adopted_after_brief_restored_and_outline_reconfirmed(document):
    store, gateway, service, _ = document
    project = ready(document)

    def invalidate_confirmation(_, __):
        current = service.get(project['id'])
        original_brief = copy.deepcopy(current['brief'])
        current = service.patch(project['id'], current['version'],
                                {'brief': {**original_brief, 'purpose': '作者临时调整写作要求，需要重新确认纲要。'}})
        # The original request is safe again only after explicit confirmation.
        # Section versions stay unchanged, so the preserved candidate can then
        # be adopted without replacing any newer chapter edit.
        service.patch(project['id'], current['version'], {'brief': original_brief})

    gateway.callback = invalidate_confirmation
    current, run = finished(document, project)
    assert not any(section['content'] for section in current['sections'])
    current = service.confirm(project['id'], current['version'])
    app = create_app(data_dir=store.root, model_service=gateway)
    with TestClient(app) as client:
        path = '/api/writing/projects/' + project['id'] + '/runs/' + run['runId'] + '/sections/'
        first_id = project['sections'][0]['id']
        response = client.post(path + first_id + '/apply', headers=LOCAL,
                               json={'expectedVersion': current['version']})
        assert response.status_code == 200, response.text
        current = response.json()['project']
        assert current['sections'][0]['content'] and not current['sections'][1]['content']
        again = client.post(path + first_id + '/apply', headers=LOCAL,
                            json={'expectedVersion': current['version']})
        assert again.status_code in (409, 422)
        foreign = client.post(path + 'section_unknown/apply', headers=LOCAL,
                              json={'expectedVersion': current['version']})
        assert foreign.status_code in (404, 422)
    assert len(gateway.calls) == 1


def test_http_confirmation_plan_worker_source_lookup_and_read_only_boundary(document):
    store, gateway, service, _ = document
    project = ready(document, confirmed=False)
    path = '/api/writing/projects/' + project['id']
    app = create_app(data_dir=store.root, model_service=gateway)
    with TestClient(app) as client:
        before = service.get(project['id'])
        response = client.get(path + '/generation-plan')
        assert response.status_code == 200 and response.json()['plan']
        assert not gateway.calls and service.get(project['id'])['version'] == before['version']
        assert client.post(path + '/generate-document', json={'expectedVersion': project['version'], 'confirmOutline': True}).status_code == 403
        assert client.post(path + '/generate-document', headers=LOCAL, json={'expectedVersion': project['version']}).status_code == 422
        assert client.post(path + '/generate-document', headers=LOCAL, json={'expectedVersion': True, 'confirmOutline': True}).status_code == 422
        response = client.post(path + '/generate-document', headers=LOCAL,
                               json={'expectedVersion': project['version'], 'confirmOutline': True})
        assert response.status_code == 202, response.text
        assert run_one(app.state.store, app.state.enrichment, app.state.wiki, app.state.writing)
        current = client.get(path).json()['project']
        assert current['outlineConfirmed'] and all(section['content'] for section in current['sections'])
        assert PRIVATE not in json.dumps(current, ensure_ascii=False)
        for section in current['sections']:
            for ref in section['evidence']:
                original = client.get('/api/documents/' + ref['sourceId'], params={'revision_id': ref['revisionId']})
                assert original.status_code == 200
                block = next(item for item in original.json()['document']['blocks'] if item['id'] == ref['blockId'])
                assert block['text'][ref['start']:ref['end']] == ref['quote']
        stale = client.post(path + '/generate-document', headers=LOCAL,
                            json={'expectedVersion': project['version'], 'confirmOutline': True})
        assert stale.status_code == 409
