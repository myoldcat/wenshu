"""Contract and identity tests; semantic relevance requires actual model review."""
import json
import sqlite3
import pytest

from server.classification import (NAMING_VERSION, canonicalize_manual_paths, existing_paths,
                                   make_result, path_tags, prompt_contract, validate_paths)


def candidate(**changes):
    return {'level1Id': 'artificial_intelligence', 'level2': '机器学习', 'level3': '模型训练',
            'primary': True, 'reason': '文章主体分析训练方法及实验结果。', 'blockIds': ['b1'], **changes}


def test_all_three_levels_become_tags_and_multiple_paths_have_one_primary():
    data = {'paths': [candidate(), candidate(level1Id='cybersecurity', level2='模型安全', level3='对抗攻击', primary=False, blockIds=['b2'])]}
    result = make_result(validate_paths(data, {'b1', 'b2'}))
    assert result['tags'] == ['人工智能', '机器学习', '模型训练', '网络安全', '模型安全', '对抗攻击']
    assert len(result['classificationPaths']) == 2
    assert all(len(path['nodes']) == 3 for path in result['classificationPaths'])
    assert result['classificationPaths'][0]['nodes'][0] == {'id': 'artificial_intelligence', 'name': '人工智能'}
    assert result['classificationPaths'][1]['blockIds'] == ['b2']


@pytest.mark.parametrize('changes', [
    {'level1Id': 'invented'}, {'level1Id': []}, {'level1Id': {}}, {'level1Name': '错误名称'}, {'level2': ''}, {'level3': None},
    {'level2': 'a' * 33}, {'level2': '模型/算法'}, {'level2': '模型\\算法'}, {'level3': '模型→训练'},
    {'level3': '模型\n训练'}, {'level3': '机器学习'}, {'level2': '人工智能'},
    {'level2': '未归类', 'level3': '模型训练'}, {'primary': 'true'}, {'primary': False},
    {'reason': ''}, {'reason': '字' * 301}, {'blockIds': []}, {'blockIds': ['foreign']},
    {'blockIds': ['b1', 'b2', 'b3', 'b4']},
])
def test_invalid_hierarchy_or_evidence_is_rejected(changes):
    with pytest.raises(ValueError):
        validate_paths({'paths': [candidate(**changes)]}, {'b1', 'b2', 'b3', 'b4'})


@pytest.mark.parametrize('paths', [[], [candidate()] * 2, [candidate(level3=str(index), primary=index == 0) for index in range(4)]])
def test_empty_duplicate_or_excess_paths_fail(paths):
    with pytest.raises(ValueError):
        validate_paths({'paths': paths}, {'b1'})


def test_explicit_unclassified_preserves_three_levels_without_fabricating_topics():
    result = make_result(validate_paths({'paths': [candidate(level2='未归类', level3='未归类')]}, {'b1'}))
    assert [node['name'] for node in result['classificationPaths'][0]['nodes']] == ['人工智能', '未归类', '未归类']
    assert result['tags'] == ['人工智能', '未归类']


def test_program_generated_ids_are_stable_normalized_and_parent_scoped():
    first = canonicalize_manual_paths([candidate(level2='  ＭＬ  ', level3='模型训练')])
    second = canonicalize_manual_paths([candidate(level2='ml', level3='模型训练', id='model-generated-id')])
    other = canonicalize_manual_paths([candidate(level1Id='science_technology', level2='ml', level3='模型训练')])
    assert [n['id'] for n in first[0]['nodes']] == [n['id'] for n in second[0]['nodes']]
    assert first[0]['nodes'][1]['id'] != other[0]['nodes'][1]['id']
    assert first[0]['nodes'][2]['id'] != other[0]['nodes'][2]['id']
    assert 'model-generated-id' not in json.dumps(second)


