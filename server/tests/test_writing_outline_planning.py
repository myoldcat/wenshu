"""Evidence-bound outline planning with isolated stores and controlled models.

The fake support decisions verify gates/checkpoints, never model accuracy.
"""
import copy
import json

import pytest
from pydantic import ValidationError

from server.prompts import prompt_text, snapshot
from server.store import Store
from server.tests.test_writing_integrity import add_source
from server.writing import WritingConflict, WritingService
from server.writing_api import PatchProject, SectionEdit, SectionOutline
from server.writing_citation_matching import matching_policy
from server.writing_outline_planning import (
    LEGACY_OUTLINE_PLANNING_PROTOCOL, OUTLINE_ISSUE_REVIEW_INSTRUCTIONS, OUTLINE_PLANNING_PROTOCOL,
    prepare_strict_outline,
)
from server.writing_reference_scope import scope_prepared


SECRET = 'outline-private-provider-credential'


class OutlineGateway:
    def __init__(self):
        self.config = {'id': 'outline-model', 'version': 3, 'name': '纲要隔离测试', 'model': 'controlled',
                       'contextChars': 40000, 'maxOutputTokens': 4096, 'apiKey': SECRET}
        self.generations, self.reviews = [], []
        self.callback, self.transform = None, None
        self.fail_reviews = set()
        self.goal_status, self.goal_basis = 'supported', 'paraphrase'
        self.issue_reviews, self.fail_issue_reviews = [], set()
        self.issue_dispositions = {}
        self.issue_transform, self.issue_callback = None, None

    def get_preferences(self):
        return {'defaultModelId': self.config['id']}

    def resolve(self, model_id=None, version=None):
        assert model_id == self.config['id']
        return {**self.config, 'version': version if version is not None else self.config['version']}

    @staticmethod
    def outline(payload):
        block = payload['blocks'][0]
        return {'protocol': payload['outlinePlanningProtocol'], 'sections': [
            {'title': '系泊雷的机理', 'goal': '说明系泊雷由锚固定并在船只碰撞时引爆。', 'mode': 'strict', 'targetWords': 300,
             'planningEvidence': [{'blockId': block['blockId'], 'quote': block['text']}]},
            {'title': '感应雷的探测', 'goal': '概括感应雷位于海床且更难探测的特点。', 'mode': 'strict', 'targetWords': 300,
             'planningEvidence': [{'blockId': block['blockId'], 'quote': block['text']}]},
        ], 'issues': []}

    def complete(self, config, messages):
        payload = json.loads(messages[1]['content'])
        assert sum(len(item['content']) for item in messages) <= config['contextChars']
        if payload['task'] == 'writing_outline':
            self.generations.append(copy.deepcopy(payload))
            result = self.outline(payload)
            if self.transform:
                self.transform(result)
        elif payload['task'] == 'writing_outline_issue_review':
            self.issue_reviews.append((copy.deepcopy(config), copy.deepcopy(messages)))
            if self.issue_callback:
                callback, self.issue_callback = self.issue_callback, None
                callback()
            if len(self.issue_reviews) in self.fail_issue_reviews:
                raise RuntimeError(SECRET + ': confidential issue review failure')
            result = {'decisions': [{'issueId': item['issueId'],
                'disposition': self.issue_dispositions.get(item['issueId'], 'blocking'),
                'sectionIds': [payload['sections'][0]['sectionId']],
                'reason': '受控测试显式判断该提示与当前规划的关系，不用提示中的关键词推断状态。'} for item in payload['issues']]}
            if self.issue_transform:
                self.issue_transform(result)
        else:
            assert payload['task'] == 'writing_citation_match'
            assert payload['reviewPurpose'] == 'outline_scope' and payload['mode'] == 'strict'
            self.reviews.append((copy.deepcopy(config), copy.deepcopy(payload)))
            if self.callback:
                callback, self.callback = self.callback, None
                callback()
            if len(self.reviews) in self.fail_reviews:
                raise RuntimeError(SECRET + ': confidential failure')
            assignments = []
            for sentence in payload['sentences']:
                goal = payload['unitRoles'][sentence['sentenceId']] == 'goal'
                status = self.goal_status if goal else 'supported'
                ids = [] if status in ('not_required', 'unsupported') else [payload['evidence'][0]['evidenceId']]
                basis = 'none' if not ids else self.goal_basis if goal else 'paraphrase'
                assignments.append({'sentenceId': sentence['sentenceId'], 'evidenceIds': ids,
                    'support': status, 'supportBasis': basis,
                    'reason': '受控测试按预设目录范围与原文关系判断，仅验证程序流程与缺口保留。'})
            result = {'assignments': assignments}
        return {'text': json.dumps(result, ensure_ascii=False), 'usage': {'total_tokens': 17}}


