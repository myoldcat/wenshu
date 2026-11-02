"""Whole-draft chapter aliases stay inside each frozen evidence whitelist.

Controlled model responses deliberately use a different 40-block whitelist in
each chapter.  No provider or production library is touched by these checks.
"""
import copy
import json
import re

import pytest

from server.store import Store, dumps
from server.writing import WritingConflict, WritingService
from server.tests.test_writing_document import document, queue_and_claim
from server.tests.test_writing_edit_integrity import request_for
from server.tests.test_writing_integrity import freeze_legacy_generation_output


PROTOCOL = 'chapter-block-alias-v1'


def scoped_project(document, *, words=(100, 100)):
    store, gateway, service, _ = document
    gateway.config['contextChars'] = 100000
    texts = [
        f'{topic} 第 {index:03d} 条固定版本原文。必须保留。必须保留。'
        '材料只能用于本章，原始段落与来源版本应当保持不变。'
        for topic in ('Alpha', 'Beta') for index in range(1, 41)
    ]
    queued = store.queue('file', {}, '不同章节的资料范围')
    job = store.claim()
    assert job['id'] == queued['id']
    url = 'https://example.test/chapter-reference-scopes'
    parsed = {'title': '章节范围固定版本资料', 'body': texts, 'language': 'zh',
              'parser_version': 'chapter-scopes-test-1',
              'blocks': [{'kind': 'paragraph', 'ordinal': index, 'text': text,
                          'locator': {'ordinal': index}}
                         for index, text in enumerate(texts)]}
    store.publish(job, {'url': url}, '\n'.join(texts).encode(), parsed, url)
    source = store.job(job['id'])['source_id']
    project = service.create({'title': '多章独立引用范围', 'brief': {'topic': '章级证据隔离', 'targetWords': sum(words)},
                              'mode': 'strict', 'sourceIds': [source],
                              'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': f'{topic} 论述', 'goal': f'{topic} 事实', 'mode': 'inherit',
         'targetWords': target}
        for topic, target in zip(('Alpha', 'Beta'), words)
    ])
    return service.confirm(project['id'], project['version'])


def frozen_steps(store, run_id):
    with store.connect() as db:
        return [json.loads(row['input_json']) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run_id,))]


def allowed_ids(payload):
    scope = payload.get('citationScope', {})
    return payload.get('allowedBlockIds', scope.get('allowedBlockIds'))


def chapter_scope(payload):
    return payload.get('citationScope', payload.get('section', {}).get('citationScope', {}))


def assert_error_location(error, section, block_id, paragraph=1):
    assert section['title'] in error, error
    assert section['id'] in error, error
    assert block_id in error, error
    assert re.search(r'第\s*1\s*章|章节\s*1|第\s*1\s*章节', error), error
    assert re.search(rf'第\s*{paragraph}\s*段|段落\s*{paragraph}', error), error


def test_single_request_registers_distinct_chapter_aliases_as_original_evidence(document):
    store, gateway, service, _ = document
    project = scoped_project(document)
    run, job = queue_and_claim(document, project)
    prepared = frozen_steps(store, run['runId'])
    original_ids = []
    for index, item in enumerate(prepared, 1):
        scope = item['citationScope']
        assert scope['protocol'] == PROTOCOL
        assert scope['chapterNumber'] == index
        assert scope['namespace'] == f'C{index:02d}'
        aliases = scope['aliases']
        assert list(aliases) == [f'C{index:02d}-B{number:03d}' for number in range(1, 41)]
        block_ids = {block['blockId'] for block in item['blocks']}
        assert set(aliases.values()) == block_ids
        original_ids.append(block_ids)
        payload = json.loads(item['messages'][1]['content'])
        assert {block['blockId'] for block in payload['blocks']} == set(aliases)
        assert set(allowed_ids(payload)) == set(aliases)
        assert chapter_scope(payload)['protocol'] == PROTOCOL
        assert all(block['blockId'] not in block_ids for block in payload['blocks'])
    assert original_ids[0].isdisjoint(original_ids[1])

    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert len(gateway.calls) == 1
    request = gateway.calls[0]['payload']
    assert request['task'] == 'writing_document'
    for index, (item, output) in enumerate(zip(prepared, service.get(project['id'])['sections']), 1):
        sent = request['sections'][index - 1]
        aliases = item['citationScope']['aliases']
        assert set(allowed_ids(sent)) == set(aliases)
        assert chapter_scope(sent)['protocol'] == PROTOCOL
        first = next(iter(aliases.values()))
        assert output['evidence'][0]['blockId'] == first
        assert output['evidence'][0]['sourceId'] == project['sources'][0]['sourceId']
        assert output['evidence'][0]['revisionId'] == project['sources'][0]['revisionId']
        assert '[E1]' in output['content'] and 'C01-B' not in output['content']
        original = next(block for block in item['blocks'] if block['blockId'] == first)
        ref = output['evidence'][0]
        assert original['text'][ref['start']:ref['end']] == ref['quote']


