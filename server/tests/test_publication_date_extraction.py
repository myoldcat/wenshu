import json
import unittest

from server.publication_date_extraction import extract_publication_date, normalize_publication_day


def snapshot(body='', head=''):
    return f'<html><head><meta charset="utf-8">{head}</head><body>{body}</body></html>'.encode()


class PublicationDateExtractionTests(unittest.TestCase):
    def test_complete_dates_preserve_original_day(self):
        for value in ['2024-02-29', '2024-02-29T23:59:59.123-12:00', '2024/2/29',
                      '2024年2月29日', 'February 29, 2024', '29 Feb 2024', '2024-02-29 12:00:00 +0800']:
            with self.subTest(value=value):
                self.assertEqual(normalize_publication_day(value), '2024-02-29')

    def test_incomplete_relative_invalid_and_ambiguous_dates_are_rejected(self):
        for value in ['昨天', '2024', '2024-02', '02/03/2024', '2026-02-29', '0000-01-01',
                      '2024-04-31', '2024-02-29T24:00Z', '2024-02-29T12:60Z',
                      '2024-02-29T12:00+99:00', '2024-02-29 trailing prose', None, 20240229]:
            with self.subTest(value=value):
                self.assertIsNone(normalize_publication_day(value))

    def test_explicit_head_meta_has_exact_evidence(self):
        result = extract_publication_date(snapshot(head='<meta property="article:published_time" content="2024-02-29T00:01+14:00">'))
        self.assertEqual(result['date'], '2024-02-29')
        self.assertEqual(result['evidence'][0]['locator'], '/html/head/meta[2]/@content')
        self.assertEqual(result['evidence'][0]['raw'], '2024-02-29T00:01+14:00')

    def test_generic_meta_updated_and_bare_time_are_not_publication(self):
        result = extract_publication_date(snapshot('<article><h1>标题</h1><time datetime="2024-02-29"></time></article>',
            '<meta name="date" content="2024-02-29"><meta property="article:modified_time" content="2024-03-01">'))
        self.assertIsNone(result['date'])

    def test_article_json_ld_matches_title_and_ignores_recommendations(self):
        data = {'@graph': [
            {'@type': 'NewsArticle', 'headline': '标题', 'datePublished': '2024-02-29'},
            {'@type': 'NewsArticle', 'headline': '其他文章', 'datePublished': '2020-01-01'},
        ]}
        result = extract_publication_date(snapshot(head=f'<script type="application/ld+json">{json.dumps(data)}</script>'), title='标题')
        self.assertEqual(result['date'], '2024-02-29')
        self.assertEqual(result['rejected'][0]['reason'], 'unrelated_article')

    def test_multiple_unmatched_json_articles_are_ambiguous(self):
        data = [{'@type': 'NewsArticle', 'datePublished': day} for day in ['2024-02-29', '2024-03-01']]
        result = extract_publication_date(snapshot(head=f'<script type="application/ld+json">{json.dumps(data)}</script>'))
        self.assertIsNone(result['date'])

    def test_json_modified_event_and_recommended_item_dates_are_ignored(self):
        data = {'@type': 'WebPage', 'datePublished': '2020-01-01', 'mainEntity':
                {'@type': 'Article', 'dateModified': '2024-03-01'}, 'itemListElement':
                [{'@type': 'NewsArticle', 'datePublished': '2024-02-29'}]}
        result = extract_publication_date(snapshot(head=f'<script type="application/ld+json">{json.dumps(data)}</script>'))
        self.assertIsNone(result['date'])

    def test_json_url_can_identify_main_article(self):
        data = [{'@type': 'Article', 'url': 'https://example.org/story', 'datePublished': '2024-02-29'},
                {'@type': 'Article', 'url': 'https://example.org/other', 'datePublished': '2024-03-01'}]
        result = extract_publication_date(snapshot(head=f'<script type="application/ld+json">{json.dumps(data)}</script>'), source_url='https://example.org/story#top')
        self.assertEqual(result['date'], '2024-02-29')

    def test_main_title_area_publication_label(self):
        result = extract_publication_date(snapshot('<article><h1>标题</h1><p>发布日期：2024年2月29日</p><p>这是一段实际文章正文，涉及历史事件与资料，但不是发表信息。</p></article>'), title='标题')
        self.assertEqual(result['date'], '2024-02-29')
        self.assertEqual(result['evidence'][0]['kind'], 'publication_label')

    def test_updates_imports_events_footer_and_urls_are_not_evidence(self):
        body = ('<article><h1>标题</h1><p>最后更新：2024-02-29</p><p>导入日期：2024-03-01</p>'
                '<p>2024年2月29日，公司发布了一款产品，本段仅说明事件日期而非文章发表日期。</p>'
                '<p>发布日期：2020-01-01</p><footer>Published: 2019-01-01</footer></article>')
        result = extract_publication_date(snapshot(body), source_url='https://example.org/2024/02/29/story')
        self.assertIsNone(result['date'])

    def test_microdata_requires_article_scope_and_publishing_semantics(self):
        result = extract_publication_date(snapshot('<article itemscope itemtype="https://schema.org/NewsArticle"><h1>标题</h1><time itemprop="datePublished" datetime="2024-02-29"></time><time itemprop="dateModified" datetime="2024-03-01"></time></article>'))
        self.assertEqual(result['date'], '2024-02-29')

    def test_nonarticle_microdata_is_ignored(self):
        result = extract_publication_date(snapshot('<article><h1>标题</h1><div itemscope itemtype="https://schema.org/Event"><time itemprop="datePublished" datetime="2024-02-29"></time></div></article>'))
        self.assertIsNone(result['date'])

    def test_multiple_html_articles_require_unique_title(self):
        body = '<article><h1>甲</h1><p>发布日期：2024-02-29</p></article><article><h1>乙</h1><p>发布日期：2024-03-01</p></article>'
        self.assertIsNone(extract_publication_date(snapshot(body))['date'])
        self.assertEqual(extract_publication_date(snapshot(body), title='甲')['date'], '2024-02-29')

    def test_conflicting_publication_evidence_rejects_without_priority_guess(self):
        result = extract_publication_date(snapshot('<article><h1>标题</h1><p>发布日期：2024-03-01</p></article>',
            '<meta property="article:published_time" content="2024-02-29">'))
        self.assertIsNone(result['date'])
        self.assertEqual(result['reason'], 'conflicting_publication_dates')
        self.assertEqual(len(result['evidence']), 2)

    def test_agreeing_evidence_keeps_all_original_locators(self):
        result = extract_publication_date(snapshot('<article><h1>标题</h1><p>Published: February 29, 2024</p></article>',
            '<meta property="article:published_time" content="2024-02-29">'))
        self.assertEqual(result['date'], '2024-02-29')
        self.assertEqual(len(result['evidence']), 2)

    def test_broken_json_does_not_execute_or_hide_explicit_meta(self):
        result = extract_publication_date(snapshot(head='<meta name="pubdate" content="2024-02-29"><script type="application/ld+json">fetch("https://example.org")</script>'))
        self.assertEqual(result['date'], '2024-02-29')
        self.assertEqual(result['rejected'][0]['reason'], 'invalid_json_ld')

    def test_snapshot_encoding_is_explicit(self):
        raw = '<html><head><meta charset="gb2312"></head><body><article><h1>标题</h1><p>发布日期：2024年2月29日</p></article></body></html>'.encode('gbk')
        self.assertEqual(extract_publication_date(raw)['date'], '2024-02-29')
        self.assertEqual(extract_publication_date(b'')['reason'], 'invalid_snapshot')

    def test_hkcert_publication_label_is_distinct_from_event_dateline(self):
        body = ('<main><div><div><h1>新闻标题</h1><div><div class="page-date page-date--btm">發佈日期: 2026年02月16日</div></div></div>'
                '<div><p>（香港，2026年2月13日）中心举办活动，这里是新闻中事件的时间，不能覆盖页面发表日期。</p></div></div></main>')
        self.assertEqual(extract_publication_date(snapshot(body), title='新闻标题')['date'], '2026-02-16')

    def test_wenweipo_timestamp_belongs_to_primary_title_container(self):
        body = ('<main><div><div class="title-bar"><h1>新闻标题</h1><div><div class="info-bar"><span class="time">2026-09-16 04:42:52</span></div></div></div></div>'
                '<div><p>这是一段实际文章正文，涉及历史事件与资料，但不是发表信息。</p></div>'
                '<aside><span class="time">2025-01-01 00:00:00</span></aside></main>')
        self.assertEqual(extract_publication_date(snapshot(body), source_url='https://www.wenweipo.com/a/202609/16/story.html')['date'], '2026-09-16')
        self.assertIsNone(extract_publication_date(snapshot(body), source_url='https://wenweipo.com.example.org/story')['date'])
        self.assertIsNone(extract_publication_date(snapshot(body))['date'])

    def test_pcpd_news_date_requires_news_path_and_primary_title_area(self):
        body = '<div><h1>新闻标题</h1><div id="mainContent"><strong>日期: 2026年4月23日</strong><p>这是一段实际文章正文，涉及历史事件与资料，但不是发表信息。</p></div></div>'
        url = 'https://www.pcpd.org.hk/tc_chi/news_events/media_statements/press_20260423.html'
        self.assertEqual(extract_publication_date(snapshot(body), source_url=url)['date'], '2026-04-23')
        self.assertIsNone(extract_publication_date(snapshot(body), source_url='https://www.pcpd.org.hk/calendar')['date'])

    def test_government_release_timestamp_is_not_body_event_date(self):
        body = ('<div><h1>新闻标题</h1><p>以下是今日活动的正文，文中提到2020年1月1日的历史活动，此处没有发布日期。</p>'
                '<span id="pressrelease"><div class="mB15 f15">2026年9月17日（星期四） 香港時間19時32分</div></span></div>')
        url = 'https://www.info.gov.hk/gia/general/202609/17/P202609170001.htm'
        self.assertEqual(extract_publication_date(snapshot(body), source_url=url)['date'], '2026-09-17')
        self.assertIsNone(extract_publication_date(snapshot(body), source_url='https://example.org/news')['date'])

    def test_collection_wrapper_date_never_becomes_publication_evidence(self):
        body = '<div id="dsh-page"><h1>职责</h1><div class="dsh-meta">职责 ｜ 日期：2026-01-01 ｜ 原文：https://example.org/2026/01/01/</div><p>法定职责与条例生效时间不是本页发表日期，不能将材料整理时的日期当成发布证据。</p></div>'
        self.assertIsNone(extract_publication_date(snapshot(body), title='职责', source_url='https://example.org/2026/01/01/')['date'])

    def test_pcpd_whatsnew_date_belongs_to_the_news_title(self):
        body = '<div><h1>新闻标题</h1><div class="contentArea"><h2>最新消息</h2><h3>新闻标题</h3><div id="mainContent"><strong>日期: 2026年8月17日</strong><p>文章内容。</p></div></div></div>'
        url = 'https://www.pcpd.org.hk/tc_chi/whatsnew/20260817a.html'
        self.assertEqual(extract_publication_date(snapshot(body), title='新闻标题', source_url=url)['date'], '2026-08-17')
        self.assertIsNone(extract_publication_date(snapshot(body), title='其他新闻', source_url=url)['date'])

    def test_chinanews_primary_timestamp_and_source_line_agree(self):
        body = ('<div class="content_maincontent_more"><h1>新闻标题</h1><div id="BaiduSpider"><span id="pubtime_baidu">2025-03-20 20:22:21</span></div>'
                '<div class="content_left_time">2025年03月20日 20:22 来源：中国新闻网</div><p>文中提到2026年1月1日的事件日期，这不是发表日期。</p></div>')
        result = extract_publication_date(snapshot(body), title='新闻标题', source_url='https://www.chinanews.com/dwq/story.shtml')
        self.assertEqual(result['date'], '2025-03-20')
        self.assertEqual(len(result['evidence']), 2)

    def test_hkcd_primary_msg_timestamp_excludes_recommended_cards(self):
        body = ('<div><h1>新闻标题 - 香港商報</h1><div class="newsDetailBox"><h2>新闻标题</h2><div class="msg"><span>来源</span><span>2025-01-24 08:33:26原創</span></div>'
                '<p>这里是新闻正文。</p></div><div class="list"><a class="newItem"><h3>其他文章</h3><div class="msg"><span>2025-01-23</span></div></a></div></div>')
        result = extract_publication_date(snapshot(body), title='新闻标题 - 香港商報', source_url='https://www.hkcd.com.hk/hkcdweb/content/story.html')
        self.assertEqual(result['date'], '2025-01-24')
        self.assertEqual(len(result['evidence']), 1)

    def test_stnn_primary_title_timestamp_excludes_side_news(self):
        body = ('<div><h1>新闻标题</h1><div class="title_con"><div class="ar_title"><h2>新闻标题</h2><div><span>2026-03-07 08:43</span></div></div></div>'
                '<aside><h3>其他文章</h3><span class="time">2026-10-27</span></aside></div>')
        result = extract_publication_date(snapshot(body), title='新闻标题', source_url='https://m.stnn.cc/detail/story.html')
        self.assertEqual(result['date'], '2026-03-07')

    def test_orange_new_detail_requires_matching_article_title(self):
        body = ('<div><h1>新闻标题 - 議事堂 - 橙新聞</h1><div><div class="new_title">新闻标题</div><ul class="new_detail">'
                '<li>作者甲</li><li>新闻栏目</li><li>2025-03-19 17:50</li></ul><div>文章正文。</div></div></div>')
        url = 'https://apps.orangenews.hk/app/common/details_html'
        self.assertEqual(extract_publication_date(snapshot(body), title='新闻标题 - 議事堂 - 橙新聞', source_url=url)['date'], '2025-03-19')
        self.assertIsNone(extract_publication_date(snapshot(body), title='别的文章', source_url=url)['date'])

    def test_orange_primary_detail_title_and_third_timestamp_field(self):
        body = ('<div id="dsh-page"><h1>新闻标题 - 議事堂 - 橙新聞</h1><div><ul class="new_detail">'
                '<li>新闻标题</li><li>作者甲</li><li>2025-03-19 17:50</li></ul><div>文章正文。</div></div></div>')
        url = 'https://apps.orangenews.hk/app/common/details_html'
        self.assertEqual(extract_publication_date(snapshot(body), title='新闻标题 - 議事堂 - 橙新聞', source_url=url)['date'], '2025-03-19')
        self.assertIsNone(extract_publication_date(snapshot(body), title='新闻标题续篇 - 議事堂 - 橙新聞', source_url=url)['date'])

    def test_chinanews_known_publisher_suffix_without_spaces(self):
        body = ('<div id="dsh-page"><h1>新闻标题</h1><div class="content_maincontent_more"><h1>新闻标题</h1>'
                '<div id="BaiduSpider"><span id="pubtime_baidu">2025-03-20 20:22:21</span></div></div></div>')
        url = 'https://www.chinanews.com/dwq/story.shtml'
        self.assertEqual(extract_publication_date(snapshot(body), title='新闻标题-中新网', source_url=url)['date'], '2025-03-20')
        self.assertIsNone(extract_publication_date(snapshot(body), title='新闻标题-附加主题', source_url=url)['date'])

    def test_hkcd_original_heading_survives_duplicate_dsh_heading(self):
        body = ('<div id="dsh-page"><h1>新闻标题</h1><div class="newsDetailBox"><h1>新闻标题</h1>'
                '<div class="msg"><span>责任编辑甲</span><span>2025-01-24 08:33:26原創</span><span>来源：香港商報</span></div>'
                '<p>新闻正文。</p></div></div>')
        url = 'https://www.hkcd.com.hk/hkcdweb/content/story.html'
        self.assertEqual(extract_publication_date(snapshot(body), title='新闻标题 - 香港商報', source_url=url)['date'], '2025-01-24')
        self.assertIsNone(extract_publication_date(snapshot(body), title='新闻标题-附加主题 - 香港商報', source_url=url)['date'])

    def test_hkcd_timestamp_allows_nested_original_badge(self):
        body = ('<div id="dsh-page"><h1>新闻标题</h1><div class="newsDetailBox"><h1>新闻标题</h1>'
                '<div class="msg"><span>责任编辑甲</span><span>2025-01-24 08:33:26<span>原創</span></span><span>来源：香港商報</span></div>'
                '<p>新闻正文。</p></div></div>')
        url = 'https://www.hkcd.com.hk/hkcdweb/content/story.html'
        result = extract_publication_date(snapshot(body), title='新闻标题 - 香港商報', source_url=url)
        self.assertEqual(result['date'], '2025-01-24')
        self.assertEqual(len(result['evidence']), 1)
        self.assertEqual(result['evidence'][0]['context']['timestampText'], '2025-01-24 08:33:26 原創')

    def test_global_meta_cannot_date_an_ambiguous_article_collection(self):
        body = '<article><h1>甲</h1></article><article><h1>乙</h1></article>'
        result = extract_publication_date(snapshot(body, '<meta name="pubdate" content="2024-02-29">'))
        self.assertIsNone(result['date'])
        self.assertEqual(result['rejected'][0]['reason'], 'ambiguous_global_publication_date')

    def test_selected_article_uses_own_label_instead_of_global_collection_date(self):
        body = ('<article><h1>甲</h1><p>发布日期：2024-03-01</p></article>'
                '<article><h1>乙</h1><p>发布日期：2024-02-29</p></article>')
        result = extract_publication_date(snapshot(body, '<meta name="pubdate" content="2024-02-29">'), title='甲')
        self.assertEqual(result['date'], '2024-03-01')
        self.assertEqual(len(result['evidence']), 1)

    def test_json_article_ambiguity_is_document_wide_across_scripts(self):
        scripts = ''.join(f'<script type="application/ld+json">{json.dumps(data)}</script>' for data in [
            {'@type': 'Article', 'datePublished': '2024-02-29'},
            {'@type': 'Article', 'datePublished': '2024-03-01'}])
        result = extract_publication_date(snapshot(head='<meta name="pubdate" content="2024-02-29">' + scripts))
        self.assertIsNone(result['date'])
        self.assertEqual(result['evidence'], [])

    def test_undated_main_json_article_still_disambiguates_dated_other_article(self):
        data = [{'@type': 'Article', 'headline': '本篇'}, {'@type': 'Article', 'datePublished': '2024-02-29'}]
        result = extract_publication_date(snapshot(head=f'<script type="application/ld+json">{json.dumps(data)}</script>'), title='本篇')
        self.assertIsNone(result['date'])

    def test_json_article_url_must_not_contradict_the_requested_source(self):
        for data in [
            {'@type': 'Article', 'url': 'https://example.org/other', 'datePublished': '2024-02-29'},
            {'@type': 'Article', 'headline': '本篇', 'url': 'https://example.org/other', 'datePublished': '2024-02-29'},
            {'@type': 'Article', 'headline': '本篇', 'url': 'https://example.org/other',
             'mainEntityOfPage': 'https://example.org/main', 'datePublished': '2024-02-29'},
        ]:
            with self.subTest(data=data):
                result = extract_publication_date(snapshot(head=f'<script type="application/ld+json">{json.dumps(data)}</script>'),
                                                  title='本篇', source_url='https://example.org/main')
                self.assertIsNone(result['date'])

    def test_recommendation_quote_and_collection_contexts_never_supply_a_date(self):
        for wrapper in ['<aside>{}</aside>', '<nav>{}</nav>', '<footer>{}</footer>', '<blockquote>{}</blockquote>',
                        '<div class="relatedlist">{}</div>', '<div class="rmtj_date">{}</div>',
                        '<div class="dsh-meta">{}</div>', '<a href="/other">{}</a>']:
            with self.subTest(wrapper=wrapper):
                publication = '<span>Published: 2024-02-29</span><time itemprop="datePublished" datetime="2024-02-29"></time>'
                result = extract_publication_date(snapshot('<article><h1>本篇</h1>' + wrapper.format(publication) + '</article>'), title='本篇')
                self.assertIsNone(result['date'])

    def test_nested_article_microdata_is_not_main_article_evidence(self):
        body = ('<article itemscope itemtype="https://schema.org/Article"><h1>本篇</h1>'
                '<article itemscope itemtype="https://schema.org/Article"><h2>其他文章</h2>'
                '<time itemprop="datePublished" datetime="2024-02-29"></time></article></article>')
        self.assertIsNone(extract_publication_date(snapshot(body), title='本篇')['date'])

    def test_microdata_owner_requires_explicit_article_type_and_matching_identity(self):
        for owner in ['itemscope', 'itemscope itemtype="https://schema.org/Event"',
                      'itemscope itemtype="https://schema.org/Article"']:
            with self.subTest(owner=owner):
                body = ('<article><h1>本篇</h1><div ' + owner + '><h2>其他文章</h2>'
                        '<time itemprop="datePublished" datetime="2024-02-29"></time></div></article>')
                self.assertIsNone(extract_publication_date(snapshot(body), title='本篇')['date'])

    def test_global_meta_does_not_choose_between_microdata_articles(self):
        body = ''.join('<div itemscope itemtype="https://schema.org/Article"><h1>' + title + '</h1></div>'
                       for title in ['甲', '乙'])
        result = extract_publication_date(snapshot(body, '<meta name="pubdate" content="2024-02-29">'))
        self.assertIsNone(result['date'])

    def test_mismatching_heading_cannot_supply_a_local_publication_label(self):
        result = extract_publication_date(snapshot('<article><h1>其他文章</h1><p>发布日期：2024-02-29</p></article>'), title='本篇')
        self.assertIsNone(result['date'])

    def test_even_a_short_body_paragraph_closes_the_header_date_area(self):
        body = '<article><h1>本篇</h1><p>事件已经结束。</p><p>发布日期：2024-02-29</p></article>'
        self.assertIsNone(extract_publication_date(snapshot(body), title='本篇')['date'])

    def test_explicit_body_container_and_quotation_close_generic_header_area(self):
        for body in ['<div class="article-body"><span>Published: 2024-02-29</span></div>',
                     '<blockquote>历史报道引用。</blockquote><p>Published: 2024-02-29</p>',
                     '<div>事件已经结束。</div><p>Published: 2024-02-29</p>']:
            with self.subTest(body=body):
                self.assertIsNone(extract_publication_date(snapshot('<article><h1>本篇</h1>' + body + '</article>'), title='本篇')['date'])

    def test_invalid_relevant_publication_field_blocks_fallback_to_another_candidate(self):
        for invalid in ['2026-02-29', '2024-02', '昨天', '']:
            with self.subTest(invalid=invalid):
                result = extract_publication_date(snapshot('<article><h1>本篇</h1><p>Published: 2024-02-29</p></article>',
                    f'<meta name="pubdate" content="{invalid}">'), title='本篇')
                self.assertIsNone(result['date'])
                self.assertEqual(result['reason'], 'invalid_publication_evidence')

    def test_invalid_unrelated_json_publication_cannot_block_an_explicit_main_date(self):
        data = [{'@type': 'Article', 'headline': '本篇', 'datePublished': '2024-02-29'},
                {'@type': 'Article', 'headline': '其他文章', 'datePublished': 'yesterday'}]
        result = extract_publication_date(snapshot(head=f'<script type="application/ld+json">{json.dumps(data)}</script>'), title='本篇')
        self.assertEqual(result['date'], '2024-02-29')

    def test_wenweipo_title_time_does_not_match_a_different_requested_article(self):
        body = '<main><div class="title-bar"><h1>其他文章</h1><div class="info-bar"><span class="time">2024-02-29 12:00:00</span></div></div></main>'
        result = extract_publication_date(snapshot(body), title='本篇', source_url='https://www.wenweipo.com/story')
        self.assertIsNone(result['date'])

    def test_orange_cannot_borrow_main_heading_for_a_different_detail_listing(self):
        body = ('<div><h1>本篇</h1><ul class="new_detail"><li>其他文章</li><li>作者甲</li><li>2024-02-29 12:00</li></ul>'
                '<ul class="new_detail"><li>另一篇</li><li>作者乙</li><li>2024-02-29 13:00</li></ul></div>')
        result = extract_publication_date(snapshot(body), title='本篇', source_url='https://apps.orangenews.hk/story')
        self.assertIsNone(result['date'])

    def test_plain_span_prose_and_new_subheading_end_generic_metadata_area(self):
        for prose in ['<span>本次事件已经结束。</span>', '<h3>相关文章</h3>']:
            with self.subTest(prose=prose):
                body = '<article><h1>本篇</h1>' + prose + '<p>Published: 2024-02-29</p></article>'
                self.assertIsNone(extract_publication_date(snapshot(body), title='本篇')['date'])

    def test_inline_title_and_publication_children_do_not_hide_header_conflicts(self):
        body = ('<article><h1><span>本篇</span></h1><p>Published: <b>2024-02-29</b></p>'
                '<p>发布日期：<strong>2024-03-01</strong></p></article>')
        result = extract_publication_date(snapshot(body), title='本篇')
        self.assertIsNone(result['date'])
        self.assertEqual(result['reason'], 'conflicting_publication_dates')

    def test_duplicate_dom_titles_need_a_unique_json_source_url_binding(self):
        body = '<article><h1>甲</h1></article><article><h1>甲</h1></article>'
        main = {'@type': 'Article', 'headline': '甲', 'datePublished': '2024-02-29'}
        bound = dict(main, url='https://example.org/main')
        for objects, source_url, expected in [
            ([main], None, None),
            ([dict(main, url='https://example.org/other')], 'https://example.org/main', None),
            ([bound], 'https://example.org/main', '2024-02-29'),
            ([bound, bound], 'https://example.org/main', None),
        ]:
            with self.subTest(objects=objects, source_url=source_url):
                head = f'<script type="application/ld+json">{json.dumps(objects)}</script>'
                result = extract_publication_date(snapshot(body, head), title='甲', source_url=source_url)
                self.assertEqual(result['date'], expected)


if __name__ == '__main__':
    unittest.main()
