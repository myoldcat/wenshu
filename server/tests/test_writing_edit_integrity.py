"""Adversarial acceptance for bounded edits; run with a fake provider."""
import copy
import json

import pytest

from server.store import Store
from server.writing import WritingConflict, WritingService
from server.tests.test_writing_integrity import controlled_matching_result


class EditGateway:
    def __init__(self):
        self.config = {'id': 'edit_integrity_model', 'version': 1, 'name': '选区编辑测试',
                       'model': 'controlled', 'contextChars': 24000,
                       'maxOutputTokens': 4096, 'apiKey': 'edit-private-token'}
        self.calls = []
        self.matching_calls = []
        self.generated_paragraphs = []
        self.callback = None
        self.fail = False
        self.response = {'replacementText': '改后的文字', 'changeSummary': '调整表达',
                         'issues': [], 'supplement': False}

    def get_preferences(self):
        return {'summaryModelId': self.config['id']}

    def resolve(self, model_id=None, version=None):
        assert model_id in (None, self.config['id'])
        return {**self.config, 'version': version or self.config['version']}

    def complete(self, config, messages):
        request = json.loads(messages[1]['content'])
        if request.get('task') == 'writing_citation_match':
            self.matching_calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
            return controlled_matching_result(request, self.generated_paragraphs)
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.fail:
            raise RuntimeError('edit-private-token provider error')
        if request['task'] == 'writing_section':
            block = request['blocks'][0]
            result = {'paragraphs': [{'text': '证据保留原始版本，便于读者核对来源。', 'kind': 'supported',
                                     'evidence': [{'blockId': block['blockId'], 'quote': block['text'],
                                                   'claim': '证据保留原始版本，便于读者核对来源。'}]}], 'issues': []}
            self.generated_paragraphs = result['paragraphs']
        else:
            assert request['task'] == 'writing_edit'
            result = self.response
        return {'text': json.dumps(result, ensure_ascii=False), 'usage': {'total_tokens': 31}}


def publish_source(store):
    text = '证据应保留原始版本，读者可以据此核对来源、时间与适用范围；修改文章时仍需检查既有引文能否支持新的表达。'
    queued = store.queue('file', {}, '选区编辑资料')
    job = store.claim()
    assert queued['id'] == job['id']
    parsed = {'title': '选区编辑资料', 'body': [text], 'language': 'zh', 'parser_version': 'edit-integrity-1',
              'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': 0}]}
    store.publish(job, {'url': 'https://example.test/edit-integrity'}, ('<p>' + text + '</p>').encode(), parsed)
    return store.job(job['id'])['source_id']


@pytest.fixture
def edit_environment(tmp_path):
    store = Store(tmp_path / 'isolated-writing-edit')
    gateway = EditGateway()
    service = WritingService(store, gateway)
    source = publish_source(store)
    project = service.create({'title': '选区事务', 'brief': {}, 'mode': 'strict',
                              'sourceIds': [source], 'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': '原文与修订', 'goal': '保持依据和边界', 'mode': 'inherit', 'targetWords': 600}])
    project = service.confirm(project['id'], project['version'])
    return store, gateway, service, project['id'], project['sections'][0]['id']


def write(environment, text):
    _, _, service, project_id, section_id = environment
    project = service.get(project_id)
    return service.save_section(project_id, section_id, project['version'], {'content': text})


def utf16_length(text):
    return len(text.encode('utf-16-le')) // 2


def request_for(project, start, end, operation='polish'):
    section = project['sections'][0]
    text = section['content']
    return {'expectedSectionVersion': section['version'], 'baseContentHash': section['contentHash'],
            'operation': operation,
            'selection': {'start': utf16_length(text[:start]), 'end': utf16_length(text[:end]),
                          'selectedExact': text[start:end], 'unit': 'utf16'}}


def queued_edit(environment, project, start, end, operation='polish'):
    store, _, service, project_id, section_id = environment
    result = service.queue_edit(project_id, project['version'], section_id, request_for(project, start, end, operation))
    job = store.claim()
    assert job['id'] == result['job']['id']
    return result, job


