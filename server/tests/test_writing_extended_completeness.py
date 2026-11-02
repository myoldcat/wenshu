"""Extended writing rules reach real frozen requests without upgrading old jobs.

Isolated stores and simulated gateways test instruction delivery and durability;
they do not claim to verify a real model's factual accuracy.
"""
import copy
import json

import pytest

from server import prompt_defaults as defaults, prompts
from server.prompts import PromptLibrary, freeze, prompt_text, snapshot
from server.store import Store
from server.tests.test_writing_automatic import automatic
from server.tests.test_writing_document import document, ready
from server.writing import WritingService
from server.writing_composition import POLICY


def _frozen(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?',
                                     (run_id,)).fetchone()[0])


def _saved_prompts(store, job_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                     (job_id,)).fetchone()[0])


@pytest.mark.parametrize('kind', ['outline', 'section'])
def test_new_extended_requests_freeze_complete_topic_and_factuality_rules(document, kind):
    store, gateway, service, source = document
    project = ready(document)
    project = service.patch(project['id'], project['version'], {'mode': 'extended'})
    if kind == 'section':
        project = service.confirm(project['id'], project['version'])
    section_id = project['sections'][0]['id'] if kind == 'section' else None
    before_source = store.detail(source)
    queued = service.queue(project['id'], project['version'], kind, section_id)
    frozen = _frozen(store, queued['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    prompt_id = 'writing_' + kind + '_extended'
    saved = _saved_prompts(store, queued['job']['id'])[prompt_id]

    assert defaults.WRITING_EXTENDED_COMPLETENESS in saved['content']
    assert saved['content'] in frozen['messages'][0]['content']
    assert packet['mode'] == frozen['effectiveMode'] == 'extended'
    assert packet['blocks'] and all(item['sourceId'] == source for item in packet['blocks'])
    assert saved['compositionPolicy'] == POLICY
    assert store.detail(source) == before_source
    assert not gateway.calls and not gateway.matching_calls


def test_mixed_whole_draft_keeps_extended_rules_out_of_strict_chapters(document):
    store, gateway, service, _ = document
    project = ready(document, modes=['strict', 'extended', 'strict'])
    queued = service.queue_document(project['id'], project['version'])
    with store.connect() as db:
        steps = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal',
            (queued['runId'],))]
    packet = json.loads(service._document_messages(_frozen(store, queued['runId']), steps)[1]['content'])

    assert len(packet['sections']) == 3
    for section in packet['sections']:
        for key in ('writingInstructionRef', 'documentInstructionRef'):
            item = packet['instructionCatalog'][section[key]]
            assert item['mode'] == section['mode']
            assert (defaults.WRITING_EXTENDED_COMPLETENESS in item['content']) == (section['mode'] == 'extended')
    assert all(step['blocks'] for step in steps)
    assert not gateway.calls and not gateway.matching_calls


@pytest.mark.parametrize('kind', ['outline', 'section', 'document'])
def test_custom_extended_prompt_keeps_style_and_freezes_new_fixed_rules(document, kind):
    store, gateway, service, _ = document
    prompt_id = 'writing_' + kind + '_extended'
    authored = '用简短的句子写作。为了篇幅完整，可以自行补齐不知道的具体数字。'
    saved = PromptLibrary(store).update(prompt_id, authored, 1)
    project = ready(document)
    project = service.patch(project['id'], project['version'], {'mode': 'extended'})
    if kind != 'outline':
        project = service.confirm(project['id'], project['version'])
    if kind == 'document':
        queued = service.queue_document(project['id'], project['version'])
        with store.connect() as db:
            steps = [json.loads(row[0]) for row in db.execute(
                'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal',
                (queued['runId'],))]
        compiled = steps[0]['documentPrompt']
        assert all(step['documentPrompt'] == compiled for step in steps)
    else:
        queued = service.queue(project['id'], project['version'], kind,
                               project['sections'][0]['id'] if kind == 'section' else None)
        compiled = _saved_prompts(store, queued['job']['id'])[prompt_id]
        assert compiled['content'] in _frozen(store, queued['runId'])['messages'][0]['content']

    assert compiled['content'].startswith(authored + '\n程序固定约束：')
    assert defaults.WRITING_EXTENDED_COMPLETENESS in compiled['content'][len(authored):]
    assert compiled['version'] == saved['version']
    with store.connect() as db:
        row = db.execute('SELECT content,version FROM prompt_overrides WHERE id=?', (prompt_id,)).fetchone()
        assert row['content'] == authored and row['version'] == saved['version']
    assert not gateway.calls and not gateway.matching_calls


