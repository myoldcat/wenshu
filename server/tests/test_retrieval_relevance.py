"""Precision checks over several synthetic writing topics, without live models.

The neighboring topics deliberately share a region, audience or generic policy
word. A match still needs the substance of the requested writing topic. These
tests complement revision and quote integrity tests; they do not benchmark the
meaning understood by an actual embeddings model.
"""
import json

import httpx
import pytest

from server.retrieval import CHANNELS, RetrievalService
from server.tests.test_retrieval import bench, publish
from server.tests.test_wiki_exploration import ExplorationBench, entry, relation


TOPICS = [
    {
        'topic': '人工智能公共治理',
        'title': '人工智能公共治理的问责机制',
        'body': '人工智能公共治理需要明确算法决策的责任主体，设置公众申诉和独立审查程序，并保留可追溯的决定依据。公共部门应公布评估结果及纠错流程。',
        'neighbor_title': '公司治理与董事会建设',
        'neighbor_body': '公司治理改革关注制造企业董事会结构、股东表决权和财务审计制度。治理程序需要根据企业规模设置，年度报告应说明管理层责任及投资安排。',
    },
    {
        'topic': '香港人口老龄化与基层医疗',
        'title': '香港基层医疗应对人口老龄化',
        'body': '香港人口老龄化增加了基层医疗的慢性病随访需求。社区诊所需要扩大护理人员配置和家庭医生服务，并衔接长者转诊、康复和上门照护。',
        'neighbor_title': '香港住宅交易与楼市供应',
        'neighbor_body': '香港住宅交易受按揭利率和新楼供应影响，地产代理关注成交周期、租金收入及市区重建。楼市报告讨论住宅价格和购房者的融资成本。',
    },
    {
        'topic': '新能源汽车出口与欧盟关税',
        'title': '欧盟关税对新能源汽车出口的影响',
        'body': '欧盟对新能源汽车进口调整关税后，汽车制造商需要重新核算出口价格、物流成本和海外销售布局。电动汽车企业正在评估本地生产与贸易措施的影响。',
        'neighbor_title': '新能源储能电站调度',
        'neighbor_body': '新能源发电需要储能电站平衡电网负荷，储能设备运营商通过电价信号安排充放电。该资料讨论光伏并网、调峰容量和电池循环寿命。',
    },
    {
        'topic': '海底光缆的网络安全风险',
        'title': '海底光缆网络安全与应急恢复',
        'body': '海底光缆承载跨境数据通信，其网络安全风险涉及登陆站访问控制、线路监测和故障恢复。运营商需要建立缆线损坏后的备用路由和跨区域协作机制。',
        'neighbor_title': '深海生物与海底地形调查',
        'neighbor_body': '海底地形调查使用采样设备记录深海生物的分布及沉积物变化。海洋科考人员分析温度、盐度和生物群落，相关记录服务于生态保护工作。',
    },
    {
        'topic': '小学生近视防控',
        'title': '小学生近视防控与户外活动',
        'body': '小学生近视防控应协调课堂用眼、课间户外活动和定期视力筛查。学校应改善照明并建立家校沟通，避免持续近距离阅读造成额外负担。',
        'neighbor_title': '小学生午餐与食堂管理',
        'neighbor_body': '小学生午餐需要均衡搭配谷物、蔬菜和蛋白质。学校食堂应落实食品留样、餐具清洁和采购验收，为有食物过敏的学生提供合适餐食。',
    },
    {
        'topic': '红海航运风险与国际贸易',
        'title': '红海航运中断对国际贸易的影响',
        'body': '红海航运面临的安全风险迫使集装箱船绕航，国际贸易因此承担额外运费、保险费用和交货延迟。航运企业通过调整航线和舱位安排缓解供应链压力。',
        'neighbor_title': '海滨旅游与港口观光',
        'neighbor_body': '海滨旅游项目将码头观光与海上游船结合，游客可以参观港口历史建筑和滨海景点。旅游经营者关注门票、节庆活动及餐饮服务安排。',
    },
]