@pytest.mark.parametrize('damage', ['cross_chapter_alias', 'own_original_id', 'foreign_original_id',
                                    'invented_alias', 'false_quote', 'ambiguous_quote'])
@pytest.mark.parametrize('legacy_output', [False, True], ids=['current-preserve', 'legacy-hard-failure'])
def test_invalid_chapter_reference_fails_atomically_with_specific_location(document, damage, legacy_output):
    store, gateway, service, _ = document
    project = scoped_project(document)
    run, job = queue_and_claim(document, project)
    assert run['project']['chapterConfirmationPolicy'] == 'chapter-confirmation-v1'
    for section in project['sections']:
        section['chapterConfirmed'] = False
    if legacy_output:
        freeze_legacy_generation_output(store, run['runId'], whole_document=True)
    prepared = frozen_steps(store, run['runId'])
    own = prepared[0]['citationScope']['aliases']
    foreign = prepared[1]['citationScope']['aliases']
    chosen_id = {
        'cross_chapter_alias': next(iter(foreign)),
        'own_original_id': next(iter(own.values())),
        'foreign_original_id': next(iter(foreign.values())),
        'invented_alias': 'C01-B999',
        'false_quote': next(iter(own)),
        'ambiguous_quote': next(iter(own)),
    }[damage]

    def corrupt(result, _):
        ref = result['sections'][0]['paragraphs'][0]['evidence'][0]
        ref['blockId'] = chosen_id
        if damage == 'false_quote':
            ref['quote'] = '该句完全不存在于本章冻结的任何原文之中。'
        elif damage == 'ambiguous_quote':
            ref['quote'] = '必须保留。'
        return result

    gateway.transform = corrupt
    service.process(job)
    finished = store.job(job['id'])
    assert len(gateway.calls) == 1
    current = service.get(project['id'])
    if legacy_output:
        assert finished['status'] == 'failed', finished
        assert_error_location(finished['error'], project['sections'][0], chosen_id)
        assert current['sections'] == project['sections']
        assert current['version'] == project['version']
        with store.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM writing_document_steps WHERE run_id=? AND candidate_json IS NOT NULL',
                              (run['runId'],)).fetchone()[0] == 0
    else:
        assert finished['status'] == 'succeeded' and finished['stage'] == 'candidate', finished
        # The bad sentence is retained for comparison, never silently anchored
        # using another paragraph or another chapter's otherwise valid quote.
        assert current['sections'][0] == project['sections'][0]
        assert current['sections'][1]['content']
        with store.connect() as db:
            row = db.execute('SELECT status,candidate_json FROM writing_document_steps WHERE run_id=? AND section_id=?',
                (run['runId'], project['sections'][0]['id'])).fetchone()
            interaction = db.execute('SELECT response_text FROM writing_model_interactions WHERE run_id=? ORDER BY started_at,id LIMIT 1',
                (run['runId'],)).fetchone()
        assert row['status'] == 'candidate'
        chapter = json.loads(row['candidate_json'])['section']
        first_sentence = chapter['content'].split('\n\n')[0]
        assert '[E' not in first_sentence and chapter['citationBindingState'] == 'needs_review'
        assert 'citationBindings' not in chapter
        assert all(item['status'] == 'unreviewed' for item in chapter['citationIssues'])
        assert not gateway.matching_calls
        assert chapter['generationNotices'] and chapter['citationIssues']
        assert all(reference['blockId'] in {block['blockId'] for block in prepared[0]['blocks']}
                   for reference in chapter['evidence'])
        # The rejected spelling/quote is still inspectable in the original
        # model interaction; no repaired ID is substituted into that record.
        raw_ref = json.loads(interaction['response_text'])['sections'][0]['paragraphs'][0]['evidence'][0]
        assert raw_ref['blockId'] == chosen_id
        if damage == 'false_quote':
            assert raw_ref['quote'] == '该句完全不存在于本章冻结的任何原文之中。'
        elif damage == 'ambiguous_quote':
            assert raw_ref['quote'] == '必须保留。'


