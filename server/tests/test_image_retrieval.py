"""Pinned image retrieval with isolated stores and controlled model responses.

These tests exercise provenance, budgets and rejection boundaries, not semantic
quality of a real vision model. They never inspect the user's image library.
"""
import base64
import copy
import json
import struct
import zlib

import httpx
import pytest

from server.image_retrieval import ImageRetrievalService, QUALITY_POLICY
from server.parser import parse_html
from server.store import Store


PROSE = '以下正文用于保留独立来源与固定版本，介绍资料中的研究对象，所有图片图注均保持原始内容。'


def png(seed=1, width=640, height=400):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    row = b'\0' + bytes([seed % 255, 80, 120]) * width
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * height)) + chunk(b'IEND', b''))


def figure(caption='太阳能电池生产线。', *, seed=1, alt='', width=640, height=400):
    data = base64.b64encode(png(seed, width, height)).decode()
    return f'<figure><img src="data:image/png;base64,{data}" alt="{alt}">' + (
        f'<figcaption>{caption}</figcaption>' if caption else '') + '</figure>'


def publish(store, markup='', *, title='固定来源', url='https://images.example/research'):
    raw = f'<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{PROSE}</p>{markup}</article></body></html>'.encode()
    queued = store.queue('file', {'url': url}, title)
    job = store.claim()
    assert job['id'] == queued['id']
    store.publish(job, {'url': url}, raw, parse_html(raw))
    result = store.job(job['id'])
    return store.detail(result['source_id'], result['revision_id'])


def manuscript(*documents, content='本段说明太阳能电池生产线。', cited=None, allowed=None):
    sources = [{'sourceId': doc['id'], 'revisionId': doc['revisionId'], 'title': doc['title']} for doc in documents]
    if allowed is not None:
        sources[0]['blockIds'] = allowed
    evidence = []
    if cited is not None:
        doc = documents[cited]
        block = next(block for block in doc['blocks'] if block['kind'] == 'paragraph')
        evidence = [{'id': 'E1', 'sourceId': doc['id'], 'revisionId': doc['revisionId'],
                     'blockId': block['id'], 'quote': block['text'], 'start': 0, 'end': len(block['text'])}]
        content += '[E1]'
    return {'id': 'writing_fixture', 'version': 1, 'mode': 'strict', 'sources': sources,
            'sections': [{'id': 'section_fixture', 'title': '研究说明', 'content': content, 'evidence': evidence}]}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'isolated-image-retrieval')


def search(store, project, **kwargs):
    return ImageRetrievalService(store).search(project, 'section_fixture', **kwargs)


def visual_response(config, messages):
    packet = json.loads(messages[1]['content'][0]['text'])
    return {'text': json.dumps({'items': [
        {'assetId': item['assetId'], 'relevance': 'relevant', 'readable': True,
         'contextConsistent': True, 'reason': '受控测试的画面与原图注及目标段落一致。'}
        for item in packet['candidates']]}, ensure_ascii=False)}


VISION = {'id': 'controlled-vision', 'version': 1, 'model': 'fixture', 'contextChars': 30000,
          'maxOutputTokens': 8192}


@pytest.mark.parametrize('relevance,readable,consistent,passed', [
    ('relevant', True, True, True),
    ('uncertain', True, True, False),
    ('irrelevant', True, True, False),
    ('relevant', None, True, False),
    ('relevant', True, None, False),
    ('relevant', False, True, False),
    ('relevant', True, False, False),
])
def test_quality_policy_requires_all_visual_checks_to_be_explicitly_positive(store, relevance, readable, consistent, passed):
    doc = publish(store, figure())
    def vision(config, messages):
        response = json.loads(visual_response(config, messages)['text'])
        response['items'][0].update(relevance=relevance, readable=readable, contextConsistent=consistent)
        return {'text': json.dumps(response)}
    result = search(store, manuscript(doc), model_config=VISION, vision_call=vision, quality_policy=QUALITY_POLICY)
    item = result['candidates'][0]
    assert item['visualStatus'] == 'reviewed'
    assert item['qualityGate']['policy'] == QUALITY_POLICY and item['qualityGate']['passed'] is passed
    assert item['qualityGate']['reason']
    assert result['coverage']['qualityPassedCount'] == int(passed)
    assert any('未找到已确认合适的配图' in warning for warning in result['warnings']) is (not passed)


@pytest.mark.parametrize('failure', ['no_model', 'exception', 'invalid_response', 'input_too_long', 'output_too_short'])
def test_quality_policy_never_passes_without_a_complete_valid_visual_result(store, failure):
    doc = publish(store, figure())
    config = dict(VISION)
    calls = []
    def vision(actual, messages):
        calls.append(messages)
        if failure == 'exception':
            raise RuntimeError('controlled failure')
        if failure == 'invalid_response':
            return {'text': '{"items":[]}'}
        pytest.fail('a skipped review must not call the model')
    if failure == 'no_model':
        config = None
    elif failure == 'input_too_long':
        config['contextChars'] = 100
    elif failure == 'output_too_short':
        config['maxOutputTokens'] = 128
    result = search(store, manuscript(doc), model_config=config, vision_call=vision, quality_policy=QUALITY_POLICY)
    item = result['candidates'][0]
    assert item['status'] == 'needs_review'
    assert item['qualityGate']['passed'] is False
    assert result['coverage']['qualityPassedCount'] == 0
    assert len(calls) == int(failure in ('exception', 'invalid_response'))
    assert any('保留纯文字' in warning for warning in result['warnings'])


