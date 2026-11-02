"""Isolated contracts for chapter placement; no library or real model requests.

Unittest-compatible so the pure matching boundary can also be exercised without
pytest; Ordinary pytest discovery runs the same cases with real imports.
"""
import copy
import json
import unittest
from unittest.mock import patch

from server.image_placement import (ImagePlacementInterrupted, LEGACY_PLACEMENT_PROTOCOL,
                                    PLACEMENT_PROTOCOL, PLACEMENT_SYSTEM, QUALITY_POLICY,
                                    recommend_placements, validate_placement_response)
from server.writing_images import anchor_for

CONFIG = {'id': 'test-vision', 'version': 1, 'model': 'mock', 'contextChars': 24000, 'maxOutputTokens': 1800}
IMAGE = 'data:image/png;base64,iVBORw0KGgo='


def candidate(asset_id='image_a', **changes):
    item = {'assetId': asset_id, 'sourceId': 'source_a', 'revisionId': 'revision_a',
            'sourceType': 'reference', 'sourceTitle': '现场报道', 'originalCaption': 'Ohio One 工地桁架吊装。',
            'caption': 'Ohio One 工地桁架吊装。', 'altText': '桁架吊装。', 'scopeEligible': True,
            'status': 'needs_review', 'issues': [], 'width': 640, 'height': 360}
    item.update(changes)
    return item


def section(content='本章介绍芯片产品。\n\nFab 62 安装设备。\n\nOhio One 工地正在吊装桁架。'):
    return {'id': 'section_a', 'title': '工厂进展', 'content': content}


def match(paragraph_id='p2', **changes):
    item = {'paragraphId': paragraph_id, 'relevance': 'relevant', 'readable': True,
            'contextConsistent': True, 'reason': '画面桁架吊装与本段施工内容相符。'}
    item.update(changes)
    return item


def response(packet, matches=None):
    return {'text': json.dumps({'items': [{'assetId': item['assetId'],
                'matches': [match(item['paragraphIds'][-1])] if matches is None else copy.deepcopy(matches),
                'reason': '已比较可用段落与实际画面。'} for item in packet['candidates']]}, ensure_ascii=False)}


