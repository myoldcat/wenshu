"""Shared manuscript presentation, referring to GB/T 9704—2012.

This is a body-only manuscript template. It never invents issuing authorities,
document numbers, confidentiality labels, dates or seals, or edits saved prose.
"""
import re

PAGE_WIDTH_MM, PAGE_HEIGHT_MM = 210, 297
MARGIN_TOP_MM, MARGIN_BOTTOM_MM = 37, 35
MARGIN_LEFT_MM, MARGIN_RIGHT_MM = 28, 26
BODY_WIDTH_MM, BODY_HEIGHT_MM = 156, 225
BODY_SIZE_PT, TITLE_SIZE_PT = 16, 22
# A conventional fixed pitch gives approximately 22 lines in the 225 mm body.
LINE_PT = 28.8
FIRST_LINE_PT = 32


def mm_twips(value):
    return str(round(value * 1440 / 25.4))


def chinese_number(number):
    if number < 10:
        return '零一二三四五六七八九'[number]
    if number < 100:
        tens, units = divmod(number, 10)
        return (chinese_number(tens) if tens > 1 else '') + '十' + (chinese_number(units) if units else '')
    # The writing service bounds chapter counts below this; keep a safe label
    # for imported snapshots without truncating any authored title.
    return str(number)


def body_heading_level(text):
    """Recognize short, explicitly numbered headings; keep all authored text."""
    value = text.strip()
    if not value or len(value) > 80 or re.search(r'[。！？；]', value):
        return None
    for level, pattern in (
        (1, r'^[一二三四五六七八九十百零〇]+、\S'),
        (2, r'^[（(][一二三四五六七八九十百零〇]+[）)]\s*\S'),
        (3, r'^\d{1,3}[.．](?!\d)\s*\S'),
        (4, r'^[（(]\d{1,3}[）)]\s*\S'),
    ):
        if re.match(pattern, value):
            return level
    return None


def chapter_heading(number, title):
    # Already numbered titles must not acquire a second generated sequence.
    return title if body_heading_level(title) else chinese_number(number) + '、' + title