def test_quality_policy_scope_restriction_cannot_be_promoted_by_same_source_or_positive_callback(store):
    doc = publish(store, figure())
    body = next(block for block in doc['blocks'] if block['text'] == PROSE)
    def forbidden(*args):
        pytest.fail('out-of-scope image must not reach vision')
    result = search(store, manuscript(doc, allowed=[body['id']], cited=0), model_config=VISION,
                    vision_call=forbidden, quality_policy=QUALITY_POLICY)
    item = result['candidates'][0]
    assert not item['scopeEligible'] and item['qualityGate']['passed'] is False
    assert result['coverage']['qualityPassedCount'] == 0


def test_quality_policy_does_not_turn_credit_only_or_library_images_into_automatic_recommendations(store):
    from server.image_library import freeze_library_scope
    reference = publish(store, figure('(Image credit: Intel)'))
    library = publish(store, figure('太阳能电池生产线。', seed=2), url='https://images.example/library')
    project = manuscript(reference)
    with store.connect() as db:
        scope = freeze_library_scope(db, project)
    result = search(store, project, search_scope='library', library_scope=scope, model_config=VISION,
                    vision_call=visual_response, quality_policy=QUALITY_POLICY)
    assert {item['sourceId'] for item in result['candidates']} == {reference['id'], library['id']}
    assert all(item['qualityGate']['passed'] is True and item['status'] == 'needs_review' for item in result['candidates'])
    assert result['coverage']['qualityPassedCount'] == 2


def test_quality_policy_visual_limit_does_not_pass_unreviewed_candidates(store):
    doc = publish(store, ''.join(figure(seed=seed) for seed in range(1, 15)))
    result = search(store, manuscript(doc), model_config=VISION, vision_call=visual_response, quality_policy=QUALITY_POLICY)
    assert result['coverage']['qualityPassedCount'] == 12
    unreviewed = [item for item in result['candidates'] if item['visualStatus'] == 'not_reviewed']
    assert len(unreviewed) == 2 and all(item['qualityGate']['passed'] is False for item in unreviewed)


def test_quality_policy_chapter_defers_gate_without_claiming_final_no_match(store):
    doc = publish(store, figure())
    def forbidden(*args):
        pytest.fail('chapter mode delegates visual review to placement')
    result = search(store, manuscript(doc), placement_mode='chapter', model_config=VISION,
                    vision_call=forbidden, quality_policy=QUALITY_POLICY)
    assert result['coverage']['visualStatus'] == 'deferred_to_placement'
    assert all(item['qualityGate']['passed'] is False for item in result['candidates'])
    assert not any('未找到已确认合适的配图' in warning for warning in result['warnings'])


def test_quality_policy_preserves_legacy_output_and_frozen_visual_prompt(store):
    doc = publish(store, figure())
    frozen = '旧任务冻结规则，逐字保留，不附加新规则。'
    calls = []
    def vision(config, messages):
        calls.append(messages[0]['content'])
        response = json.loads(visual_response(config, messages)['text'])
        response['items'][0].update(relevance='uncertain', contextConsistent=None)
        return {'text': json.dumps(response)}
    result = search(store, manuscript(doc), model_config=VISION, vision_call=vision,
                    vision_system=frozen, quality_policy=None)
    assert calls == [frozen]
    assert result['candidates'][0]['status'] == 'needs_review'
    assert 'qualityGate' not in result['candidates'][0] and 'qualityPassedCount' not in result['coverage']
    assert not any('未找到已确认合适的配图' in warning for warning in result['warnings'])


@pytest.mark.parametrize('invalid', ['', 'writing-image-quality-unknown', False, 1, {}, []])
def test_quality_policy_rejects_unknown_explicit_rules(store, invalid):
    with pytest.raises(ValueError, match='配图质量规则无效'):
        search(store, manuscript(), quality_policy=invalid)


def test_quality_policy_empty_scope_explicitly_allows_no_illustration(store):
    result = search(store, manuscript(), quality_policy=QUALITY_POLICY)
    assert result['candidates'] == [] and result['coverage']['qualityPassedCount'] == 0
    assert any('保留纯文字' in warning for warning in result['warnings'])


def test_history_is_pinned_and_source_revision_ownership_is_checked(store):
    old = publish(store, figure('旧版本太阳能电池。'))
    current = publish(store, figure('新版本图像。', seed=2), title='更新来源')
    result = search(store, manuscript(old))
    assert result['candidates'][0]['revisionId'] == old['revisionId']
    assert result['candidates'][0]['originalCaption'] == '旧版本太阳能电池。'
    assert result['candidates'][0]['assetId'] != current['assets'][0]['id']
    other = publish(store, figure('另一来源。', seed=3), url='https://images.example/other')
    invalid = manuscript(old)
    invalid['sources'][0]['revisionId'] = other['revisionId']
    assert search(store, invalid)['candidates'] == []
    assert search(store, manuscript())['candidates'] == []


