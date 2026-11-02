"""Independent writing acceptance: immutable evidence and asynchronous edit races.

These tests use a controlled provider; the suite is intended to run in isolation and
does not call the configured live model or touch the persistent sample library.
"""
import copy
import json

import pytest

from server.store import Store, dumps
from server.writing import WritingConflict, WritingService


def controlled_outline_result(packet, result):
    """Supply exact registered planning quotes only for the new protocol.

    This controlled fixture exercises the request/validation contract. It is
    not an independent assessment that a real plan is semantically supported.
    """
    if packet.get('outlinePlanningProtocol') not in ('evidence-plan-v1', 'evidence-plan-v2'):
        return result
    result = copy.deepcopy(result)
    result['protocol'] = packet['outlinePlanningProtocol']
    if not packet['blocks']:
        result.update(sections=[], issues=['受控测试输入没有可支持严格章节的原文。'])
        return result
    block = packet['blocks'][0]
    for section in result.get('sections', []):
        section['planningEvidence'] = [{'blockId': block['blockId'], 'quote': block['text']}]
    return result


def controlled_matching_result(packet, paragraphs):
    """Explicit fake semantic decisions follow the fixture's exact claim map.

    Model calls remain visible in operation logs. Generation request counters
    below intentionally count generation separately from these review calls.
    """
    claims = {}
    for paragraph in paragraphs:
        for reference in paragraph.get('evidence', []):
            claims.setdefault(reference.get('claim'), []).append(reference)
    assignments = []
    for sentence in packet['sentences']:
        if packet.get('reviewPurpose') == 'outline_scope':
            # Title and goal are scope commitments, not source-free body
            # transitions. Deliberately return an explicit fake judgment for
            # each program-owned unit, independently of the prose claim map.
            ids = [item['evidenceId'] for item in packet['evidence']]
        else:
            ids = list(dict.fromkeys(evidence['evidenceId'] for reference in claims.get(sentence['text'], [])
                                     for evidence in packet['evidence']
                                     if evidence['quote'] == reference.get('quote')))
        assignments.append({'sentenceId': sentence['sentenceId'], 'evidenceIds': ids,
                            'support': 'supported' if ids else 'unsupported',
                            'reason': '受控测试模型按预设逐句支持关系返回，不用于证明真实模型准确性。',
                            **({'supportBasis': 'paraphrase' if ids else 'none'} if packet.get('mode') is not None else {})})
    return {'text': json.dumps({'assignments': assignments}, ensure_ascii=False), 'usage': {'total_tokens': 7}}


class ControlledGateway:
    def __init__(self):
        self.config = {'id': 'integrity_model', 'version': 1, 'name': '写作边界测试',
                       'model': 'controlled', 'contextChars': 24000,
                       'maxOutputTokens': 4096, 'apiKey': 'integrity-private-token'}
        self.calls = []
        self.matching_calls = []
        self.generated_paragraphs = []
        self.callback = None
        self.transform = None
        self.fail = False

    def get_preferences(self):
        return {'summaryModelId': self.config['id']}

    def models(self):
        return [{key: copy.deepcopy(value) for key, value in self.config.items() if key != 'apiKey'}]

    def resolve(self, model_id=None, version=None):
        assert model_id in (None, self.config['id'])
        return {**self.config, 'version': version if version is not None else self.config['version']}

    def complete(self, config, messages):
        assert sum(len(message['content']) for message in messages) <= config['contextChars']
        request = json.loads(messages[1]['content'])
        if request.get('task') == 'writing_citation_match':
            self.matching_calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
            return controlled_matching_result(request, self.generated_paragraphs)
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.fail:
            raise RuntimeError('integrity-private-token: confidential provider response')
        request = json.loads(messages[1]['content'])
        if request['task'] == 'writing_outline':
            result = {'sections': [{'title': '来源与边界', 'goal': '概述原始证据及边界',
                                    'mode': request['mode'], 'targetWords': 600}], 'issues': []}
            result = controlled_outline_result(request, result)
        else:
            block = request['blocks'][0]
            result = {'paragraphs': [{'text': '资料记载应保留原始证据和固定版本。',
                                     'kind': 'supported',
                                     'evidence': [{'blockId': block['blockId'], 'quote': block['text'],
                                                   'claim': '资料记载应保留原始证据和固定版本。'}]}],
                      'issues': []}
        if self.transform:
            result = self.transform(result, request)
        self.generated_paragraphs = copy.deepcopy(result.get('paragraphs', []))
        return {'text': json.dumps(result, ensure_ascii=False), 'usage': {'total_tokens': 23}}


