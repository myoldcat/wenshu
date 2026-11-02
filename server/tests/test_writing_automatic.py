"""Automatic workflow acceptance with isolated data and controlled model calls."""
import copy
import json

import pytest

from server.store import Store, dumps
from server.tests.test_writing_integrity import ControlledGateway, add_source
from server.writing import WritingConflict, WritingService


PROSE = '原始证据和固定版本应予保留。'


class AutomaticGateway(ControlledGateway):
    def __init__(self):
        super().__init__()
        self.fail_chapter = None
        self.unsupported_plan = None
        self.unsupported_body = None
        self.generated = []
        self.before_call = None

    def complete(self, config, messages):
        packet = json.loads(messages[-1]['content'])
        self.generated.append((packet['task'], packet.get('section', {}).get('title'), config['version']))
        if self.before_call:
            callback, self.before_call = self.before_call, None
            callback(packet)
        if packet['task'] == 'writing_automatic_material_queries':
            return {'text': dumps({'queries': [packet['topic'][:300]]}), 'usage': {}}
        if packet['task'] == 'writing_citation_match':
            ids = [e['evidenceId'] for e in packet['evidence']]
            unsupported = (packet.get('reviewPurpose') == 'outline_scope' and self.unsupported_plan
                           and self.unsupported_plan in ''.join(s['text'] for s in packet['sentences']))
            unsupported = unsupported or (packet.get('reviewPurpose') != 'outline_scope' and self.unsupported_body) or not ids
            return {'text': dumps({'assignments': [
                {'sentenceId': s['sentenceId'], 'evidenceIds': [] if unsupported else ids,
                 'support': 'unsupported' if unsupported else 'supported',
                 'supportBasis': 'none' if unsupported else 'paraphrase',
                 'reason': '受控独立复核返回。'} for s in packet['sentences']]}), 'usage': {}}
        if packet['task'] == 'writing_outline':
            block = packet['blocks'][0] if packet['blocks'] else None
            sections = [{'title': title, 'goal': '保存原始证据与固定版本。', 'mode': packet['mode'], 'targetWords': 100}
                        for title in ('原始证据', '版本保存')]
            if packet['mode'] == 'strict' and packet.get('outlinePlanningProtocol'):
                for section in sections:
                    section['planningEvidence'] = [{'blockId': block['blockId'], 'quote': block['text']}]
                result = {'protocol': packet['outlinePlanningProtocol'], 'sections': sections, 'issues': []}
            else:
                result = {'sections': sections, 'issues': []}
        else:
            if packet['section']['title'] == self.fail_chapter:
                raise RuntimeError('integrity-private-token: private upstream details')
            evidence = [{'blockId': packet['blocks'][0]['blockId'], 'quote': packet['blocks'][0]['text'], 'claim': PROSE}] if packet['blocks'] else []
            result = {'paragraphs': [{'text': PROSE, 'kind': 'supported' if evidence else 'model_supplement', 'evidence': evidence}],
                      'issues': ['用于最终报告的作者提示。']}
        return {'text': dumps(result), 'usage': {'total_tokens': 7}}


@pytest.fixture
def automatic(tmp_path):
    store, gateway = Store(tmp_path / 'automatic-isolated'), AutomaticGateway()
    service = WritingService(store, gateway)
    source = add_source(store)
    project = service.create({'title': '原始证据管理', 'mode': 'strict', 'modelId': gateway.config['id'],
                              # These cases exercise the sequential chapter workflow.
                              'brief': {'automation': 'automatic', 'targetWords': 10000}, 'sourceIds': [source]})
    return store, gateway, service, project


def run_automatic(env, *, legacy_outline=False):
    store, gateway, service, project = env
    queued = service.queue_automatic(project['id'], project['version'])
    if legacy_outline:
        # Parents frozen before abstract outlines retain their original
        # evidence-plan children and strict scope review on restart.
        with store.connect() as db:
            frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
            frozen.pop('outlinePolicy', None)
            frozen.pop('citationReviewPolicy', None)
            frozen.pop('automaticMaterialPolicy', None)
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    job = store.claim()
    assert job['id'] == queued['job']['id']
    service.process(job)
    current = service.get(project['id'])
    return queued, current


def test_automatic_finishes_without_confirmation_and_reports_separately(automatic):
    store, gateway, service, project = automatic
    queued, current = run_automatic(automatic)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['outlineConfirmed'] and current['outlineApproval']['method'] == 'automatic'
    assert all(s['content'].startswith(PROSE) and not s['manual'] for s in current['sections'])
    report = current['automatic']['report']
    assert report['summary']['generatedSections'] == 2 and report['outputAvailable']
    assert report['projectVersion'] == current['version']
    assert any(i['message'] == '用于最终报告的作者提示。' for i in report['issues'])
    assert all('最终报告' not in s['content'] for s in current['sections'])
    assert current['illustrationReview']['status'] == 'skipped'
    assert not any(r['status'] in ('queued', 'running') for r in current['runs'])
    assert all(version == gateway.config['version'] for _, _, version in gateway.generated)
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone()