class PlacementTests(unittest.TestCase):
    def run_search(self, content=None, items=None, **kwargs):
        options = {'model_config': copy.deepcopy(CONFIG), 'image_loader': lambda item: IMAGE,
                   'vision_call': lambda config, messages: response(json.loads(messages[1]['content'][0]['text'])),
                   'placement_system': PLACEMENT_SYSTEM}
        options.update(kwargs)
        return recommend_placements(content or section(), items if items is not None else [candidate()], **options)

    def test_best_position_uses_program_exact_anchor_and_does_not_mutate(self):
        original_section = section('  首段含空格。 \n\n第二段内部\n换行。\n\n  Ohio One 工地吊装桁架。[E3]  ')
        original_items = [candidate()]
        before = copy.deepcopy((original_section, original_items))
        result = self.run_search(original_section, original_items)
        suggestion = result['candidates'][0]['placementSuggestions'][0]
        self.assertEqual({key: suggestion[key] for key in ('paragraphIndex', 'paragraphExact', 'contentHash')}, anchor_for(original_section, 2))
        self.assertEqual(suggestion['status'], 'recommended')
        self.assertEqual(before, (original_section, original_items))
        self.assertEqual(result['candidates'][0]['status'], 'recommended')

    def test_two_positions_keep_model_preference_order(self):
        result = self.run_search(vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), [match('p2'), match('p1', relevance='uncertain')]))
        suggestions = result['candidates'][0]['placementSuggestions']
        self.assertEqual([p['paragraphIndex'] for p in suggestions], [2, 1])
        self.assertEqual([p['status'] for p in suggestions], ['recommended', 'needs_review'])

    def test_no_match_does_not_force_first_paragraph(self):
        result = self.run_search(vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), []))
        item = result['candidates'][0]
        self.assertEqual(item['placementSuggestions'], [])
        self.assertEqual((item['status'], item['placementStatus']), ('unsuitable', 'no_match'))

    def test_uncertain_identity_stays_manual(self):
        result = self.run_search(vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), [match(contextConsistent=None)]))
        self.assertEqual(result['candidates'][0]['status'], 'needs_review')
        self.assertEqual(result['candidates'][0]['placementSuggestions'][0]['status'], 'needs_review')

    def test_negative_readability_or_context_cannot_make_a_suggestion(self):
        for field in ('readable', 'contextConsistent'):
            with self.subTest(field=field):
                result = self.run_search(vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), [match(**{field: False})]))
                self.assertEqual(result['candidates'][0]['placementSuggestions'], [])
                self.assertEqual(result['candidates'][0]['status'], 'unsuitable')

    def test_credit_only_or_missing_caption_cannot_auto_recommend(self):
        for caption in ('', 'Image credit: Intel', '(图片来源：Intel)', '© Intel', 'Intel', 'Nvidia.', 'SK hynix', '英特尔'):
            with self.subTest(caption=caption):
                item = self.run_search(items=[candidate(originalCaption=caption)])['candidates'][0]
                self.assertEqual(item['status'], 'needs_review')
                self.assertEqual(item['placementSuggestions'][0]['status'], 'needs_review')

    def test_descriptive_product_label_is_not_treated_as_a_byline(self):
        item = self.run_search(items=[candidate(originalCaption='The Nvidia DSX Pod.')])['candidates'][0]
        self.assertEqual(item['placementSuggestions'][0]['status'], 'recommended')

    def test_library_is_manual_even_after_full_relevant_visual_check(self):
        item = self.run_search(items=[candidate(sourceType='library', status='recommended')])['candidates'][0]
        self.assertEqual(item['status'], 'needs_review')
        self.assertEqual(item['placementSuggestions'][0]['status'], 'needs_review')
        self.assertTrue(any('不能作为正文依据' in value for value in item['issues']))

    def test_existing_quality_issue_is_not_cleared(self):
        item = self.run_search(items=[candidate(issues=['低清原图需核对'])])['candidates'][0]
        self.assertIn('低清原图需核对', item['issues'])
        self.assertEqual(item['placementSuggestions'][0]['status'], 'needs_review')

    def test_quality_policy_requires_all_three_positive_visual_fields(self):
        for relevance in ('relevant', 'uncertain'):
            for readable in (True, False, None):
                for consistent in (True, False, None):
                    with self.subTest(relevance=relevance, readable=readable, consistent=consistent):
                        result = self.run_search(quality_policy=QUALITY_POLICY,
                            vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']),
                                [match(relevance=relevance, readable=readable, contextConsistent=consistent)]))
                        item = result['candidates'][0]
                        passed = relevance == 'relevant' and readable is True and consistent is True
                        self.assertEqual(bool(item['placementSuggestions']), passed)
                        self.assertEqual(item['qualityGate']['passed'], passed)
                        self.assertEqual(item['qualityGate']['policy'], QUALITY_POLICY)
                        if passed:
                            self.assertIs(item['placementSuggestions'][0]['qualityPassed'], True)
                            self.assertEqual(item['placementSuggestions'][0]['status'], 'recommended')
                        elif relevance == 'uncertain' or readable is None or consistent is None:
                            self.assertEqual((item['status'], item['placementStatus']), ('needs_review', 'unconfirmed'))
                        else:
                            self.assertEqual((item['status'], item['placementStatus']), ('unsuitable', 'no_match'))

    def test_quality_policy_keeps_only_qualified_position_and_its_exact_anchor(self):
        original_section, original_items = section(), [candidate()]
        before = copy.deepcopy((original_section, original_items))
        result = self.run_search(original_section, original_items, quality_policy=QUALITY_POLICY,
            vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']),
                                              [match('p0', relevance='uncertain'), match('p2')]))
        suggestion, = result['candidates'][0]['placementSuggestions']
        self.assertEqual({key: suggestion[key] for key in ('paragraphIndex', 'paragraphExact', 'contentHash')}, anchor_for(original_section, 2))
        self.assertTrue(suggestion['qualityPassed'])
        self.assertEqual((original_section, original_items), before)

    def test_quality_policy_allows_direct_credit_only_and_library_manual_positions(self):
        for changes in ({'originalCaption': 'Image credit: Intel'}, {'originalCaption': 'Intel'},
                        {'sourceType': 'library'}, {'issues': ['原图来源需人工确认']}):
            with self.subTest(changes=changes):
                item = self.run_search(items=[candidate(**changes)], quality_policy=QUALITY_POLICY)['candidates'][0]
                suggestion, = item['placementSuggestions']
                self.assertEqual((item['status'], suggestion['status']), ('needs_review', 'needs_review'))
                self.assertTrue(item['qualityGate']['passed'])
                self.assertIs(suggestion['qualityPassed'], True)

    def test_quality_policy_no_match_and_uncertain_are_distinct_and_not_actionable(self):
        for matches, expected in (([], 'no_match'), ([match(relevance='uncertain')], 'unconfirmed')):
            with self.subTest(expected=expected):
                item = self.run_search(quality_policy=QUALITY_POLICY,
                    vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), matches))['candidates'][0]
                self.assertEqual(item['placementStatus'], expected)
                self.assertFalse(item['qualityGate']['passed'])
                self.assertEqual(item['placementSuggestions'], [])
                self.assertIn('可以不配图', item['qualityGate']['reason'])

    def test_quality_policy_partial_review_does_not_reject_unseen_positions(self):
        result = self.run_search(section('\n\n'.join('段落内容。'*100 for _ in range(20))),
            quality_policy=QUALITY_POLICY, model_config={**CONFIG, 'contextChars': 6500},
            vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), []))
        item = result['candidates'][0]
        self.assertFalse(item['placementCoverage']['complete'])
        self.assertEqual((item['status'], item['placementStatus']), ('needs_review', 'unconfirmed'))
        self.assertFalse(item['qualityGate']['passed'])
        self.assertEqual(item['placementSuggestions'], [])

    def test_quality_policy_partial_but_complete_selected_paragraph_match_stays_manual(self):
        item = self.run_search(section('\n\n'.join('段落内容。'*100 for _ in range(20))),
            quality_policy=QUALITY_POLICY, model_config={**CONFIG, 'contextChars': 6500})['candidates'][0]
        self.assertFalse(item['placementCoverage']['complete'])
        self.assertEqual(item['status'], 'needs_review')
        self.assertTrue(item['qualityGate']['passed'])
        self.assertTrue(all(p['qualityPassed'] and p['status'] == 'needs_review' for p in item['placementSuggestions']))

    def test_quality_policy_unreviewed_and_failed_candidates_are_read_only(self):
        cases = ({'model_config': None}, {'image_loader': None}, {'image_loader': lambda item: 'invalid'},
                 {'model_config': {**CONFIG, 'maxOutputTokens': 1000}},
                 {'vision_call': lambda c, m: {'text': '{truncated'}},
                 {'vision_call': lambda c, m: (_ for _ in ()).throw(ValueError('mock failure'))})
        for index, options in enumerate(cases):
            with self.subTest(index=index):
                item = self.run_search(quality_policy=QUALITY_POLICY, **options)['candidates'][0]
                self.assertEqual(item['placementSuggestions'], [])
                self.assertFalse(item['qualityGate']['passed'])
                self.assertNotEqual(item['placementStatus'], 'no_match')
                self.assertIn('不配图', item['qualityGate']['reason'])

    def test_quality_policy_cannot_inherit_an_unreviewed_pass(self):
        item = self.run_search(items=[candidate(status='recommended', qualityGate={'policy': QUALITY_POLICY, 'passed': True},
                                                placementSuggestions=[{'paragraphIndex': 0, 'qualityPassed': True}])],
                               quality_policy=QUALITY_POLICY, model_config=None)['candidates'][0]
        self.assertFalse(item['qualityGate']['passed'])
        self.assertEqual(item['placementSuggestions'], [])

    def test_quality_policy_prioritizes_direct_library_match_over_uncertain_reference(self):
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            return response(packet, [match(relevance='uncertain')] if packet['candidates'][0]['sourceType'] == 'reference' else [match()])
        result = self.run_search(items=[candidate('reference'), candidate('library', sourceType='library')],
                                 quality_policy=QUALITY_POLICY, vision_call=call)
        self.assertEqual([item['assetId'] for item in result['candidates']], ['library', 'reference'])
        self.assertEqual([item['qualityGate']['passed'] for item in result['candidates']], [True, False])

    def test_quality_policy_absent_or_none_keeps_legacy_manual_semantics(self):
        for options in ({}, {'quality_policy': None}):
            with self.subTest(options=options):
                item = self.run_search(vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']),
                    [match(relevance='uncertain', contextConsistent=None)]), **options)['candidates'][0]
                self.assertEqual(item['placementSuggestions'][0]['status'], 'needs_review')
                self.assertNotIn('qualityPassed', item['placementSuggestions'][0])
                self.assertNotIn('qualityGate', item)

    def test_quality_policy_is_independent_of_id_protocol_and_uses_frozen_prompt_verbatim(self):
        for protocol in (LEGACY_PLACEMENT_PROTOCOL, PLACEMENT_PROTOCOL):
            with self.subTest(protocol=protocol):
                calls = []
                frozen = '这是任务已冻结的质量核对提示词。'
                def call(config, messages):
                    calls.append(copy.deepcopy(messages))
                    return response(json.loads(messages[1]['content'][0]['text']))
                item = self.run_search(quality_policy=QUALITY_POLICY, placement_protocol=protocol,
                    placement_system=frozen, vision_call=call)['candidates'][0]
                self.assertEqual(calls[0][0]['content'], frozen)
                self.assertTrue(item['qualityGate']['passed'])
                self.assertTrue(item['placementSuggestions'][0]['qualityPassed'])
                actual_id = json.loads(calls[0][1]['content'][0]['text'])['candidates'][0]['assetId']
                self.assertEqual(actual_id, 'i0' if protocol == PLACEMENT_PROTOCOL else 'image_a')
                self.assertEqual(item['assetId'], 'image_a')

    def test_unknown_quality_policy_fails_before_any_model_request(self):
        for value in ('', 'writing-image-quality-future', False, 1, {}, []):
            with self.subTest(value=value):
                calls = []
                with self.assertRaisesRegex(ValueError, '质量规则版本无效'):
                    self.run_search(quality_policy=value, vision_call=lambda *args: calls.append(args))
                self.assertEqual(calls, [])

    def test_ineligible_and_unsuitable_candidates_are_not_sent(self):
        calls = []
        result = self.run_search(items=[candidate(scopeEligible=False), candidate('image_b', status='unsuitable')],
                                 vision_call=lambda *a: calls.append(a))
        self.assertEqual(calls, [])
        self.assertTrue(all(not item['placementSuggestions'] for item in result['candidates']))

    def test_missing_model_is_explicit_manual_fallback(self):
        result = self.run_search(model_config=None)
        self.assertEqual(result['candidates'][0]['placementSuggestions'], [])
        self.assertEqual(result['candidates'][0]['status'], 'needs_review')
        self.assertFalse(result['coverage']['complete'])

    def test_frozen_prompt_is_used_verbatim(self):
        seen = []
        def call(config, messages):
            seen.append(messages[0]['content'])
            return response(json.loads(messages[1]['content'][0]['text']))
        self.run_search(vision_call=call, placement_system='已冻结的原始规则，不从全局更新。')
        self.assertEqual(seen, ['已冻结的原始规则，不从全局更新。'])

    def test_v2_model_only_sees_short_batch_ids_and_results_map_exactly(self):
        asset_ids = ['asset_6639438cca6c454bbcf10542b600a46c',
                     'asset_884f7bb12ae4494ab29c5d29a170eb7b',
                     'asset_3fce6bd30aa644649a83282162eec4da']
        calls = []
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            calls.append(copy.deepcopy(messages))
            value = json.loads(response(packet)['text'])
            for item in value['items']:
                item['matches'] = [match('p1' if item['assetId'] == 'i1' else 'p2')]
            # Response order does not own image identity.
            value['items'].reverse()
            return {'text': json.dumps(value, ensure_ascii=False)}
        result = self.run_search(items=[candidate(asset_id) for asset_id in asset_ids],
                                 placement_protocol=PLACEMENT_PROTOCOL,
                                 model_config={**CONFIG, 'maxOutputTokens': 2400}, vision_call=call)
        self.assertEqual([[item['assetId'] for item in json.loads(messages[1]['content'][0]['text'])['candidates']] for messages in calls], [['i0', 'i1'], ['i0']])
        for messages in calls:
            self.assertTrue(all(asset_id not in json.dumps(messages) for asset_id in asset_ids))
            packet = json.loads(messages[1]['content'][0]['text'])
            labels = [part['text'] for part in messages[1]['content'][1:] if part['type'] == 'text']
            self.assertEqual(labels, ['assetId='+item['assetId'] for item in packet['candidates']])
        self.assertEqual({item['assetId']: item['placementSuggestions'][0]['paragraphIndex'] for item in result['candidates']},
                         {asset_ids[0]: 2, asset_ids[1]: 1, asset_ids[2]: 2})
        self.assertTrue(all(item['status'] == 'recommended' for item in result['candidates']))
        self.assertEqual(result['coverage']['modelBatches'], [
            {'batchIndex': 0, 'assetIds': {'i0': asset_ids[0], 'i1': asset_ids[1]}},
            {'batchIndex': 1, 'assetIds': {'i0': asset_ids[2]}}])

    def test_v2_unknown_or_real_id_is_rejected_without_guessing_alias(self):
        real_id = 'asset_6639438cca6c454bbcf10542b600a46c'
        for bad_id in ('i1', 'i00', 'I0', real_id, real_id[:15]+real_id[20:]):
            with self.subTest(bad_id=bad_id):
                def call(config, messages):
                    packet = json.loads(messages[1]['content'][0]['text'])
                    value = json.loads(response(packet)['text'])
                    value['items'][0]['assetId'] = bad_id
                    return {'text': json.dumps(value)}
                result = self.run_search(items=[candidate(real_id)], placement_protocol=PLACEMENT_PROTOCOL, vision_call=call)
                item = result['candidates'][0]
                self.assertEqual(item['assetId'], real_id)
                self.assertEqual(item['placementSuggestions'], [])
                self.assertEqual(item['placementStatus'], 'failed')
                self.assertEqual(result['coverage']['modelBatches'], [{'batchIndex': 0, 'assetIds': {'i0': real_id}}])

    def test_failed_call_retains_audit_mapping_without_replacing_candidate_ids(self):
        asset_ids = ['asset_6639438cca6c454bbcf10542b600a46c', 'asset_884f7bb12ae4494ab29c5d29a170eb7b']
        def call(config, messages):
            self.assertTrue(all(asset_id not in json.dumps(messages) for asset_id in asset_ids))
            raise RuntimeError('controlled transport failure')
        result = self.run_search(items=[candidate(asset_id) for asset_id in asset_ids], placement_protocol=PLACEMENT_PROTOCOL,
                                 model_config={**CONFIG, 'maxOutputTokens': 2400}, vision_call=call)
        self.assertEqual(result['coverage']['modelBatches'], [{'batchIndex': 0, 'assetIds': dict(zip(('i0', 'i1'), asset_ids))}])
        self.assertEqual([item['assetId'] for item in result['candidates']], asset_ids)
        self.assertTrue(all(item['placementStatus'] == 'failed' and not item['placementSuggestions'] for item in result['candidates']))

    def test_v1_default_and_explicit_keep_real_ids_and_frozen_prompt(self):
        real_id = 'asset_6639438cca6c454bbcf10542b600a46c'
        frozen = '这是旧任务已保存的完整规则。'
        calls = []
        def call(config, messages):
            calls.append(copy.deepcopy(messages))
            return response(json.loads(messages[1]['content'][0]['text']))
        for options in ({}, {'placement_protocol': LEGACY_PLACEMENT_PROTOCOL}):
            result = self.run_search(items=[candidate(real_id)], placement_system=frozen, vision_call=call, **options)
            self.assertEqual(result['candidates'][0]['placementSuggestions'][0]['paragraphIndex'], 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0][0]['content'], frozen)
        self.assertEqual(json.loads(calls[0][1]['content'][0]['text'])['candidates'][0]['assetId'], real_id)
        self.assertEqual(calls[0][1]['content'][1]['text'], 'assetId='+real_id)

    def test_legacy_misspelled_uuid_remains_invalid(self):
        real_id = 'asset_6639438cca6c454bbcf10542b600a46c'
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            value = json.loads(response(packet)['text'])
            value['items'][0]['assetId'] = real_id[:15]+real_id[20:]
            return {'text': json.dumps(value)}
        result = self.run_search(items=[candidate(real_id)], vision_call=call)
        self.assertEqual(result['candidates'][0]['placementStatus'], 'failed')

    def test_unknown_placement_protocol_never_calls_model(self):
        for value in ('writing-image-placement-3', '', None, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.run_search(placement_protocol=value)

    def test_invalid_frozen_prompt_never_falls_back(self):
        for value in (None, '', ' ', 12, '甲'*20001):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):
                    self.run_search(placement_system=value)

    def test_output_budget_batches_one_at_1800_and_two_when_safe(self):
        for budget, expected in ((1800, [1, 1, 1]), (2400, [2, 1])):
            calls = []
            def call(config, messages):
                packet = json.loads(messages[1]['content'][0]['text'])
                calls.append(len(packet['candidates']))
                return response(packet)
            result = self.run_search(items=[candidate(str(i)) for i in range(3)], model_config={**CONFIG, 'maxOutputTokens': budget}, vision_call=call)
            self.assertEqual(calls, expected)
            self.assertEqual(result['coverage']['visualReviewedCandidateCount'], 3)

    def test_too_small_output_budget_makes_no_request(self):
        calls = []
        result = self.run_search(model_config={**CONFIG, 'maxOutputTokens': 1000}, vision_call=lambda *a: calls.append(a))
        self.assertFalse(calls)
        self.assertEqual(result['candidates'][0]['placementSuggestions'], [])

    def test_all_chapter_paragraphs_are_reviewed_when_they_fit(self):
        text = '\n\n'.join(f'完整段落{i}说明对象。' for i in range(30))
        seen = []
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            seen.extend(packet['paragraphs'])
            return response(packet)
        result = self.run_search(section(text), vision_call=call)
        self.assertEqual([p['text'] for p in seen], text.split('\n\n'))
        self.assertTrue(result['candidates'][0]['placementCoverage']['complete'])

    def test_long_chapter_shortlists_late_match_from_all_paragraphs(self):
        values = [('无关产品基础说明。'*45) + str(i) for i in range(27)]
        values[-1] = 'Ohio One 工地桁架吊装。' + '施工情况。'*70
        text = '\n\n'.join(values)
        seen = []
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            seen.extend(packet['paragraphs'])
            return response(packet, [match('p26')])
        result = self.run_search(section(text), model_config={**CONFIG, 'contextChars': 7000}, vision_call=call)
        item = result['candidates'][0]
        self.assertIn('p26', [p['paragraphId'] for p in seen])
        self.assertTrue(all(p['text'] == values[int(p['paragraphId'][1:])] for p in seen))
        self.assertEqual(item['placementSuggestions'][0]['paragraphIndex'], 26)
        self.assertEqual(item['placementSuggestions'][0]['status'], 'needs_review')
        self.assertFalse(item['placementCoverage']['complete'])
        self.assertEqual(result['coverage']['lexicalParagraphCount'], 27)

    def test_lexical_ties_cover_end_of_chapter_instead_of_first_n(self):
        values = [f'段落{i}。' + '同类内容。'*90 for i in range(40)]
        result = self.run_search(section('\n\n'.join(values)), model_config={**CONFIG, 'contextChars': 6500})
        indexes = result['candidates'][0]['placementCoverage']['reviewedParagraphIndexes']
        self.assertIn(39, indexes)
        self.assertLess(len(indexes), 40)

    def test_oversized_paragraph_is_never_truncated_to_confirm(self):
        values = ['Ohio One 桁架吊装。'*2000+'结尾条件不能丢。', '另一个完整短段落。']
        seen = []
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            seen.extend(packet['paragraphs'])
            return response(packet)
        result = self.run_search(section('\n\n'.join(values)), model_config={**CONFIG, 'contextChars': 5000}, vision_call=call)
        self.assertEqual(seen, [{'paragraphId': 'p1', 'text': values[1]}])
        self.assertFalse(result['candidates'][0]['placementCoverage']['complete'])
        self.assertEqual(result['candidates'][0]['placementSuggestions'][0]['paragraphExact'], values[1])

    def test_nothing_fits_remains_manual_without_call(self):
        calls = []
        result = self.run_search(section('长段落。'*10000), model_config={**CONFIG, 'contextChars': 3000}, vision_call=lambda *a: calls.append(a))
        self.assertFalse(calls)
        self.assertEqual(result['candidates'][0]['placementStatus'], 'over_budget')
        self.assertEqual(result['candidates'][0]['placementSuggestions'], [])

    def test_partial_review_with_no_match_does_not_reject_unseen_paragraphs(self):
        result = self.run_search(section('\n\n'.join('段落内容。'*100 for _ in range(20))),
            model_config={**CONFIG, 'contextChars': 6500}, vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), []))
        self.assertEqual(result['candidates'][0]['status'], 'needs_review')
        self.assertEqual(result['candidates'][0]['placementStatus'], 'partial')

    def test_semantics_uses_complete_paragraphs_and_reuses_vectors(self):
        values = [f'段落{i}。'+'全段语义。'*80 for i in range(10)]
        seen = []
        def embed(config, texts):
            seen.extend(texts)
            return [[1., 0.] if value.startswith('段落9') or value.startswith('Ohio') else [0., 1.] for value in texts]
        result = self.run_search(section('\n\n'.join(values)), items=[candidate('a'), candidate('b')],
            model_config={**CONFIG, 'contextChars': 5000}, embedding_config={'contextChars': 16000}, embed_call=embed)
        for value in values:
            self.assertEqual(seen.count(value), 1)
        self.assertEqual(result['coverage']['semanticParagraphCount'], 10)
        self.assertEqual(result['coverage']['semanticStatus'], 'ready')
        self.assertIn(9, result['candidates'][0]['placementCoverage']['selectedParagraphIndexes'])

    def test_bad_embedding_degrades_to_lexical_without_leaking_error(self):
        def embed(config, texts):
            raise RuntimeError('SECRET_PROVIDER_BODY')
        result = self.run_search(section('\n\n'.join('较长段落。'*100 for _ in range(20))),
            model_config={**CONFIG, 'contextChars': 5000}, embedding_config={'contextChars': 16000}, embed_call=embed)
        self.assertEqual(result['coverage']['semanticStatus'], 'unavailable')
        self.assertNotIn('SECRET_PROVIDER_BODY', json.dumps(result))
        self.assertEqual(result['coverage']['visualReviewedCandidateCount'], 1)

    def test_invalid_visual_batch_does_not_partially_apply_first_item(self):
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            value = json.loads(response(packet)['text'])
            value['items'][-1]['assetId'] = 'unknown'
            return {'text': json.dumps(value)}
        result = self.run_search(items=[candidate('a'), candidate('b')], model_config={**CONFIG, 'maxOutputTokens': 2400}, vision_call=call)
        self.assertTrue(all(item['placementStatus'] == 'failed' and not item['placementSuggestions'] for item in result['candidates']))

    def test_guard_cancellation_before_and_after_model_is_not_swallowed(self):
        with self.assertRaises(ImagePlacementInterrupted):
            self.run_search(request_guard=lambda: (_ for _ in ()).throw(ValueError('stop')))
        state = {'cancel': False}
        def guard():
            if state['cancel']:
                raise ValueError('changed')
        def call(config, messages):
            state['cancel'] = True
            return response(json.loads(messages[1]['content'][0]['text']))
        with self.assertRaises(ImagePlacementInterrupted):
            self.run_search(vision_call=call, request_guard=guard)

    def test_guard_cancellation_during_embedding_is_not_a_fallback(self):
        state = {'cancel': False}
        def guard():
            if state['cancel']:
                raise ValueError('changed')
        def embed(config, texts):
            state['cancel'] = True
            return [[1., 0.] for _ in texts]
        with self.assertRaises(ImagePlacementInterrupted):
            self.run_search(section('\n\n'.join('较长段落。'*100 for _ in range(20))),
                model_config={**CONFIG, 'contextChars': 5000}, embedding_config={'contextChars': 16000}, embed_call=embed, request_guard=guard)

    def test_untrusted_image_url_cannot_trigger_remote_fetch(self):
        calls = []
        result = self.run_search(image_loader=lambda item: 'https://invalid.example/secret', vision_call=lambda *a: calls.append(a))
        self.assertFalse(calls)
        self.assertEqual(result['candidates'][0]['placementStatus'], 'unavailable')

    def test_twelve_image_limit_is_explicit_and_others_remain_unreviewed(self):
        result = self.run_search(items=[candidate(str(i)) for i in range(14)])
        self.assertEqual(result['coverage']['visualReviewedCandidateCount'], 12)
        self.assertTrue(result['coverage']['candidateLimitReached'])
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(sum(item['placementStatus'] == 'not_reviewed' for item in result['candidates']), 2)

    def test_mixed_scope_reserves_library_visual_slots_and_refills_gaps(self):
        for ref_count, lib_count, expected in ((16, 8, (9, 3)), (16, 1, (11, 1)), (2, 16, (2, 10))):
            with self.subTest(ref_count=ref_count, lib_count=lib_count):
                items = [candidate(f'ref{i}') for i in range(ref_count)] + [candidate(f'lib{i}', sourceType='library') for i in range(lib_count)]
                items.insert(0, candidate('bad-library', sourceType='library', status='unsuitable'))
                result = self.run_search(items=items)
                reviewed = [item for item in result['candidates'] if item.get('visualStatus') == 'reviewed']
                self.assertEqual((sum(item['sourceType'] == 'reference' for item in reviewed), sum(item['sourceType'] == 'library' for item in reviewed)), expected)
                self.assertEqual(next(item for item in result['candidates'] if item['assetId'] == 'bad-library')['placementSuggestions'], [])

    def test_internal_context_is_preserved_in_packet_without_becoming_an_anchor(self):
        context = '相邻原文仅供理解。'*75 + '末尾限定仍保留。'
        calls = []
        def call(config, messages):
            packet = json.loads(messages[1]['content'][0]['text'])
            calls.append(packet)
            return response(packet)
        result = self.run_search(items=[candidate(_contextText=context, _searchText='来源语境检索词')], vision_call=call)
        self.assertEqual(calls[0]['candidates'][0]['contextOnly'], context)
        self.assertEqual(result['candidates'][0]['placementSuggestions'][0]['paragraphExact'], anchor_for(section(), 2)['paragraphExact'])

    def test_reference_sorts_before_library_at_same_manual_quality(self):
        result = self.run_search(items=[candidate('library', sourceType='library'), candidate('ref', issues=['人工确认'])])
        self.assertEqual([item['assetId'] for item in result['candidates']], ['ref', 'library'])

    def test_valid_manual_library_position_ranks_before_unreviewed_reference(self):
        result = self.run_search(items=[candidate('ref', originalCaption='超长原图注。'*10000), candidate('library', sourceType='library')])
        self.assertEqual([item['assetId'] for item in result['candidates']], ['library', 'ref'])
        self.assertTrue(result['candidates'][0]['placementSuggestions'])
        self.assertFalse(result['candidates'][1]['placementSuggestions'])
        self.assertTrue(all(item['status'] == 'needs_review' for item in result['candidates']))

    def test_source_priority_does_not_override_actual_review_coverage(self):
        result = self.run_search(section('\n\n'.join('较长段落。'*100 for _ in range(20))),
            items=[candidate('ref', originalCaption='超长原图注。'*10000), candidate('library', sourceType='library')],
            model_config={**CONFIG, 'contextChars': 6500},
            vision_call=lambda c, m: response(json.loads(m[1]['content'][0]['text']), []))
        self.assertEqual([item['assetId'] for item in result['candidates']], ['library', 'ref'])
        self.assertEqual(result['candidates'][0]['placementStatus'], 'partial')
        self.assertEqual(result['candidates'][1]['placementStatus'], 'over_budget')
        self.assertTrue(all(not item['placementSuggestions'] and item['status'] == 'needs_review' for item in result['candidates']))

    def test_invalid_candidate_ids_fail_before_model(self):
        for items in ([candidate('a'), candidate('a')], [candidate(None)], [None]):
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    self.run_search(items=items)