def add_source(store, *, text=None, url='https://example.test/writing-integrity'):
    text = text or '知识治理应保留原始证据和固定版本，读者可以回查原文、数据口径与适用范围，修订不能悄然改变既有引文的来源。'
    queued = store.queue('file', {}, '写作完整性资料')
    job = store.claim()
    assert job['id'] == queued['id']
    parsed = {'title': '写作完整性资料', 'body': [text], 'language': 'zh',
              'parser_version': 'writing-integrity-1',
              'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': 0}]}
    store.publish(job, {'url': url}, ('<p>' + text + '</p>').encode(), parsed, url)
    result = store.job(queued['id'])
    assert result['status'] == 'succeeded'
    return result['source_id']


@pytest.fixture
def writing(tmp_path):
    store = Store(tmp_path / 'isolated-writing-integrity')
    gateway = ControlledGateway()
    service = WritingService(store, gateway)
    source = add_source(store)
    project = service.create({'title': '证据治理实践', 'brief': {'topic': '来源与修订'},
                              'mode': 'strict', 'sourceIds': [source], 'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': '来源与边界', 'goal': '说明资料支持的认识', 'mode': 'strict', 'targetWords': 600}])
    project = service.confirm(project['id'], project['version'])
    return store, gateway, service, source, project


def enqueue(environment):
    store, _, service, _, initial = environment
    project = service.get(initial['id'])
    run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    claimed = store.claim()
    assert claimed['id'] == run['job']['id']
    return run, claimed


def freeze_legacy_generation_output(store, run_id, *, whole_document=False):
    """Emulate a queued request from before usable-output preservation.

    Mutate the saved input, including every durable whole-draft step, so a
    worker restart cannot silently turn an old hard-validation test into the
    current publication policy. Keep source scope and evidence intact.
    """
    freeze_legacy_inline_review(store, run_id, whole_document=whole_document)

    def legacy(frozen):
        frozen.pop('generationOutputPolicy', None)
        frozen.pop('generationOutputRequirements', None)
        frozen.pop('citationReviewPolicy', None)
        if frozen.get('messages'):
            packet = json.loads(frozen['messages'][1]['content'])
            packet.pop('generationOutputPolicy', None)
            packet.pop('generationOutputRequirements', None)
            packet.pop('citationReviewPolicy', None)
            packet.pop('citationReviewScope', None)
            packet.pop('citationReviewRequirement', None)
            frozen['messages'][1]['content'] = dumps(packet)
        return frozen

    with store.connect() as db:
        row = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()
        assert row
        frozen = legacy(json.loads(row['input_json']))
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run_id))
        if whole_document:
            steps = db.execute('SELECT section_id,input_json FROM writing_document_steps WHERE run_id=?', (run_id,)).fetchall()
            assert steps
            for step in steps:
                item = legacy(json.loads(step['input_json']))
                db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                           (dumps(item), run_id, step['section_id']))
    return frozen


def freeze_legacy_inline_review(store, run_id, *, whole_document=False):
    """Persist the complete pre-six-step inline-review task shape for regression.

    Old tasks keep their saved matcher text, effective mode, support policy and
    prompt version. This helper changes only isolated queued test snapshots;
    production defaults and execution are exercised without monkeypatching.
    """
    from server.prompts import prompt_text, snapshot
    from server.writing_citation_matching import matching_policy

    with store.connect() as db:
        row = db.execute('SELECT job_id,input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()
        assert row
        instruction = prompt_text('writing_citation_match', db=db)

        def legacy(value):
            value.pop('citationReviewPolicy', None)
            if value.get('messages'):
                packet = json.loads(value['messages'][1]['content'])
                for field in ('citationReviewPolicy', 'citationReviewRequirement', 'citationReviewScope'):
                    packet.pop(field, None)
                value['messages'][1]['content'] = dumps(packet)
                value['messages'][0]['content'] = value['messages'][0]['content'].split('\n本次正文引用规则：')[0]
                if isinstance(value.get('sectionInstructions'), str):
                    value['sectionInstructions'] = value['sectionInstructions'].split('\n本次正文引用规则：')[0]
                mode = value['effectiveMode']
                value.setdefault('citationMatchInstructions', instruction)
                value.setdefault('citationMatchMode', mode)
                value.setdefault('citationMatchPolicy', matching_policy(mode))
            return value

        parent = legacy(json.loads(row['input_json']))
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(parent), run_id))
        if whole_document:
            steps = db.execute('SELECT section_id,input_json FROM writing_document_steps WHERE run_id=?', (run_id,)).fetchall()
            assert steps
            for step in steps:
                value = legacy(json.loads(step['input_json']))
                db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                           (dumps(value), run_id, step['section_id']))
        prompt_row = db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (row['job_id'],)).fetchone()
        saved = json.loads(prompt_row['snapshot_json'])
        saved.setdefault('writing_citation_match', snapshot(db, ('writing_citation_match',))['writing_citation_match'])
        db.execute('UPDATE job_prompt_snapshots SET snapshot_json=? WHERE job_id=?', (dumps(saved), row['job_id']))
    return parent


