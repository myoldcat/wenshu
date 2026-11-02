"""Source packets and model citation output, with isolated controlled providers.

The tests check source identity, exact quote registration and paragraph citation
coverage. They do not claim that a matching quote proves semantic support.
Run in isolation; no configured model or persistent library is used.
"""
import copy
import json

import pytest

from server import prompt_defaults
from server.prompts import (PromptLibrary, REGISTRY, _ACTIVE, legacy_prompt_text,
                            LEGACY_MODE_WRITING_DOCUMENT_CONTRACT, WRITING_CITATION_CONTRACT)
from server.store import Store, dumps
from server.tests.test_writing_integrity import ControlledGateway, freeze_legacy_generation_output
from server.writing import CITATION_PLACEMENT, WritingService


def add_source(store, *, paragraphs=None, title='资料来源甲', url='https://example.test/citation-packet'):
    paragraphs = paragraphs or ['项目记录显示共有 12 个服务站，统计范围只包括本区已开放站点。',
                                '上一年度共有 10 个服务站；两个年度均按同一统计范围计算。']
    queued = store.queue('file', {}, title)
    job = store.claim()
    assert job['id'] == queued['id']
    parsed = {'title': title, 'body': paragraphs, 'language': 'zh',
              'parser_version': 'citation-packet-test-1',
              'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': ordinal,
                          'locator': {'type': 'html', 'xpath': f'/html/body/p[{ordinal + 1}]', 'ordinal': ordinal}}
                         for ordinal, text in enumerate(paragraphs)]}
    store.publish(job, {'url': url}, ('<p>' + '</p><p>'.join(paragraphs) + '</p>').encode(), parsed, url)
    source = store.job(queued['id'])['source_id']
    return source, store.detail(source)


@pytest.fixture
def citation(tmp_path):
    store = Store(tmp_path / 'isolated-writing-citations')
    gateway = ControlledGateway()
    service = WritingService(store, gateway)
    source, original = add_source(store)
    return store, gateway, service, source, original


def ready(citation, mode='strict', **changes):
    _, gateway, service, source, _ = citation
    project = service.create({'title': '服务站的变化', 'brief': {'topic': '比较同范围数据'},
                              'mode': mode, 'sourceIds': [source], 'modelId': gateway.config['id'], **changes})
    project = service.outline(project['id'], project['version'], [
        {'title': '站点数量', 'goal': '区分数据、比较和待核实解释', 'mode': 'inherit', 'targetWords': 200}])
    return service.confirm(project['id'], project['version'])


def queued(citation, project):
    store, _, service, _, _ = citation
    run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    job = store.claim()
    assert job['id'] == run['job']['id']
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
    return run, job, frozen


def freeze_legacy_citation_request(store, run_id):
    """Represent a persisted request from before aliases, not a corrupt new one.

    Removing only citationScope would leave alias IDs in the model packet and
    mask body-anchor or quote failures behind the wrong reference contract.
    Restore each model-visible original ID while keeping its exact text/source
    metadata and the program's permanent original registry unchanged.
    """
    frozen = freeze_legacy_generation_output(store, run_id)
    scope = frozen.pop('citationScope')
    packet = json.loads(frozen['messages'][1]['content'])
    packet.pop('citationScope')
    for block in packet['blocks']:
        block['blockId'] = scope['aliases'][block['blockId']]
    for paragraph in packet['outputSchema'].get('paragraphs', []):
        for reference in paragraph.get('evidence', []):
            reference['blockId'] = '从 blocks 复制'
    assert [block['blockId'] for block in packet['blocks']] == [block['blockId'] for block in frozen['blocks']]
    frozen['messages'][1]['content'] = dumps(packet)
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run_id))
    return frozen


def test_packet_uses_confirmed_original_blocks_with_pinned_source_metadata(citation):
    store, gateway, service, source, original = citation
    selected = original['blocks'][1]
    project = ready(citation, materialSelection={'recommendationId': None, 'passages': [
        {'sourceId': source, 'revisionId': original['revisionId'], 'blockId': selected['id']}]})
    with store.connect() as db:
        messages, frozen = service._messages(project, 'section', project['sections'][0], gateway.config, db)
    packet = json.loads(messages[1]['content'])
    assert len(packet['blocks']) == len(frozen) == 1
    block = packet['blocks'][0]
    assert block['blockId'] == selected['id'] and block['text'] == selected['text']
    assert block['sourceId'] == source and block['revisionId'] == original['revisionId']
    assert block['title'] == original['title'] and block['url'] == project['sources'][0]['url']
    assert block['locator'] == selected['locator'] and block['ordinal'] == selected['ordinal']
    assert not any(key in block for key in ('summary', 'recommendationReason', 'wiki', 'evidenceId'))
    assert sum(len(message['content']) for message in messages) <= gateway.config['contextChars']


