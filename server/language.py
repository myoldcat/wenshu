"""Conservative offline prose analysis; semantic language shares come from classification.

Only substantive natural language counts. Simplified and Traditional Chinese are
one language; at least two languages must each cover 30% to label a text mixed.
Original text and evidence are never rewritten by this analysis.
"""
from collections import Counter
import re
import unicodedata

LANGUAGE_POLICY_VERSION = 3
MIXED_LANGUAGE_MIN_SHARE = .30

_HAN = re.compile(r'[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003134f]')
_TECHNICAL = re.compile(r'https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', re.I)
_LANGUAGE = re.compile(r'^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*$', re.I)
_LATIN_WORD = r'[A-Za-z\u00c0-\u024f][A-Za-z\u00c0-\u024f0-9.+_-]*'
_TERMS = re.compile(
    r'\b(?:OpenAI|Google|Nvidia|Microsoft|Amazon|Apple|Meta|ChatGPT|GitHub|'
    r'Python|JavaScript|TypeScript|PyTorch|TensorFlow|Docker|Kubernetes|Linux|'
    r'machine learning|deep learning|large language models?|artificial intelligence|'
    r'GPT[-\w.]*|Qwen[-\w.]*|Llama[-\w.]*|API|SDK|CPU|GPU|AI|LLM|RAG|HTML|SQL|JSON)\b', re.I)
_FUNCTION_WORDS = set('a an the this that these those is are was were be been being '
                      'and or but with without for from to of in on at by as we you they '
                      'it our their can will should have has do not le la les des du un '
                      'une et est dans pour de el los las una y es der die das und ist '
                      'en se que il nous vous'.split())

LANGUAGE_LABELS = {
    'zh': '中文', 'en': '英语', 'ja': '日语', 'ko': '韩语', 'fr': '法语', 'de': '德语',
    'es': '西班牙语', 'pt': '葡萄牙语', 'it': '意大利语', 'ru': '俄语', 'ar': '阿拉伯语',
    'hi': '印地语', 'th': '泰语', 'vi': '越南语', 'el': '希腊语', 'he': '希伯来语',
    'uk': '乌克兰语', 'tr': '土耳其语', 'id': '印度尼西亚语', 'nl': '荷兰语',
    'pl': '波兰语', 'fa': '波斯语', 'bn': '孟加拉语', 'sv': '瑞典语', 'fi': '芬兰语',
    'da': '丹麦语', 'no': '挪威语', 'ms': '马来语', 'ta': '泰米尔语', 'ur': '乌尔都语',
    'mixed': '多语混合', 'und': '未确定', 'other': '其他外语',
}
SOURCE_LANGUAGE_CODES = tuple(LANGUAGE_LABELS)


def normalize_language_code(value, default='und'):
    """Map stored/parser language hints to the program-owned display codes."""
    if not isinstance(value, str):
        return default
    code = value.strip().lower()
    if code == 'foreign':
        return 'other'
    if code in LANGUAGE_LABELS:
        return code
    if _LANGUAGE.fullmatch(code):
        base = re.split('[-_]', code)[0]
        if base in LANGUAGE_LABELS:
            return base
    return default


def language_label(code):
    return LANGUAGE_LABELS[normalize_language_code(code)]


def language_tag(code):
    return '语言：' + language_label(code)