@pytest.fixture
def planning(tmp_path):
    store, gateway = Store(tmp_path / 'strict-outline-planning'), OutlineGateway()
    service = WritingService(store, gateway)
    source = add_source(store, text='伊朗拥有系泊雷。系泊雷由锚固定，船只碰撞时引爆。伊朗还拥有感应雷，位于海床且更难探测。')
    project = service.create({'title': '资料内水雷纲要', 'mode': 'strict', 'brief': {'topic': '水雷机理与探测'},
        'sourceIds': [source], 'modelId': gateway.config['id']})
    project = service.outline(project['id'], project['version'], [
        {'title': '原有空白纲要', 'goal': '保留当前规划直到新结果通过复核', 'mode': 'strict', 'targetWords': 300}])
    with store.connect() as db:
        messages, blocks = service._messages(project, 'outline', None, gateway.config, db, outline_policy=None)
    frozen = {'projectId': project['id'], 'title': project['title'], 'brief': copy.deepcopy(project['brief']),
        'sources': copy.deepcopy(project['sources']), 'mode': 'strict', 'effectiveMode': 'strict',
        'messages': messages, 'blocks': blocks, 'outlinePlanningProtocol': OUTLINE_PLANNING_PROTOCOL,
        'outlineIssueReviewInstructions': OUTLINE_ISSUE_REVIEW_INSTRUCTIONS,
        'citationMatchMode': 'strict', 'citationMatchPolicy': matching_policy('strict'),
        'citationMatchInstructions': prompt_text('writing_citation_match')}
    return store, gateway, service, project, frozen


def raw_result(planning):
    return planning[1].outline(json.loads(planning[4]['messages'][1]['content']))


def queue_historical_outline(planning, project=None):
    """Persist the actual former evidence-plan contract for compatibility tests.

    Public queues now freeze abstract outlines. Reconstruct the old request,
    instructions and prompt snapshot before any worker call, as an existing
    historical job would have stored them; keep all old validation gates.
    """
    store, gateway, service, initial, _ = planning
    project = project or initial
    run = service.queue(project['id'], project['version'], 'outline')
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        messages, blocks = service._messages(project, 'outline', None, gateway.config, db, outline_policy=None)
        frozen.pop('outlinePolicy', None)
        frozen.update(messages=messages, blocks=blocks,
            outlinePlanningProtocol=OUTLINE_PLANNING_PROTOCOL,
            outlineIssueReviewInstructions=OUTLINE_ISSUE_REVIEW_INSTRUCTIONS,
            citationMatchInstructions=prompt_text('writing_citation_match', db=db),
            citationMatchMode='strict', citationMatchPolicy=matching_policy('strict'))
        frozen = prepare_strict_outline(frozen, context_chars=gateway.config['contextChars'])
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), run['runId']))
        db.execute('UPDATE job_prompt_snapshots SET snapshot_json=? WHERE job_id=?',
                   (json.dumps(snapshot(db, ('writing_outline_strict', 'writing_citation_match'))), run['job']['id']))
    return run


