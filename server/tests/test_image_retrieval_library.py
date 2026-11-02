"""Whole-library scope tests use isolated, generated material only."""
import copy
import json

import pytest

from server.image_library import freeze_library_scope, library_scope_sources
from server.image_retrieval import ImageRetrievalService
from server.tests.test_image_retrieval import (VISION, figure, manuscript, publish,
                                             search, store, visual_response)


def freeze(store, project):
    with store.connect() as db:
        return freeze_library_scope(db, project)


def library_search(store, project, **kwargs):
    scope = kwargs.pop('library_scope', None)
    return search(store, project, search_scope='library',
                  library_scope=freeze(store, project) if scope is None else scope, **kwargs)


def test_freeze_is_read_only_and_excludes_selected_source_across_versions(store, monkeypatch):
    old = publish(store, figure('旧版太阳能电池。'), url='https://images.example/selected')
    publish(store, figure('新版更多图片。', seed=2), url='https://images.example/selected')
    other = publish(store, figure('独立图库图片。', seed=3), url='https://images.example/library')
    project = manuscript(old, allowed=[])
    monkeypatch.setattr(store, 'original', lambda *args: pytest.fail('freeze must not read original'))
    monkeypatch.setattr(store, 'asset', lambda *args: pytest.fail('freeze must not read image'))
    with store.connect() as db:
        before = db.total_changes
        scope = freeze_library_scope(db, project)
        assert db.total_changes == before
    assert scope['excludedSourceIds'] == [old['id']]
    assert scope['sources'] == [{'sourceId': other['id'], 'revisionId': other['revisionId'],
                                 'assetIds': [other['assets'][0]['id']]}]
    assert scope['sourceCount'] == scope['assetCount'] == 1


def test_library_scope_never_follows_updates_or_includes_later_assets(store):
    ref = publish(store, figure('固定旧参考。', seed=1), url='https://images.example/ref')
    old = publish(store, figure('排队时图库太阳能。', seed=2), url='https://images.example/library')
    project = manuscript(ref)
    scope = freeze(store, project)
    current = publish(store, figure('更新后图库。', seed=3), url='https://images.example/library')
    new = publish(store, figure('新增来源。', seed=4), url='https://images.example/new')
    # Simulate a separately saved asset arriving in the frozen revision later.
    with store.connect() as db:
        db.execute('INSERT INTO assets(id,revision_id,blob_path,mime,alt,locator_json) '
                   'SELECT ?,?,blob_path,mime,alt,locator_json FROM assets WHERE id=?',
                   ('late_asset', old['revisionId'], new['assets'][0]['id']))
    result = library_search(store, project, library_scope=scope)
    ids = {item['assetId'] for item in result['candidates']}
    assert ids == {ref['assets'][0]['id'], old['assets'][0]['id']}
    assert current['assets'][0]['id'] not in ids and 'late_asset' not in ids
    library = next(item for item in result['candidates'] if item['sourceType'] == 'library')
    assert library['revisionId'] == old['revisionId']
    assert result['coverage']['libraryScannedAssetCount'] == 1


def test_library_cannot_bypass_narrowed_source_even_with_preselection_manifest(store):
    selected = publish(store, figure('范围外图注。', seed=1))
    scope = freeze(store, manuscript())
    project = manuscript(selected, allowed=[])
    assert library_scope_sources(scope, project) == []
    result = library_search(store, project, library_scope=scope)
    assert len(result['candidates']) == 1
    candidate = result['candidates'][0]
    assert candidate['sourceType'] == 'reference' and not candidate['scopeEligible']
    assert result['coverage']['libraryScannedAssetCount'] == 0


