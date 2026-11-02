"""Explicit automation options freeze source, mode and picture decisions."""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_writing_automatic import automatic, run_automatic
from server.tests.test_writing_image_tasks import images_env, accept, latest
from server.writing import WritingService
from server.writing_automatic_materials import RetrievalService
from server.writing_images import valid_placement


def options(env, restrict, images=True):
    _, _, service, project = env
    brief = {**project['brief'], 'automation': 'automatic', 'automaticOptions': {
        'restrictToSelectedSources': restrict, 'includeImages': images}}
    project = service.patch(project['id'], project['version'], {'brief': brief})
    return (*env[:3], project)


def frozen(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


def test_restricted_scope_uses_only_selected_revisions_without_search(automatic, monkeypatch):
    env = options(automatic, True, False)
    store, gateway, service, project = env
    searches = []

    def forbidden(*args, **kwargs):
        searches.append(True)
        raise AssertionError('Restricted writing cannot retrieve additional sources')

    monkeypatch.setattr(RetrievalService, 'recommend', forbidden)
    queued, after = run_automatic(env)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert not searches
    assert not any(task == 'writing_automatic_material_queries' for task, _, _ in gateway.generated)
    assert after['sources'] == project['sources'] and after['mode'] == 'strict'
    assert after['materialSelection']['confirmed']
    assert after['automatic']['report']['summary']['generatedSections'] == 2
    assert not any(run['kind'] == 'images' for run in after['runs'])
    assert after['illustrationReview']['status'] == 'skipped'
    assert any(stage['stage'] == 'images' and stage['status'] == 'skipped'
               for stage in after['automatic']['report']['stages'])
    assert frozen(store, queued['runId'])['automaticEffectiveMode'] == 'strict'


def test_open_option_keeps_extended_mode_even_after_materials_are_found(automatic):
    env = options(automatic, False, False)
    store, gateway, _, _ = env
    seen = []
    original = gateway.complete

    def recording(config, messages):
        packet = json.loads(messages[-1]['content'])
        if packet['task'] in ('writing_outline', 'writing_section'):
            seen.append(packet['mode'])
        return original(config, messages)

    gateway.complete = recording
    queued, after = run_automatic(env)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert after['sources'] and after['mode'] == 'extended'
    assert seen and set(seen) == {'extended'}
    saved = frozen(store, queued['runId'])
    assert saved['automaticOptions'] == {'restrictToSelectedSources': False, 'includeImages': False}
    assert saved['automaticEffectiveMode'] == saved['mode'] == 'extended'


def test_restricting_without_selected_sources_is_rejected_before_queue_or_provider(automatic):
    store, gateway, service, project = automatic
    project = service.patch(project['id'], project['version'], {'sourceIds': []})
    env = options((store, gateway, service, project), True, False)
    before = store.jobs()['counts']
    with pytest.raises(ValueError, match='资料'):
        service.queue_automatic(project['id'], env[3]['version'])
    assert store.jobs()['counts'] == before and not gateway.generated


def test_image_option_defaults_on_when_omitted(automatic):
    store, _, service, project = automatic
    project = service.patch(project['id'], project['version'], {'brief': {
        **project['brief'], 'automaticOptions': {'restrictToSelectedSources': True}}})
    queued, after = run_automatic((store, automatic[1], service, project))
    assert frozen(store, queued['runId'])['automaticOptions']['includeImages'] is True
    assert any(run['kind'] == 'images' for run in after['runs'])
    assert any(stage['stage'] == 'images' and stage['status'] == 'complete'
               for stage in after['automatic']['report']['stages'])


def test_options_and_model_version_survive_restart_without_reselection(automatic, monkeypatch):
    env = options(automatic, True, False)
    store, gateway, service, project = env
    original = service._automatic_checkpoint

    class PowerLoss(BaseException):
        pass

    def stop_after_scope(run, job, state):
        original(run, job, state)
        if state.get('materialsReady'):
            raise PowerLoss()

    monkeypatch.setattr(service, '_automatic_checkpoint', stop_after_scope)
    monkeypatch.setattr(service, '_automatic_finish', lambda *args, **kwargs: None)
    queued = service.queue_automatic(project['id'], project['version'])
    project['brief']['automaticOptions']['includeImages'] = True
    with pytest.raises(PowerLoss):
        service.process(store.claim())
    gateway.config['version'] = 8
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (queued['job']['id'],))
    restarted = WritingService(store, gateway)
    restarted.process(store.claim())
    after = restarted.get(project['id'])
    assert after['automatic']['report']['summary']['generatedSections'] == 2
    assert all(version == 1 for _, _, version in gateway.generated)
    assert frozen(store, queued['runId'])['automaticOptions']['includeImages'] is False
    assert not any(run['kind'] == 'images' for run in after['runs'])


def test_old_frozen_parent_without_option_fields_keeps_previous_automation(automatic):
    env = options(automatic, True, False)
    store, gateway, service, project = env
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        saved = frozen(store, queued['runId'])
        for key in ('automaticOptions', 'automaticOptionsPolicy', 'automaticEffectiveMode'):
            saved.pop(key)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(saved), queued['runId']))
    service.process(store.claim())
    after = service.get(project['id'])
    assert after['mode'] == 'strict'
    assert any(task == 'writing_automatic_material_queries' for task, _, _ in gateway.generated)
    assert any(run['kind'] == 'images' for run in after['runs'])


def test_disabled_images_preserve_existing_approved_locked_figure(images_env):
    env = images_env
    project = latest(env)
    project = env.service.patch(env.project_id, project['version'], {'brief': {
        **project['brief'], 'automation': 'automatic', 'automaticOptions': {
            'restrictToSelectedSources': True, 'includeImages': False}}})
    project = accept(env)
    figure_id = project['illustrations'][0]['id']
    project = env.service.patch_illustration(env.project_id, figure_id, project['version'], {'locked': True})
    project = env.service.complete_illustrations(env.project_id, project['version'])
    before = copy.deepcopy(project['illustrations'])
    vision_calls = len(env.gateway.calls)

    def review_only(config, messages):
        packet = json.loads(messages[-1]['content'])
        assert packet['task'] == 'writing_citation_match'
        return {'text': dumps({'assignments': [{'sentenceId': item['sentenceId'], 'evidenceIds': [],
            'support': 'unsupported', 'supportBasis': 'none', 'reason': '人工正文没有登记本句原文摘录。'}
            for item in packet['sentences']]}), 'usage': {}}

    env.gateway.complete = review_only
    queued = env.service.queue_automatic(env.project_id, project['version'])
    env.service.process(env.store.claim())
    after = latest(env)
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert len(env.gateway.calls) == vision_calls and len(env.searches) == 1
    assert after['illustrations'] == before
    assert after['illustrations'][0]['locked'] and valid_placement(after, after['illustrations'][0])
    assert after['illustrationReview']['status'] == 'complete'