def test_strict_unsupported_outline_is_narrowed_before_chapters(automatic):
    store, gateway, service, project = automatic
    gateway.unsupported_plan = '版本保存'
    _, current = run_automatic(automatic, legacy_outline=True)
    assert [s['title'] for s in current['sections']] == ['原始证据']
    assert current['sections'][0]['content'].startswith(PROSE)
    assert not any(task == 'writing_section' and title == '版本保存' for task, title, _ in gateway.generated)
    report = current['automatic']['report']
    assert report['summary']['skippedSections'] == 1
    assert any(i['code'] == 'outline_narrowed' for i in report['issues'])
    rejected = next(r for r in current['runs'] if r['kind'] == 'outline')
    assert rejected['candidate']['outlinePlanningState'] == 'needs_review'
    assert not rejected['applied']  # The original rejected plan stays rejected.


def test_strict_unsupported_prose_is_saved_with_unresolved_support_in_report(automatic):
    _, gateway, _, _ = automatic
    gateway.unsupported_body = True
    _, current = run_automatic(automatic)
    assert all(s['content'] == PROSE and s['citationBindingState'] == 'needs_review' for s in current['sections'])
    assert current['automatic']['report']['summary']['generatedSections'] == 2
    assert current['automatic']['report']['summary']['skippedSections'] == 0
    assert current['automatic']['report']['outputAvailable']
    assert any(i['code'] == 'citation_support' for i in current['automatic']['report']['issues'])
    assert all(r['applied'] for r in current['runs'] if r['kind'] == 'section')


def test_failed_chapter_continues_and_never_exposes_provider_details(automatic):
    _, gateway, _, _ = automatic
    gateway.fail_chapter = '原始证据'
    _, current = run_automatic(automatic)
    assert not current['sections'][0]['content'] and current['sections'][1]['content'].startswith(PROSE)
    report = current['automatic']['report']
    assert report['summary']['generatedSections'] == report['summary']['skippedSections'] == 1
    assert 'integrity-private-token' not in dumps(report) and 'private upstream' not in dumps(report)
    assert any(i.get('sectionTitle') == '原始证据' for i in report['issues'])


def test_cancelled_queued_parent_gets_terminal_report(automatic):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    store.cancel(queued['job']['id'])
    current = service.get(project['id'])
    assert current['automatic']['status'] == 'cancelled'
    assert current['automatic']['report']['status'] == 'cancelled'
    assert not gateway.generated
    with store.connect() as db:
        saved = json.loads(db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
    assert saved['automatic']['report']['status'] == 'cancelled'


def test_cancellation_during_outline_stops_further_model_phases(automatic):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    gateway.before_call = lambda _: store.cancel(queued['job']['id'])
    service.process(store.claim())
    current = service.get(project['id'])
    assert current['automatic']['status'] == 'cancelled'
    assert len(gateway.generated) == 1
    assert not current['sections']
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone()


def test_edit_during_generation_preserves_new_settings_and_ends_with_report(automatic):
    _, gateway, service, project = automatic

    def edit_current_version(_):
        current = service.get(project['id'])
        service.patch(project['id'], current['version'], {'title': '人工修改后的标题'})

    gateway.before_call = edit_current_version
    _, current = run_automatic(automatic)
    assert current['title'] == '人工修改后的标题' and not current['sections']
    assert any(i['code'] == 'project_changed' for i in current['automatic']['report']['issues'])


def test_empty_scope_uses_extended_mode_and_completes_with_material_gap_report(automatic):
    store, gateway, service, project = automatic
    empty = service.create({'title': '没有材料', 'mode': 'strict', 'modelId': gateway.config['id'],
                            'brief': {'targetWords': 10000}})
    queued, current = run_automatic((store, gateway, service, empty))
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['mode'] == 'extended'
    assert all(section['content'] for section in current['sections'])
    assert any(i['code'] == 'materials_missing' for i in current['automatic']['report']['issues'])


def test_model_version_is_frozen_even_if_configuration_changes_after_start(automatic):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    gateway.config['version'] = 9
    service.process(store.claim())
    current = service.get(project['id'])
    assert current['automatic']['report']['summary']['generatedSections'] == 2
    assert all(version == 1 for _, _, version in gateway.generated)


def test_automatic_never_claims_an_unrelated_queued_job(automatic):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    job = store.claim()
    other = store.queue('file', {'path': '/unused'}, '其他待处理任务')
    service.process(job)
    assert store.job(other['id'])['status'] == 'queued'
    assert service.get(project['id'])['automatic']['report']['summary']['generatedSections'] == 2


def test_locked_manual_content_survives_automatic_writing(automatic):
    store, gateway, service, project = automatic
    project = service.outline(project['id'], project['version'], [{'title': '人工章节', 'goal': '人工保留', 'mode': 'strict', 'targetWords': 100}])
    project = service.save_section(project['id'], project['sections'][0]['id'], project['version'], {'content': '保留人工编辑的正文。', 'locked': True})
    _, current = run_automatic((store, gateway, service, project))
    section = current['sections'][0]
    assert section['content'] == '保留人工编辑的正文。' and section['manual'] and section['locked']
    assert all(task == 'writing_citation_match' for task, _, _ in gateway.generated)
    assert current['automatic']['report']['summary']['preservedSections'] == 1


def test_extended_auto_writes_without_fabricating_original_evidence(automatic):
    store, gateway, service, project = automatic
    project = service.patch(project['id'], project['version'], {'mode': 'extended', 'sourceIds': []})
    _, current = run_automatic((store, gateway, service, project))
    assert all(s['content'] == PROSE and s['evidence'] == [] for s in current['sections'])
    assert current['automatic']['report']['summary']['generatedSections'] == 2


def test_completed_child_checkpoint_is_reused_after_worker_recovery(automatic, monkeypatch):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    job = store.claim()
    original = service._automatic_checkpoint
    interrupted = False

    class PowerLoss(BaseException):
        pass

    def checkpoint(run, parent_job, state):
        nonlocal interrupted
        original(run, parent_job, state)
        if state.get('generatedSectionIds') and not interrupted:
            interrupted = True
            raise PowerLoss()

    # A genuine abrupt worker stop bypasses finally; simulate by preserving
    # the last durable checkpoint and returning the parent to queued.
    monkeypatch.setattr(service, '_automatic_checkpoint', checkpoint)
    monkeypatch.setattr(service, '_automatic_finish', lambda *_args, **_kwargs: None)
    with pytest.raises(PowerLoss):
        service.process(job)
    calls_before = copy.deepcopy(gateway.generated)
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (queued['job']['id'],))
    restarted = WritingService(store, gateway)
    restarted.process(store.claim())
    current = restarted.get(project['id'])
    first = [(task, title) for task, title, _ in gateway.generated if task == 'writing_section' and title == '原始证据']
    assert len(first) == 1
    assert len(gateway.generated) >= len(calls_before)
    assert current['automatic']['report']['summary']['generatedSections'] == 2
    assert current['automatic']['report']['summary']['preservedSections'] == 0