def queued(planning):
    store = planning[0]
    run = queue_historical_outline(planning)
    claimed = store.claim()
    assert claimed['id'] == run['job']['id']
    return run, claimed


def test_registers_unique_original_quotes_with_program_ids_and_pinned_source_versions(planning):
    _, _, service, project, frozen = planning
    result = raw_result(planning)
    before = copy.deepcopy((frozen, result, project))
    candidate = service._validate_planned_outline(frozen, result)
    assert candidate['outlinePlanningPending'] and candidate['outlinePlanningState'] == 'pending'
    assert len({section['id'] for section in candidate['sections']}) == 2
    assert all(section['id'] != project['sections'][0]['id'] for section in candidate['sections'])
    for section in candidate['sections']:
        reference = section['planningEvidence'][0]
        assert reference['id'] == 'P1'
        block = frozen['blocks'][0]
        assert (reference['sourceId'], reference['revisionId'], reference['blockId']) == tuple(block[key] for key in ('sourceId', 'revisionId', 'blockId'))
        assert block['text'][reference['start']:reference['end']] == reference['quote']
    assert (frozen, result, project) == before


def test_old_frozen_requests_are_not_upgraded_or_given_planning_evidence(planning):
    _, _, service, _, frozen = planning
    frozen = copy.deepcopy(frozen)
    frozen.pop('outlinePlanningProtocol')
    request = json.loads(frozen['messages'][1]['content'])
    request.pop('outlinePlanningProtocol', None)
    frozen['messages'][1]['content'] = json.dumps(request)
    legacy = {'sections': [{'title': '旧纲要', 'goal': '旧目标', 'targetWords': 300, 'mode': 'strict'}]}
    assert service._validate_planned_outline(frozen, legacy) is None
    candidate = service._candidate({'kind': 'outline'}, frozen, {'text': json.dumps(legacy)})
    assert 'outlinePlanningState' not in candidate
    assert 'planningEvidence' not in candidate['sections'][0]


@pytest.mark.parametrize('mutate', [
    lambda value: value.pop('protocol'),
    lambda value: value.update(protocol='old'),
    lambda value: value['sections'][0].update(id='invented'),
    lambda value: value['sections'][0].update(mode='extended'),
    lambda value: value['sections'][0].update(goal=' '),
    lambda value: value['sections'][0]['planningEvidence'][0].update(blockId='unknown'),
    lambda value: value['sections'][0]['planningEvidence'][0].update(quote='不存在的引文'),
    lambda value: value['sections'][0]['planningEvidence'][0].update(start=0),
    lambda value: value['sections'][0]['planningEvidence'].append(copy.deepcopy(value['sections'][0]['planningEvidence'][0])),
])
def test_structural_protocol_scope_and_original_quote_failures_never_produce_reviewed_candidates(planning, mutate):
    _, _, service, _, frozen = planning
    result = raw_result(planning)
    mutate(result)
    with pytest.raises(ValueError):
        service._validate_planned_outline(frozen, result)


@pytest.mark.parametrize('change', [{'sourceId': 'wrong-source'}, {'revisionId': 'wrong-version'}])
def test_frozen_blocks_cannot_cross_pinned_source_or_revision_scope(planning, change):
    _, _, service, _, frozen = planning
    changed = copy.deepcopy(frozen)
    changed['blocks'][0].update(change)
    with pytest.raises(ValueError, match='冻结来源版本'):
        service._validate_planned_outline(changed, raw_result(planning))


def test_ambiguous_quotes_and_conflicting_duplicate_block_ids_are_rejected(planning):
    _, _, service, _, frozen = planning
    changed, result = copy.deepcopy(frozen), raw_result(planning)
    changed['blocks'][0]['text'] = '重复。重复。'
    result['sections'][0]['planningEvidence'][0]['quote'] = '重复。'
    with pytest.raises(ValueError, match='重复'):
        service._validate_planned_outline(changed, result)
    changed = copy.deepcopy(frozen)
    changed['blocks'].append(dict(changed['blocks'][0], text='冲突原文'))
    with pytest.raises(ValueError, match='重复'):
        service._validate_planned_outline(changed, raw_result(planning))