def test_caption_links_follow_dom_not_independent_ordinals_and_neighbor_is_context_only(store):
    doc = publish(store, '<p>这只是图片前的无关背景。</p><p>这里增加正文块，故图片与正文序号不会相同。</p>'
                  + figure('图一：太阳能组件的排列。') + '<p>图后的说明只是相邻语境。</p>')
    item = search(store, manuscript(doc))['candidates'][0]
    caption = next(block for block in doc['blocks'] if block['text'] == '图一：太阳能组件的排列。')
    assert item['captionBlockIds'] == [caption['id']]
    assert item['originalCaption'] == caption['text']
    assert item['relation'] == 'caption'
    assert len(item['contextBlockIds']) > 1
    assert item['status'] == 'needs_review' and item['visualStatus'] == 'not_reviewed'
    assert 'evidence' not in item


def test_explicit_single_image_publisher_component_preserves_caption_and_scope(store):
    img = figure('').replace('<figure>', '').replace('</figure>', '')
    markup = ('<div class="ArticleBase-FeaturedImageWrapper"><div class="CaptionedContent">'
              '<div class="CaptionedContent-Content">' + img + '</div>'
              '<div class="CaptionedContent-Caption"><span>The Nvidia DSX Pod.</span><span>Nvidia</span></div>'
              '</div></div><p>A separate paragraph about deployment is only context.</p>')
    doc = publish(store, markup)
    caption = next(block for block in doc['blocks'] if 'The Nvidia DSX Pod.' in block['text'])
    item = search(store, manuscript(doc, allowed=[caption['id']]), model_config=VISION,
                  vision_call=visual_response)['candidates'][0]
    assert item['originalCaption'] == caption['text']
    assert item['captionBlockIds'] == [caption['id']] and item['scopeBlockIds'] == [caption['id']]
    assert item['scopeEligible'] and item['relation'] == 'caption'
    assert item['status'] == 'recommended'


@pytest.mark.parametrize('variant', ['multiple_images', 'ordinary_sibling', 'other_component'])
def test_publisher_component_cannot_promote_ambiguous_or_neighboring_text(store, variant):
    img = figure('', seed=1).replace('<figure>', '').replace('</figure>', '')
    other = figure('', seed=2).replace('<figure>', '').replace('</figure>', '')
    if variant == 'multiple_images':
        markup = ('<div class="CaptionedContent"><div class="CaptionedContent-Content">' + img + other
                  + '</div><div class="CaptionedContent-Caption">One caption cannot identify these two different images.</div></div>')
    elif variant == 'ordinary_sibling':
        markup = ('<div class="CaptionedContent"><div class="CaptionedContent-Content">' + img
                  + '</div><div>An ordinary sibling body paragraph is not an explicit image caption.</div></div>')
    else:
        markup = ('<div class="CaptionedContent"><div class="CaptionedContent-Content">' + img + '</div></div>'
                  '<div class="CaptionedContent"><div class="CaptionedContent-Caption">This caption belongs to another component.</div></div>')
    doc = publish(store, markup)
    result = search(store, manuscript(doc, allowed=[block['id'] for block in doc['blocks']]),
                    model_config=VISION, vision_call=visual_response)
    assert result['candidates']
    assert all(not item['originalCaption'] and not item['scopeEligible'] and item['status'] == 'needs_review'
               for item in result['candidates'])


def test_plain_paragraph_after_image_remains_context_even_when_it_looks_like_caption(store):
    img = figure('').replace('<figure>', '').replace('</figure>', '')
    description = 'SK 海力士在 OCP Global Summit 2026 展出的 CMM-DDR5、MARS 与 CMM-Ax 方案 图片来源：SK hynix'
    doc = publish(store, '<p><span>' + img + '</span></p><p>' + description + '</p>')
    caption_like = next(block for block in doc['blocks'] if block['text'] == description)
    full = search(store, manuscript(doc), model_config=VISION, vision_call=visual_response)['candidates'][0]
    assert full['originalCaption'] == '' and full['status'] == 'needs_review'
    assert caption_like['id'] in full['contextBlockIds']
    narrowed = search(store, manuscript(doc, allowed=[caption_like['id']]))['candidates'][0]
    assert not narrowed['scopeEligible'] and not narrowed['scopeBlockIds']


@pytest.mark.parametrize('credit', ['(Image credit: Intel)', '（图片来源：英特尔）', 'Photo: Intel', '© Intel'])
def test_credit_only_caption_cannot_be_automatically_recommended(store, credit):
    doc = publish(store, figure(credit))
    caption = next(block for block in doc['blocks'] if block['text'] == credit)
    item = search(store, manuscript(doc, allowed=[caption['id']]), model_config=VISION,
                  vision_call=visual_response)['candidates'][0]
    assert item['originalCaption'] == credit and item['scopeBlockIds'] == [caption['id']]
    assert item['scopeEligible'] and item['visualStatus'] == 'reviewed'
    assert item['status'] == 'needs_review' and any('仅标明图片来源' in issue for issue in item['issues'])