def use_legacy_inline_fixture(environment, monkeypatch):
    """Queue actual old-format snapshots for a historical integration module.

    The normal service creates and processes every job. Only the isolated
    fixture's persisted protocol and project age are adjusted at enqueue time;
    no model response, validation, application or review code is replaced.
    """
    store, _, service, _ = environment
    create, queue, queue_document = service.create, service.queue, service.queue_document

    def historical_create(data):
        project = create(data)
        with store.connect() as db:
            old = service._load(db, project['id'])
            old.pop('workflowVersion', None)
            db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(old), old['id']))
            db.execute('UPDATE writing_history SET content_json=? WHERE project_id=? AND version=?',
                       (dumps(old), old['id'], old['version']))
        return service.get(project['id'])

    def historical_queue(project_id, expected, kind, section_id=None):
        result = queue(project_id, expected, kind, section_id)
        if kind == 'section':
            freeze_legacy_inline_review(store, result['runId'])
        return result

    def historical_document(project_id, expected, *args, **kwargs):
        result = queue_document(project_id, expected, *args, **kwargs)
        freeze_legacy_inline_review(store, result['runId'], whole_document=True)
        return result

    monkeypatch.setattr(service, 'create', historical_create)
    monkeypatch.setattr(service, 'queue', historical_queue)
    monkeypatch.setattr(service, 'queue_document', historical_document)


def finish(environment):
    store, _, service, _, initial = environment
    run, claimed = enqueue(environment)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    return service.get(initial['id'])


def test_generation_requires_current_outline_confirmation(writing):
    store, gateway, service, _, project = writing
    changed = service.patch(project['id'], project['version'], {'brief': {'topic': '调整后的任务'}})
    assert not changed['outlineConfirmed']
    with pytest.raises(ValueError, match='纲要'):
        service.queue(project['id'], changed['version'], 'section', changed['sections'][0]['id'])
    assert not gateway.calls
    assert not any(job['kind'].startswith('writing_') for job in store.jobs()['jobs'])


def test_manual_edit_during_call_preserves_text_and_requires_explicit_replace(writing):
    store, gateway, service, _, project = writing
    run, claimed = enqueue(writing)
    section_id = project['sections'][0]['id']
    gateway.callback = lambda: service.save_section(project['id'], section_id, project['version'], {'content': '人工新写的稿件，不应被迟到的模型输出覆盖。'})
    service.process(claimed)
    current = service.get(project['id'])
    assert current['sections'][0]['content'].startswith('人工新写')
    assert store.job(run['job']['id'])['stage'] == 'candidate'
    assert current['runs'][0]['candidate'] and not current['runs'][0]['applied']
    with pytest.raises(WritingConflict):
        service.apply(project['id'], run['runId'], current['version'])
    accepted = service.apply(project['id'], run['runId'], current['version'], replace_manual=True)
    assert accepted['sections'][0]['content'] != current['sections'][0]['content']
    assert service.historical(project['id'], current['version'])['sections'][0]['content'] == current['sections'][0]['content']


def test_lock_during_call_blocks_publication_and_explicit_candidate_application(writing):
    _, gateway, service, _, project = writing
    run, claimed = enqueue(writing)
    gateway.callback = lambda: service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'locked': True})
    service.process(claimed)
    current = service.get(project['id'])
    assert current['sections'][0]['locked'] and current['sections'][0]['content'] == ''
    with pytest.raises(WritingConflict, match='锁定'):
        service.apply(project['id'], run['runId'], current['version'], replace_manual=True)


def test_cancelled_inflight_result_cannot_create_candidate_or_text(writing):
    store, gateway, service, _, project = writing
    run, claimed = enqueue(writing)
    gateway.callback = lambda: store.cancel(run['job']['id'])
    service.process(claimed)
    current = service.get(project['id'])
    assert store.job(run['job']['id'])['status'] == 'cancelled'
    assert current['sections'][0]['content'] == ''
    assert current['runs'][0]['candidate'] is None
    store.retry(run['job']['id'])
    service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert service.get(project['id'])['sections'][0]['content']