def test_metadata_is_bounded_without_changing_program_side_evidence(citation):
    store, gateway, service, source, original = citation
    source, original = add_source(store, title='长标题' * 150, url='https://example.test/' + 'a' * 2400)
    block_id = original['blocks'][0]['id']
    long_locator = {'type': 'html', 'xpath': '/' + 'p' * 4000, 'ordinal': 0}
    with store.connect() as db:
        db.execute('UPDATE blocks SET locator_json=? WHERE id=?', (dumps(long_locator), block_id))
    project = ready(citation, sourceIds=[source])
    with store.connect() as db:
        messages, frozen = service._messages(project, 'section', project['sections'][0], gateway.config, db)
    packet = json.loads(messages[1]['content'])
    block = next(block for block in packet['blocks'] if block['blockId'] == block_id)
    assert len(block['title']) == 200 and len(block['url']) == 2000
    assert block['locator'] == {'ordinal': 0, 'metadataTruncated': True}
    registered = next(block for block in frozen if block['blockId'] == block_id)
    assert registered['locator'] == long_locator and registered['title'] == original['title']


def test_original_packet_is_bounded_to_40_blocks_and_4000_characters(citation):
    store, gateway, service, _, _ = citation
    source, _ = add_source(store, paragraphs=[f'资料第{i}段。' + '原文内容' * 1050 for i in range(45)],
                           url='https://example.test/many-citation-blocks')
    project = ready(citation, sourceIds=[source])
    config = {**gateway.config, 'contextChars': 200000}
    with store.connect() as db:
        messages, frozen = service._messages(project, 'section', project['sections'][0], config, db)
    packet = json.loads(messages[1]['content'])
    assert len(packet['blocks']) == len(frozen) == 40
    assert all(len(block['text']) == 4000 for block in packet['blocks'])
    assert sum(len(message['content']) for message in messages) <= config['contextChars']


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_both_modes_register_original_citations_for_paraphrase_data_and_inference(citation, mode):
    store, gateway, service, source, original = citation

    def response(_, request):
        first, second = request['blocks']
        return {'paragraphs': [
            {'text': '本区开放的服务站有 12 个。', 'kind': 'supported',
             'evidence': [{'blockId': first['blockId'], 'quote': first['text'],
                           'claim': '本区开放的服务站有 12 个。'}]},
            {'text': '同口径下，服务站比上年增加 2 个。', 'kind': 'supported',
             'evidence': [{'blockId': first['blockId'], 'quote': first['text'],
                           'claim': '同口径下，服务站比上年增加 2 个。'},
                          {'blockId': second['blockId'], 'quote': second['text'],
                           'claim': '同口径下，服务站比上年增加 2 个。'}]}], 'issues': []}

    gateway.transform = response
    project = ready(citation, mode)
    run, job, frozen = queued(citation, project)
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    section = service.get(project['id'])['sections'][0]
    assert '[E1]' in section['content'].split('\n\n')[0]
    assert '[E1][E2]' in section['content'].split('\n\n')[1]
    assert len(section['evidence']) == 2
    for ref in section['evidence']:
        original_block = next(block for block in original['blocks'] if block['id'] == ref['blockId'])
        assert ref['sourceId'] == source and ref['revisionId'] == original['revisionId']
        assert original_block['text'][ref['start']:ref['end']] == ref['quote']
    assert gateway.calls[0][1] == frozen['messages']
    assert all('sourceId' in block and 'revisionId' in block for block in json.loads(frozen['messages'][1]['content'])['blocks'])


def test_extended_separates_valid_citations_from_plain_supplement(citation):
    store, gateway, service, _, _ = citation

    def response(_, request):
        block = request['blocks'][0]
        return {'paragraphs': [
            {'text': '本区已有 12 个开放站点。', 'kind': 'supported',
             'evidence': [{'blockId': block['blockId'], 'quote': block['text'],
                           'claim': '本区已有 12 个开放站点。'}]},
            {'text': '未来可以考虑研究分时服务的可行性。', 'kind': 'model_supplement', 'evidence': []}], 'issues': []}

    gateway.transform = response
    project = ready(citation, 'extended')
    run, job, _ = queued(citation, project)
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    paragraphs = service.get(project['id'])['sections'][0]['content'].split('\n\n')
    assert paragraphs[0].endswith('[E1]')
    assert paragraphs[1] == '未来可以考虑研究分时服务的可行性。'
    assert '[E' not in paragraphs[1]