def test_credit_only_caption_still_allows_explicit_visual_rejection(store):
    doc = publish(store, figure('(Image credit: Intel)'))
    def reject(config, messages):
        response = json.loads(visual_response(config, messages)['text'])
        response['items'][0].update(relevance='irrelevant', reason='击拳照不能说明生产设备安装。')
        return {'text': json.dumps(response, ensure_ascii=False)}
    item = search(store, manuscript(doc), model_config=VISION, vision_call=reject)['candidates'][0]
    assert item['status'] == 'unsuitable' and item['visualStatus'] == 'reviewed'


def test_descriptive_caption_with_separate_credit_is_not_treated_as_credit_only(store):
    doc = publish(store, figure('A crane lifts a structural truss.<br>(Image credit: Intel)'))
    item = search(store, manuscript(doc), model_config=VISION, vision_call=visual_response)['candidates'][0]
    assert 'structural truss' in item['originalCaption'] and 'Image credit' in item['originalCaption']
    assert item['status'] == 'recommended'


def test_narrowed_scope_requires_explicit_caption_and_all_caption_blocks_allowed(store):
    doc = publish(store, figure('图注属于图片的明确说明。'))
    prose = next(block for block in doc['blocks'] if block['text'] == PROSE)
    caption = next(block for block in doc['blocks'] if block['text'] == '图注属于图片的明确说明。')
    rejected = search(store, manuscript(doc, allowed=[prose['id']]))['candidates'][0]
    assert not rejected['scopeEligible'] and rejected['status'] == 'needs_review'
    accepted = search(store, manuscript(doc, allowed=[caption['id']]))['candidates'][0]
    assert accepted['scopeEligible'] and accepted['contextBlockIds'] == [caption['id']]
    assert not search(store, manuscript(doc, allowed=[]))['candidates'][0]['scopeEligible']
    no_caption = publish(store, figure('', seed=2), url='https://images.example/no-caption')
    narrowed = manuscript(no_caption, allowed=[block['id'] for block in no_caption['blocks']])
    assert not search(store, narrowed)['candidates'][0]['scopeEligible']


def test_shared_bytes_preserve_occurrence_context_and_cited_source_is_preferred(store):
    first = publish(store, figure('太阳能生产线照片。'), url='https://images.example/first')
    second = publish(store, figure('同一照片用于介绍工厂参观。'), url='https://images.example/second')
    result = search(store, manuscript(first, second, content='说明工厂参观。', cited=1))
    assert len(result['candidates']) == 1
    item = result['candidates'][0]
    assert item['sourceId'] == second['id'] and item['originalCaption'] == '同一照片用于介绍工厂参观。'
    assert item['otherOccurrences'][0]['sourceId'] == first['id']
    assert item['otherOccurrences'][0]['originalCaption'] == '太阳能生产线照片。'
    assert len(item['assetHash']) == 64
    assert result['coverage']['duplicateOccurrencesCollapsed'] == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM assets').fetchone()[0] == 2


def test_explicit_fragment_reference_can_authorize_an_uncaptioned_image_in_narrowed_scope(store):
    markup = '<p>太阳能生产线布局见<a href="#production">下图</a>，该指向属于原始正文。</p>'
    markup += figure('').replace('<figure>', '<figure id="production">')
    doc = publish(store, markup)
    reference = next(block for block in doc['blocks'] if '该指向属于原始正文' in block['text'])
    item = search(store, manuscript(doc, allowed=[reference['id']]))['candidates'][0]
    assert item['scopeEligible'] and item['scopeBlockIds'] == [reference['id']]
    assert item['relation'] == 'explicit_reference'
    assert item['originalCaption'] == '' and item['captionBlockIds'] == []
    assert item['status'] == 'needs_review'


def test_ambiguous_fragment_target_or_link_text_cannot_authorize_narrowed_scope(store):
    markup = '<p>这段只说下图而没有链接。<br>这里明确指向<a href="#production">下图</a>。</p>'
    markup += figure('').replace('<figure>', '<figure id="production">')
    doc = publish(store, markup)
    unrelated = next(block for block in doc['blocks'] if '没有链接' in block['text'])
    item = search(store, manuscript(doc, allowed=[unrelated['id']]))['candidates'][0]
    assert not item['scopeEligible']


def test_uncaptioned_cited_images_remain_discoverable_without_keyword_match(store):
    doc = publish(store, figure(''))
    result = search(store, manuscript(doc, content='该事件中的场景值得查看。', cited=0), query='不同检索词')
    item = result['candidates'][0]
    assert item['scopeEligible'] and '来自本段引用资料' in item['rankReasons']
    assert not item['caption'] and item['status'] == 'needs_review'
    assert item['issues']


def test_effective_translation_is_search_copy_and_original_caption_stays_exact(store):
    doc = publish(store, figure('Photovoltaic modules at the factory.'))
    caption = next(block for block in doc['blocks'] if block['text'] == 'Photovoltaic modules at the factory.')
    with store.connect() as db:
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)', (doc['revisionId'], caption['id'], '厂房中的光伏组件。'))
    item = search(store, manuscript(doc, content='光伏组件制造。'))['candidates'][0]
    assert item['caption'] == '厂房中的光伏组件。'
    assert item['originalCaption'] == 'Photovoltaic modules at the factory.'
    assert item['captionBlockIds'] == [caption['id']]
    assert any('文字匹配' in reason for reason in item['rankReasons'])


