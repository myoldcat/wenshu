"""Durable rematching with controlled support decisions and isolated stores."""
import copy
import json

import pytest

from server.prompts import PromptLibrary
from server.store import Store
from server.tests.test_writing_integrity import add_source
from server.writing import WritingConflict, WritingService
from server.writing_sentence_citations import strip_citations, valid_bindings


SECRET = 'citation-task-private-credential'


class MatcherGateway:
    def __init__(self):
        self.config = {'id': 'matching-model', 'version': 4, 'name': '受控逐句复核',
                       'model': 'controlled', 'contextChars': 100000,
                       'maxOutputTokens': 65536, 'apiKey': SECRET}
        self.calls, self.fail_calls = [], set()
        self.callback = None
        self.invalid = False

    def get_preferences(self):
        return {'defaultModelId': self.config['id']}

    def resolve(self, model_id=None, version=None):
        assert model_id == self.config['id']
        return {**self.config, 'version': version if version is not None else self.config['version']}

    def complete(self, config, messages):
        payload = json.loads(messages[1]['content'])
        assert payload['task'] == 'writing_citation_match'
        assert config['maxOutputTokens'] == self.config['maxOutputTokens'] == 65536
        assert len(payload['sentences']) <= 8
        assert sum(len(item['content']) for item in messages) <= config['contextChars']
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if len(self.calls) in self.fail_calls:
            raise RuntimeError(SECRET + ': provider failure must not leak')
        result = {'assignments': [
            {'sentenceId': sentence['sentenceId'], 'evidenceIds': ['E1'] if sentence['sentenceId'] == 'S1' else [],
             'support': 'supported' if sentence['sentenceId'] == 'S1' else 'unsupported',
             'reason': '受控测试按预先审查的关系判断，保留缺口及限定。' + SECRET,
             **({'supportBasis': 'paraphrase' if sentence['sentenceId'] == 'S1' else 'none'} if payload.get('mode') is not None else {})}
            for sentence in payload['sentences']]}
        if self.invalid:
            result['assignments'][0]['sentenceId'] = 'invented-sentence'
        return {'text': json.dumps(result, ensure_ascii=False), 'usage': {'total_tokens': 12, 'secret': SECRET}}


@pytest.fixture
def environment(tmp_path):
    store, gateway = Store(tmp_path / 'citation-tasks'), MatcherGateway()
    service = WritingService(store, gateway)
    source = add_source(store, text='原始资料要求保留固定版本，并能够回查论述依据、人工记录与各项必要的适用条件。')
    project = service.create({'title': '现有文稿', 'mode': 'strict', 'brief': {},
                              'sourceIds': [source], 'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': '第一章', 'goal': '', 'mode': 'strict', 'targetWords': 300},
        {'title': '第二章', 'goal': '', 'mode': 'strict', 'targetWords': 300}])
    with store.connect() as db:
        project = service._load(db, project['id'])
        block = db.execute('SELECT b.*,r.source_id FROM blocks b JOIN revisions r ON r.id=b.revision_id WHERE r.source_id=?', (source,)).fetchone()
        for index, section in enumerate(project['sections']):
            section['content'] = '原始资料要求保留固定版本。后面的断言缺少支持。[E1]'
            section['evidence'] = [{'id': 'E1', 'sourceId': source, 'revisionId': block['revision_id'],
                'blockId': block['id'], 'quote': block['text'], 'start': 0, 'end': len(block['text']),
                'title': '固定原文', 'url': 'https://example.test/source', 'locator': {}}]
            section['manual'] = True
            section['locked'] = index == 1
        service._save(db, project, 'controlled_existing_manuscript')
    return store, gateway, service, service.get(project['id'])


def queued(environment):
    store, _, service, project = environment
    result = service.queue_citation_match(project['id'], project['version'])
    claimed = store.claim()
    assert claimed['id'] == result['job']['id']
    return result, claimed


