"""Versioned, item-local selection of clear and independently useful Wiki facts."""
from collections import Counter

POLICY_VERSION = 'wiki-clear-selection-v1'
INSTRUCTIONS = (
    '本任务冻结的明确性与独立词条筛选规则优先于本请求中其他编译或修复提示词要求至少一条结果等旧收录约束。'
    '只收录原文明示、对象和适用范围明确、脱离本文叙述仍有独立意义的词条及陈述。'
    '词条名称和定义须能独立识别实际概念、实体、主张或事实，不能把仅为本文组织论述而临时组合的框架名称当成知识词条；'
    '例如“香港三大机遇”若仅表示本文所列三项机遇且缺少独立明确的概念定义，应舍弃，不猜其具体所指；'
    '这不是字串禁用规则，须结合本次原文语义判断。'
    '不明确、依赖未给出的上下文、归属冲突或证据不足的内容直接省略，不补写、不猜测、也不以待核实事项收录。'
    '保留原文明确陈述的条件和不确定性，但不能把无法确定归属的推断冒充有据事实。'
    '在同一次JSON输出中逐项完成筛选：每个entry必须有布尔standalone，只有名称和定义具备上述独立意义时才为true；'
    'standalone=true还须保证所有保留别名能独立明确指称同一对象；依赖“上述、这几项”等本文指代的别名应直接省略。'
    '每个summary、section.claim和relation必须有布尔clear，只有该项全部实质内容被所给精确引文及适用上下文明示支持时才为true。'
    '缺失或非布尔标记不算通过。standalone或clear不是true的项可直接不输出；不能为凑数量将其设为true。'
    '任何不能明确支持的年份、名称、别名、章节标题或关系都舍弃所在最小完整项，不把单项问题扩大为整批失败。'
    'summary不明确则舍弃整个entry；可保留合格定义并返回sections=[]；不要通过改写坏定义或补全年份来修补依据。'
    '允许entries=[]和relations=[]，全部不合格时返回这两个空数组及issues=[]。'
    '不明确事项不得进入issues或词条页面；其余JSON、字段类型、全局数量及长度上限、固定来源和精确引用约束保持。'
)


def prune_result(cls, value, pieces, document, year_contexts=None, *, trusted=False):
    """Filter complete local units, never repair their text or manufacture support.

    ``trusted`` is only for a same-request, already-normalized checkpoint marked
    with this policy. It permits its omitted model markers, not new raw results.
    """
    from .wiki import _text, _list, _subtype, _check_years
    from .year_context import build_contexts

    if not isinstance(value, dict):
        raise ValueError('Wiki 结果必须为 JSON 对象')
    raw_entries = _list(value.get('entries'), '词条', 0, 12)
    raw_relations = _list(value.get('relations', []), '关系', 0, 30)
    _list(value.get('issues', []), '待核对事项', 0, 8)

    def marked(item, field):
        return item.get(field) is True or (trusted and field not in item)

    def years(text, evidence):
        contexts = build_contexts([document], evidence) if year_contexts is not None else None
        _check_years(text, evidence, contexts)

    def statement(item, field, limit):
        if not isinstance(item, dict) or not marked(item, 'clear'):
            return None
        try:
            clean = {'text': _text(item.get('text'), field, limit),
                     'evidence': cls._evidence(item.get('evidence'), pieces, document)}
            years(clean['text'], clean['evidence'])
            return clean
        except ValueError:
            return None

    # Duplicate local identities cannot safely own relationship endpoints.
    key_counts = Counter()
    for entry in raw_entries:
        if isinstance(entry, dict):
            try:
                key_counts[_text(entry.get('localKey'), '本批词条键', 80)] += 1
            except ValueError:
                pass
    entries = []
    for entry in raw_entries:
        if not isinstance(entry, dict) or not marked(entry, 'standalone'):
            continue
        try:
            key = _text(entry.get('localKey'), '本批词条键', 80)
            if key_counts[key] != 1:
                continue
            kind = entry.get('type')
            if kind not in ('concept', 'entity', 'claim', 'fact'):
                continue
            definition = statement(entry.get('summary'), '定义', 500)
            if definition is None:
                continue
            label = _text(entry.get('label'), '名称', 150)
            sections = []
            for section in _list(entry.get('sections', []), '章节', 0, 4):
                if not isinstance(section, dict):
                    continue
                try:
                    claims = [clean for claim in _list(section.get('claims'), '章节陈述', 0, 6)
                              if (clean := statement(claim, '陈述', 800)) is not None]
                    if not claims:
                        continue
                    title = _text(section.get('title'), '章节标题', 80)
                    years(title, [reference for claim in claims for reference in claim['evidence']])
                    sections.append({'title': title, 'claims': claims})
                except ValueError:
                    continue
            page_evidence = definition['evidence'] + [reference for section in sections
                for claim in section['claims'] for reference in claim['evidence']]
            years(label, page_evidence)
            aliases = []
            try:
                raw_aliases = _list(entry.get('aliases', []), '别名', 0, 8)
            except ValueError:
                raw_aliases = []
            for alias in raw_aliases:
                try:
                    alias = _text(alias, '别名', 100)
                    years(alias, page_evidence)
                    if alias not in aliases:
                        aliases.append(alias)
                except ValueError:
                    continue
            entries.append({'localKey': key, 'label': label, 'type': kind,
                            'subtype': _subtype(kind, entry.get('subtype')),
                            'summary': definition, 'aliases': aliases, 'sections': sections})
        except ValueError:
            continue

    keys = {entry['localKey'] for entry in entries}
    relations = []
    for relation in raw_relations:
        if not isinstance(relation, dict) or not marked(relation, 'clear'):
            continue
        try:
            source = _text(relation.get('from'), '关系来源词条键', 80)
            target = _text(relation.get('to'), '关系目标词条键', 80)
            if source not in keys or target not in keys or source == target:
                continue
            kind = relation.get('type')
            if kind not in ('hierarchy', 'association'):
                continue
            clean = {'from': source, 'to': target, 'type': kind,
                     'label': _text(relation.get('label'), '关系名称', 100),
                     'evidence': cls._evidence(relation.get('evidence'), pieces, document)}
            years(clean['label'], clean['evidence'])
            relations.append(clean)
        except ValueError:
            continue
    return {'entries': entries, 'relations': relations, 'issues': [],
            'selectionPolicyVersion': POLICY_VERSION}