def test_invalid_or_small_images_are_unsuitable_but_not_deleted(store):
    doc = publish(store, figure('小图标。', width=32, height=32))
    result = search(store, manuscript(doc))
    assert result['candidates'][0]['status'] == 'unsuitable'
    assert len(store.detail(doc['id'])['assets']) == 1


def test_missing_external_references_are_reported_without_network_fetch(store):
    doc = publish(store, figure() + '<img src="https://external.invalid/unavailable.jpg">')
    result = search(store, manuscript(doc))
    assert result['coverage']['savedImageCount'] == 1
    assert result['coverage']['unsavedImageReferences'] == 1
    assert any('未形成已保存素材' in warning for warning in result['warnings'])


def test_visual_review_is_explicit_batched_and_cannot_invent_caption(store):
    doc = publish(store, ''.join(figure('太阳能电池生产线。', seed=seed) for seed in range(1, 15)))
    calls = []
    def vision(config, messages):
        packet = json.loads(messages[1]['content'][0]['text'])
        calls.append(len(packet['candidates']))
        images = [part for part in messages[1]['content'] if part['type'] == 'image_url']
        assert all(part['image_url']['detail'] == 'low' for part in images)
        return visual_response(config, messages)
    result = search(store, manuscript(doc), model_config=VISION, vision_call=vision)
    assert calls == [6, 6]
    assert result['coverage']['visualReviewedCount'] == 12
    assert len([item for item in result['candidates'] if item['status'] == 'recommended']) == 12
    assert all(item['caption'] == '太阳能电池生产线。' for item in result['candidates'])
    assert result['coverage']['visualStatus'] == 'partial'


def test_visual_batches_fit_configured_output_without_changing_model_config(store):
    doc = publish(store, ''.join(figure(seed=seed) for seed in range(1, 6)))
    config = {**VISION, 'maxOutputTokens': 1800, 'reasoningEffort': 'medium'}
    before = copy.deepcopy(config)
    calls, seen = [], []
    def constrained_vision(actual, messages):
        packet = json.loads(messages[1]['content'][0]['text'])
        calls.append(len(packet['candidates']))
        seen.extend(item['assetId'] for item in packet['candidates'])
        assert actual == before
        # Simulates the observed failure mode: a larger response cannot return
        # all IDs within this provider's output/hidden-reasoning allowance.
        if len(packet['candidates']) > 2:
            return {'text': '{"items":['}
        assert '最多60字' in messages[0]['content']
        return visual_response(actual, messages)
    result = search(store, manuscript(doc), model_config=config, vision_call=constrained_vision)
    assert calls == [2, 2, 1] and len(set(seen)) == 5
    assert config == before
    assert result['coverage']['visualReviewedCount'] == 5
    assert all(item['status'] == 'recommended' for item in result['candidates'])


def test_insufficient_output_allowance_keeps_candidates_unreviewed_without_call(store):
    doc = publish(store, figure())
    def forbidden(*args):
        pytest.fail('do not request an output that cannot include one full candidate')
    result = search(store, manuscript(doc), model_config={**VISION, 'maxOutputTokens': 128}, vision_call=forbidden)
    item = result['candidates'][0]
    assert item['status'] == 'needs_review' and item['visualStatus'] == 'not_reviewed'
    assert result['coverage']['visualReviewedCount'] == 0 and result['coverage']['visualStatus'] == 'partial'
    assert any('人工确认' in issue for issue in item['issues'])


def test_frozen_visual_system_is_used_exactly_and_counts_towards_input_allowance(store):
    doc = publish(store, figure())
    frozen = '冻结的既有核对规则；保留其原文与输出协议。'
    calls = []
    def vision(config, messages):
        calls.append(messages[0]['content'])
        assert messages[0]['content'] == frozen
        return visual_response(config, messages)
    result = search(store, manuscript(doc), model_config=VISION, vision_call=vision, vision_system=frozen)
    assert calls == [frozen] and result['candidates'][0]['visualStatus'] == 'reviewed'
    def forbidden(*args):
        pytest.fail('the frozen system text must count towards the full input size')
    result = search(store, manuscript(doc), model_config={**VISION, 'contextChars': 2000},
                    vision_call=forbidden, vision_system=frozen * 200)
    assert result['candidates'][0]['status'] == 'needs_review'
    assert result['candidates'][0]['visualStatus'] == 'not_reviewed'
    for invalid in ('', '  ', 0, []):
        with pytest.raises(ValueError, match='规则无效'):
            search(store, manuscript(doc), model_config=VISION, vision_call=forbidden, vision_system=invalid)