def test_one_task_can_freeze_an_existing_local_model_version_without_changing_settings(environment):
    store, gateway, service, project = environment
    before = copy.deepcopy(gateway.config)
    result = service.queue_citation_match(project['id'], project['version'], model_version=2)
    service.process(store.claim())
    assert store.job(result['job']['id'])['status'] == 'succeeded'
    assert gateway.config == before
    assert all(config['version'] == 2 for config, _ in gateway.calls)
    after = service.get(project['id'])
    assert after['modelId'] == project['modelId']
    assert after['runs'][0]['model']['version'] == 2


def test_rematch_preserves_plain_body_manual_locks_source_registry_and_audits_every_call(environment):
    store, gateway, service, before = environment
    result, claimed = queued(environment)
    service.process(claimed)
    assert store.job(result['job']['id'])['status'] == 'succeeded'
    after = service.get(before['id'])
    assert len(gateway.calls) == 2 and gateway.config['maxOutputTokens'] == 65536
    assert after['version'] == before['version'] + 1
    for old, new in zip(before['sections'], after['sections']):
        assert strip_citations(old['content']) == strip_citations(new['content'])
        assert new['content'] == '原始资料要求保留固定版本。[E1]后面的断言缺少支持。'
        assert (new['evidence'], new['manual'], new['locked']) == (old['evidence'], old['manual'], old['locked'])
        assert new['citationBindingState'] == 'needs_review'
        assert valid_bindings(new['content'], new['evidence'], new['citationBindings'])
        assert {item['status'] for item in new['citationBindings']['items']} == {'supported', 'unsupported'}
    assert after['runs'][0]['applied']
    assert after['review'] and after['review']['version'] == after['version']
    calls_before_reload = copy.deepcopy(gateway.calls)
    assert WritingService(store, gateway).get(before['id'])['review'] == after['review']
    assert gateway.calls == calls_before_reload
    with store.connect() as db:
        steps = db.execute('SELECT * FROM writing_citation_match_steps WHERE run_id=?', (result['runId'],)).fetchall()
        calls = db.execute('SELECT * FROM writing_model_interactions WHERE run_id=?', (result['runId'],)).fetchall()
        assert len(steps) == len(calls) == 2
        assert all(row['kind'] == 'citation_match' and row['validation_status'] == 'valid' for row in calls)
        encoded = '\n'.join(str(dict(row)) for row in [*steps, *calls])
        encoded += str(dict(db.execute('SELECT * FROM writing_runs WHERE id=?', (result['runId'],)).fetchone()))
        assert SECRET not in encoded and '[REDACTED]' in encoded
    assert service.historical(before['id'], before['version'])['sections'][0]['content'] == before['sections'][0]['content']