def test_manual_paths_do_not_accept_model_ids_or_evidence_and_can_be_cleared():
    paths = canonicalize_manual_paths([candidate(), candidate(level1Id='cybersecurity', level2='模型安全', level3='对抗攻击')])
    assert [path['primary'] for path in paths] == [True, False]
    assert all(set(path) == {'nodes', 'primary'} for path in paths)
    assert canonicalize_manual_paths([]) == []
    assert path_tags(paths) == ['人工智能', '机器学习', '模型训练', '网络安全', '模型安全', '对抗攻击']


def test_general_topic_prompt_does_not_shorten_or_reclassify_manual_names():
    # Granularity is an automatic naming instruction, not a migration or a new
    # length gate for previously accepted human classifications.
    second, third = '用户确定的专门研究分类' + '甲' * 21, '用户确定的项目专题分类' + '乙' * 21
    assert len(second) == len(third) == 32
    values = [candidate(level2=second, level3=third)]
    saved = canonicalize_manual_paths(values)
    assert [node['name'] for node in saved[0]['nodes']] == ['人工智能', second, third]
    assert canonicalize_manual_paths(values) == saved


@pytest.mark.parametrize('value', [None, [], {}, {'level1Id': []}, {'level1Id': {}}])
def test_invalid_manual_path_objects_raise_validation_errors(value):
    with pytest.raises(ValueError):
        canonicalize_manual_paths([value])


def test_boundary_prompt_requires_substantial_independent_content_and_reuses_paths():
    contract = prompt_contract()
    for term in ('相当比重', '独立、实质', '不要凑满3条', 'existingPaths', '网络战', '科技公司财报', '法律效力', '一级-二级-三级'):
        assert term in contract
    assert '只选择候选中已经出现的完整三级路径' in prompt_contract(reduce=True)
    assert 'blocks[].blockId' in contract
    assert '所选完整路径候选的blockIds' in prompt_contract(reduce=True)
    assert 'pieceId、start、end只用于片段定位' in contract


@pytest.mark.parametrize('reduce', [False, True])
def test_military_bargaining_context_does_not_replace_substantive_military_analysis(reduce):
    contract = prompt_contract(reduce=reduce)
    for rule in ('军事后果对谈判的影响不等于军事运作机制',
                 '罗列援助、协防、派兵等行动，不足以增加军事路径',
                 '持续具体展开军事能力或力量运用本身的机制',
                 '威胁可信性、威慑或作战如何运作及其效果', '不要求一定提供部署方案',
                 '此类军事机制与外交过程均充分展开时，仍可同时保留两条路径'):
        assert rule in contract
    if reduce:
        assert '只选择候选中已经出现的完整三级路径' in contract


def test_topic_granularity_guidance_reaches_map_and_reduce_without_relaxing_reduce_gate():
    mapping, reduction = prompt_contract(), prompt_contract(reduce=True)
    for contract in (mapping, reduction):
        assert '二级是稳定子领域，三级是其中可反复使用的一般议题' in contract
        assert '不是只删字缩短' in contract
        assert '一级、语义和粒度均合适' in contract
        assert '跨一级的不同实质角度仍按边界规则保留' in contract
        assert '只要默认二级能容纳主题，就直接用其原名' in contract
        assert contract.index('命名按以下顺序执行') < contract.index('每篇选择1至3条')
    assert '在分块分类阶段归并为可复用的一般议题' in mapping
    assert '不得为改粒度而重命名或新造路径' in reduction
    assert '只选择候选中已经出现的完整三级路径' in reduction


@pytest.mark.parametrize('reduce', [False, True])
def test_angle_review_does_not_count_sections_or_prior_paths_as_independent_topics(reduce):
    # Both original-text and reduction requests need this review; semantic
    # relevance itself is checked with the real corpus, not this string test.
    contract = prompt_contract(reduce=reduce)
    for rule in ('先检查跨一级的实质角度', '再审核同一级是否确需额外路径',
                 '背景—风险—措施—结果链通常合为一个议题', '包含、相邻或近义议题先合并',
                 '不支持沿用旧归属或旧路径数', '不要求算法创新',
                 '不能把“可能赔付”当成治理分析', '跨层级、跨主体的职责分工'):
        assert rule in contract
    assert '跨一级的不同实质机制仍可共同解释同一事件' in contract
    assert '同一级路径若只是语义上位/下位或整体/组成环节，不可并列' in contract
    assert '只有另回答独立问题且有充分展开的不同机制，才增加路径' in contract
    if reduce:
        assert '只选择候选中已经出现的完整三级路径' in contract
        assert '不能拼接不同候选的层级' in contract


