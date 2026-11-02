"""Deterministic source-balanced input ordering; original evidence stays intact."""
from __future__ import annotations

import re

from .retrieval_relevance import contains, terms

POLICY = 'source-balanced-originals-v1'
TEMPORAL_REQUIREMENT = (
    'sourceContext中的publishedAt仅为该固定版本已保存的发布日期，不是事件发生日期；'
    '未提供日期时不得把入库时间当发布日期。原文中的今日、去年、近几个月、下个月'
    '须保留原文时点和说话人归属，不能指向本次写作日期；不能把将推出写成已实施。'
    '调查中、怀疑、可能、尚未确定、机构声称等限定必须保留。标题与目标同样遵守，'
    '例如尚在调查是否外泄的事故不能归为已确认资料外泄。'
)


def material_order(blocks, query, *, outline=False, planned_ids=()):
    """Use coherent terms, then distribute outline slots across confirmed sources.

    This is bounded lexical ordering, never a claim to semantic relevance or
    complete reading. A short title/caption cannot crowd out an actual paragraph.
    """
    query_terms = terms(query)
    def rank(block):
        text = block['text']
        hits = sum(contains(text, term) for term in query_terms)
        substantive = block.get('kind') not in ('heading', 'image', 'caption') and len(text.strip()) >= 32
        return (-int(substantive), -hits, -min(len(text), 600), block.get('ordinal', 0))

    ranked = sorted(blocks, key=rank)
    if not outline:
        required = set(planned_ids)
        return sorted(ranked, key=lambda block: block['blockId'] not in required)
    groups = {}
    for block in ranked:
        groups.setdefault(block['sourceId'], []).append(block)
    ordered = []
    # First one useful original paragraph from every source; further rounds
    # prevent a long report from monopolising all 40 input slots.
    while any(groups.values()):
        for group in groups.values():
            if group:
                ordered.append(group.pop(0))
    return ordered


def date_block(block):
    text = block['text'].strip()
    return len(text) <= 180 and bool(re.search(
        r'(?:發[佈布]|发布|更新|出版|日期).{0,10}(?:20\d{2}|[一二三四五六七八九十]+月)|'
        r'20\d{2}年.{0,8}月.{0,5}日[（(](?:星期|周)|'
        r'今日[（(][一二三四五六七八九十\d]+月[一二三四五六七八九十\d]+日[）)].{0,50}20\d{2}年', text))
