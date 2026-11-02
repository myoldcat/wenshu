"""Source starvation, source-date and input coverage regression checks."""
import json

import pytest

from server.store import Store
from server.tests.test_writing_citation_contract import add_source
from server.tests.test_writing_integrity import ControlledGateway
from server.writing import WritingService


def test_outline_reads_all_available_confirmed_blocks_when_budget_allows(tmp_path):
    store = Store(tmp_path / 'materials')
    service = WritingService(store, ControlledGateway())
    a, _ = add_source(store, paragraphs=[f'本地网络安全报告第{i}段详细记载明确的机构事件和统计信息。' for i in range(55)])
    b, original = add_source(store, paragraphs=['支付卡绑定的钓鱼骗案仍在调查，金管局提醒市民核实通知来源，并且不要向未经核实的网站提供账户资料。'], url='https://example.test/bank')
    with store.connect() as db:
        db.execute('UPDATE revisions SET published_at=? WHERE id=?', ('2026-09-04T00:00:00+08:00', original['revisionId']))
    project = service.create({'title': '香港网络安全调研', 'mode': 'strict', 'sourceIds': [a, b]})
    with store.connect() as db:
        messages, frozen = service._messages(project, 'outline', None, {'contextChars': 200000}, db)
    packet = json.loads(messages[1]['content'])
    assert len(frozen) == 56
    assert {entry['sourceId'] for entry in packet['blocks'][:2]} == {a, b}
    assert packet['inputCoverage']['complete'] is True
    assert packet['inputCoverage']['includedSources'] == 2
    metadata = next(item for item in packet['sourceContext'] if item['sourceId'] == b)
    assert metadata['publishedAt'] == '2026-09-04T00:00:00+08:00'
    assert '调查中' in packet['temporalAndAttributionRequirements']


def test_source_import_time_is_never_substituted_for_missing_publication_date(tmp_path):
    store = Store(tmp_path / 'date')
    service = WritingService(store, ControlledGateway())
    source, _ = add_source(store)
    project = service.create({'title': '调研', 'sourceIds': [source]})
    with store.connect() as db:
        messages, _ = service._messages(project, 'outline', None, {'contextChars': 30000}, db)
    context = json.loads(messages[1]['content'])['sourceContext'][0]
    assert context['publishedAt'] is None
    assert 'createdAt' not in context and 'fetchedAt' not in context


def test_truncated_original_is_not_reported_as_complete_reading(tmp_path):
    store = Store(tmp_path / 'truncation')
    service = WritingService(store, ControlledGateway())
    source, _ = add_source(store, paragraphs=['原文完整段落。' * 600])
    project = service.create({'title': '调研', 'sourceIds': [source]})
    with store.connect() as db:
        messages, _ = service._messages(project, 'outline', None, {'contextChars': 30000}, db)
    coverage = json.loads(messages[1]['content'])['inputCoverage']
    assert coverage['includedBlocks'] == coverage['availableBlocks'] == 1
    assert coverage['truncatedBlocks'] == 1 and coverage['complete'] is False


def test_insufficient_budget_cannot_silently_drop_a_confirmed_source(tmp_path):
    store = Store(tmp_path / 'budget')
    gateway = ControlledGateway()
    service = WritingService(store, gateway)
    ids = [add_source(store, paragraphs=[f'机构{i}的原文报告。' + '原文明示事实。' * 400], url=f'https://example.test/{i}')[0] for i in range(3)]
    project = service.create({'title': '原文事实调研', 'sourceIds': ids})
    with store.connect() as db:
        with pytest.raises(ValueError, match='无法覆盖每篇所选资料'):
            service._messages(project, 'outline', None, {'contextChars': 9000}, db)