def test_library_requires_valid_manifest_and_checks_owned_revision(store):
    first = publish(store, figure('图一。'), url='https://images.example/one')
    second = publish(store, figure('图二。', seed=2), url='https://images.example/two')
    project = manuscript()
    with pytest.raises(ValueError, match='范围'):
        search(store, project, search_scope='library')
    scope = freeze(store, project)
    broken = copy.deepcopy(scope)
    broken['sources'][0]['assetIds'].append('injected')
    with pytest.raises(ValueError, match='范围'):
        library_search(store, project, library_scope=broken)
    # A structurally valid internal manifest still cannot cross source ownership.
    from server.image_library import _digest
    scope['sources'] = [{'sourceId': first['id'], 'revisionId': second['revisionId'],
                         'assetIds': [second['assets'][0]['id']]}]
    scope.update(sourceCount=1, assetCount=1)
    scope['scopeHash'] = _digest({k: v for k, v in scope.items() if k != 'scopeHash'})
    result = library_search(store, project, library_scope=scope)
    assert result['candidates'] == [] and result['coverage']['libraryMissingAssetCount'] == 1


def test_empty_frozen_scope_does_not_expand_to_live_library(store):
    project = manuscript()
    empty = freeze(store, project)
    publish(store, figure('稍后才加入。'))
    result = library_search(store, project, library_scope=empty)
    assert result['candidates'] == [] and result['coverage']['libraryFrozenAssetCount'] == 0


def test_all_library_assets_are_scanned_before_shortlist_and_file_inspection(store, monkeypatch):
    best = publish(store, figure('极地考察破冰船。'), url='https://images.example/oldest')
    for i in range(8):
        publish(store, figure('城市汽车道路。', seed=i + 2), url=f'https://images.example/later-{i}')
    project = manuscript(content='极地考察破冰船。')
    import server.image_retrieval as module
    monkeypatch.setattr(module, 'MAX_LIBRARY_CANDIDATES', 2)
    original = ImageRetrievalService._asset_facts
    inspected = []
    def inspect(service, asset):
        inspected.append(asset['id'])
        return original(service, asset)
    monkeypatch.setattr(ImageRetrievalService, '_asset_facts', inspect)
    result = library_search(store, project)
    assert result['candidates'][0]['assetId'] == best['assets'][0]['id']
    coverage = result['coverage']
    assert coverage['libraryFrozenAssetCount'] == coverage['libraryScannedAssetCount'] == 9
    assert coverage['libraryShortlistTruncated'] and coverage['libraryRecalledCount'] == 2
    assert len(inspected) == 2


def test_library_visual_match_stays_manual_and_never_becomes_prose_evidence(store):
    doc = publish(store, figure('太阳能电池生产线。'))
    project = manuscript()
    before = copy.deepcopy(project)
    result = library_search(store, project, model_config=VISION, vision_call=visual_response)
    candidate = result['candidates'][0]
    assert candidate['assetId'] == doc['assets'][0]['id']
    assert candidate['sourceType'] == 'library' and candidate['scopeEligible']
    assert candidate['status'] == 'needs_review' and candidate['visualStatus'] == 'reviewed'
    assert any('不能作为正文依据' in issue for issue in candidate['issues'])
    assert project == before and not project['sources'] and 'evidence' not in candidate


def test_reference_volume_cannot_crowd_library_out_of_visual_review(store):
    refs = [publish(store, figure('太阳能电池生产线。', seed=i + 1),
                    url=f'https://images.example/reference-{i}') for i in range(15)]
    other = publish(store, figure('太阳能电池生产线。', seed=30), url='https://images.example/library')
    seen = []
    def vision(config, messages):
        seen.extend(item['assetId'] for item in json.loads(messages[1]['content'][0]['text'])['candidates'])
        return visual_response(config, messages)
    result = library_search(store, manuscript(*refs), model_config=VISION, vision_call=vision)
    assert other['assets'][0]['id'] in seen and len(seen) == 12
    assert any(item['sourceType'] == 'reference' and item['status'] == 'recommended' for item in result['candidates'])
    assert all(item['status'] != 'recommended' for item in result['candidates'] if item['sourceType'] == 'library')


