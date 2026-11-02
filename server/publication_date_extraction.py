"""Conservative, offline publication-day evidence from an original HTML snapshot.

This module neither reparses stored blocks nor writes metadata. A caller must
review and version-check its result before changing a stored revision. All
returned locators address this input document; scripts are parsed as JSON only.
"""
from __future__ import annotations

from datetime import date
import json
import re
from urllib.parse import urlsplit, urlunsplit

from lxml import etree, html


MAX_BYTES = 64 * 1024 * 1024
_SPACE = re.compile(r'\s+')
_ARTICLE_TYPES = {'article', 'newsarticle', 'blogposting', 'report', 'techarticle', 'scholarlyarticle'}
_PUBLICATION_META = {
    'article:published_time', 'og:published_time', 'datepublished', 'pubdate',
    'publishdate', 'publish-date', 'publication_date', 'publication-date',
    'dc.date.issued', 'dcterms.issued', 'citation_publication_date',
}
_LABEL = re.compile(
    r'^(?:发布日期|發[佈布]日期|发表日期|發表日期|发布时间|發[佈布]時間|发表时间|發表時間|'
    r'发布于|發[佈布]於|发表于|發表於|published(?:\s+(?:on|at))?|publication\s+date|release\s+date)'
    r'\s*[:：|]?\s*(.+)$', re.I)
_NON_PUBLICATION = re.compile(
    r'^(?:更新|最后更新|最後更新|抓取|采集|採集|导入|導入|下载|下載|访问|訪問|'
    r'updated|last\s+updated|retrieved|fetched|imported|downloaded|accessed)', re.I)
_MONTHS = {name: index for index, name in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
     'september', 'october', 'november', 'december'], 1)}
_MONTHS.update({name[:3]: value for name, value in list(_MONTHS.items())})
_TIME = (r'(?:[Tt ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?'
         r'(?:\s*(?:[Zz]|[+-](?:[01]\d|2[0-3]):?[0-5]\d|UTC|GMT))?)?')
_EXCLUDED = re.compile(
    r'(?:^|[-_\s])(?:dsh-meta|related(?:list|posts|articles)?|recommend(?:ed|ations?)?|'
    r'sidebar|comments?|rmtj(?:[-_]date)?|newsletter)(?:$|[-_\s])', re.I)
_BODY = re.compile(r'(?:^|[-_\s])(?:article[-_]?body|article[-_]?content|entry[-_]?content|'
                   r'post[-_]?content|content[-_]?body|body[-_]?text|正文)(?:$|[-_\s])', re.I)


def _text(value):
    return _SPACE.sub(' ', str(value or '').replace('\u200b', '')).strip()


def _day(year, month, day):
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except (TypeError, ValueError):
        return None