def test_all_alias_protocol_overhead_is_counted_in_the_generation_plan(document):
    _, gateway, service, _ = document
    project = scoped_project(document)
    with service.store.connect() as db:
        plan, prepared = service._document_plan(db, project, gateway.config)
        messages = service._document_messages(project, prepared, db=db)
    assert plan['strategy'] == 'single'
    assert plan['estimatedInputChars'] == sum(len(message['content']) for message in messages)
    assert plan['estimatedInputChars'] <= gateway.config['contextChars']
    request = json.loads(messages[1]['content'])
    assert all(chapter_scope(section)['protocol'] == PROTOCOL for section in request['sections'])
    assert all(set(allowed_ids(section)) == {block['blockId'] for block in section['blocks']}
               for section in request['sections'])
    assert gateway.calls == []


def test_legacy_frozen_document_still_uses_its_original_request_ids(document):
    store, gateway, service, _ = document
    project = scoped_project(document)
    run, job = queue_and_claim(document, project)
    prepared = frozen_steps(store, run['runId'])
    for item in prepared:
        aliases = item.pop('citationScope')['aliases']
        # This fixture represents the queued pre-review protocol, not a newly
        # queued sentence-review job with only its alias envelope removed.
        item.pop('citationMatchInstructions', None)
        payload = json.loads(item['messages'][1]['content'])
        payload['citationPlacement'] = 'claim-exact-v1'
        for key in ('citationScope', 'allowedBlockIds', 'chapterNumber', 'namespace'):
            payload.pop(key, None)
            payload['section'].pop(key, None)
        for block in payload['blocks']:
            block['blockId'] = aliases[block['blockId']]
        item['messages'][1]['content'] = dumps(payload)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        prompt_snapshot = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?', (job['id'],)).fetchone()[0])
        prompt_snapshot.pop('writing_citation_match', None)
        db.execute('UPDATE job_prompt_snapshots SET snapshot_json=? WHERE job_id=?', (dumps(prompt_snapshot), job['id']))
        for item in prepared:
            db.execute('UPDATE writing_document_steps SET input_json=? WHERE run_id=? AND section_id=?',
                       (dumps(item), run['runId'], item['section']['id']))
    expected = service._document_messages(frozen, prepared)
    sent = []
    complete = gateway.complete

    def capture(config, messages):
        sent.append(copy.deepcopy(messages))
        return complete(config, messages)

    gateway.complete = capture
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded', store.job(job['id'])
    assert sent == [expected]
    assert gateway.matching_calls == []
    assert all('citationBindings' not in section for section in service.get(project['id'])['sections'])
    payload = gateway.calls[0]['payload']
    assert all('citationScope' not in section for section in payload['sections'])
    assert [section['blocks'][0]['blockId'] for section in payload['sections']] == [
        item['blocks'][0]['blockId'] for item in prepared]