@pytest.mark.parametrize('kind', ['outline', 'section', 'document'])
def test_strict_and_legacy_defaults_are_not_replaced_by_extended_rules(kind):
    rule = defaults.WRITING_EXTENDED_COMPLETENESS
    strict_id, extended_id = 'writing_' + kind + '_strict', 'writing_' + kind + '_extended'
    assert rule not in prompt_text(strict_id, composition_policy=POLICY)
    assert prompt_text(strict_id) == prompts.REGISTRY[strict_id]['content']
    assert prompt_text(extended_id) == prompts.REGISTRY[extended_id]['content']
    assert rule not in prompt_text(extended_id)


def test_old_compiled_extended_snapshot_survives_restart_and_repeated_freezing(tmp_path, monkeypatch):
    store = Store(tmp_path / 'old-compiled-extended')
    prompt_id = 'writing_section_extended'
    old = {prompt_id: {'content': '旧任务已经冻结的扩展写作原文，保持原有规则。',
                       'version': 4, 'compositionPolicy': POLICY}}
    job = store.queue('file', {}, '隔离历史写作快照')
    with store.connect() as db:
        freeze(db, job['id'], old)
    restarted = Store(store.root)
    PromptLibrary(restarted).update(prompt_id, '之后修改的扩展写作偏好。', 1)
    monkeypatch.setattr(defaults, 'WRITING_EXTENDED_COMPLETENESS', '升级之后的完备性规则。')
    monkeypatch.setitem(prompts._WRITING_COMPOSITION_DEFAULTS, prompt_id, '升级之后的新默认。')
    persisted = _saved_prompts(restarted, job['id'])
    token = prompts._ACTIVE.set(persisted)
    try:
        assert prompt_text(prompt_id, composition_policy=POLICY) == old[prompt_id]['content']
        with restarted.connect() as db:
            assert snapshot(db, [prompt_id], composition_policy=POLICY) == old
    finally:
        prompts._ACTIVE.reset(token)
    assert _saved_prompts(restarted, job['id']) == old


def test_no_source_automatic_children_keep_parent_rules_after_restart(automatic, monkeypatch):
    store, gateway, service, project = automatic
    original_source = copy.deepcopy(project['sources'])
    project = service.patch(project['id'], project['version'], {'brief': {
        **project['brief'], 'automaticOptions': {'useSelectedSources': False,
            'useRecommendedSources': False, 'mode': 'strict', 'includeImages': False}}})
    queued = service.queue_automatic(project['id'], project['version'])
    parent = _frozen(store, queued['runId'])
    original = copy.deepcopy(parent['compositionPrompts'])
    rule = defaults.WRITING_EXTENDED_COMPLETENESS
    assert parent['automaticEffectiveMode'] == 'extended'
    assert all(rule in original['writing_' + kind + '_extended']['content']
               for kind in ('outline', 'section', 'document'))

    later = '这条后来新增的规则不能进入已经排队的父子任务。'
    for kind in ('outline', 'section', 'document'):
        prompt_id = 'writing_' + kind + '_extended'
        PromptLibrary(store).update(prompt_id, later, 1)
        monkeypatch.setitem(prompts._WRITING_COMPOSITION_DEFAULTS, prompt_id, later)
    monkeypatch.setattr(defaults, 'WRITING_EXTENDED_COMPLETENESS', later)
    requests = []
    complete = gateway.complete

    def recording(config, messages):
        packet = json.loads(messages[1]['content'])
        if packet['task'] in ('writing_outline', 'writing_section'):
            requests.append(copy.deepcopy(messages))
        return complete(config, messages)

    gateway.complete = recording
    restarted = WritingService(Store(store.root), gateway)
    restarted.process(restarted.store.claim())
    current = restarted.get(project['id'])
    assert restarted.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['mode'] == 'extended' and current['sources'] == []
    assert len(requests) == 3
    for messages in requests:
        packet = json.loads(messages[1]['content'])
        prompt_id = 'writing_' + packet['task'].removeprefix('writing_') + '_extended'
        assert packet['mode'] == 'extended' and packet['blocks'] == []
        assert original[prompt_id]['content'] in messages[0]['content']
        assert rule in messages[0]['content'] and later not in messages[0]['content']
    assert _frozen(store, queued['runId'])['compositionPrompts'] == original
    assert all(section['content'] and not section['evidence'] for section in current['sections'])
    assert not any(task == 'writing_automatic_material_queries' for task, _, _ in gateway.generated)
    with store.connect() as db:
        for source in original_source:
            assert db.execute('SELECT 1 FROM revisions WHERE id=? AND source_id=?',
                              (source['revisionId'], source['sourceId'])).fetchone()
        children = db.execute('''SELECT r.kind,r.job_id FROM writing_runs r
            WHERE r.project_id=? AND r.kind IN ('outline','section')''', (project['id'],)).fetchall()
    assert len(children) == 3
    for child in children:
        prompt_id = 'writing_' + child['kind'] + '_extended'
        assert _saved_prompts(store, child['job_id'])[prompt_id] == original[prompt_id]