def test_second_repeated_selection_after_emoji_edits_only_the_requested_utf16_span(edit_environment):
    store, gateway, service, project_id, _ = edit_environment
    original = '  起点😀 同一句话。\n\n🧑‍💻 中间 同一句话。\n\n结束保留空格  \n'
    project = write(edit_environment, original)
    assert project['sections'][0]['content'] == original
    start = original.rindex('同一句话')
    end = start + len('同一句话')
    gateway.response['replacementText'] = ' 修订后的表达 '
    result, job = queued_edit(edit_environment, project, start, end)
    service.process(job)
    current = service.get(project_id)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert current['sections'][0]['content'] == original  # A completed edit is always a candidate.
    assert not current['runs'][0]['applied']
    candidate = current['runs'][0]['candidate']['edit']
    assert candidate['selectedExact'] == '同一句话' and candidate['semanticVerified'] is False
    assert candidate['replacementText'] == ' 修订后的表达 '
    accepted = service.apply(project_id, result['runId'], current['version'])
    assert accepted['sections'][0]['content'] == original[:start] + ' 修订后的表达 ' + original[end:]
    assert accepted['sections'][0]['content'].count('同一句话') == 1
    assert accepted['review'] is None


@pytest.mark.parametrize('damage', ['surrogate_start', 'surrogate_end', 'wrong_exact', 'wrong_hash', 'wrong_section_version', 'negative', 'past_end'])
def test_invalid_selection_contract_is_rejected_before_model_work(edit_environment, damage):
    store, gateway, service, project_id, section_id = edit_environment
    project = write(edit_environment, '甲😀乙：明确选择的内容。')
    request = request_for(project, 1, 2)
    if damage == 'surrogate_start':
        request['selection']['start'] = 2  # Inside the two UTF-16 code units of the emoji.
    elif damage == 'surrogate_end':
        request['selection']['end'] = 2
    elif damage == 'wrong_exact':
        request['selection']['selectedExact'] = '另一个片段'
    elif damage == 'wrong_hash':
        request['baseContentHash'] = '0' * 64
    elif damage == 'wrong_section_version':
        request['expectedSectionVersion'] += 1
    elif damage == 'negative':
        request['selection']['start'] = -1
    else:
        request['selection']['end'] = 10000
    before = store.jobs()['counts']['total']
    with pytest.raises(ValueError):
        service.queue_edit(project_id, project['version'], section_id, request)
    assert store.jobs()['counts']['total'] == before
    assert gateway.calls == []


@pytest.mark.parametrize('text,selected', [
    ('引用必须保持完整 [E1]。', 'E1'),
    ('【模型补充·待核实】这一段需要核验。', '模型补充'),
])
def test_selection_cannot_slice_through_a_citation_or_supplement_marker(edit_environment, text, selected):
    store, gateway, service, project_id, section_id = edit_environment
    project = write(edit_environment, text)
    start = text.index(selected)
    before = store.jobs()['counts']['total']
    with pytest.raises(ValueError):
        service.queue_edit(project_id, project['version'], section_id, request_for(project, start, start + len(selected)))
    assert store.jobs()['counts']['total'] == before and gateway.calls == []


def cited_section(environment):
    store, _, service, project_id, section_id = environment
    project = service.get(project_id)
    result = service.queue(project_id, project['version'], 'section', section_id)
    service.process(store.claim())
    assert store.job(result['job']['id'])['status'] == 'succeeded'
    project = service.get(project_id)
    assert project['sections'][0]['evidence']
    return project


@pytest.mark.parametrize('replacement', ['删除了引用。', '改变引用编号 [E99]', '重复借用同一引文 [E1][E1]'])
def test_model_cannot_remove_invent_or_duplicate_selected_citations(edit_environment, replacement):
    store, gateway, service, project_id, _ = edit_environment
    project = cited_section(edit_environment)
    original = project['sections'][0]['content']
    gateway.response['replacementText'] = replacement
    _, job = queued_edit(edit_environment, project, 0, len(original))
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert service.get(project_id)['sections'][0]['content'] == original