def test_chapter_recalls_late_subject_and_balances_semantics_without_visual_calls(store):
    publish(store, figure('农业灌溉田地。'), url='https://images.example/farm')
    last = publish(store, figure('极地考察破冰船。', seed=2), url='https://images.example/polar')
    content = ('农业灌溉田地。' * 500 + '\n\n' + '山地森林。' * 500
               + '\n\n极地考察破冰船。本项只涉及夏季航行。')
    project = manuscript(content=content)
    sent = []
    def embed(config, texts):
        sent.extend(texts)
        return [[1., 0.] for _ in texts]
    result = library_search(store, project, placement_mode='chapter', embed_call=embed,
                            embedding_config={'id': 'controlled', 'model': 'qwen3-embedding:4b', 'version': 1},
                            model_config=VISION, vision_call=lambda *args: pytest.fail('chapter must defer placement vision'))
    assert last['assets'][0]['id'] in {item['assetId'] for item in result['candidates']}
    assert '极地考察破冰船' in sent[0] and '夏季航行' in sent[0] and len(sent[0]) <= 2400
    coverage = result['coverage']
    assert coverage['lexicalParagraphCount'] == coverage['semanticQueryParagraphsRepresented'] == 3
    assert coverage['semanticQueryTruncated'] and coverage['visualStatus'] == 'deferred_to_placement'
    assert coverage['lexicalBodyChars'] > 6000


def test_defaults_remain_reference_only_and_paragraph_visual_review(store):
    ref = publish(store, figure('太阳能电池生产线。'))
    publish(store, figure('太阳能电池生产线。', seed=2), url='https://images.example/other')
    result = search(store, manuscript(ref), model_config=VISION, vision_call=visual_response)
    assert len(result['candidates']) == 1 and result['candidates'][0]['sourceType'] == 'reference'
    assert result['candidates'][0]['status'] == 'recommended'
    assert result['coverage']['searchScope'] == 'references' and result['coverage']['placementMode'] == 'paragraph'


def test_small_images_cannot_fill_library_shortlist_or_visual_slots(store, monkeypatch):
    for i in range(5):
        publish(store, figure('极地考察破冰船。', seed=i + 1, width=32, height=32),
                url=f'https://images.example/icon-{i}')
    good = publish(store, figure('极地考察破冰船。', seed=10), url='https://images.example/usable')
    project = manuscript(content='极地考察破冰船。')
    import server.image_retrieval as module
    monkeypatch.setattr(module, 'MAX_LIBRARY_CANDIDATES', 1)
    seen = []
    def vision(config, messages):
        seen.extend(item['assetId'] for item in json.loads(messages[1]['content'][0]['text'])['candidates'])
        return visual_response(config, messages)
    result = library_search(store, project, model_config=VISION, vision_call=vision)
    assert seen == [good['assets'][0]['id']]
    assert result['coverage']['libraryScannedAssetCount'] == 6
    assert result['coverage']['librarySizeExcludedCount'] == 5
    assert result['coverage']['libraryEligibleAssetCount'] == result['coverage']['libraryRecalledCount'] == 1
    assert not result['coverage']['libraryShortlistTruncated']


def test_context_handoff_is_explicit_and_does_not_expose_sorting_or_vectors(store):
    doc = publish(store, '<p>太阳能生产设施的辅助语境。</p>' + figure('太阳能电池生产线。'))
    project = manuscript(doc)
    normal = search(store, project, placement_mode='chapter')['candidates'][0]
    assert not any(key.startswith('_') for key in normal)
    internal = search(store, project, placement_mode='chapter', preserve_context=True)['candidates'][0]
    assert {key for key in internal if key.startswith('_')} == {'_contextText', '_searchText'}
    assert '太阳能' in internal['_contextText'] and '太阳能电池生产线' in internal['_searchText']