def test_insufficient_empty_outline_is_a_candidate_and_preserves_existing_outline(planning):
    _, _, service, project, frozen = planning
    result = {'protocol': OUTLINE_PLANNING_PROTOCOL, 'sections': [], 'issues': ['现有摘录没有说明要求的清扫设备参数。']}
    candidate = service._validate_planned_outline(frozen, result)
    assert candidate['outlinePlanningState'] == 'insufficient' and not candidate['outlinePlanningPending']
    assert candidate['sections'] == [] and candidate['outlinePlanningIssues'][0]['message'] == result['issues'][0]
    assert service.get(project['id'])['sections'] == project['sections']
    with pytest.raises(ValueError, match='具体缺口'):
        service._validate_planned_outline(frozen, dict(result, issues=[]))


def test_real_queue_generation_independent_review_and_auto_publication_keep_planning_anchors(planning):
    store, gateway, service, before, _ = planning
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.generations) == 1 and len(gateway.reviews) == 2
    after = service.get(before['id'])
    assert after['version'] == before['version'] + 1 and after['runs'][0]['applied']
    for chapter in after['sections']:
        assert chapter['planningEvidence'] and chapter['planningReview']['state'] == 'supported'
        assert chapter['planningReview']['purpose'] == 'outline_scope'
        assert chapter['planningReview']['content'] == chapter['title'] + '\n' + chapter['goal']
        assert {assignment['supportBasis'] for assignment in chapter['planningReview']['assignments']} == {'paraphrase'}
    with store.connect() as db:
        calls = db.execute('SELECT * FROM writing_model_interactions WHERE run_id=?', (run['runId'],)).fetchall()
        assert len(calls) == 3 and all(row['validation_status'] == 'valid' for row in calls)
        assert SECRET not in '\n'.join(str(dict(row)) for row in calls)


def test_missing_scope_support_keeps_candidate_and_original_outline(planning):
    store, gateway, service, before, _ = planning
    gateway.goal_status = 'partial'
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert store.job(run['job']['id'])['stage'] == 'candidate'
    after = service.get(before['id'])
    assert after['version'] == before['version'] and after['sections'] == before['sections']
    candidate = after['runs'][0]['candidate']
    assert candidate['outlinePlanningState'] == 'needs_review' and not candidate['outlinePlanningPending']
    assert len(candidate['outlinePlanningIssues']) == 2
    assert {issue['field'] for issue in candidate['outlinePlanningIssues']} == {'goal'}


@pytest.mark.parametrize('status,basis', [('not_required', 'none'), ('supported', 'inference')])
def test_titles_and_goals_cannot_bypass_review_as_organisational_or_supported_inference(planning, status, basis):
    store, gateway, service, before, _ = planning
    gateway.goal_status, gateway.goal_basis = status, basis
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'failed'
    after = service.get(before['id'])
    assert after['version'] == before['version'] and after['sections'] == before['sections']
    assert after['runs'][0]['candidate']['outlinePlanningPending']


def test_retry_reuses_generated_outline_and_completed_scope_review(planning):
    store, gateway, service, before, _ = planning
    gateway.fail_reviews = {2}
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'failed'
    current = service.get(before['id'])
    assert current['version'] == before['version']
    assert current['runs'][0]['candidate']['outlinePlanningCheckpoints']
    gateway.config['version'] = 4
    store.retry(run['job']['id'])
    service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.generations) == 1 and len(gateway.reviews) == 3
    assert [config['version'] for config, _ in gateway.reviews] == [3, 3, 3]
    assert gateway.reviews[1][1]['sectionId'] == gateway.reviews[2][1]['sectionId']