def test_preserved_citation_keeps_its_original_mapping_and_version(edit_environment):
    store, gateway, service, project_id, _ = edit_environment
    project = cited_section(edit_environment)
    original = project['sections'][0]['content']
    evidence = copy.deepcopy(project['sections'][0]['evidence'])
    gateway.response['replacementText'] = '读者可借原始版本核对来源和条件。 [E1]'
    result, job = queued_edit(edit_environment, project, 0, len(original))
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    before_apply = service.get(project_id)
    accepted = service.apply(project_id, result['runId'], before_apply['version'])
    assert accepted['sections'][0]['evidence'] == evidence
    assert accepted['sections'][0]['content'] == gateway.response['replacementText']


@pytest.mark.parametrize('change', ['outside_text', 'lock', 'lock_then_unlock', 'brief'])
def test_edit_candidate_cannot_be_applied_to_a_later_state_even_with_current_expected_version(edit_environment, change):
    _, gateway, service, project_id, section_id = edit_environment
    original = '前段保持原样。选中内容。后段保持原样。'
    project = write(edit_environment, original)
    start = original.index('选中内容')
    result, job = queued_edit(edit_environment, project, start, start + len('选中内容'))
    service.process(job)
    current = service.get(project_id)
    if change == 'outside_text':
        current = service.save_section(project_id, section_id, current['version'], {'content': original + '人工补充在选区之外。'})
    elif change in ('lock', 'lock_then_unlock'):
        current = service.save_section(project_id, section_id, current['version'], {'locked': True})
        if change == 'lock_then_unlock':
            current = service.save_section(project_id, section_id, current['version'], {'locked': False})
    else:
        current = service.patch(project_id, current['version'], {'brief': {'purpose': '已改变的写作要求'}})
    preserved = copy.deepcopy(current['sections'])
    with pytest.raises(WritingConflict):
        service.apply(project_id, result['runId'], current['version'], replace_manual=True)
    assert service.get(project_id)['sections'] == preserved
    assert service.get(project_id)['runs'][0]['candidate']['edit']['selectedExact'] == '选中内容'
    assert len(gateway.calls) == 1


def test_manual_edit_while_model_is_running_survives_with_a_stale_candidate(edit_environment):
    store, gateway, service, project_id, section_id = edit_environment
    original = '只修改这一处，其他内容保持原样。'
    project = write(edit_environment, original)
    result, job = queued_edit(edit_environment, project, 0, 6)
    gateway.callback = lambda: service.save_section(project_id, section_id, project['version'], {'content': '人工已经改完，请保留这一稿。'})
    service.process(job)
    current = service.get(project_id)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert current['sections'][0]['content'] == '人工已经改完，请保留这一稿。'
    assert current['runs'][0]['candidate'] and not current['runs'][0]['applied']
    with pytest.raises(WritingConflict):
        service.apply(project_id, result['runId'], current['version'])


def test_candidate_and_selection_are_durable_and_cannot_apply_to_another_project(edit_environment):
    store, gateway, service, project_id, _ = edit_environment
    original = '旧表达需要改善。'
    project = write(edit_environment, original)
    result, job = queued_edit(edit_environment, project, 0, len(original))
    service.process(job)
    before = service.get(project_id)
    restarted = WritingService(Store(store.root), gateway)
    after = restarted.get(project_id)
    assert after['runs'][0]['candidate'] == before['runs'][0]['candidate']
    assert after['runs'][0]['editTarget'] == before['runs'][0]['editTarget']
    other = restarted.create({'title': '另一篇文稿', 'brief': {}, 'mode': 'strict', 'sourceIds': []})
    with pytest.raises(KeyError):
        restarted.apply(other['id'], result['runId'], other['version'])
    accepted = restarted.apply(project_id, result['runId'], after['version'])
    assert accepted['sections'][0]['content'] == gateway.response['replacementText']
    assert gateway.calls and len(gateway.calls) == 1