@pytest.mark.parametrize('old_call_fails', [False, True])
def test_old_attempt_cannot_finish_or_fail_retried_running_attempt(writing, old_call_fails):
    store, gateway, service, _, project = writing
    run, old_claimed = enqueue(writing)
    new_claimed = []

    def cancel_and_reclaim():
        store.cancel(run['job']['id'])
        store.retry(run['job']['id'])
        new_claimed.append(store.claim())

    gateway.callback = cancel_and_reclaim
    gateway.fail = old_call_fails
    service.process(old_claimed)
    state = store.job(run['job']['id'])
    assert state['status'] == 'running' and state['attempts'] == 2
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == '' and current['runs'][0]['candidate'] is None
    gateway.fail = False
    service.process(new_claimed[0])
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 2


def test_validated_candidate_survives_publication_failure_without_second_model_call(writing, monkeypatch):
    store, gateway, service, _, project = writing
    run, claimed = enqueue(writing)
    original_save = service._save

    def interrupted_save(*args, **kwargs):
        raise RuntimeError('simulated publication interruption')

    monkeypatch.setattr(service, '_save', interrupted_save)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'failed'
    assert service.get(project['id'])['runs'][0]['candidate']
    monkeypatch.setattr(service, '_save', original_save)
    store.retry(run['job']['id'])
    service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 1


def test_pinned_old_source_and_model_versions_survive_updates_and_restart(writing):
    store, gateway, service, source, project = writing
    original = store.detail(source)
    run, claimed = enqueue(writing)
    gateway.config['version'] = 2
    updated_source = add_source(store, text='原始资料现已修订为第二个版本，新增限制条件并改变正文，旧文章的引文必须继续指向第一版本，不能被悄然替换。')
    assert updated_source == source
    assert store.detail(source)['revisionId'] != original['revisionId']
    service.process(claimed)
    current = service.get(project['id'])
    reference = current['sections'][0]['evidence'][0]
    assert reference['revisionId'] == original['revisionId']
    old_block = next(block for block in original['blocks'] if block['id'] == reference['blockId'])
    assert old_block['text'][reference['start']:reference['end']] == reference['quote']
    assert gateway.calls[0][0]['version'] == 1
    report = service.review(project['id'], current['version'])['review']
    assert any(issue['code'] == 'source_new_revision' for issue in report['issues'])
    restarted = WritingService(Store(store.root), gateway)
    assert restarted.get(project['id'])['sections'] == current['sections']
    assert restarted.get(project['id'])['runs'][0]['applied']
    with store.connect() as db:
        persisted = str([tuple(row) for row in db.execute('SELECT * FROM writing_runs')])
    assert gateway.config['apiKey'] not in persisted


def test_foreign_project_candidate_and_stale_edits_cannot_overwrite(writing):
    _, _, service, source, project = writing
    current = finish(writing)
    other = service.create({'title': '独立项目', 'brief': {}, 'mode': 'strict', 'sourceIds': [source]})
    with pytest.raises(KeyError):
        service.apply(other['id'], current['runs'][0]['id'], other['version'], replace_manual=True)
    with pytest.raises(WritingConflict):
        service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'content': '旧标签页中的文本'})
    assert service.get(project['id'])['sections'] == current['sections']
    assert service.get(other['id'])['sections'] == []


@pytest.mark.parametrize('change', [lambda text: text.replace('资料记载', '未经来源支持的新结论'), lambda text: text + '\n\n新段落没有依据。'])
def test_manual_text_changes_invalidate_review_without_promoting_citation_to_truth(writing, change):
    _, _, service, _, project = writing
    current = finish(writing)
    reviewed = service.review(project['id'], current['version'])
    assert reviewed['review']['semanticVerified'] is False
    assert reviewed['project']['review'] is not None
    edited = service.save_section(project['id'], current['sections'][0]['id'], current['version'], {'content': change(current['sections'][0]['content'])})
    assert edited['review'] is None
    report = service.review(project['id'], edited['version'])['review']
    assert report['semanticVerified'] is False
    assert any(issue['code'] == 'manual_edit_recheck' for issue in report['issues'])
    assert report['status'] != 'rules_passed'