def test_current_visual_rules_separate_unrelated_object_from_uncertain_identity(store):
    doc = publish(store, figure('(Image credit: Intel)'))
    def vision(config, messages):
        system = messages[0]['content']
        assert '不相关不得留作 uncertain' in system
        assert '已经存在可能说明本段的实质关联' in system
        assert '不得把画面或相邻语境推成首座、首个、具体地点或时间等身份事实' in system
        response = json.loads(visual_response(config, messages)['text'])
        response['items'][0].update(relevance='irrelevant', contextConsistent=None,
                                    reason='光罩不是施工场景，不能说明本段吊装。')
        return {'text': json.dumps(response, ensure_ascii=False)}
    item = search(store, manuscript(doc, content='施工人员正在吊装桁架。'),
                  model_config=VISION, vision_call=vision)['candidates'][0]
    assert item['status'] == 'unsuitable' and item['visualStatus'] == 'reviewed'


def test_visual_review_sees_long_paragraph_final_condition(store):
    doc = publish(store, figure())
    target = '太阳能电池生产线的介绍。' * 280 + '但本文所述图片必须是另一场景，不能使用这张生产线照片。'
    calls = []
    def vision(config, messages):
        packet = json.loads(messages[1]['content'][0]['text'])
        calls.append(packet)
        assert packet['targetParagraph'] == target
        response = json.loads(visual_response(config, messages)['text'])
        response['items'][0]['contextConsistent'] = False
        response['items'][0]['reason'] = '完整段落末尾的场景条件与这张图片不相容。'
        return {'text': json.dumps(response, ensure_ascii=False)}
    result = search(store, manuscript(doc, content=target), model_config=VISION, vision_call=vision)
    assert len(calls) == 1 and len(target) > 2400
    assert result['candidates'][0]['status'] == 'unsuitable'


def test_visual_review_submits_complete_original_translated_caption_and_alt(store):
    original = 'Photovoltaic modules at the factory. ' * 28 + 'Only an experimental installation.'
    translated = '厂房中的光伏组件，图片呈现实验装置的布局。' * 48 + '仅为实验装置。'
    alt = '光伏组件实验装置的完整说明。' * 30 + '仅为实验装置。'
    doc = publish(store, figure(original, alt=alt))
    caption = next(block for block in doc['blocks'] if block['text'] == original)
    with store.connect() as db:
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)', (doc['revisionId'], caption['id'], translated))
    def vision(config, messages):
        packet = json.loads(messages[1]['content'][0]['text'])
        candidate = packet['candidates'][0]
        assert candidate['originalCaption'] == original
        assert candidate['caption'] == translated
        assert candidate['altText'] == alt
        assert '中文图注必须忠实原图注' in messages[0]['content']
        return visual_response(config, messages)
    result = search(store, manuscript(doc), model_config=VISION, vision_call=vision)
    item = result['candidates'][0]
    assert item['status'] == 'recommended'
    assert item['caption'] == translated and item['originalCaption'] == original
    with store.connect() as db:
        assert db.execute('SELECT text FROM blocks WHERE id=?', (caption['id'],)).fetchone()[0] == original
        assert db.execute('SELECT text FROM translated_blocks WHERE block_id=?', (caption['id'],)).fetchone()[0] == translated


def test_full_visual_input_over_budget_remains_available_for_manual_review(store):
    doc = publish(store, figure())
    target = '太阳能生产线的完整段落。' * 550 + '末尾限定条件必须保留。'
    def forbidden(*args):
        pytest.fail('never submit a truncated paragraph to fit visual input')
    result = search(store, manuscript(doc, content=target), model_config={**VISION, 'contextChars': 4000}, vision_call=forbidden)
    item = result['candidates'][0]
    assert item['scopeEligible'] and item['status'] == 'needs_review'
    assert item['visualStatus'] == 'not_reviewed'
    assert item['caption'] == '太阳能电池生产线。'
    assert result['coverage']['visualReviewedCount'] == 0
    assert result['coverage']['visualStatus'] == 'partial'
    assert any('人工核对' in issue for issue in item['issues'])
    assert all('预算' not in text for text in [*item['issues'], *result['warnings']])


def test_overlong_caption_is_preserved_and_cannot_be_automatically_recommended(store):
    original = '实验装置的原始图注。' * 230 + '末尾限定：仅供演示。'
    translated = '中文译文保留实验装置原始图注。' * 170 + '末尾限定：仅供演示。'
    doc = publish(store, figure(original))
    caption = next(block for block in doc['blocks'] if block['text'] == original)
    with store.connect() as db:
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)', (doc['revisionId'], caption['id'], translated))
    def vision(config, messages):
        candidate = json.loads(messages[1]['content'][0]['text'])['candidates'][0]
        assert candidate['originalCaption'] == original and candidate['caption'] == translated
        return visual_response(config, messages)
    item = search(store, manuscript(doc), model_config=VISION, vision_call=vision)['candidates'][0]
    assert len(original) > 2000 and len(translated) > 2000
    assert item['originalCaption'] == original and item['caption'] == translated
    assert item['status'] == 'needs_review' and item['visualStatus'] == 'reviewed'
    assert any('缩写' in issue for issue in item['issues'])


def test_oversized_candidate_does_not_block_other_full_visual_inputs(store):
    doc = publish(store, figure('完整图注。' * 1000, seed=1) + figure('太阳能电池生产线。', seed=2))
    calls = []
    def vision(config, messages):
        packet = json.loads(messages[1]['content'][0]['text'])
        calls.extend(packet['candidates'])
        return visual_response(config, messages)
    result = search(store, manuscript(doc), model_config={**VISION, 'contextChars': 4000}, vision_call=vision)
    assert len(calls) == 1 and calls[0]['originalCaption'] == '太阳能电池生产线。'
    assert {item['status'] for item in result['candidates']} == {'needs_review', 'recommended'}