@pytest.mark.parametrize('old_call_fails', [False, True])
def test_cancelled_edit_attempt_cannot_publish_or_fail_a_retried_attempt(edit_environment, old_call_fails):
    store, gateway, service, project_id, _ = edit_environment
    original = '取消期间原稿需要保持。'
    project = write(edit_environment, original)
    result, old_job = queued_edit(edit_environment, project, 0, len(original))
    new_job = []

    def reclaim():
        store.cancel(old_job['id'])
        store.retry(old_job['id'])
        new_job.append(store.claim())

    gateway.callback = reclaim
    gateway.fail = old_call_fails
    service.process(old_job)
    state = store.job(old_job['id'])
    assert state['status'] == 'running' and state['attempts'] == 2
    assert service.get(project_id)['runs'][0]['candidate'] is None
    gateway.fail = False
    service.process(new_job[0])
    current = service.get(project_id)
    assert store.job(old_job['id'])['status'] == 'succeeded'
    assert current['sections'][0]['content'] == original
    assert current['runs'][0]['candidate'] and not current['runs'][0]['applied']
    assert len(gateway.calls) == 2


def test_strict_expansion_without_related_evidence_is_rejected_without_changing_mode(edit_environment):
    store, gateway, service, project_id, section_id = edit_environment
    project = write(edit_environment, '这是一段尚无原文依据的人工思考。')
    text = project['sections'][0]['content']
    with pytest.raises(ValueError):
        service.queue_edit(project_id, project['version'], section_id, request_for(project, 0, len(text), 'expand'))
    assert gateway.calls == []
    assert service.get(project_id)['sections'][0]['effectiveMode'] == 'strict'
    assert not any(job['kind'] == 'writing_edit' for job in store.jobs()['jobs'])


def test_extended_expansion_keeps_plain_prose_without_claiming_verification(edit_environment):
    store, gateway, service, project_id, _ = edit_environment
    project = write(edit_environment, '来源之外的一点思考。')
    project = service.patch(project_id, project['version'], {'mode': 'extended'})
    project = service.confirm(project_id, project['version'])
    text = project['sections'][0]['content']
    gateway.response['replacementText'] = '这里是模型提供的延伸解释，需要进一步核实。'
    gateway.response['supplement'] = False
    _, job = queued_edit(edit_environment, project, 0, len(text), 'expand')
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    candidate = service.get(project_id)['runs'][0]['candidate']['edit']
    assert candidate['replacementText'] == gateway.response['replacementText']
    assert candidate['semanticVerified'] is False
    assert candidate['extendedContentPolicy'] == 'retain-as-prose-v2'
    assert candidate['issues'] == []


def test_model_cannot_remove_existing_supplement_marker(edit_environment):
    store, gateway, service, project_id, _ = edit_environment
    project = write(edit_environment, '【模型补充·待核实】这是一段延伸讨论。')
    project = service.patch(project_id, project['version'], {'mode': 'extended'})
    project = service.confirm(project_id, project['version'])
    text = project['sections'][0]['content']
    gateway.response['replacementText'] = '这是一段润色后的延伸讨论。'
    _, job = queued_edit(edit_environment, project, 0, len(text))
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert service.get(project_id)['sections'][0]['content'] == text


@pytest.mark.parametrize('original,selected,replacement', [
    ('这是普通文字 [E待改]，尚非引用。', '待改', '99'),
    ('这是普通文字 【模型补待改】，尚非标记。', '待改', '充·待核实'),
])
def test_replacement_cannot_construct_new_markers_across_selection_boundaries(edit_environment, original, selected, replacement):
    store, gateway, service, project_id, _ = edit_environment
    project = write(edit_environment, original)
    gateway.response['replacementText'] = replacement
    start = original.index(selected)
    _, job = queued_edit(edit_environment, project, start, start + len(selected))
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    assert service.get(project_id)['sections'][0]['content'] == original