def test_cancelled_scope_request_does_not_apply_or_continue_other_chapters(planning):
    store, gateway, service, before, _ = planning
    run, claimed = queued(planning)
    gateway.callback = lambda: store.cancel(run['job']['id'])
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'cancelled' and len(gateway.reviews) == 1
    assert service.get(before['id'])['sections'] == before['sections']


def test_changed_project_after_scope_request_stops_further_calls_and_preserves_new_requirements(planning):
    store, gateway, service, before, _ = planning
    run, claimed = queued(planning)
    gateway.callback = lambda: service.patch(before['id'], before['version'], {'brief': {'topic': '人工更新要求'}})
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'failed' and len(gateway.reviews) == 1
    after = service.get(before['id'])
    assert after['brief'] == {'topic': '人工更新要求'} and after['sections'] == before['sections']


def test_no_registered_planning_quotes_are_explicitly_unsupported_without_review_calls(planning):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: [chapter.update(planningEvidence=[]) for chapter in result['sections']]
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['stage'] == 'candidate' and not gateway.reviews
    after = service.get(before['id'])
    assert after['version'] == before['version']
    assert after['runs'][0]['candidate']['outlinePlanningState'] == 'needs_review'


def test_generator_reported_material_gap_is_not_hidden_by_positive_scope_reviews(planning):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: result.update(issues=['当前原文不能覆盖用户要求的设备参数。'])
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['stage'] == 'candidate'
    after = service.get(before['id'])
    assert after['version'] == before['version']
    assert after['runs'][0]['candidate']['outlinePlanningState'] == 'needs_review'
    assert '设备参数' in after['runs'][0]['candidate']['outlinePlanningIssues'][0]['message']
    assert len(gateway.issue_reviews) == 1
    assert after['runs'][0]['candidate']['outlineIssueReviews'][0]['disposition'] == 'blocking'
    with pytest.raises(WritingConflict, match='无法支持'):
        service.apply(before['id'], run['runId'], before['version'])


def test_v2_explicit_review_allows_advisory_notes_and_preserves_every_original_issue(planning):
    store, gateway, service, before, _ = planning
    notes = ['清扫设备参数没有依据，未规划该项，也非本次要求。', '某些原文表述冲突，本章目标已限定只介绍系泊方式。']
    gateway.transform = lambda result: result.update(issues=notes.copy())
    gateway.issue_dispositions = {'I1': 'outside_scope', 'I2': 'addressed'}
    run, claimed = queued(planning)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
    assert frozen['outlinePlanningProtocol'] == OUTLINE_PLANNING_PROTOCOL
    assert frozen['outlineIssueReviewInstructions'] == OUTLINE_ISSUE_REVIEW_INSTRUCTIONS
    service.process(claimed)
    after = service.get(before['id'])
    candidate = after['runs'][0]['candidate']
    assert after['runs'][0]['applied'] and after['version'] == before['version'] + 1
    assert candidate['outlinePlanningState'] == 'supported' and candidate['outlinePlanningIssues'] == []
    assert candidate['issues'] == notes and [item['message'] for item in candidate['outlineIssueReviews']] == notes
    assert [item['disposition'] for item in candidate['outlineIssueReviews']] == ['outside_scope', 'addressed']
    assert len(gateway.issue_reviews) == 1
    packet = json.loads(gateway.issue_reviews[0][1][1]['content'])
    assert packet['brief'] == before['brief'] and packet['title'] == before['title']
    assert [item['goal'] for item in packet['sections']] == [item['goal'] for item in after['sections']]
    with store.connect() as db:
        rows = db.execute('SELECT * FROM writing_model_interactions WHERE run_id=?', (run['runId'],)).fetchall()
    assert len(rows) == 4 and all(row['validation_status'] == 'valid' for row in rows)