@pytest.mark.parametrize('reduce', [False, True])
def test_angle_qualification_precedes_naming_and_distinguishes_disclosure_from_execution(reduce):
    contract = prompt_contract(reduce=reduce)
    assert contract.index('先审角度资格，再定目录名称') < contract.index('命名按以下顺序执行')
    for rule in ('逐节扫描当前输入的全部主文段落', '不能用主标题、前文主题或事项的行业覆盖后文另一实质角度',
                 '须单独判断治理机制，不能只当行业背景', '被引用文件的篇幅描述', '风险披露不等于AI治理',
                 '即使分成多个段落或称其为责任治理，也不得另列AI',
                 '财经文章若有前述实质专业过程，仍保留AI交叉角度',
                 '不要求另有权力冲突、法律程序、量化效果或完整闭环',
                 '未说明角色、动作和衔接，才视为背景', '不能先填满路径配额'):
        assert rule in contract
    # The positive technical/cyber angles and immutable-candidate reduction
    # contract must remain when rejecting unsupported extra angles.
    assert '不要求算法创新' in contract and '不要求出现攻击事件' in contract
    if reduce:
        assert '只选择候选中已经出现的完整三级路径' in contract


@pytest.mark.parametrize('reduce', [False, True])
def test_names_stand_alone_with_minimal_domain_context_without_narrowing_topics(reduce):
    contract = prompt_contract(reduce=reduce)
    for rule in ('各级名称也会单独作为标签展示', '仅补足最低必要的领域或概念限定',
                 '不机械拼接父级名称', '不是按具体主体、对象类型、措施、场景、并列动作或结果进一步细分',
                 'AI模型与算法、AI智能体', 'AI责任与问责', '智能体行为评估',
                 '访问权限管理', '网络安全防护', '企业经营风险', '国际冲突调解',
                 '股票市场、上市融资、数据安全等本身已自足的名称不重复添加祖先词',
                 '可为独立表意适当超出，不是字数门禁'):
        assert rule in contract
    # Domain context supplements the existing coarse taxonomy; it does not
    # permit article-specific names or bypass immutable-candidate reduction.
    assert '二级是稳定子领域，三级是其中可反复使用的一般议题' in contract
    assert '不要把标题、摘要、因果链或一次性结论改写成目录' in contract
    if reduce:
        assert '不得为改粒度而重命名或新造路径' in contract


@pytest.mark.parametrize('reduce', [False, True])
def test_broad_third_level_references_take_priority_without_becoming_a_closed_taxonomy(reduce):
    contract = prompt_contract(reduce=reduce)
    for rule in ('默认三级参考（非封闭分类表、非文章映射）',
                 'AI治理：AI责任与问责、AI安全、AI伦理',
                 'AI智能体：智能体行为评估、智能体执行机制',
                 '身份与访问控制：身份认证、访问权限管理、令牌安全',
                 '企业金融：上市融资、企业估值、企业财务风险',
                 '企业经营：企业经营风险、企业业绩、供应链管理',
                 '角度资格通过后，三级先从对应参考中选择',
                 '凡能容纳实质议题必须使用参考原名', '参考数量不构成多路径理由',
                 '参考确实未覆盖的新议题可建同等宽度名称，不强套不相关参考',
                 '不再叠加协议、技术架构、业务环节、上市阶段或具体资源对象限定',
                 '上市融资等通用议题本身仍可使用', '不同实质问题仍按角度资格规则判定'):
        assert rule in contract
    # The naming shortlist cannot create an angle or relax candidate provenance.
    assert '先审角度资格，再定目录名称' in contract
    assert '同一级路径若只是语义上位/下位或整体/组成环节，不可并列' in contract
    if reduce:
        assert '只选择候选中已经出现的完整三级路径' in contract