@pytest.mark.parametrize('legacy', [False, True], ids=['current-preserved-candidate', 'legacy-hard-failure'])
def test_existing_but_out_of_scope_block_is_not_admitted(writing, legacy):
    store, gateway, service, _, project = writing
    foreign_source = add_source(store, url='https://example.test/unselected-writing-source')
    foreign_block = store.detail(foreign_source)['blocks'][0]

    def add_foreign(result, request):
        result['paragraphs'][0]['evidence'] = [{'blockId': foreign_block['id'], 'quote': foreign_block['text']}]
        return result

    gateway.transform = add_foreign
    before = service.get(project['id'])
    run, claimed = enqueue(writing)
    if legacy:
        freeze_legacy_generation_output(store, run['runId'])
    service.process(claimed)
    current = service.get(project['id'])
    saved_run = next(item for item in current['runs'] if item['id'] == run['runId'])
    assert current['version'] == before['version'] and current['sections'] == before['sections']
    assert current['sections'][0]['content'] == '' and current['sections'][0]['evidence'] == []
    assert not saved_run['applied']
    if legacy:
        assert store.job(run['job']['id'])['status'] == 'failed'
        assert saved_run['candidate'] is None
    else:
        assert store.job(run['job']['id'])['status'] == 'succeeded'
        candidate = saved_run['candidate']['section']
        assert candidate['content'] and candidate['evidence'] == []
        assert '[E' not in candidate['content'] and candidate['citationBindingState'] == 'needs_review'
        assert candidate['generationNotices']
        assert 'citationBindings' not in candidate
        assert candidate['citationIssues'] and all(item['status'] == 'unreviewed' for item in candidate['citationIssues'])
        assert not gateway.matching_calls
        assert not saved_run['candidate'].get('citationMatchingPending')
        assert foreign_block['id'] not in dumps(candidate)
        with store.connect() as db:
            raw = db.execute('SELECT response_text FROM writing_model_interactions WHERE run_id=?', (run['runId'],)).fetchone()[0]
        assert json.loads(raw)['paragraphs'][0]['evidence'][0]['blockId'] == foreign_block['id']


def test_model_supplement_cannot_hide_behind_a_real_citation(writing):
    store, gateway, service, _, project = writing
    changed = service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'mode': 'extended'})
    service.confirm(project['id'], changed['version'])

    def mark_supplement(result, request):
        result['paragraphs'][0]['kind'] = 'model_supplement'
        return result

    gateway.transform = mark_supplement
    run, claimed = enqueue(writing)
    service.process(claimed)
    current = service.get(project['id'])
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    section = current['sections'][0]
    assert section['content'] and '【模型补充' not in section['content']
    assert section['evidence'] == [] and '[E' not in section['content']
    assert section['extendedContentPolicy'] == 'retain-as-prose-v2'


@pytest.mark.parametrize('legacy', [False, True], ids=['current-preserved-candidate', 'legacy-hard-failure'])
def test_repeated_exact_quote_must_not_be_assigned_an_arbitrary_first_offset(writing, legacy):
    store, gateway, service, _, project = writing
    repeated = add_source(store, text='保留证据。第一种场景中须核对引用上下文和适用条件。保留证据。第二种场景有不同的限制，不能仅凭相同短语判断引文所指的位置。', url='https://example.test/repeated-writing-text')
    changed = service.patch(project['id'], project['version'], {'sourceIds': [repeated]})
    service.confirm(project['id'], changed['version'])

    def ambiguous(result, request):
        result['paragraphs'][0]['evidence'][0]['quote'] = '保留证据。'
        return result

    gateway.transform = ambiguous
    before = service.get(project['id'])
    run, claimed = enqueue(writing)
    if legacy:
        freeze_legacy_generation_output(store, run['runId'])
    service.process(claimed)
    current = service.get(project['id'])
    saved_run = next(item for item in current['runs'] if item['id'] == run['runId'])
    assert current['version'] == before['version'] and current['sections'] == before['sections']
    assert current['sections'][0]['content'] == '' and current['sections'][0]['evidence'] == []
    assert not saved_run['applied']
    if legacy:
        assert store.job(run['job']['id'])['status'] == 'failed'
        assert saved_run['candidate'] is None
    else:
        assert store.job(run['job']['id'])['status'] == 'succeeded'
        candidate = saved_run['candidate']['section']
        assert candidate['content'] and candidate['evidence'] == []
        assert '[E' not in candidate['content'] and candidate['citationBindingState'] == 'needs_review'
        assert candidate['generationNotices']
        assert 'citationBindings' not in candidate
        assert candidate['citationIssues'] and all(item['status'] == 'unreviewed' for item in candidate['citationIssues'])
        assert not gateway.matching_calls
        assert not saved_run['candidate'].get('citationMatchingPending')
        # Neither repeated occurrence receives a fabricated quote anchor.
        assert not any(item.get('quote') == '保留证据。' for item in candidate['evidence'])
        with store.connect() as db:
            raw = db.execute('SELECT response_text FROM writing_model_interactions WHERE run_id=?', (run['runId'],)).fetchone()[0]
        assert json.loads(raw)['paragraphs'][0]['evidence'][0]['quote'] == '保留证据。'