def test_advisory_note_review_never_overrides_an_unresolved_chapter_scope(planning):
    store, gateway, service, before, _ = planning
    gateway.goal_status = 'partial'
    gateway.transform = lambda result: result.update(issues=['图片排版留待正文完成后处理。'])
    gateway.issue_dispositions = {'I1': 'outside_scope'}
    run, claimed = queued(planning)
    service.process(claimed)
    after = service.get(before['id'])
    candidate = after['runs'][0]['candidate']
    assert after['version'] == before['version'] and not after['runs'][0]['applied']
    assert candidate['outlinePlanningState'] == 'needs_review'
    assert len(candidate['outlinePlanningIssues']) == 2 and all(item['field'] == 'goal' for item in candidate['outlinePlanningIssues'])


@pytest.mark.parametrize('mutate', [
    lambda value: value['decisions'].pop(),
    lambda value: value['decisions'].append(copy.deepcopy(value['decisions'][0])),
    lambda value: value['decisions'][0].update(issueId='invented'),
    lambda value: value['decisions'][0].update(disposition='uncertain'),
    lambda value: value['decisions'][0].update(sectionIds=['unknown']),
    lambda value: value['decisions'][0].update(disposition='addressed', sectionIds=[]),
    lambda value: value['decisions'][0].update(reason=' '),
    lambda value: value['decisions'][0].update(replacementGoal='自行改写的目标'),
])
def test_incomplete_or_invalid_issue_review_cannot_release_an_outline(planning, mutate):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: result.update(issues=['已排除事项也必须取得明确复核。'])
    gateway.issue_dispositions = {'I1': 'outside_scope'}
    gateway.issue_transform = mutate
    run, claimed = queued(planning)
    service.process(claimed)
    after = service.get(before['id'])
    assert store.job(run['job']['id'])['status'] == 'failed'
    assert after['version'] == before['version'] and after['sections'] == before['sections']
    assert after['runs'][0]['candidate']['outlinePlanningPending']
    with store.connect() as db:
        assert db.execute('SELECT validation_status FROM writing_model_interactions WHERE run_id=? ORDER BY started_at DESC,id DESC LIMIT 1',
                          (run['runId'],)).fetchone()[0] == 'invalid'


def test_issue_review_retry_keeps_frozen_model_instruction_and_completed_chapter_reviews(planning, monkeypatch):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: result.update(issues=['未规划的配图版式提示。'])
    gateway.issue_dispositions = {'I1': 'outside_scope'}
    gateway.fail_issue_reviews = {1}
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'failed'
    gateway.config['version'] = 4
    monkeypatch.setattr('server.writing_outline_planning.OUTLINE_ISSUE_REVIEW_INSTRUCTIONS', '更新后的实时指令不应进入旧任务')
    store.retry(run['job']['id'])
    service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(gateway.generations) == 1 and len(gateway.reviews) == 2 and len(gateway.issue_reviews) == 2
    assert [config['version'] for config, _ in gateway.issue_reviews] == [3, 3]
    assert all(messages[0]['content'] == OUTLINE_ISSUE_REVIEW_INSTRUCTIONS for _, messages in gateway.issue_reviews)


@pytest.mark.parametrize('action', ['cancel', 'change'])
def test_late_issue_review_cannot_apply_after_cancellation_or_requirements_change(planning, action):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: result.update(issues=['未规划事项。'])
    gateway.issue_dispositions = {'I1': 'outside_scope'}
    run, claimed = queued(planning)
    gateway.issue_callback = (lambda: store.cancel(run['job']['id'])) if action == 'cancel' else (
        lambda: service.patch(before['id'], before['version'], {'brief': {'topic': '作者更新后的要求'}}))
    service.process(claimed)
    after = service.get(before['id'])
    assert store.job(run['job']['id'])['status'] == ('cancelled' if action == 'cancel' else 'failed')
    assert after['sections'] == before['sections'] and not after['runs'][0]['applied']