def test_json_escaped_credential_is_rejected_after_visual_decoding(store):
    secret = 'private-test-token'
    doc = publish(store, figure())
    def vision(config, messages):
        response = json.loads(visual_response(config, messages)['text'])
        response['items'][0]['reason'] = secret
        raw = json.dumps(response).replace(secret, ''.join('\\u%04x' % ord(char) for char in secret))
        assert secret not in raw
        return {'text': raw}
    result = search(store, manuscript(doc), model_config={**VISION, 'apiKey': secret}, vision_call=vision)
    assert result['candidates'][0]['status'] == 'needs_review'
    assert result['candidates'][0]['visualStatus'] == 'failed'
    assert secret not in json.dumps(result)


@pytest.mark.parametrize('change', ['foreign', 'duplicate', 'missing', 'boolean', 'exception'])
def test_invalid_visual_response_never_marks_any_image_reviewed_or_leaks_provider_text(store, change):
    doc = publish(store, figure(seed=1) + figure(seed=2))
    def bad(config, messages):
        if change == 'exception':
            raise RuntimeError('private-test-token: confidential provider body')
        response = visual_response(config, messages)
        output = json.loads(response['text'])
        if change == 'foreign':
            output['items'][0]['assetId'] = 'outside-candidate'
        elif change == 'duplicate':
            output['items'][1]['assetId'] = output['items'][0]['assetId']
        elif change == 'missing':
            output['items'].pop()
        else:
            output['items'][0]['readable'] = 1
        return {'text': json.dumps(output)}
    result = search(store, manuscript(doc), model_config={**VISION, 'apiKey': 'private-test-token'}, vision_call=bad)
    assert all(item['status'] == 'needs_review' and item['visualStatus'] == 'failed' for item in result['candidates'])
    assert result['coverage']['visualReviewedCount'] == 0
    assert 'private-test-token' not in json.dumps(result)


def test_ineligible_images_are_not_sent_to_vision_and_uncaptioned_images_need_human_review(store):
    doc = publish(store, figure())
    project = manuscript(doc, allowed=[])
    def forbidden(*args):
        pytest.fail('out-of-scope images must not be sent for automatic review')
    assert not search(store, project, model_config=VISION, vision_call=forbidden)['candidates'][0]['scopeEligible']
    no_caption = publish(store, figure('', seed=2), url='https://images.example/missing-caption')
    result = search(store, manuscript(no_caption), model_config=VISION, vision_call=visual_response)
    assert result['candidates'][0]['status'] == 'needs_review'


class Embeddings:
    def __init__(self):
        self.inputs = []
        self.transport = httpx.MockTransport(self.respond)

    def respond(self, request):
        assert request.url.path == '/v1/embeddings'
        packet = json.loads(request.content)
        self.inputs.append(packet['input'])
        # Deliberately reverse provider results to exercise the shared adapter.
        values = [{'index': index, 'embedding': [1., 0.] if 'Photovoltaic' in text or '光伏' in text else [0., 1.]}
                  for index, text in enumerate(packet['input'])]
        return httpx.Response(200, json={'data': list(reversed(values))})


EMBED = {'id': 'controlled-embedding', 'version': 1, 'model': 'qwen3-embedding:4b',
         'baseUrl': 'https://embedding.invalid/v1', 'timeoutSeconds': 3}


def test_text_vectors_use_shared_adapter_cache_and_keep_old_pinned_revision(store):
    old = publish(store, figure('Photovoltaic modules on a production line.'))
    models = Embeddings()
    service = ImageRetrievalService(store, models)
    project = manuscript(old, content='光伏组件生产。')
    first = service.search(project, 'section_fixture', embedding_config=EMBED)
    assert first['coverage']['semanticStatus'] == 'ready'
    assert first['candidates'][0]['semanticScore'] == 1.0
    assert len(models.inputs) == 2
    assert models.inputs[0][0].startswith('Instruct:')
    assert 'Photovoltaic' in models.inputs[1][0]
    service.search(project, 'section_fixture', embedding_config=EMBED)
    assert len(models.inputs) == 3  # only the query is embedded again
    publish(store, figure('Replacement version.', seed=2))
    historical = service.search(project, 'section_fixture', embedding_config=EMBED)
    assert historical['candidates'][0]['revisionId'] == old['revisionId']
    assert len(models.inputs) == 4
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_image_vectors WHERE revision_id=?', (old['revisionId'],)).fetchone()[0] == 1