def language_content(text):
    """Remove obvious non-prose locally; classification handles semantic entities.

    This deliberately does not claim to be a general named-entity recognizer.
    Short isolated foreign terms in Chinese prose are ignored, whereas complete
    foreign clauses remain eligible for the language share calculation.
    """
    text = str(text or '')
    text = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`', ' ', text)
    text = _TECHNICAL.sub(' ', text)
    text = re.sub(r'(?m)^\s*(?:(?:from\s+\S+\s+import|import|def|class|const|let|var|function|return)\b[^\n]*|[\w.]+\s*\([^\n]*\)\s*;?)$', ' ', text)
    text = _TERMS.sub(' ', text)
    text = re.sub(r'\b[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*\b', ' ', text)
    if _HAN.search(text):
        def foreign_run(match):
            value = match.group()
            words = re.findall(_LATIN_WORD, value)
            # Isolated names and technical phrases are not a foreign passage.
            contains_grammar = any(w.lower() in _FUNCTION_WORDS for w in words)
            name_phrase = len(words) >= 2 and all(w[0].isupper() for w in words)
            return ' ' if not contains_grammar and (len(words) <= 3 or name_phrase) else value
        text = re.sub(_LATIN_WORD + r'(?:[ \t]+' + _LATIN_WORD + r')*', foreign_run, text)
        # A short inline kana/Hangul entity does not determine the whole text.
        if not re.search(r'[\u3040-\u309f]', text):
            text = re.sub(r'(?<=[\u3400-\u9fff，、（(「『 ])(?:[\u30a0-\u30ff]{1,6}|[\uac00-\ud7af]{1,5})(?=[\u3400-\u9fff，、。；）)」』 ])', ' ', text)
    return text


def language_weight(text):
    return sum(char.isalpha() for char in language_content(text))


def source_language_from_shares(shares, fallback='und'):
    counts = Counter()
    for code, amount in (shares or {}).items():
        if amount > 0:
            counts[normalize_language_code(code)] += amount
    if not counts or not sum(counts.values()):
        return normalize_language_code(fallback)
    total = sum(counts.values())
    qualifying = [code for code, count in counts.items()
                  if code not in ('mixed', 'und') and count / total >= MIXED_LANGUAGE_MIN_SHARE - 1e-9]
    if len(qualifying) >= 2:
        return 'mixed'
    return counts.most_common(1)[0][0]


def merge_source_languages(codes, fallback='und', weights=None, shares=None):
    """Merge source coverage, never vote by number of batches."""
    codes = list(codes)
    if weights is None and shares is None:
        values = {normalize_language_code(code) for code in codes}
        return next(iter(values)) if len(values) == 1 else 'und' if values else normalize_language_code(fallback)
    weights = list(weights) if weights is not None else [1] * len(codes)
    shares = list(shares) if shares is not None else [None] * len(codes)
    if len(weights) != len(codes) or len(shares) != len(codes):
        raise ValueError('Language codes, weights and shares must have equal lengths')
    coverage = Counter()
    for code, weight, distribution in zip(codes, weights, shares):
        if weight <= 0:
            continue
        code = normalize_language_code(code)
        if distribution:
            total = sum(distribution.values())
            if total:
                for language, share in distribution.items():
                    coverage[normalize_language_code(language)] += weight * share / total
        elif code != 'mixed':
            coverage[code] += weight
        else:
            # An old bare mixed label has no measurable language coverage.
            coverage['und'] += weight
    return source_language_from_shares(coverage, fallback)


def _script_counts(text, declared=''):
    text = language_content(text)
    hint = normalize_language_code(declared)
    counts = Counter()
    for sentence in re.split(r'[\n。！？!?]+', text):
        local = Counter()
        for char in sentence:
            if not char.isalpha():
                continue
            name = unicodedata.name(char, '')
            if _HAN.fullmatch(char):
                local['zh'] += 1
            else:
                script = next((code for keyword, code in (
                    ('HIRAGANA', 'ja'), ('KATAKANA', 'ja'), ('HANGUL', 'ko'),
                    ('CYRILLIC', 'cyrillic'), ('ARABIC', 'arabic'), ('GREEK', 'el'),
                    ('HEBREW', 'hebrew'), ('DEVANAGARI', 'devanagari'), ('THAI', 'th'),
                    ('LATIN', 'latin')) if keyword in name), 'other')
                local[script] += 1
        # Han in a Japanese sentence belongs to Japanese, not a second language.
        # A tiny inline name must not absorb an otherwise Chinese paragraph.
        east_asian = 'ja' if local['ja'] else 'ko' if local['ko'] else None
        if east_asian and (local[east_asian] >= 3 and local[east_asian] >= local['zh'] * .2):
            local[east_asian] += local.pop('zh', 0)
        elif not east_asian and hint in ('ja', 'ko') and local['zh']:
            # Ambiguous Han-only Japanese headings can use a declared hint;
            # recognizable Chinese prose overrides an incorrect HTML hint.
            chinese_prose = re.search(r'[这這该該们們吗嗎与與为為将將让讓说說后後发發]|资料|資料|中文|证据|證據|人工智能|原始|保存', sentence)
            if not chinese_prose:
                local[hint] += local.pop('zh')
        counts.update(local)
    return counts


def _script_code(script, declared):
    hint = normalize_language_code(declared)
    compatible = {'cyrillic': {'ru', 'uk'}, 'arabic': {'ar', 'fa', 'ur'},
                  'devanagari': {'hi'}, 'hebrew': {'he'}}
    if script in compatible:
        return hint if hint in compatible[script] else 'other'
    if script == 'latin':
        non_latin = {'zh', 'ja', 'ko', 'ru', 'uk', 'ar', 'fa', 'ur', 'hi', 'he', 'th', 'el', 'bn', 'ta', 'und', 'mixed', 'other'}
        return hint if hint not in non_latin else 'other'
    return script


def language_shares(text, declared=''):
    counts = Counter()
    for script, count in _script_counts(text, declared).items():
        counts[_script_code(script, declared)] += count
    total = sum(counts.values())
    return {code: count / total for code, count in counts.items() if count} if total else {}


def detect_language(text, declared=''):
    counts = _script_counts(text, declared)
    total = sum(counts.values())
    if not total:
        return 'und'
    # Distinct script families establish independent prose even while exact
    # languages sharing Latin/Cyrillic/Arabic still await classification.
    if sum(count / total >= MIXED_LANGUAGE_MIN_SHARE - 1e-9 for count in counts.values() if count) >= 2:
        return 'mixed'
    code = _script_code(counts.most_common(1)[0][0], declared)
    return 'foreign' if code == 'other' else code


def needs_translation(text, language=''):
    return detect_language(text, language) not in ('zh', 'und')


def document_needs_translation(document):
    language = document.get('languageCode') or document.get('language', '')
    if (document.get('languageIdentification') == 'model'
            and document.get('languagePolicyVersion') == LANGUAGE_POLICY_VERSION):
        return normalize_language_code(language) not in ('zh', 'und')
    title = document.get('originalTitle', document.get('title', ''))
    text = document_language_text(document)
    return needs_translation(text or title, language)


def document_language_text(document):
    # Titles, names and headings cannot outweigh the article's actual prose.
    return '\n'.join(block.get('text', '') for block in document.get('blocks', [])
                     if block.get('kind') not in ('code', 'heading', 'title'))


def require_chinese(text, label):
    """Reject wholly untranslated prose, while allowing named terms inline.

    This gate cannot establish semantic translation quality. It catches a
    provider ignoring the required output language before publication/indexing.
    """
    if any(char.isalpha() for char in text) and not _HAN.search(text):
        raise ValueError(f'{label}须使用简体中文，模型返回了未翻译的外语内容')
    return text
