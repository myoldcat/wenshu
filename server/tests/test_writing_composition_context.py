import copy
import json

from server.writing_composition import chapter_context, snapshot


def article():
    return {'title': '安全与发展', 'brief': {'topic': '围绕明确问题展开'},
            'sourceIds': ['current'], 'sources': [{'sourceId': 'current', 'revisionId': 'r-current'}],
            'sections': [
                {'id': 'one', 'title': '问题', 'goal': '说明范围', 'content': '已经说明的中心问题。[E1]'},
                {'id': 'two', 'title': '展开', 'goal': '讨论另一个维度', 'content': '中段落点。[E2]'},
                {'id': 'three', 'title': '收束', 'goal': '回应中心问题', 'content': ''}]}


def test_each_chapter_gets_its_position_and_actual_neighbor_without_new_evidence():
    project = article()
    before = copy.deepcopy(project)
    frozen = snapshot()
    first, middle, last = [chapter_context(project, section, frozen) for section in project['sections']]
    assert first['position']['role'] == 'opening' and 'previousChapter' not in first
    assert first['nextChapter']['title'] == '展开'
    assert middle['position']['role'] == 'development'
    assert middle['previousChapter']['tail'] == '已经说明的中心问题。'
    assert middle['nextChapter']['goal'] == '回应中心问题'
    assert last['position']['role'] == 'closing'
    assert last['articleOpening'] == '已经说明的中心问题。'
    assert last['previousChapter']['tail'] == '中段落点。'
    assert all(value['evidenceRole'] == 'none' for value in (first, middle, last))
    assert all(not any(key in value for key in ('blocks', 'sources', 'sourceContext', 'evidence')) for value in (first, middle, last))
    assert project == before


def test_unwritten_previous_chapter_does_not_claim_previous_discussion():
    project = article()
    project['sections'][1]['content'] = ''
    context = chapter_context(project, project['sections'][2], snapshot())
    assert context['previousChapter']['written'] is False
    assert 'tail' not in context['previousChapter']


def test_frozen_limits_and_guidance_are_respected_even_for_long_context():
    project = article()
    for section in project['sections']:
        section.update(title='很长的标题' * 100, goal='很长的规划' * 100, content=('这是已经表达的内容。[E9]' * 200))
    frozen = snapshot()
    frozen.update(contextLimit=760, previousTailChars=400, openingChars=120)
    frozen['roleInstructions']['closing'] = '固定的收束要求'
    before = copy.deepcopy(frozen)
    context = chapter_context(project, project['sections'][2], frozen)
    assert len(json.dumps(context, ensure_ascii=False, separators=(',', ':'))) <= 760
    assert context['roleInstruction'] == '固定的收束要求'
    assert '[E9]' not in json.dumps(context, ensure_ascii=False)
    assert frozen == before


def test_legacy_and_unknown_policies_do_not_acquire_new_context():
    project = article()
    assert chapter_context(project, project['sections'][0], None) is None
    assert chapter_context(project, project['sections'][0], {'policy': 'other'}) is None
    assert chapter_context(project, {'id': 'missing'}, snapshot()) is None
    frozen = snapshot()
    frozen['contextLimit'] = 200
    assert chapter_context(project, project['sections'][0], frozen) is None


def test_one_chapter_is_an_article_instead_of_a_fake_middle_chapter():
    project = article()
    project['sections'] = project['sections'][:1]
    context = chapter_context(project, project['sections'][0], snapshot())
    assert context['position'] == {'ordinal': 1, 'total': 1, 'role': 'whole'}
    assert 'previousChapter' not in context and 'nextChapter' not in context