def test_translated_caption_change_invalidates_only_derived_vector(store):
    doc = publish(store, figure('Photovoltaic modules.'))
    caption = next(block for block in doc['blocks'] if block['text'] == 'Photovoltaic modules.')
    models = Embeddings()
    service = ImageRetrievalService(store, models)
    project = manuscript(doc, content='光伏组件生产。')
    service.search(project, 'section_fixture', embedding_config=EMBED)
    with store.connect() as db:
        db.execute('INSERT INTO translated_blocks VALUES(?,?,?)', (doc['revisionId'], caption['id'], '光伏组件的人工修订说明。'))
    result = service.search(project, 'section_fixture', embedding_config=EMBED)
    assert len(models.inputs) == 4
    assert result['candidates'][0]['caption'] == '光伏组件的人工修订说明。'
    assert store.detail(doc['id'])['blocks'] == doc['blocks']


def test_embedding_failure_falls_back_without_fake_scores_or_credential_disclosure(store):
    doc = publish(store, figure())
    models = Embeddings()
    models.transport = httpx.MockTransport(lambda request: httpx.Response(503, text='private-provider-body'))
    result = ImageRetrievalService(store, models).search(manuscript(doc), 'section_fixture', embedding_config=EMBED)
    assert result['coverage']['semanticStatus'] == 'unavailable'
    assert result['candidates'] and 'semanticScore' not in result['candidates'][0]
    assert 'private-provider-body' not in json.dumps(result)


def test_embedding_callback_preserves_actual_inputs_wire_reply_and_redaction(store):
    from server.retrieval import RetrievalService
    from server.writing_operations import WritingOperations
    doc = publish(store, figure())
    models = Embeddings()
    service = ImageRetrievalService(store, models)
    operations = WritingOperations(store)
    config = {**EMBED, 'apiKey': 'private-embedding-credential'}
    def embed_call(frozen, texts):
        with operations.capture_materials('image-embedding-fixture', frozen, []) as capture:
            result = RetrievalService._embed(service, frozen, texts, observer=capture)
            capture.validated()
            return result
    result = service.search(manuscript(doc), 'section_fixture', embedding_config=config, embed_call=embed_call)
    assert result['coverage']['semanticStatus'] == 'ready'
    with store.connect() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM writing_material_interactions ORDER BY rowid')]
    assert len(rows) == 2
    assert [json.loads(row['request_json'])['input'] for row in rows] == models.inputs
    for row in rows:
        request = json.loads(row['request_json'])
        assert request['encoding_format'] == 'float'
        assert row['status'] == 'succeeded' and row['validation_status'] == 'valid'
        assert row['http_status'] == 200 and row['response_text'] is None
        assert 'data' in json.loads(row['response_raw'])
    assert 'private-embedding-credential' not in json.dumps(rows)


@pytest.mark.parametrize('failure', ['http', 'network', 'invalid_vector'])
def test_embedding_observer_keeps_failed_wire_reply_with_generic_errors(store, failure):
    from server.retrieval import RetrievalService
    from server.writing_operations import WritingOperations
    models = Embeddings()
    secret = 'private-embedding-credential'
    def response(request):
        if failure == 'network':
            raise httpx.ConnectError(secret)
        if failure == 'http':
            return httpx.Response(401, json={'error': secret})
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [0, 0]}], 'error': secret})
    models.transport = httpx.MockTransport(response)
    service = ImageRetrievalService(store, models)
    operations = WritingOperations(store)
    config = {**EMBED, 'apiKey': secret}
    with pytest.raises(Exception):
        with operations.capture_materials('failed-image-embedding', config, []) as capture:
            RetrievalService._embed(service, config, ['真实请求文本'], observer=capture)
            capture.validated()
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM writing_material_interactions').fetchone())
    assert row['status'] == 'failed' and row['validation_status'] == 'unavailable'
    assert (row['response_raw'] is None) == (failure == 'network')
    assert row['error'] == '向量请求或响应校验失败'
    assert secret not in json.dumps(row)


def test_cancellation_between_embedding_requests_stops_without_degraded_success(store):
    doc = publish(store, figure())
    models = Embeddings()
    def guard():
        if models.inputs:
            raise ValueError('cancelled')
    with pytest.raises(ValueError, match='取消'):
        ImageRetrievalService(store, models).search(manuscript(doc), 'section_fixture',
                                                    embedding_config=EMBED, request_guard=guard)
    assert len(models.inputs) == 1


def test_cancellation_between_visual_batches_stops_next_model_request(store):
    doc = publish(store, ''.join(figure(seed=seed) for seed in range(1, 9)))
    calls = []
    def guard():
        if calls:
            raise ValueError('cancelled')
    def vision(config, messages):
        calls.append(messages)
        return visual_response(config, messages)
    with pytest.raises(ValueError, match='取消'):
        search(store, manuscript(doc), model_config=VISION, vision_call=vision, request_guard=guard)
    assert len(calls) == 1


def test_input_contract_and_discovery_do_not_change_project_or_create_jobs(store):
    doc = publish(store, figure())
    project = manuscript(doc)
    original = copy.deepcopy(project)
    with store.connect() as db:
        before = [dict(row) for row in db.execute('SELECT * FROM jobs')]
    search(store, project)
    assert project == original
    with store.connect() as db:
        assert [dict(row) for row in db.execute('SELECT * FROM jobs')] == before
    for index in (-1, 1, True):
        with pytest.raises(ValueError):
            search(store, project, paragraph_index=index)
    project['sections'][0]['content'] = ''
    with pytest.raises(ValueError):
        search(store, project)