@pytest.fixture
def paths_db():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE sources(id TEXT,classification_paths_json TEXT)')
    yield db
    db.close()


def save_exemplar(db, source_id, paths):
    db.execute('INSERT INTO sources VALUES (?,?)', (source_id, json.dumps(paths, ensure_ascii=False)))


@pytest.mark.parametrize('obsolete_version', ['wenshu-topic-names-1.0', 'wenshu-topic-names-1.1'])
def test_naming_version_is_program_owned_and_legacy_manual_paths_are_not_reuse_examples(paths_db, obsolete_version):
    current = make_result(validate_paths({'paths': [candidate(namingVersion='model-forged-version')]}, {'b1'}))
    assert current['classificationPaths'][0]['namingVersion'] == NAMING_VERSION
    save_exemplar(paths_db, 'current', current['classificationPaths'])
    legacy = make_result(validate_paths({'paths': [candidate(level3='历史细分类')]}, {'b1'}))['classificationPaths']
    legacy[0].pop('namingVersion')
    save_exemplar(paths_db, 'legacy', legacy)
    obsolete = make_result(validate_paths({'paths': [candidate(level3='旧规则命名')]}, {'b1'}))['classificationPaths']
    obsolete[0]['namingVersion'] = obsolete_version
    save_exemplar(paths_db, 'obsolete', obsolete)
    manual = canonicalize_manual_paths([candidate(level3='人工保留专题', namingVersion=NAMING_VERSION)])
    assert 'namingVersion' not in manual[0]
    save_exemplar(paths_db, 'manual', manual)
    before = list(paths_db.execute('SELECT * FROM sources ORDER BY id'))
    assert existing_paths(paths_db) == [{'level1Id': 'artificial_intelligence', 'level2': '机器学习', 'level3': '模型训练'}]
    assert list(paths_db.execute('SELECT * FROM sources ORDER BY id')) == before


def test_reuse_budget_visits_each_top_level_before_extra_paths_and_ranks_frequency(paths_db):
    seeds = [
        candidate(level2='模型与算法', level3='模型训练'),
        candidate(level1Id='cybersecurity', level2='身份与访问控制', level3='权限管理'),
        candidate(level1Id='politics_governance', level2='公共政策', level3='科技政策'),
        candidate(level1Id='economy_finance', level2='金融市场', level3='债券市场'),
    ]
    # Many earlier AI paths used to consume the budget and hide later domains.
    for index in range(16):
        result = make_result(validate_paths({'paths': [candidate(level2='AI应用', level3='专题' + str(index))]}, {'b1'}))
        save_exemplar(paths_db, 'other-' + str(index), result['classificationPaths'])
    for index, seed in enumerate(seeds):
        result = make_result(validate_paths({'paths': [seed]}, {'b1'}))
        for repetition in range(3):
            save_exemplar(paths_db, f'preferred-{index}-{repetition}', result['classificationPaths'])
    expected = [{key: seed[key] for key in ('level1Id', 'level2', 'level3')} for seed in seeds]
    budget = len(json.dumps(expected, ensure_ascii=False, separators=(',', ':')))
    assert existing_paths(paths_db, max_chars=budget) == expected
    for limit in (0, 80, budget, 1800):
        found = existing_paths(paths_db, max_chars=limit)
        assert not found or len(json.dumps(found, ensure_ascii=False, separators=(',', ':'))) <= limit
    assert len(existing_paths(paths_db)) > len(expected)


@pytest.mark.parametrize('refs,feedback', [([], '非空数组'), (['b1'] * 4, '最多3个'),
                                        ([1], '字符串'), (['b1@0:100'], '不能使用 pieceId')])
def test_reference_validation_feedback_distinguishes_correctable_errors(refs, feedback):
    with pytest.raises(ValueError, match=feedback):
        validate_paths({'paths': [candidate(blockIds=refs)]}, {'b1'})