@pytest.mark.parametrize('notice', [
    '(圖片由生成式AI創建，並經人類專業監督及審核。)',
    'Image generated by AI under editorial supervision.',
    '圖片來源：示例機構',
    '最後更新 2026年05月12日 發佈日期: 2026年05月10日 13815 觀看次數',
])
def test_context_steps_over_notices_without_promoting_them_to_caption_or_scope(store, notice):
    image = figure('').replace('<figure>', '').replace('</figure>', '')
    detail = 'Zephyr 教学平台发现异常登录，机构提醒用户核查账户。'
    doc = publish(store, '<p>' + image + '</p><p>' + notice + '</p><p>' + detail + '</p>')
    paragraph = next(block for block in doc['blocks'] if block['text'] == detail)
    full = search(store, manuscript(doc, content='Zephyr 教学平台异常登录。'), preserve_context=True)['candidates'][0]
    assert detail in full['_contextText'] and notice in full['_contextText']
    assert paragraph['id'] in full['contextBlockIds']
    assert full['originalCaption'] == '' and full['captionBlockIds'] == []
    scoped = search(store, manuscript(doc, allowed=[paragraph['id']]), preserve_context=True)['candidates'][0]
    assert not scoped['scopeEligible'] and scoped['scopeBlockIds'] == []


@pytest.mark.parametrize('barrier', ['heading', 'image'])
def test_notice_context_does_not_cross_next_image_or_section(store, barrier):
    image = figure('', seed=1).replace('<figure>', '').replace('</figure>', '')
    intervening = '<h2>另一个主题</h2>' if barrier == 'heading' else figure('', seed=2)
    unrelated = '此段只描述后面的风力发电机，不属于前图语境。'
    doc = publish(store, '<p>' + image + '</p><p>图片由生成式AI创建。</p>' + intervening + '<p>' + unrelated + '</p>')
    result = search(store, manuscript(doc), preserve_context=True)
    first = next(item for item in result['candidates'] if item['assetId'] == doc['assets'][0]['id'])
    assert unrelated not in first['_contextText']
    assert first['originalCaption'] == ''


def test_library_shortlist_keeps_distinct_source_lane_even_with_large_gallery():
    from server.image_retrieval import _library_diverse
    def item(asset, source, score):
        return {'assetId': asset, 'sourceId': source, 'sourceType': 'library', 'scopeEligible': True,
                'status': 'needs_review', '_rank': score}
    gallery = [item(f'gallery-{i}', 'same-source', 10 - i / 100) for i in range(40)]
    independent = item('independent', 'another-source', 3)
    third = item('third', 'third-source', 2)
    pool = _library_diverse(gallery + [independent, third], 8, reservoir=True)
    assert len(pool) <= 16 and independent in pool and third in pool
    final = _library_diverse(pool, 8)
    assert len(final) == 8 and independent in final and third in final
    assert final[0] == gallery[0]


def test_lexical_search_ignores_generic_reporting_words_and_latin_substrings():
    from server.image_retrieval import _lexical, _terms
    assert '事件' not in _terms('事件表示有关 Zephyr 可能问题')
    assert _lexical('claims about rainfall', ['AI']) == 0
    assert _lexical('中文AI示意图', ['AI']) == 1
    assert _lexical('Zephyr system', ['Zephyr']) == 1


def test_uncaptioned_subject_after_notice_survives_library_shortlist(store, monkeypatch):
    image = figure('', seed=1).replace('<figure>', '').replace('</figure>', '')
    subject = publish(store, '<p>' + image + '</p><p>图片由生成式AI创建。</p>'
                      '<p>Zephyr 教学平台发生异常登录。</p>', title='教学平台安全提示', url='https://images.example/subject')
    for i in range(6):
        publish(store, figure('机构表示目前有关事件可能影响平台。', seed=i + 2),
                url=f'https://images.example/noise-{i}')
    import server.image_retrieval as module
    monkeypatch.setattr(module, 'MAX_LIBRARY_CANDIDATES', 2)
    result = library_search(store, manuscript(content='Zephyr 教学平台异常登录。'), preserve_context=True)
    candidate = next(item for item in result['candidates'] if item['assetId'] == subject['assets'][0]['id'])
    assert candidate['originalCaption'] == '' and candidate['status'] == 'needs_review'
    assert 'Zephyr' in candidate['_contextText']