WRITING_BRIEF = {
    'purpose': '提供风险分析和政策建议',
    'audience': '普通读者',
    'requirements': '结构清晰，介绍背景与发展，比较不同影响，给出建议',
    'focus': '现状、问题及对策',
}


def corpus(store):
    positives = {}
    for index, case in enumerate(TOPICS):
        positives[case['topic']] = publish(
            store, [case['body']], title=case['title'], url=f'https://example.test/relevance/main/{index}')
        publish(store, [case['neighbor_body']], title=case['neighbor_title'],
                url=f'https://example.test/relevance/neighbor/{index}')
    publish(store, ['这份写作指南面向普通读者，建议用清晰结构介绍背景与发展，提供风险分析和政策建议。正文可依次呈现现状、问题及对策，比较不同影响并给出建议。'],
            title='政策分析文章的写作结构', url='https://example.test/relevance/generic-brief')
    return positives


@pytest.mark.parametrize('case', TOPICS, ids=[case['topic'] for case in TOPICS])
def test_multiple_topics_reject_shared_generic_words_and_neighbor_topics(bench, case):
    store, _, service, _, calls = bench
    positives = corpus(store)
    expected = positives[case['topic']]
    result = service.recommend(case['topic'], brief=WRITING_BRIEF)
    assert {item['sourceId'] for item in result['items']} == {expected}
    assert not calls
    for item in result['items']:
        original = {block['id']: block['text'] for block in store.detail(item['sourceId'])['blocks']}
        assert all(passage['quote'] in original[passage['blockId']] for passage in item['passages'])


def test_writing_requirements_alone_do_not_fill_an_unmatched_topic(bench):
    store, _, service, _, calls = bench
    corpus(store)
    result = service.recommend('火星温室土壤改良', brief=WRITING_BRIEF)
    assert result['items'] == []
    assert not calls


def test_year_and_generic_policy_words_cannot_establish_topic_relevance(bench):
    store, _, service, _, calls = bench
    relevant = publish(store, ['2026年人工智能公共治理需要建立算法问责、公众申诉和独立审查机制，公共部门应保留可定位的决策依据并说明纠错程序。'],
                       title='2026年人工智能公共治理', url='https://example.test/relevance/year-good')
    publish(store, ['2026年县域供水政策关注管网改造、漏损控制和水费调整。相关部门讨论民生影响与风险分析，并为公共服务发展提出政策建议。'],
            title='2026年供水政策与公共服务发展', url='https://example.test/relevance/year-bad')
    result = service.recommend('2026年人工智能公共治理的政策与风险分析')
    assert {item['sourceId'] for item in result['items']} == {relevant}
    assert not calls


def test_broad_model_expansion_cannot_replace_the_writing_topic(bench):
    store, models, service, model_id, calls = bench
    positives = corpus(store)
    models.save_preferences({'defaultModelId': model_id})
    service.save_settings({'queryExpansion': True})

    def planner(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
            {'queries': ['公共治理', '风险分析', '政策建议', '背景与发展']}, ensure_ascii=False)}}]})

    models.transport = httpx.MockTransport(planner)
    result = service.recommend('新能源汽车出口与欧盟关税', brief=WRITING_BRIEF)
    assert result['queryOrigin'] == 'model'
    assert {item['sourceId'] for item in result['items']} == {positives['新能源汽车出口与欧盟关税']}
    assert len(calls) == 1