class PlacementResponseTests(unittest.TestCase):
    def setUp(self):
        self.packet = {'paragraphs': [{'paragraphId': 'p0', 'text': '完整段落。'}, {'paragraphId': 'p1', 'text': '另一段落。'}],
                       'candidates': [{'assetId': 'a', 'paragraphIds': ['p0']}, {'assetId': 'b', 'paragraphIds': ['p1']}]}
        self.value = json.loads(response(self.packet)['text'])

    def validate(self, value):
        return validate_placement_response({'text': json.dumps(value, ensure_ascii=False)}, self.packet)

    def test_complete_response_and_empty_matches_are_valid(self):
        self.assertEqual(set(self.validate(self.value)), {'a', 'b'})
        self.value['items'][0]['matches'] = []
        self.assertEqual(self.validate(self.value)['a']['matches'], [])

    def test_schema_and_ids_are_closed(self):
        def mutations():
            yield lambda v: v.update(extra=True)
            yield lambda v: v['items'].pop()
            yield lambda v: v['items'].append(copy.deepcopy(v['items'][0]))
            yield lambda v: v['items'][0].update(assetId='b')
            yield lambda v: v['items'][0].update(assetId='outside')
            yield lambda v: v['items'][0].update(extra='new')
            yield lambda v: v['items'][0].pop('reason')
            yield lambda v: v['items'][0].update(reason='English only')
            yield lambda v: v['items'][0].update(reason='中'*61)
            yield lambda v: v['items'][0]['matches'][0].update(paragraphId='p1')
            yield lambda v: v['items'][0]['matches'][0].update(paragraphIndex=0)
            yield lambda v: v['items'][0]['matches'][0].update(paragraphExact='伪造正文')
            yield lambda v: v['items'][0]['matches'][0].update(contentHash='forged')
            yield lambda v: v['items'][0]['matches'][0].update(relevance='irrelevant')
            yield lambda v: v['items'][0]['matches'][0].update(readable=1)
            yield lambda v: v['items'][0]['matches'][0].update(contextConsistent='true')
            yield lambda v: v['items'][0]['matches'][0].update(reason='')
            yield lambda v: v['items'][0]['matches'].append(copy.deepcopy(v['items'][0]['matches'][0]))
        for index, mutation in enumerate(mutations()):
            with self.subTest(index=index):
                value = copy.deepcopy(self.value)
                mutation(value)
                with self.assertRaises(ValueError):
                    self.validate(value)

    def test_truncated_json_duplicate_keys_and_extra_text_fail(self):
        raw = json.dumps(self.value)
        for value in (raw[:-4], '```json\n'+raw+'\n```', raw+' trailing', '{"items":[],"items":[]}'):
            with self.subTest(value=value[:30]):
                with self.assertRaises(ValueError):
                    validate_placement_response({'text': value}, self.packet)

    def test_plain_or_escaped_credential_cannot_enter_reasons(self):
        for ensure_ascii in (True, False):
            value = copy.deepcopy(self.value)
            value['items'][0]['reason'] = '敏感密钥不可保存'
            raw = json.dumps(value, ensure_ascii=ensure_ascii)
            with self.assertRaises(ValueError) as raised:
                validate_placement_response({'text': raw}, self.packet, forbidden_text='敏感密钥')
            self.assertNotIn('敏感密钥', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