def test_retry_reuses_completed_chapter_checkpoint_and_does_not_regenerate_prose(environment):
    store, gateway, service, before = environment
    result, claimed = queued(environment)
    gateway.fail_calls = {2}
    service.process(claimed)
    assert store.job(result['job']['id'])['status'] == 'failed'
    assert service.get(before['id'])['version'] == before['version']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_citation_match_steps WHERE result_json IS NOT NULL').fetchone()[0] == 1
    store.retry(result['job']['id'])
    gateway.config['version'] = 5
    service.process(store.claim())
    assert store.job(result['job']['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 3
    assert [call[0]['version'] for call in gateway.calls] == [4, 4, 4]
    assert json.loads(gateway.calls[1][1][1]['content'])['sectionId'] == json.loads(gateway.calls[2][1][1]['content'])['sectionId']


def test_queue_is_idempotent_and_freezes_instructions_before_future_edits(environment):
    store, gateway, service, project = environment
    first = service.queue_citation_match(project['id'], project['version'])
    again = service.queue_citation_match(project['id'], project['version'])
    assert again['runId'] == first['runId'] and again['job']['id'] == first['job']['id']
    library = PromptLibrary(store)
    item = next(item for item in library.list()['items'] if item['id'] == 'writing_citation_match')
    library.update(item['id'], item['content'] + '\n未来新指令。', item['version'])
    service.process(store.claim())
    assert store.job(first['job']['id'])['status'] == 'succeeded'
    assert all('未来新指令' not in call[1][0]['content'] for call in gateway.calls)


@pytest.mark.parametrize('outcome', ['succeeded', 'failed', 'cancelled'])
def test_explicit_new_check_supersedes_skip_without_confirming_or_rewriting(environment, outcome):
    store, gateway, service, before = environment
    skipped = service.review_skip(before['id'], before['version'])['review']
    assert skipped['disposition']['status'] == 'skipped' and not skipped['requiresDraftConsent']
    history = service.history(before['id'])
    result = service.queue_citation_match(before['id'], before['version'])
    queued_project = result['project']
    assert queued_project['version'] == before['version']
    assert queued_project['sections'] == before['sections']
    assert service.history(before['id']) == history
    assert queued_project['review']['disposition'] is None
    assert queued_project['review']['requiresDraftConsent'] is True
    assert not queued_project['review']['authorConfirmed']
    assert not gateway.calls
    assert service.queue_citation_match(before['id'], before['version'])['runId'] == result['runId']
    if outcome == 'failed':
        gateway.fail_calls = {1}
    if outcome == 'cancelled':
        store.cancel(result['job']['id'])
    else:
        service.process(store.claim())
    assert store.job(result['job']['id'])['status'] == outcome
    current = service.get(before['id'])
    assert current['review']['disposition'] is None
    assert not current['review']['authorConfirmed']
    if outcome != 'succeeded':
        assert current['version'] == before['version'] and current['sections'] == before['sections']


def test_failed_check_preflight_preserves_explicit_skip(environment):
    store, gateway, service, before = environment
    skipped = service.review_skip(before['id'], before['version'])['project']
    gateway.config['contextChars'] = 1000
    with pytest.raises(ValueError, match='不能省略证据'):
        service.queue_citation_match(before['id'], before['version'])
    current = service.get(before['id'])
    assert current['review'] == skipped['review']
    assert current['reviewDisposition'] == skipped['reviewDisposition']
    assert current['version'] == before['version'] and not current['runs']
    assert not gateway.calls


def test_cancel_during_request_does_not_save_or_publish_late_review(environment):
    store, gateway, service, project = environment
    result, claimed = queued(environment)
    gateway.callback = lambda: store.cancel(result['job']['id'])
    service.process(claimed)
    assert store.job(result['job']['id'])['status'] == 'cancelled'
    assert service.get(project['id'])['version'] == project['version']
    assert len(gateway.calls) == 1
    with store.connect() as db:
        assert not db.execute('SELECT 1 FROM writing_citation_match_steps WHERE result_json IS NOT NULL').fetchone()
        assert db.execute('SELECT validation_status FROM writing_model_interactions').fetchone()[0] == 'discarded'


def test_manual_edit_during_request_blocks_results_and_further_model_calls(environment):
    store, gateway, service, project = environment
    result, claimed = queued(environment)
    gateway.callback = lambda: service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'content': '人工后来修改，必须保留。'})
    service.process(claimed)
    assert store.job(result['job']['id'])['status'] == 'failed'
    assert len(gateway.calls) == 1
    assert service.get(project['id'])['sections'][0]['content'] == '人工后来修改，必须保留。'
    assert not service.get(project['id'])['runs'][0]['applied']


def test_invalid_sentence_ids_fail_without_partial_citation_changes(environment):
    store, gateway, service, before = environment
    result, claimed = queued(environment)
    gateway.invalid = True
    service.process(claimed)
    assert store.job(result['job']['id'])['status'] == 'failed'
    after = service.get(before['id'])
    assert after['version'] == before['version']
    assert after['sections'] == before['sections']
    with store.connect() as db:
        assert db.execute('SELECT validation_status FROM writing_model_interactions').fetchone()[0] == 'invalid'


def test_queue_rejects_bad_quote_before_any_task_or_model_call(environment):
    store, gateway, service, project = environment
    with store.connect() as db:
        current = service._load(db, project['id'])
        current['sections'][0]['evidence'][0]['quote'] = '伪造摘录'
        service._save(db, current, 'controlled_invalid_evidence')
    with pytest.raises(ValueError, match='逐字定位'):
        service.queue_citation_match(project['id'], current['version'])
    assert not gateway.calls
    assert not [job for job in store.jobs()['jobs'] if job['kind'] == 'writing_citation_match']


