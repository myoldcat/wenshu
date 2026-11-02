"""Conservative topic checks after discovery, independent of RRF channel ranks.

Discovery uses recall-oriented search tokens. Recommendation needs a coherent
query in a title, summary, category, grounded claim or local body window.
These are relevance heuristics, never probabilities or evidence validators.
"""
import math
import json
from functools import lru_cache
import re

import jieba


FILLER = set('的 了 和 与 在 是 对 及 或 为 以 等 从 有 如何 什么 关于 进行 分析 研究 报告 文章 写作 调研 '
             '未来 一年 发展 预测 趋势 影响 风险 建议 比较 探讨 梳理 介绍 现状 最新 相关 问题 '
             'the a an and or of to in for on with is are report research analysis future development'.split())
WEAK = set('公共 治理 政策 背景 对策 普通 读者 现状 问题 安全 机制 能力 服务 技术 国际 全球 局势 合作'.split())


@lru_cache(maxsize=2)
def converter(mode):
    from opencc import OpenCC
    return OpenCC(mode)


@lru_cache(maxsize=256)
def normalized(text):
    # A transient search representation only. Never saved over original text,
    # revisions, human translations or quoted evidence.
    return converter('t2s').convert(text).casefold()


def stem(word):
    if re.fullmatch(r'[a-z]{4,}', word):
        if word.endswith('ies'):
            return word[:-3] + 'y'
        if word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
            return word[:-1]
    return word


def discovery_queries(queries):
    return [*queries, *(converter('t2s').convert(query) for query in queries),
            *(converter('s2t').convert(query) for query in queries),
            ' '.join(stem(word) for query in queries for word in re.findall(r'[a-z]+', query.casefold()))]


def contains(text, term):
    text, term = normalized(text), normalized(term)
    if re.fullmatch(r'[a-z0-9_+.-]+', term):
        base = stem(term)
        endings = '(?:s|ies)?' if base == term else '(?:s)?'
        pattern = re.escape(base[:-1]) + '(?:y|ies)' if base.endswith('y') else re.escape(base) + endings
        return bool(re.search(r'(?<![a-z0-9_])' + pattern + r'(?![a-z0-9_])', text))
    return term in text


def terms(text):
    # Precise mode keeps 网络安全/人工智能 intact, unlike FTS search-mode
    # overlapping subwords. Years, formats and writing instructions add no topic.
    # Preserve mixed/compound identifiers that jieba otherwise reduces to
    # single characters (A+H would leave only the broad word '上市').
    text = normalized(text)
    identifiers = re.findall(r'[A-Za-z0-9]+(?:[+.-][A-Za-z0-9]+)+|[A-Za-z][\u4e00-\u9fff](?=\s|$)', text)
    for value in identifiers:
        text = text.replace(value, ' ')
    words = []
    for word in jieba.cut(text):
        # Keep a meaningful single-character suffix with its noun (智能/体),
        # while conjunctions and grammatical particles remain separators.
        if re.fullmatch(r'[\u4e00-\u9fff]', word) and word not in FILLER and words and re.fullmatch(r'[\u4e00-\u9fff]{2,}', words[-1]):
            words[-1] += word
        else:
            words.append(word)
    values = [word.casefold().strip() for word in [*identifiers, *words]]
    return list(dict.fromkeys(word for word in values if len(word) > 1 and word not in FILLER
                             and re.search(r'[a-z\u4e00-\u9fff]', word)
                             and not re.fullmatch(r'\d+[年月日号]?', word)))[:32]


def query_groups(queries, *, topic=None, origin=None):
    groups = [group for query in queries if (group := terms(query)) and any(term not in WEAK for term in group)]
    if origin == 'model' and topic:
        subject = [term for term in terms(topic) if term not in WEAK]
        chinese = bool(re.search(r'[\u4e00-\u9fff]', topic))
        # Same-language generated angles cannot replace the subject or omit
        # its scope. Translated queries remain useful across languages.
        groups = [group for group in groups if not chinese or not any(re.search(r'[\u4e00-\u9fff]', t) for t in group)
                  or all(any(contains(term, anchor) or contains(anchor, term) for term in group) for anchor in subject)]
    return groups


def category_names(tags_json, paths_json):
    # Reasons can include negated examples; IDs and evidence offsets have no
    # semantic meaning. Only names/tags are discovery text.
    tags, paths = json.loads(tags_json), json.loads(paths_json)
    return ' '.join([*tags, *(node['name'] for path in paths for node in path.get('nodes', []))])


def discovery_text(text):
    text = re.sub(r'https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+', ' ', text, flags=re.I)
    text = re.sub(r'[（(][^()（）\n]{0,120}(?:图片|圖片|image)[^()（）\n]{0,120}[）)]', ' ', text, flags=re.I)
    text = re.sub(r'(?:图片|圖片)[^。\n]{0,50}(?:AI|人工智能|生成式)[^。\n]{0,80}(?:。|\n|$)', ' ', text, flags=re.I)
    return normalized(text)


def lexical_relevance(fields, body, groups):
    """Require co-occurrence; incidental mentions in a long article do not win.

    Separate paragraphs and 420-character windows prevent disconnected topic
    words (or a footer's related-links list) from qualifying the entire article.
    """
    fields = [discovery_text(text) for text in fields if text]
    # Metadata may describe complementary facets (e.g. ferry fares in the
    # summary, urban transport in a manually assigned category).
    metadata = '\n'.join(fields)
    fields.append(metadata)
    body = discovery_text(body)
    windows = [body[start:start + 420] for start in range(0, len(body), 300)]
    best = 0.0
    for group in groups:
        subjects = [term for term in group if term not in WEAK]
        needed = len(subjects) if len(subjects) <= 3 else math.ceil(len(subjects) * .8)
        for text in fields + windows:
            hits = [term for term in group if contains(text, term)]
            if sum(term not in WEAK for term in hits) < needed:
                continue
            if len(subjects) == 1 and text not in fields:
                # A lone body mention is weak unless repeated or a compound
                # subject; title/category/summary matches remain sufficient.
                term = subjects[0]
                occurrences = len(re.findall(r'(?<![a-z0-9_])' + re.escape(term) + r'(?![a-z0-9_])', body)) if re.fullmatch(r'[a-z]+', term) else body.count(term)
                if len(term) < 4 and occurrences < 2:
                    continue
            best = max(best, len(hits) / len(group))
            if best == 1:
                return best
    return best


def topic_alignment(topic, fields):
    subject = [term for term in terms(topic) if term not in WEAK]
    text = discovery_text('\n'.join(fields))
    return sum(contains(text, term) for term in subject) >= min(2, len(subject)) if subject else False


def semantic_relevance(value, best, model=''):
    # Positive cosine is common for unrelated documents. A standalone semantic
    # recommendation must clear both absolute and query-relative thresholds.
    floor, ratio = (.48, .65) if re.fullmatch(r'qwen3-embedding(?::[^\s:]+)?', model, re.I) else (.68, .9)
    return value >= floor and value >= best * ratio