@pytest.mark.parametrize('weak_component', [0.0001, 0.2])
def test_positive_but_weak_cosine_is_not_enough_for_a_recommendation(bench, weak_component):
    store, models, service, model_id, _ = bench
    relevant = publish(store, ['Container vessels reroute around an unsafe passage, increasing freight insurance costs and delaying international shipments.'],
                       title='Liners change route', url='https://example.test/relevance/vector-good')
    publish(store, ['Unrelated material about baking sourdough bread, kneading flour and arranging kitchen utensils for a weekend cookery class.'],
            title='Unrelated cookery', url='https://example.test/relevance/vector-weak')
    requests = []

    def embeddings(request):
        body = json.loads(request.content)
        requests.append(body)
        data = []
        for index, text in enumerate(body['input']):
            vector = [weak_component, 1.0] if ('Unrelated cookery' in text or 'sourdough' in text) else [1.0, 0.0]
            data.append({'index': index, 'embedding': vector})
        return httpx.Response(200, json={'data': data})

    models.transport = httpx.MockTransport(embeddings)
    service.save_settings({'embeddingModelId': model_id,
                           'channels': {channel: channel == 'vector' for channel in CHANNELS}})
    result = service.recommend('红海航运风险')
    assert {item['sourceId'] for item in result['items']} == {relevant}
    assert all(request['model'] == 'embedding-mock' for request in requests)


def test_graph_two_hop_navigation_cannot_promote_an_unrelated_source(tmp_path):
    graph = ExplorationBench(tmp_path / 'isolated-relevance-graph')
    shared_definition = '通信设施是用于发送和接收信息的设备与线路。'
    related, _ = graph.build(
        [entry('cable', '海底光缆网络安全', summary='海底光缆网络安全需要限制登陆站访问并监测缆线故障。'),
         entry('shared', '通信设施', summary=shared_definition)],
        [relation('cable', 'shared', '属于通信设施', 'hierarchy')], title='海底光缆安全')
    unrelated, _ = graph.build(
        [entry('shared', '通信设施', summary=shared_definition),
         entry('school', '小学生近视防控', summary='小学生近视防控通过户外活动和合理用眼维护视力健康。')],
        [relation('shared', 'school', '校园说明涉及两个独立事项')], title='校园设施和健康说明')
    service = RetrievalService(graph.store, graph.models)
    service.save_settings({'queryExpansion': False, 'rerankEnabled': False, 'graphHops': 2,
                           'channels': {channel: channel == 'graph' for channel in CHANNELS}})
    result = service.recommend('海底光缆网络安全')
    assert {item['sourceId'] for item in result['items']} == {related}
    assert unrelated not in {item['sourceId'] for item in result['items']}


def use_planner(models, service, model_id, queries):
    models.save_preferences({'defaultModelId': model_id})
    service.save_settings({'queryExpansion': True})
    models.transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={'choices': [{'message': {'content': json.dumps({'queries': queries}, ensure_ascii=False)}}]}))


def test_concrete_but_wrong_model_expansion_cannot_replace_the_topic(bench):
    store, models, service, model_id, _ = bench
    positives = corpus(store)
    use_planner(models, service, model_id, ['小学生近视防控', '小学生午餐与食堂管理'])
    result = service.recommend('新能源汽车出口与欧盟关税')
    assert {item['sourceId'] for item in result['items']} == {positives['新能源汽车出口与欧盟关税']}


def test_model_expansion_cannot_drop_an_explicit_geographic_constraint(bench):
    store, models, service, model_id, _ = bench
    relevant = publish(store, ['香港人口老龄化使基层医疗面临慢性病随访和社区护理需求。香港的社区诊所应衔接家庭医生、长者转诊和上门照护服务。'],
                       title='香港人口老龄化与基层医疗', url='https://example.test/relevance/region-good')
    publish(store, ['广州人口老龄化使基层医疗面临慢性病随访和社区护理需求。广州的社区诊所应衔接家庭医生、长者转诊和上门照护服务。'],
            title='广州人口老龄化与基层医疗', url='https://example.test/relevance/region-bad')
    use_planner(models, service, model_id, ['人口老龄化 基层医疗', '广州基层医疗'])
    result = service.recommend('香港人口老龄化与基层医疗')
    assert {item['sourceId'] for item in result['items']} == {relevant}