@pytest.mark.parametrize('legacy', [False, True], ids=['selective-review', 'historical-no-scope'])
def test_empty_evidence_distinguishes_facts_from_transitions_only_under_new_scope(environment, legacy):
    store, gateway, service, project = environment
    with store.connect() as db:
        current = service._load(db, project['id'])
        for index, section in enumerate(current['sections']):
            section['evidence'] = []
            section['content'] = '某机构已发布十份报告。' if index == 0 else '下面回顾前文。'
        service._save(db, current, 'controlled_supplement')
    result = service.queue_citation_match(project['id'], current['version'])
    if legacy:
        from server.store import dumps
        from server.writing_citation_matching import matching_messages
        with store.connect() as db:
            frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (result['runId'],)).fetchone()[0])
            frozen.pop('citationReviewPolicy', None)
            frozen.pop('continueOnReviewFailure', None)
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), result['runId']))
            for row in db.execute('SELECT section_id,batch_index,input_json FROM writing_citation_match_steps WHERE run_id=?', (result['runId'],)).fetchall():
                prepared = json.loads(row['input_json'])
                prepared['packet'].pop('reviewScope')
                prepared['messages'] = matching_messages(prepared['packet'], instruction=frozen['instructions'])
                db.execute('UPDATE writing_citation_match_steps SET input_json=? WHERE run_id=? AND section_id=? AND batch_index=?',
                           (dumps(prepared), result['runId'], row['section_id'], row['batch_index']))
    else:
        def selective(config, messages):
            packet = json.loads(messages[1]['content'])
            gateway.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
            assert not packet['evidence'] and 'reviewScope' in packet
            return {'text': json.dumps({'assignments': [{'sentenceId': sentence['sentenceId'], 'evidenceIds': [],
                'support': 'not_required' if sentence['text'] == '下面回顾前文。' else 'unsupported',
                'supportBasis': 'none', 'reason': '内容过渡无需引用；具体机构与数字缺少已登记原文依据。'}
                for sentence in packet['sentences']]}, ensure_ascii=False), 'usage': {}}
        gateway.complete = selective
    service.process(store.claim())
    assert store.job(result['job']['id'])['status'] == 'succeeded'
    after = service.get(project['id'])
    assert len(gateway.calls) == (0 if legacy else 2)
    assert (after['review'] is None) if legacy else (after['review']['version'] == after['version'])
    assert [section['content'] for section in after['sections']] == [section['content'] for section in current['sections']]
    assert all(not section['evidence'] and '[E' not in section['content'] for section in after['sections'])
    assert after['sections'][0]['citationBindingState'] == 'needs_review'
    assert after['sections'][1]['citationBindingState'] == ('needs_review' if legacy else 'matched')
    assert {item['status'] for item in after['sections'][1]['citationBindings']['items']} == ({'unsupported'} if legacy else {'not_required'})


def test_quote_registry_over_input_budget_fails_preflight_without_queue(environment):
    store, gateway, service, project = environment
    gateway.config['contextChars'] = 1000
    with pytest.raises(ValueError, match='不能省略证据'):
        service.queue_citation_match(project['id'], project['version'])
    assert not gateway.calls


def test_new_review_of_historical_extended_draft_keeps_uncited_fact_issues_visible(environment):
    store, gateway, service, project = environment
    with store.connect() as db:
        old = service._load(db, project['id'])
        old.pop('workflowVersion', None)
        old['mode'] = 'extended'
        for section in old['sections']:
            section['mode'] = 'inherit'
            section['extendedContentPolicy'] = 'retain-as-prose-v2'
        service._save(db, old, 'historical_extended_fixture')
    before = service.get(project['id'])
    queued = service.queue_citation_match(project['id'], before['version'])
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    after = WritingService(store, gateway).get(project['id'])
    assert 'workflowVersion' not in after
    assert all(section['citationReviewPolicy'] == 'deferred-to-check-v1' for section in after['sections'])
    assert any(issue['code'] == 'citation_sentence_unsupported' for issue in after['review']['issues'])
    assert after['review']['coverage']['unresolvedSentences'] == 2
    assert all(strip_citations(new['content']) == strip_citations(old['content'])
               for new, old in zip(after['sections'], before['sections']))