def normalize_publication_day(value):
    """Accept a complete explicit date, never locale guesses or relative dates."""
    if not isinstance(value, str):
        return None
    value = _text(value)
    for pattern in [r'(\d{4})-(\d{1,2})-(\d{1,2})',
                    r'(\d{4})/(\d{1,2})/(\d{1,2})',
                    r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日']:
        match = re.fullmatch(pattern + _TIME, value)
        if match:
            return _day(*match.group(1, 2, 3))
    value = re.sub(r'^(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)[,]?\s+', '', value, flags=re.I)
    match = re.fullmatch(r'([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})' + _TIME, value, re.I)
    if match and match[1].lower() in _MONTHS:
        return _day(match[3], _MONTHS[match[1].lower()], match[2])
    match = re.fullmatch(r'(\d{1,2})\s+([A-Za-z]+)\.?[,]?\s+(\d{4})' + _TIME, value, re.I)
    if match and match[2].lower() in _MONTHS:
        return _day(match[3], _MONTHS[match[2].lower()], match[1])
    return None


def _url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/') or '/', parsed.query, ''))
    except ValueError:
        return None


def _site(value, domain):
    try:
        hostname = (urlsplit(value or '').hostname or '').lower()
        return hostname == domain or hostname.endswith('.' + domain)
    except (ValueError, TypeError):
        return False


def _excluded(node):
    """Quoted, recommended and collection-added data never dates this article."""
    for item in [node, *node.iterancestors()]:
        if not isinstance(item.tag, str):
            continue
        if item.tag.lower() in ('aside', 'nav', 'footer', 'blockquote', 'a'):
            return True
        if item.get('role', '').lower() in ('complementary', 'navigation', 'contentinfo'):
            return True
        if _EXCLUDED.search((item.get('class') or '') + ' ' + (item.get('id') or '')):
            return True
    return False


def _article_nodes(tree):
    return [node for node in tree.xpath('//article[not(ancestor::article)]') if not _excluded(node)]


def _heading(scope, title):
    headings = [node for node in scope.xpath('.//h1') if not _excluded(node)]
    original_headings = [node for node in headings if node.getparent().get('id') != 'dsh-page']
    if original_headings:
        headings = original_headings
    if not headings and title:
        headings = [node for node in scope.xpath('.//h2') if not _excluded(node)
                    and _title_compatible(' '.join(node.itertext()), title)]
    matched = [node for node in headings if title and _title_compatible(' '.join(node.itertext()), title)]
    return matched[0] if len(matched) == 1 else headings[0] if len(headings) == 1 else None


def _title_compatible(actual, wanted):
    actual, wanted = _text(actual), _text(wanted)
    if not actual or not wanted:
        return False
    # Only known publisher/category suffixes may differ from the full heading;
    # an arbitrary article-title prefix is never an identity match.
    without_publisher = re.sub(
        r'\s*-\s*(?:(?:香港|內地|内地|評論|评论|快評|快评|社評|社评|來論|来论|議事堂|议事堂|港聞|港闻)\s*-\s*)?'
        r'(?:香港文匯報|香港文汇报|香港商報|香港商报|中新网|中新網|橙新聞|橙新闻|大公文匯網|大公文汇网)$',
        '', wanted).strip()
    return actual == wanted or actual == without_publisher


def _contains_title(node, title, extra_xpath=''):
    query = './/h1 | .//h2 | .//h3' + extra_xpath
    return any(not _excluded(item) and _title_compatible(' '.join(item.itertext()), title)
               for item in node.xpath(query))


def _identity(headline, urls, wanted_title, wanted_url):
    """A matching title must not override a contradictory explicit source URL."""
    title_known = bool(wanted_title and headline)
    urls = {value for value in urls if value}
    url_known = bool(wanted_url and urls)
    if title_known and not _title_compatible(headline, wanted_title):
        return 'unrelated_article'
    if url_known and urls != {wanted_url}:
        return 'unrelated_article'
    if title_known or url_known:
        return 'matched_article'
    return 'unidentified_article'


def _local_article(node, scope):
    """Reject a second/nested article even when it is embedded in the main DOM."""
    owners = node.xpath('ancestor::article[1]')
    if scope.tag == 'article':
        return not owners or owners[0] is scope
    return not owners


def _type_names(value):
    return {str(item).rstrip('/').rsplit('/', 1)[-1].lower()
            for item in (value if isinstance(value, list) else [value])}


def _json_articles(value, pointer='$'):
    """Inspect graph roots/mainEntity, never recommended-story/item-list dates."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _json_articles(item, f'{pointer}[{index}]')
    elif isinstance(value, dict):
        if _type_names(value.get('@type')) & _ARTICLE_TYPES:
            yield value, pointer
        for key in ('@graph', 'mainEntity'):
            if key in value:
                yield from _json_articles(value[key], f'{pointer}.{key}')


def _decode(raw):
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ValueError('Snapshot is empty or outside the HTML byte limit')
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        declaration = re.search(br'charset\s*=\s*[\"\x27]?\s*([a-zA-Z0-9_-]+)', raw[:8192], re.I)
        if declaration:
            encoding = declaration[1].decode('ascii').lower()
            if encoding in ('gbk', 'gb2312'):
                encoding = 'gb18030'
            return raw.decode(encoding)
        raise ValueError('Snapshot encoding is not explicit')


def _article_scope(tree, title):
    articles = _article_nodes(tree)
    if len(articles) == 1:
        return articles[0], None
    if len(articles) > 1:
        matched = [node for node in articles if _contains_title(node, title)] if title else []
        return (matched[0], None) if len(matched) == 1 else (None, 'ambiguous_article_scope')
    mains = tree.xpath('//main')
    if len(mains) == 1:
        return mains[0], None
    bodies = tree.xpath('//body')
    return (bodies[0], None) if len(bodies) == 1 else (None, 'missing_article_scope')


def extract_publication_date(raw, *, title=None, source_url=None):
    """Return an ISO day only when all relevant explicit evidence agrees.

    Result: ``date``, accepted ``evidence`` (kind/raw/locator/date), ``rejected``
    evidence with reasons, and an overall ``reason``. No filesystem or network IO.
    """
    evidence, rejected = [], []
    try:
        text = _decode(raw)
        if not text or len(text.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Snapshot is empty or outside the HTML byte limit')
        tree = html.document_fromstring(text, parser=html.HTMLParser(no_network=True))
    except (ValueError, LookupError, UnicodeError, etree.Error) as exc:
        return {'date': None, 'evidence': [], 'rejected': [], 'reason': 'invalid_snapshot', 'detail': str(exc)}
    wanted_title = _text(title)
    if not wanted_title:
        wanted_title = _text(next(iter(tree.xpath('//head/meta[@property="og:title"]/@content')), ''))
    scope, scope_reason = _article_scope(tree, wanted_title)
    wanted_url = _url(source_url)
    roottree = tree.getroottree()
    json_candidates = []
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        if _excluded(script) or (scope is not None and not _local_article(script, scope)):
            continue
        try:
            json_candidates.extend((script, obj, pointer)
                                   for obj, pointer in _json_articles(json.loads(script.text or '')))
        except (ValueError, TypeError, RecursionError):
            rejected.append({'kind': 'json_ld', 'raw': '', 'locator': roottree.getpath(script),
                             'reason': 'invalid_json_ld'})
    # A document-wide date cannot be assigned to one member of a collection.
    article_owners = [node for node in tree.xpath('//*[@itemscope and @itemtype]')
                      if not _excluded(node) and _type_names(node.get('itemtype').split()) & _ARTICLE_TYPES]
    multiple_articles = len(_article_nodes(tree)) > 1 or len(article_owners) > 1 or len(json_candidates) > 1

    def add(kind, raw_value, node, suffix='', reason=None, context=None):
        item = {'kind': kind, 'raw': str(raw_value or ''), 'locator': roottree.getpath(node) + suffix}
        if context:
            item['context'] = context
        day = normalize_publication_day(raw_value)
        if not reason and _excluded(node):
            reason = 'excluded_article_context'
        if not reason and node.tag != 'meta' and node.tag != 'script' and scope is not None and not _local_article(node, scope):
            reason = 'unrelated_article'
        if reason:
            rejected.append(dict(item, reason=reason))
        elif day:
            evidence.append(dict(item, date=day))
        else:
            rejected.append(dict(item, reason='incomplete_or_invalid_publication_date'))

    for node in tree.xpath('//head/meta[@content]'):
        key = (node.get('property') or node.get('name') or node.get('itemprop') or '').lower().strip()
        if key in _PUBLICATION_META:
            add('publication_meta', node.get('content'), node, '/@content',
                reason='ambiguous_global_publication_date' if multiple_articles or scope_reason == 'ambiguous_article_scope' else None)

    def json_urls(obj):
        entity = obj.get('mainEntityOfPage')
        entity_url = _url(entity.get('@id')) if isinstance(entity, dict) else _url(entity)
        return {value for value in (_url(obj.get('url')), _url(obj.get('@id')), entity_url) if value}

    unique_url_binding = bool(wanted_url and sum(json_urls(obj) == {wanted_url}
                                                for _, obj, _ in json_candidates) == 1)
    for script, obj, pointer in json_candidates:
        if 'datePublished' not in obj:
            continue
        headline = _text(obj.get('headline') or obj.get('name'))
        urls = json_urls(obj)
        identity = _identity(headline, urls, wanted_title, wanted_url)
        reason = 'unrelated_article' if identity == 'unrelated_article' else (
            'ambiguous_article_scope' if (multiple_articles and identity != 'matched_article') or (
                scope_reason == 'ambiguous_article_scope' and not (
                    unique_url_binding and urls == {wanted_url})) else None)
        add('json_ld_datePublished', obj.get('datePublished'), script, f'#{pointer}.datePublished', reason)

    if scope is not None:
        heading = _heading(scope, wanted_title)
        heading_text = _text(' '.join(heading.itertext())) if heading is not None else ''
        heading_matches = bool(heading is not None and (not wanted_title or _title_compatible(heading_text, wanted_title)))
        for node in scope.xpath('.//*[@itemprop] | .//time[@pubdate]'):
            props = {value.lower() for value in (node.get('itemprop') or '').split()}
            if 'datepublished' not in props and not (node.tag == 'time' and node.get('pubdate') is not None):
                continue
            owner = next(iter(node.xpath('ancestor::*[@itemscope][1]')), None)
            if _excluded(node) or not _local_article(node, scope):
                continue
            if owner is not None:
                if not (_type_names(owner.get('itemtype', '').split()) & _ARTICLE_TYPES):
                    continue
                own_titles = [_text(' '.join(item.itertext())) for item in owner.xpath(
                    './/*[@itemprop="headline"] | .//h1 | .//h2') if not _excluded(item)]
                owner_title = next((value for value in own_titles if wanted_title and _title_compatible(value, wanted_title)),
                                   own_titles[0] if len(own_titles) == 1 else '')
                identity = _identity(owner_title, (_url(owner.get('itemid')),), wanted_title, wanted_url)
                if identity == 'unrelated_article' or (owner is not scope and identity != 'matched_article' and not (
                        not wanted_title and len(article_owners) == 1 and heading_matches)):
                    continue
            elif scope.tag != 'article' or not heading_matches:
                continue
            add('publication_microdata', node.get('content') or node.get('datetime') or ' '.join(node.itertext()), node)

        if heading_matches:
            nodes = list(scope.iter())
            heading_index = nodes.index(heading)
            stop_index = min(len(nodes), heading_index + 100)
            metadata_nodes = set()
            for index, node in enumerate(nodes[heading_index + 1:stop_index], heading_index + 1):
                if not isinstance(node.tag, str):
                    continue
                if heading in node.iterancestors() or any(id(parent) in metadata_nodes for parent in node.iterancestors()):
                    continue
                if node.tag.lower() == 'blockquote':
                    stop_index = index
                    break
                if _excluded(node) or not _local_article(node, scope):
                    continue
                if node.tag.lower() in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                    stop_index = index
                    break
                if _BODY.search((node.get('class') or '') + ' ' + (node.get('id') or '')):
                    stop_index = index
                    break
                value = _text(' '.join(node.itertext()))
                if value and len(value) <= 240 and (_LABEL.match(value) or _NON_PUBLICATION.match(value) or re.match(
                        r'^(?:作者|來源|来源|By\b|Source\b)\s*[:：]?', value, re.I)):
                    metadata_nodes.add(id(node))
                    continue
                if value and len(value) <= 200 and re.search(r'(?:^|[-_\s])(?:author|byline|source)(?:$|[-_\s])',
                                                           node.get('class', ''), re.I):
                    metadata_nodes.add(id(node))
                    continue
                if value and (node.tag.lower() == 'p' or (node.tag.lower() in ('div', 'span', 'li', 'dd') and not node.xpath('./*'))):
                    stop_index = index
                    break
            for index, node in enumerate(nodes):
                if not isinstance(node.tag, str) or node.tag.lower() not in ('p', 'span', 'div', 'time', 'li', 'dd'):
                    continue
                if not (heading_index < index < stop_index):
                    continue
                if _excluded(node) or not _local_article(node, scope) or node.xpath('ancestor::script'):
                    continue
                # Only leaf-ish local text: parent wrappers repeat full body text.
                if node.xpath('./p | ./div | ./li | ./h1 | ./h2'):
                    continue
                value = _text(' '.join(node.itertext()))
                match = _LABEL.match(value)
                if match and len(value) <= 240:
                    add('publication_label', match[1], node)

        if heading is not None:
            # Reviewed snapshot templates. A site's title metadata is not the
            # same thing as any arbitrarily date-looking text on that domain.
            if heading_matches and _site(source_url, 'wenweipo.com'):
                title_container = heading.getparent()
                if 'title-bar' not in (title_container.get('class') or '').split():
                    title_container = html.fromstring('<div></div>')
                for node in title_container.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," info-bar ")]/span[contains(concat(" ",normalize-space(@class)," ")," time ")]'):
                    if node.xpath('ancestor::a | ancestor::aside | ancestor::footer'):
                        continue
                    add('wenweipo_title_publication_time', _text(' '.join(node.itertext())), node,
                        context={'heading': heading_text, 'sourceUrl': source_url})

            if _site(source_url, 'pcpd.org.hk') and any(part in (urlsplit(source_url).path or '') for part in ('/news_events/', '/whatsnew/')):
                for content in scope.xpath('.//*[@id="mainContent"]'):
                    area = content.getparent()
                    if wanted_title and not _contains_title(area, wanted_title):
                        continue
                    for node in content.xpath('./strong[1]'):
                        match = re.fullmatch(r'日期\s*[:：]\s*(.+)', _text(' '.join(node.itertext())))
                        if match:
                            add('pcpd_news_publication_date', match[1], node,
                                context={'heading': heading_text, 'sourceUrl': source_url})

            if heading_matches and _site(source_url, 'info.gov.hk') and '/gia/general/' in (urlsplit(source_url).path or ''):
                for node in scope.xpath('.//*[@id="pressrelease"]//*[contains(concat(" ",normalize-space(@class)," ")," mB15 ") and contains(concat(" ",normalize-space(@class)," ")," f15 ")]'):
                    value = _text(' '.join(node.itertext()))
                    match = re.fullmatch(r'(\d{4}年\d{1,2}月\d{1,2}日)[（(]星期[一二三四五六日天][）)]\s*香港時間(?:[01]?\d|2[0-3])時[0-5]?\d分', value)
                    if match and not node.xpath('ancestor::a | ancestor::aside | ancestor::footer'):
                        add('hkgov_press_release_publication_time', match[1], node,
                            context={'heading': heading_text, 'timestampText': value, 'sourceUrl': source_url})

            if _site(source_url, 'chinanews.com'):
                for container in scope.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," content_maincontent_more ")]'):
                    if not _contains_title(container, wanted_title or heading_text):
                        continue
                    for node in container.xpath('.//*[@id="pubtime_baidu"]'):
                        if not node.xpath('ancestor::a | ancestor::aside'):
                            add('chinanews_article_publication_time', _text(' '.join(node.itertext())), node,
                                context={'heading': heading_text, 'sourceUrl': source_url})
                    for node in container.xpath('./*[contains(concat(" ",normalize-space(@class)," ")," content_left_time ")]'):
                        value = _text(' '.join(node.itertext()))
                        match = re.fullmatch(r'(\d{4}年\d{1,2}月\d{1,2}日\s+[0-2]\d:[0-5]\d(?::[0-5]\d)?)\s+来源[:：].+', value)
                        if match:
                            add('chinanews_article_publication_time', match[1], node,
                                context={'heading': heading_text, 'timestampText': value, 'sourceUrl': source_url})

            if _site(source_url, 'hkcd.com.hk') or _site(source_url, 'hkcd.com'):
                for container in scope.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," newsDetailBox ")]'):
                    if not _contains_title(container, wanted_title or heading_text):
                        continue
                    for node in container.xpath('./*[contains(concat(" ",normalize-space(@class)," ")," msg ")]/span'):
                        value = _text(' '.join(node.itertext()))
                        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}\s+[0-2]\d:[0-5]\d:[0-5]\d)\s*(?:原創|原创)?', value)
                        if match:
                            add('hkcd_article_publication_time', match[1], node,
                                context={'heading': heading_text, 'timestampText': value, 'sourceUrl': source_url})

            if _site(source_url, 'stnn.cc'):
                for container in scope.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," ar_title ")]'):
                    if not _contains_title(container, wanted_title or heading_text):
                        continue
                    for node in container.xpath('.//span'):
                        value = _text(' '.join(node.itertext()))
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\s+[0-2]\d:[0-5]\d', value) and not node.xpath('ancestor::a | ancestor::aside'):
                            add('stnn_article_publication_time', value, node,
                                context={'heading': heading_text, 'sourceUrl': source_url})

            if _site(source_url, 'orangenews.hk'):
                for listing in scope.xpath('.//ul[contains(concat(" ",normalize-space(@class)," ")," new_detail ")]'):
                    container = listing.getparent()
                    first = listing.xpath('./li[1]')
                    listing_title = bool(first and _title_compatible(' '.join(first[0].itertext()), wanted_title or heading_text))
                    local_titles = container.xpath('./h1 | ./h2 | ./h3 | ./*[contains(concat(" ",normalize-space(@class)," ")," new_title ")]')
                    local_title = any(not _excluded(item) and _title_compatible(' '.join(item.itertext()), wanted_title or heading_text)
                                      for item in local_titles)
                    sibling_lists = container.xpath('./ul[contains(concat(" ",normalize-space(@class)," ")," new_detail ")]')
                    if not listing_title and not (local_title and len(sibling_lists) == 1):
                        continue
                    for node in listing.xpath('./li[3]'):
                        value = _text(' '.join(node.itertext()))
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\s+[0-2]\d:[0-5]\d', value):
                            add('orange_article_publication_time', value, node,
                                context={'heading': heading_text, 'sourceUrl': source_url})

    days = {item['date'] for item in evidence}
    if any(item['reason'] == 'incomplete_or_invalid_publication_date' for item in rejected):
        return {'date': None, 'evidence': evidence, 'rejected': rejected, 'reason': 'invalid_publication_evidence'}
    if len(days) > 1:
        return {'date': None, 'evidence': evidence, 'rejected': rejected, 'reason': 'conflicting_publication_dates'}
    if days:
        return {'date': next(iter(days)), 'evidence': evidence, 'rejected': rejected, 'reason': 'explicit_publication_date'}
    return {'date': None, 'evidence': evidence, 'rejected': rejected,
            'reason': scope_reason or 'no_explicit_publication_date'}