def test_cross_language_model_expansion_preserves_semantically_correct_recall(bench):
    store, models, service, model_id, _ = bench
    relevant = publish(store, ['Hong Kong cybersecurity policy requires incident reporting, coordinated responses and clear accountability. The local regulator publishes guidance for critical infrastructure operators.'],
                       title='Hong Kong Cybersecurity Policy', url='https://example.test/relevance/english-expansion')
    use_planner(models, service, model_id, ['Hong Kong cybersecurity'])
    result = service.recommend('香港网络安全')
    assert {item['sourceId'] for item in result['items']} == {relevant}


@pytest.mark.parametrize('incidental', [
    '详情参见 https://example.ai/report。',
    '（图片由AI生成）',
    '图片由AI生成。',
])
def test_url_domains_and_image_credits_do_not_turn_an_article_into_an_ai_reference(bench, incidental):
    store, _, service, _, _ = bench
    relevant = publish(store, ['AI systems are deployed in public services with model evaluation, audit records and human accountability. Researchers compare their capabilities and remaining limitations.'],
                       title='AI systems in public services', url='https://example.test/relevance/ai-good')
    publish(store, [f'山地徒步活动介绍当地步道、住宿和交通安排。游客需要根据天气准备饮水和保暖衣物，并遵循沿途生态保护规定。{incidental}'],
            title='山地徒步路线介绍', url='https://example.test/relevance/ai-incidental')
    result = service.recommend('AI')
    assert {item['sourceId'] for item in result['items']} == {relevant}


def test_category_json_field_names_are_not_research_topics(bench):
    store, _, service, _, _ = bench
    source = publish(store, ['山区植物调查记录林地分布、海拔高度和季节变化，科考人员通过固定样方持续观察生态环境及野生植物的生长情况。'],
                     title='山区植物调查', url='https://example.test/relevance/category-structure')
    store.patch(source, {'classificationPaths': [{'level1Id': 'science_technology', 'level2': '生态观察', 'level3': '山区植物'}]})
    service.save_settings({'channels': {channel: channel == 'category' for channel in CHANNELS}})
    result = service.recommend('primary')
    assert result['items'] == []


@pytest.mark.parametrize('topic,title,body', [
    ('香港网络安全', '香港網絡安全政策', '香港網絡安全政策要求運營機構記錄事故、保留審查依據，並建立資料保護和應急回應機制。政府部門定期公布本地網絡風險指引。'),
    ('香港網絡安全', '香港网络安全政策', '香港网络安全政策要求运营机构记录事故、保留审查依据，并建立资料保护和应急回应机制。政府部门定期公布本地网络风险指引。'),
])
def test_chinese_script_variants_retrieve_without_rewriting_originals(bench, topic, title, body):
    store, _, service, _, _ = bench
    source = publish(store, [body], title=title, url='https://example.test/relevance/chinese-script')
    result = service.recommend(topic)
    assert {item['sourceId'] for item in result['items']} == {source}
    detail = store.detail(source)
    assert detail['title'] == title
    assert detail['blocks'][0]['text'] == body
    assert all(passage['quote'] in body for passage in result['items'][0]['passages'])


def test_english_inflections_do_not_discard_a_relevant_policy_reference(bench):
    store, _, service, _, _ = bench
    source = publish(store, ['Cybersecurity policy requires incident reporting and clear responsibility for infrastructure operators. A national policy framework establishes oversight and regular audits.'],
                     title='Cybersecurity policy framework', url='https://example.test/relevance/english-inflection')
    result = service.recommend('Cybersecurity policies')
    assert {item['sourceId'] for item in result['items']} == {source}


def test_one_incidental_body_mention_is_not_counted_twice_by_overlapping_windows(bench):
    store, _, service, _, _ = bench
    background = '山区旅游路线介绍步道、住宿、天气和交通安排，游客应准备饮水并遵守生态保护规定。'
    body = (background * 20)[:340] + '某位旅客提到了 AI 一词，但没有展开说明。' + background * 12
    assert body.count('AI') == 1
    publish(store, [body], title='山区旅游路线与行程安排', url='https://example.test/relevance/overlapping-window')
    result = service.recommend('AI')
    assert result['items'] == []