def test_sequential_retry_keeps_frozen_chapter_namespace_and_completed_evidence(document):
    store, gateway, service, _ = document
    gateway.config['maxOutputTokens'] = 30000
    gateway.fail_calls = {2}
    project = scoped_project(document, words=(5000, 5000))
    run, job = queue_and_claim(document, project)
    before = frozen_steps(store, run['runId'])
    service.process(job)
    assert store.job(job['id'])['status'] == 'failed'
    dispatched = frozen_steps(store, run['runId'])
    for original, sent in zip(before, dispatched):
        # Sequential context is prepared once immediately before the HTTP
        # request; its growth never changes the frozen citation namespace.
        for key in ('section', 'blocks', 'citationScope', 'sectionInstructions', 'documentPrompt', 'compositionSnapshot'):
            assert sent[key] == original[key]
        assert sent['messages'][0] == original['messages'][0]
    current = service.get(project['id'])
    first = copy.deepcopy(current['sections'][0])
    assert first['content'] and not current['sections'][1]['content']
    restarted_store = Store(store.root)
    restarted = WritingService(restarted_store, gateway)
    restarted_store.retry(job['id'])
    restarted.process(restarted_store.claim())
    assert restarted_store.job(job['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 3
    assert all(call['payload']['task'] == 'writing_section' for call in gateway.calls)
    assert [chapter_scope(call['payload'])['namespace'] for call in gateway.calls] == ['C01', 'C02', 'C02']
    assert gateway.calls[1]['payload'] == gateway.calls[2]['payload']
    assert frozen_steps(restarted_store, run['runId']) == dispatched
    current = restarted.get(project['id'])
    assert current['sections'][0] == first
    assert current['sections'][1]['evidence'][0]['blockId'] in before[1]['citationScope']['aliases'].values()


def test_skipping_a_locked_chapter_does_not_renumber_the_remaining_scope(document):
    store, gateway, service, _ = document
    project = scoped_project(document)
    first = project['sections'][0]['id']
    project = service.save_section(project['id'], first, project['version'], {'locked': True})
    run, job = queue_and_claim(document, project)
    assert run['project']['chapterConfirmationPolicy'] == 'chapter-confirmation-v1'
    for section in project['sections']:
        section['chapterConfirmed'] = False
    prepared = frozen_steps(store, run['runId'])
    assert 'citationScope' not in prepared[0]
    assert prepared[1]['citationScope']['namespace'] == 'C02'
    assert prepared[1]['citationScope']['chapterNumber'] == 2
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 1
    assert [chapter_scope(section)['namespace'] for section in gateway.calls[0]['payload']['sections']] == ['C02']
    assert service.get(project['id'])['sections'][0] == project['sections'][0]


@pytest.mark.parametrize('change', ['manual', 'lock_unlock', 'requirements'])
def test_aliases_do_not_bypass_inflight_manual_lock_or_requirement_protection(document, change):
    store, gateway, service, _ = document
    project = scoped_project(document)
    first = project['sections'][0]['id']
    manual = '人工新增内容不可被迟到的带引用正文覆盖。'

    def change_during_call(_, __):
        current = service.get(project['id'])
        if change == 'requirements':
            service.patch(project['id'], current['version'], {'brief': {'topic': '新的人工要求'}})
        elif change == 'manual':
            service.save_section(project['id'], first, current['version'], {'content': manual})
        else:
            current = service.save_section(project['id'], first, current['version'], {'locked': True})
            service.save_section(project['id'], first, current['version'], {'locked': False})

    gateway.callback = change_during_call
    run, job = queue_and_claim(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 1
    current = service.get(project['id'])
    assert current['sections'][0]['content'] == (manual if change == 'manual' else '')
    if change == 'requirements':
        assert not any(section['content'] for section in current['sections'])
    else:
        assert current['sections'][1]['content']
    before = copy.deepcopy(current['sections'])
    with pytest.raises(WritingConflict):
        service.apply_document_section(project['id'], run['runId'], first, current['version'])
    assert service.get(project['id'])['sections'] == before


def test_registered_permanent_evidence_still_supports_the_bounded_edit_path(document):
    store, gateway, service, _ = document
    project = scoped_project(document)
    run, job = queue_and_claim(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    current = service.get(project['id'])
    section = current['sections'][0]
    reference = section['evidence'][0]
    assert not re.fullmatch(r'C\d+-B\d+', reference['blockId'])
    request = request_for(current, 0, section['content'].index('\n\n'))
    edit = service.queue_edit(current['id'], current['version'], section['id'], request)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (edit['runId'],)).fetchone()[0])
    evidence = json.loads(frozen['messages'][1]['content'])['evidence']
    assert evidence == [reference]
    assert len(gateway.calls) == 1
    assert service.get(project['id'])['sections'] == current['sections']


@pytest.mark.parametrize('kind', ['section', 'document'])
def test_provider_value_error_subclass_never_exposes_private_response_text(document, kind):
    store, gateway, service, _ = document
    project = scoped_project(document)
    private_response = 'provider-value-error-subclass-private-response-do-not-publish'
    requests = []

    class ProviderDecodeFailure(ValueError):
        pass

    def fail_with_private_response(config, messages):
        requests.append(copy.deepcopy(messages))
        raise ProviderDecodeFailure(private_response + ': raw provider response or diagnostic')

    gateway.complete = fail_with_private_response
    if kind == 'document':
        run, job = queue_and_claim(document, project)
        assert run['project']['chapterConfirmationPolicy'] == 'chapter-confirmation-v1'
        for section in project['sections']:
            section['chapterConfirmed'] = False
    else:
        run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
        job = store.claim()
        assert job['id'] == run['job']['id']
    service.process(job)
    failed = store.job(job['id'])
    assert failed['status'] == 'failed'
    assert len(requests) == 1
    assert '请检查模型配置后重试' in failed['error']
    current = service.get(project['id'])
    assert current['version'] == project['version']
    assert current['sections'] == project['sections']
    assert private_response not in json.dumps(current, ensure_ascii=False)
    assert private_response not in json.dumps(store.jobs(), ensure_ascii=False)
    with store.connect() as db:
        for table in ('jobs', 'writing_runs', 'writing_calls', 'writing_document_steps', 'writing_document_calls'):
            assert private_response not in str([tuple(row) for row in db.execute('SELECT * FROM ' + table)])
