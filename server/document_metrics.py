"""Deterministic counts of saved original prose, independent of AI output."""
from __future__ import annotations

import unicodedata


BODY_COUNT_VERSION = 1
EXCLUDED_BODY_KINDS = frozenset(('heading', 'title', 'code'))


def _han(character):
    value = ord(character)
    return (0x3400 <= value <= 0x4DBF or 0x4E00 <= value <= 0x9FFF
            or 0xF900 <= value <= 0xFAFF or 0x20000 <= value <= 0x2EE5F
            or 0x2F800 <= value <= 0x2FA1F or 0x30000 <= value <= 0x323AF)


def count_text_words(text):
    """Count each Han character and each other letter/number run once.

    Combining marks belong to their existing word. Normalizing to NFC keeps
    composed and decomposed spellings equivalent without altering saved text.
    Whitespace, punctuation, symbols and block boundaries separate words.
    """
    total = 0
    in_word = False
    for character in unicodedata.normalize('NFC', text):
        category = unicodedata.category(character)[0]
        if category in ('L', 'N'):
            if _han(character):
                total += 1
                in_word = False
            else:
                total += not in_word
                in_word = True
        elif category != 'M':
            in_word = False
    return total


def count_body_words(blocks):
    """Count original paragraph/list/table text once; omit headings and code."""
    return sum(count_text_words(block.get('text', '')) for block in blocks
               if block.get('kind') not in EXCLUDED_BODY_KINDS)