def test_v1_frozen_outline_retains_all_notes_blocking_and_never_uses_v2_review(planning):
    store, gateway, service, before, _ = planning
    note = '该事项已排除于实际规划，但旧版协议仍保留阻断。'
    gateway.transform = lambda result: result.update(issues=[note])
    gateway.fail_reviews = {2}
    run, claimed = queued(planning)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        frozen['outlinePlanningProtocol'] = LEGACY_OUTLINE_PLANNING_PROTOCOL
        frozen.pop('outlineIssueReviewInstructions')
        packet = json.loads(frozen['messages'][1]['content'])
        packet['outlinePlanningProtocol'] = packet['outputSchema']['protocol'] = LEGACY_OUTLINE_PLANNING_PROTOCOL
        frozen['messages'][1]['content'] = json.dumps(packet)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), run['runId']))
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'failed'
    store.retry(run['job']['id'])
    service.process(store.claim())
    after = service.get(before['id'])
    candidate = after['runs'][0]['candidate']
    assert candidate['outlinePlanningProtocol'] == LEGACY_OUTLINE_PLANNING_PROTOCOL
    assert candidate['outlinePlanningState'] == 'needs_review' and candidate['outlinePlanningIssues'] == [{'status': 'uncertain', 'message': note}]
    assert not gateway.issue_reviews and 'outlineIssueReviews' not in candidate
    assert len(gateway.generations) == 1 and len(gateway.reviews) == 3
    assert after['version'] == before['version'] and after['sections'] == before['sections']


def test_v2_missing_frozen_issue_instruction_never_substitutes_current_instruction(planning):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: result.update(issues=['尚需明确复核的提示。'])
    run, claimed = queued(planning)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        frozen.pop('outlineIssueReviewInstructions')
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (json.dumps(frozen), run['runId']))
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'failed' and not gateway.issue_reviews
    assert service.get(before['id'])['sections'] == before['sections']


@pytest.mark.parametrize('protocol', [LEGACY_OUTLINE_PLANNING_PROTOCOL, OUTLINE_PLANNING_PROTOCOL])
def test_generator_cannot_upgrade_or_downgrade_queue_time_protocol(planning, protocol):
    _, _, service, _, frozen = planning
    frozen = copy.deepcopy(frozen)
    frozen['outlinePlanningProtocol'] = protocol
    packet = json.loads(frozen['messages'][1]['content'])
    packet['outlinePlanningProtocol'] = protocol
    frozen['messages'][1]['content'] = json.dumps(packet)
    result = raw_result(planning)
    result['protocol'] = OUTLINE_PLANNING_PROTOCOL if protocol == LEGACY_OUTLINE_PLANNING_PROTOCOL else LEGACY_OUTLINE_PLANNING_PROTOCOL
    with pytest.raises(ValueError, match='冻结的原文规划格式'):
        service._validate_planned_outline(frozen, result)


@pytest.mark.parametrize('field,new_value', [('title', '人工调整的新标题'), ('goal', '人工调整后的目标。'), ('mode', 'extended')])
def test_planning_metadata_cannot_be_injected_and_author_edits_invalidate_only_the_changed_chapter(planning, field, new_value):
    store, _, service, before, _ = planning
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    reviewed = service.get(before['id'])
    fake_review = {'state': 'supported', 'content': '伪造已验证状态'}
    for schema, body in (
        (PatchProject, {'expectedVersion': reviewed['version'], 'planningReview': fake_review}),
        (SectionEdit, {'expectedVersion': reviewed['version'], 'planningEvidence': []}),
        (SectionOutline, {'title': '标题', 'planningReview': fake_review}),
    ):
        with pytest.raises(ValidationError):
            schema.model_validate(body)
    first, untouched = reviewed['sections']
    outline = [{key: chapter[key] for key in ('id', 'title', 'goal', 'mode', 'targetWords')}
               for chapter in reviewed['sections']]
    outline[0][field] = new_value
    changed = service.outline(reviewed['id'], reviewed['version'], outline)
    assert 'planningEvidence' not in changed['sections'][0] and 'planningReview' not in changed['sections'][0]
    assert changed['sections'][0]['outlinePlanningState'] == 'stale'
    assert changed['sections'][1]['planningEvidence'] == untouched['planningEvidence']
    assert changed['sections'][1]['planningReview'] == untouched['planningReview']
    with store.connect() as db:
        messages, _ = service._messages(changed, 'section', changed['sections'][0], service.models.config, db)
    assert 'plannedContent' not in json.loads(messages[1]['content'])
    # A subsequent project-wide requirements change also invalidates the other
    # chapter; no saved review is carried across a different writing brief.
    changed = service.patch(changed['id'], changed['version'], {'brief': {'topic': '新的写作范围'}})
    assert all('planningReview' not in chapter and 'planningEvidence' not in chapter for chapter in changed['sections'])