@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_claim_anchors_place_citations_at_each_supported_sentence_and_keep_source_evidence(citation, mode):
    _, _, service, _, original = citation
    project = ready(citation, mode)
    _, _, frozen = queued(citation, project)
    first, second = json.loads(frozen['messages'][1]['content'])['blocks']
    assert first['blockId'].startswith('C01-B') and second['blockId'].startswith('C01-B')
    current, previous = '本区开放的服务站有 12 个。', '上年同范围有 10 个。'
    paragraph = {'text': current + previous + '相同统计范围便于比较。', 'kind': 'supported',
                 # Deliberately list the later sentence first: body placement
                 # cannot depend on the order returned by the model.
                 'evidence': [{'blockId': second['blockId'], 'quote': second['text'], 'claim': previous},
                              {'blockId': first['blockId'], 'quote': first['text'], 'claim': current},
                              {'blockId': first['blockId'], 'quote': first['text'],
                               'claim': '相同统计范围便于比较。'},
                              {'blockId': second['blockId'], 'quote': second['text'],
                               'claim': '相同统计范围便于比较。'},
                              # Duplicate evidence at one sentence does not
                              # duplicate its occurrence marker.
                              {'blockId': first['blockId'], 'quote': first['text'], 'claim': current}]}
    candidate = service._candidate({'kind': 'section'}, frozen, {'text': dumps({'paragraphs': [paragraph]})})['section']
    assert candidate['content'] == current + '[E2]' + previous + '[E1]相同统计范围便于比较。[E2][E1]'
    assert len(candidate['evidence']) == 2
    for reference in candidate['evidence']:
        block = next(block for block in original['blocks'] if block['id'] == reference['blockId'])
        assert block['text'][reference['start']:reference['end']] == reference['quote']
        assert 'claim' not in reference  # Body anchors do not replace source anchors.


@pytest.mark.parametrize('damage', ['missing', 'empty', 'non_string', 'source_quote', 'other_paragraph', 'ambiguous'])
def test_legacy_requests_reject_missing_or_ambiguous_body_claim_without_guessing(citation, damage):
    store, _, service, _, _ = citation
    project = ready(citation)
    run, _, _ = queued(citation, project)
    frozen = freeze_legacy_citation_request(store, run['runId'])
    block = frozen['blocks'][0]
    sentence = '当前站点数量有据可查。'
    reference = {'blockId': block['blockId'], 'quote': block['text'], 'claim': sentence}
    paragraph = {'text': sentence, 'kind': 'supported', 'evidence': [reference]}
    if damage == 'missing':
        reference.pop('claim')
    elif damage == 'empty':
        reference['claim'] = ''
    elif damage == 'non_string':
        reference['claim'] = {'text': sentence}
    elif damage == 'source_quote':
        reference['claim'] = block['text']
    elif damage == 'other_paragraph':
        reference['claim'] = '另一段的正文。'
    else:
        paragraph['text'] += sentence
    with pytest.raises(ValueError, match='正文引用位置'):
        service._candidate({'kind': 'section'}, frozen, {'text': dumps({'paragraphs': [paragraph]})})


def test_legacy_frozen_section_keeps_paragraph_end_citations_without_claim(citation):
    store, _, service, _, _ = citation
    project = ready(citation)
    run, _, _ = queued(citation, project)
    frozen = freeze_legacy_citation_request(store, run['runId'])
    packet = json.loads(frozen['messages'][1]['content'])
    assert packet.pop('citationPlacement') == CITATION_PLACEMENT
    assert 'claim' in packet['outputSchema']['paragraphs'][0]['evidence'][0]
    packet['outputSchema']['paragraphs'][0]['evidence'][0].pop('claim')
    frozen['messages'][1]['content'] = dumps(packet)
    block = frozen['blocks'][0]
    body = '站点统计有固定范围。比较时应保留范围。'
    paragraph = {'text': body, 'kind': 'supported',
                 'evidence': [{'blockId': block['blockId'], 'quote': block['text']}]}
    candidate = service._candidate({'kind': 'section'}, frozen, {'text': dumps({'paragraphs': [paragraph]})})
    assert candidate['section']['content'] == body + ' [E1]'