def test_global_outline_blocker_cannot_be_bypassed_by_supported_sections(automatic, monkeypatch):
    _, gateway, service, _ = automatic
    original = service._review_planned_outline

    def blocked(*args):
        result = original(*args)
        result['outlinePlanningState'] = 'needs_review'
        result['outlinePlanningIssues'] = [{'status': 'uncertain', 'message': '全局写作要求尚未得到支持。'}]
        return result

    monkeypatch.setattr(service, '_review_planned_outline', blocked)
    _, current = run_automatic(automatic, legacy_outline=True)
    assert not current['sections']
    assert not any(task == 'writing_section' for task, _, _ in gateway.generated)
    assert any(i['code'] == 'no_supported_outline' for i in current['automatic']['report']['issues'])


def test_late_parent_attempt_cannot_replace_new_attempt_or_cancel_its_child(automatic):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    old_job = store.claim()
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (old_job['id'],))
        before = db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0]
    service.process(old_job)
    with store.connect() as db:
        after = db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0]
    assert after == before
    assert store.job(old_job['id'])['status'] == 'running'
    assert not gateway.generated


def test_retried_parent_hides_previous_report_while_active(automatic):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    store.cancel(queued['job']['id'])
    assert service.get(project['id'])['automatic']['report']
    store.retry(queued['job']['id'])
    current = service.get(project['id'])
    assert current['automatic']['status'] == 'queued' and current['automatic']['report'] is None


def test_orphan_child_cannot_run_from_a_new_service_after_parent_cancelled(automatic):
    store, gateway, service, project = automatic
    queued = service.queue_automatic(project['id'], project['version'])
    parent_job = store.claim()
    service._automatic_parent_run = queued['runId']
    service._automatic_parent_job = parent_job
    service._automatic_step = 'outline'
    service._automatic_model_config = gateway.resolve(gateway.config['id'])
    child = service.queue(project['id'], project['version'], 'outline')
    with store.connect() as db:
        claimed = dict(db.execute('SELECT * FROM jobs WHERE id=?', (child['job']['id'],)).fetchone())
    store.cancel(queued['job']['id'])
    assert store.claim() is None
    assert store.job(child['job']['id'])['status'] == 'cancelled'
    reopened = WritingService(store, gateway)
    reopened.process(claimed)
    assert not gateway.generated
    assert store.job(child['job']['id'])['status'] == 'cancelled'
    assert not reopened.get(project['id'])['sections']