def test_reviewed_planning_blocks_take_priority_and_one_shot_uses_only_chapter_aliases(planning):
    store, gateway, service, before, frozen = planning
    original = frozen['blocks'][0]
    distractor = add_source(store, text=('系泊雷的机理说明系泊雷由锚固定并在船只碰撞时引爆。' * 8),
                            url='https://example.test/outline-extra-source')
    changed = service.patch(before['id'], before['version'], {'sourceIds': [*before['sourceIds'], distractor]})
    gateway.transform = lambda result: [chapter.update(planningEvidence=[{'blockId': next(
        block['blockId'] for block in gateway.generations[-1]['blocks']
        if block['sourceId'] == original['sourceId'] and block['text'] == original['text']), 'quote': original['text']}])
                                        for chapter in result['sections']]
    run = queue_historical_outline(planning, changed)
    service.process(store.claim())
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    reviewed = service.get(before['id'])
    chapter = reviewed['sections'][0]
    with store.connect() as db:
        messages, blocks = service._messages(reviewed, 'section', chapter, gateway.config, db, outline_policy=None)
        unplanned = copy.deepcopy(chapter)
        unplanned.pop('planningReview')
        unplanned.pop('planningEvidence')
        _, unplanned_blocks = service._messages(reviewed, 'section', unplanned, gateway.config, db, outline_policy=None)
        scoped = scope_prepared({'section': copy.deepcopy(chapter), 'messages': messages, 'blocks': blocks,
                                 'effectiveMode': 'strict', 'promptId': 'writing_section_strict'}, 1)
        whole = service._document_messages(reviewed, [scoped], db=db)
    assert unplanned_blocks[0]['blockId'] != original['blockId']
    assert blocks[0]['blockId'] == original['blockId']
    packet = json.loads(messages[1]['content'])
    assert packet['plannedContent']['evidenceBlockIds'] == [original['blockId']]
    whole_chapter = json.loads(whole[1]['content'])['sections'][0]
    aliases = {block['blockId'] for block in whole_chapter['blocks']}
    assert whole_chapter['plannedContent']['evidenceBlockIds'] == ['C01-B001']
    assert set(whole_chapter['plannedContent']['evidenceBlockIds']).issubset(aliases)
    assert original['blockId'] not in whole_chapter['plannedContent']['evidenceBlockIds']
    assert whole_chapter['plannedContent']['title'] == chapter['title']
    assert whole_chapter['plannedContent']['goal'] == chapter['goal']


def test_completed_empty_material_gap_candidate_cannot_auto_or_explicitly_erase_existing_outline(planning):
    store, gateway, service, before, _ = planning
    gateway.transform = lambda result: result.update(sections=[], issues=['现有原文不足以规划所需章节。'])
    run, claimed = queued(planning)
    service.process(claimed)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert store.job(run['job']['id'])['stage'] == 'candidate'
    after = service.get(before['id'])
    assert after['sections'] == before['sections'] and after['version'] == before['version']
    assert after['runs'][0]['candidate']['outlinePlanningState'] == 'insufficient'
    assert not gateway.reviews
    with pytest.raises(WritingConflict, match='无法支持'):
        service.apply(before['id'], run['runId'], before['version'])
    assert service.get(before['id'])['sections'] == before['sections']