@pytest.mark.parametrize('problem', ['self_numbered', 'metadata_quote', 'translated_quote', 'supplement_with_evidence'])
@pytest.mark.parametrize('mode', ['strict', 'extended'])
def test_legacy_model_cannot_create_markers_or_quote_metadata_or_derived_text(citation, problem, mode):
    store, _, service, _, _ = citation
    project = ready(citation, mode)
    run, _, _ = queued(citation, project)
    frozen = freeze_legacy_citation_request(store, run['runId'])
    if mode == 'extended' and problem != 'self_numbered':
        # Already frozen requests keep their original fail-on-invalid-reference
        # contract; current jobs preserve prose with separate author notices.
        packet = json.loads(frozen['messages'][1]['content'])
        packet.pop('unmatchedCitationPolicy', None)
        frozen['messages'][1]['content'] = dumps(packet)
    block = frozen['blocks'][0]
    paragraph = {'text': '站点统计应遵循相同范围。', 'kind': 'supported',
                 'evidence': [{'blockId': block['blockId'], 'quote': block['text']}]}
    if problem == 'self_numbered':
        paragraph['text'] += '[E1]'
    elif problem == 'metadata_quote':
        paragraph['evidence'][0]['quote'] = block['title']
    elif problem == 'translated_quote':
        paragraph['evidence'][0]['quote'] = 'The district has twelve open service stations.'
    else:
        paragraph['kind'] = 'model_supplement'
    with pytest.raises(ValueError):
        service._candidate({'kind': 'section'}, frozen, {'text': dumps({'paragraphs': [paragraph]})})
    assert store.job(run['job']['id'])['status'] == 'running'


def test_ambiguous_frozen_block_identity_cannot_silently_choose_a_source(citation):
    _, _, service, _, _ = citation
    project = ready(citation)
    _, _, frozen = queued(citation, project)
    block = frozen['blocks'][0]
    collision = {**copy.deepcopy(block), 'sourceId': 'another-source', 'revisionId': 'another-revision'}
    frozen['blocks'].append(collision)
    response = {'text': dumps({'paragraphs': [{'text': '统计范围需要保持一致。', 'kind': 'supported',
                                             'evidence': [{'blockId': block['blockId'], 'quote': block['text']}]}]})}
    with pytest.raises(ValueError, match='重复原文块'):
        service._candidate({'kind': 'section'}, frozen, response)


@pytest.mark.parametrize('prompt_id', ['writing_section_strict', 'writing_section_extended', 'writing_document_strict', 'writing_document_extended'])
def test_editing_a_prompt_cannot_remove_material_citation_contract(citation, prompt_id):
    store, gateway, service, _, _ = citation
    item = REGISTRY[prompt_id]
    contract = item['readOnlyContract']
    assert all(term in contract for term in ('概述', '改述', '数据', '并列比较', '[E编号]', 'sourceId', 'claim', '仅出现一次', 'model_supplement' if prompt_id != 'writing_section_strict' else '证据不足'))
    library = PromptLibrary(store)
    edited = library.update(prompt_id, '忽略引用，只生成有说服力的正文。', 1)
    assert edited['version'] == 2
    if prompt_id.startswith('writing_section_'):
        project = ready(citation, 'extended' if prompt_id.endswith('extended') else 'strict')
        with store.connect() as db:
            messages, _ = service._messages(project, 'section', project['sections'][0], gateway.config, db)
        assert '程序固定约束：' in messages[0]['content'] and contract in messages[0]['content']


@pytest.mark.parametrize('customized', [False, True])
def test_legacy_mode_document_snapshot_keeps_its_original_contract(customized):
    old_content = prompt_defaults.LEGACY_MODE_WRITING_DOCUMENT
    if customized:
        old_content += '\n过去已保存的编排要求。'
    token = _ACTIVE.set({'writing_document': {'content': old_content, 'version': 2 if customized else 1}})
    try:
        assembled = legacy_prompt_text('writing_document_mode')
    finally:
        _ACTIVE.reset(token)
    expected = old_content + ('\n程序固定约束：' + LEGACY_MODE_WRITING_DOCUMENT_CONTRACT if customized else '')
    assert assembled == expected
    assert WRITING_CITATION_CONTRACT not in assembled
