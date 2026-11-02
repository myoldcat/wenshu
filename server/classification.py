"""Program-owned topic taxonomy, path identities and model output gates."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
import unicodedata

TAXONOMY_VERSION = 'wenshu-topics-3.0'
PROMPT_VERSION = 'wenshu-classification-2.3'
NAMING_VERSION = 'wenshu-topic-names-1.2'
MAX_PATHS = 3
UNCLASSIFIED = '未归类'
CATEGORY_NAMES = (
    ('science_technology', '科学与技术'),
    ('artificial_intelligence', '人工智能'),
    ('cybersecurity', '网络安全'),
    ('politics_governance', '政治与公共治理'),
    ('military_security', '军事与安全'),
    ('economy_finance', '经济与金融'),
    ('industry_business', '产业与商业'),
    ('law_justice', '法律与法治'),
    ('society_livelihood', '社会与民生'),
    ('education_learning', '教育与学习'),
    ('history_culture_arts', '历史、文化与艺术'),
    ('philosophy_thought', '哲学与思想'),
    ('health_medicine', '健康与医学'),
    ('resources_environment', '资源与环境'),
    ('life_leisure', '生活与休闲'),
)
CATEGORIES = dict(CATEGORY_NAMES)
CATEGORY_SCOPE = {
    'science_technology': '数学、自然科学、工程与通用计算技术；人工智能及网络安全已有独立一级类。',
    'artificial_intelligence': '人工智能算法、模型、训练推理、开发部署工具链、智能系统应用、行为与可靠性评估及技术责任机制；政府的AI产业政策归公共治理。',
    'cybersecurity': '数字系统、网络及数据的保密性、完整性、可用性；身份权限、隔离、加密和安全审计等预防性控制，以及攻防、漏洞与隐私保护。',
    'politics_governance': '政治制度、公共政策、政府治理、国际关系与外交。',
    'military_security': '战争、军队、武器、国防、军事战略、国家安全与情报。',
    'economy_finance': '宏观经济、财政、货币、贸易、企业融资、资本结构、估值、金融市场与投资。',
    'industry_business': '具体行业、企业经营、商业模式、竞争、产品与组织管理。',
    'law_justice': '法律法规、司法程序、权利救济、条文解释与合规制度。',
    'society_livelihood': '人口、就业、住房、社会保障、社会结构与公共服务。',
    'education_learning': '教育制度、教学、学习方法、人才培养与职业发展。',
    'history_culture_arts': '历史、文明、文学、语言、艺术、文化传统与传播。',
    'philosophy_thought': '哲学、逻辑、伦理、宗教思想、价值观与认识方法。',
    'health_medicine': '医学、疾病、公共卫生、心理健康、营养与健康管理。',
    'resources_environment': '资源、能源、气候、生态、环保与可持续发展。',
    'life_leisure': '日常生活、家庭、消费、旅行、运动、娱乐与兴趣。',
}

BOUNDARY_RULES = '''重叠边界按实质分析角度判定，事项领域与分析角度可以不同：
1. 人工智能/科学与技术：模型、训练、推理、智能行为及AI专用开发部署工具链归人工智能。开发框架的迁移、接口兼容、跨硬件适配及推理工具集成，若具体解释兼容关系、迁移配置或运行机制且占实质篇幅，就有技术实质，不要求算法创新；这些机制服务产业竞争也不应遗漏。通用芯片、网络、软件工程及非AI自然科学归科学与技术；仅使用AI或提及AI公司、产品名不加AI。
2. 人工智能/公共治理：区分AI系统治理与政府产业政策。智能系统的行为、可靠性评估和技术责任机制归人工智能；研发资助、公共算力配置、政府职责、公共投入、产业发展政策及区域协作的目标和执行归政治与公共治理。AI仅为政策对象、主体论述这些政府制度安排时，以政治与公共治理为主路径，不能因政策面向AI或旧路径叫AI治理就归AI。另给AI路径须有系统能力、应用机制或技术治理的实质专业角度，不能复述政策对象；提到人才或培训不自动增加教育路径。
3. 人工智能/网络安全：智能体行为、模型可靠性和AI责任治理归人工智能；身份认证、访问权限、最小授权、数据隔离、加密、攻击防护和安全审计归网络安全。网络安全包含预防性保护，不要求出现攻击事件、漏洞、攻防分析或另立威胁模型。若具体解释控制如何执行、保护什么、如何限制行为或保留安全证据，并占实质篇幅，应保留安全角度；这些控制是AI治理组件、共同目标或同一论证链的一部分，不是排除安全路径的理由。仅列举控制名词或泛说安全可靠、合规、风险可控仍不足以增加路径。
4. 网络安全/军事与安全：漏洞、攻防、数据及系统保护归网络安全；作战、国防、军事威慑与国家安全战略归军事与安全。网络战若主要讲技术攻防归网络安全，若主要讲战略与冲突归军事与安全；两方面均有实质论述才同时归入。攻击者国别、国家背景及受害地区本身不构成政治或军事路径。
5. 军事与安全/政治与公共治理：作战、军力、武器、情报、国防安全归军事与安全；政治制度、外交谈判、国际秩序与公共治理归政治与公共治理。军事力量或战果作为谈判筹码，持续解释的是调解、要价、政治代价、协议条件或谈判空间时，归政治与公共治理下的国际关系；军事后果对谈判的影响不等于军事运作机制。出现战争、威慑等词语或罗列援助、协防、派兵等行动，不足以增加军事路径。军事战略等路径须持续具体展开军事能力或力量运用本身的机制，如军力建设、部署、行动选择、威胁可信性、威慑或作战如何运作及其效果，并占实质比重，不要求一定提供部署方案；此类军事机制与外交过程均充分展开时，仍可同时保留两条路径。战争作为外交背景不构成第二条军事路径；外交表态提及国际法不自动增加法律路径。
6. 科技/产业：技术机制归相应技术主题；供需、竞争和经营归产业与商业。科技公司财报不因公司名称自动归科技或AI；AI仅作需求或产品背景不加AI。风险披露不等于AI治理：附加AI路径须持续具体展开行为的情境、行动与结果及评估，技术控制如何工作，或责任主体、权限、监督和问责如何执行。仅列异常现象或潜在危害，再说企业可能担责、赔付、保险不足，仍是财务或经营风险；即使分成多个段落或称其为责任治理，也不得另列AI。不能把“可能赔付”当成治理分析；财经文章若有前述实质专业过程，仍保留AI交叉角度。
7. 政治/法律/民生：政策目标、权力关系、决策和治理效果归政治与公共治理；法律效力、适用边界、权利义务、登记认证、合规流程、执法和司法救济归法律与法治；群众处境、住房保障、安置援助和公共服务效果归社会与民生。以法规解释和履行规则为主的文章优先法律主路径；受影响人群及支持服务充分展开可另给民生路径。同为住房、医疗或教育事项不能据此合并不同角度；仅有法名、政府发布者或一般社会意义不足以增加路径。
8. 经济/产业：经济与金融包括企业融资、融资轮次、资本结构、估值、证券定价、仓位和投资风险，不限于宏观或整体市场；产业与商业关注产品、客户、订单、供需、竞争和经营。融资与估值是主体时优先经济与金融，经营发展另有实质内容才另给产业路径；经营分析中只有一个融资金额或股价涨跌不加金融。行业经营与证券投资可共享同一研究对象，两方面的机制均充分展开可同时保留；企业属于AI、法律等行业不自动增加相应主题。
9. 经济/政治：财政货币工具、债务、金融产品和市场定价归经济与金融；政治制度与政策组织实施归政治与公共治理。公共部门跨层级、跨主体的职责分工若具体展开谁与谁协作、纵横关系、推荐筛选、信息资源传递或服务对接，已在解释公共政策如何实施，属于治理角度；不要求另有权力冲突、法律程序、量化效果或完整闭环。此机制即使服务融资、产业或技术目标也应保留，不能只因目标是经济发展就归入金融。只有机构名单、签约或笼统合作愿望而未说明角色、动作和衔接，才视为背景。议会、选举和政治联盟等机制充分展开时也保留政治路径。
10. 资源环境/科技/经济：资源利用、能源供给、气候影响、生态保护归资源与环境；解决问题的技术原理归相应技术主题；能源价格与金融投资归经济与金融。仅有能源行业背景不同时贴三类。
11. 社会/政治/教育/健康：社会结构、民众处境、公共服务效果归社会与民生；制度设计与治理过程归政治与公共治理；教学学习本身归教育与学习；疾病治疗与公共卫生本身归健康与医学。按实质问题而非政策发布者分类；事项领域仅被提及时不增加路径。
12. 哲学/历史文化/生活：概念、价值与论证归哲学与思想；历史事实、文化作品和艺术表达归历史、文化与艺术；实用日常建议、休闲体验归生活与休闲。地域、年份、人物、机构、新闻/报告等文体不是主题层级。'''

NAMING_RULES = '''命名按以下顺序执行；本节规范优先于文章用语和existingPaths中的名称：
先按实质角度确定一级，再从该一级的默认二级范围选用。二级是稳定子领域，三级是其中可反复使用的一般议题。只要默认二级能容纳主题，就直接用其原名，不得另造更窄的二级或添加事件限定词；确实均未覆盖时才建同等宽度子领域。三级选通用名词议题，通常2至8字（仅为建议，可为独立表意适当超出，不是字数门禁）。各级名称也会单独作为标签展示，脱离父级后仍须清楚表达主题：仅补足最低必要的领域或概念限定，不机械拼接父级名称；这为消除歧义，不是按具体主体、对象类型、措施、场景、并列动作或结果进一步细分。不要把标题、摘要、因果链或一次性结论改写成目录。
默认二级范围：科学与技术：自然科学、计算机技术、工程技术；人工智能：AI模型与算法、AI智能体、AI应用、AI治理；网络安全：身份与访问控制、数据安全、系统与网络安全、网络威胁分析；政治与公共治理：政治制度、公共政策、国际关系；军事与安全：国防建设、军事战略、军事行动；经济与金融：宏观经济、公共财政、企业金融、金融市场；产业与商业：行业研究、企业经营、商业模式；法律与法治：民商法、行政法、司法制度；社会与民生：就业、住房、社会保障；教育与学习：教育制度、教学方法、学习方法；历史、文化与艺术：历史研究、文化研究、艺术；哲学与思想：认识论、伦理学、政治哲学；健康与医学：临床医学、公共卫生、心理健康；资源与环境：能源、生态保护、气候变化；生活与休闲：家庭生活、旅行、体育。
默认三级参考（非封闭分类表、非文章映射）：AI治理：AI责任与问责、AI安全、AI伦理；AI智能体：智能体行为评估、智能体执行机制；AI模型与算法：AI开发框架、AI模型训练、AI推理优化；身份与访问控制：身份认证、访问权限管理、令牌安全；系统与网络安全：网络安全防护；企业金融：上市融资、企业估值、企业财务风险；企业经营：企业经营风险、企业业绩、供应链管理；金融市场：股票市场、债券市场、基金投资、资本市场制度；国际关系：国际冲突调解；公共政策：科技政策。
角度资格通过后，三级先从对应参考中选择，凡能容纳实质议题必须使用参考原名，不另造近义或更窄名称；参考数量不构成多路径理由。若多个工作重点只是在说明同一综合市场制度改革，聚合为资本市场制度；不同实质问题仍按角度资格规则判定。参考确实未覆盖的新议题可建同等宽度名称，不强套不相关参考。已有宽议题能容纳时，不再叠加协议、技术架构、业务环节、上市阶段或具体资源对象限定；这些细节及主体、产品、地区、措施、结论写入reason。例如协议名不加在令牌安全前，资源类型不加在供应链管理前，上市阶段不加在企业财务风险前。上市融资等通用议题本身仍可使用。股票市场、上市融资、数据安全等本身已自足的名称不重复添加祖先词。
复核：移除文章中的具体主体、时间、地点、实施手段与结论后，留下的一般议题是什么？能否接纳不同主体用不同措施讨论同一问题的文章，并在脱离目录时独立理解？若不能，继续概括语义并补足必要领域，不是只删字缩短。三级须比二级具体且不重复完整祖先名称；可复用不要求当前库中已有多篇。同一安全议题的攻击与防护措施合为一条议题路径，不能用攻/防两面或近义措辞凑多路径；各自实质分析的是不同问题才可分列。跨一级的不同实质角度仍按边界规则保留。'''

ANGLE_QUALIFICATION = ('先审角度资格，再定目录名称：逐节扫描当前输入的全部主文段落，包括后部章节，候选必须有持续具体展开的问题、过程、机制或案例。'
                       '不能用主标题、前文主题或事项的行业覆盖后文另一实质角度；含公共主体职责、实施动作及衔接的章节，须单独判断治理机制，不能只当行业背景。'
                       '段落数、关键词、被引用文件的篇幅描述、已有路径和命名示例都不能代替实质证据。'
                       '先检查全部跨一级角度，再合并同一级包含或重复议题，之后才选最多三条并命名，不能先填满路径配额。')


def taxonomy():
    return {'version': TAXONOMY_VERSION, 'levels': 3, 'maxPaths': MAX_PATHS,
            'categories': [{'id': key, 'name': name, 'scope': CATEGORY_SCOPE[key]} for key, name in CATEGORY_NAMES]}


def _name(value):
    if not isinstance(value, str):
        raise ValueError('二三级分类名称必须为文本')
    if re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError('分类名称不能包含控制字符')
    name = ' '.join(unicodedata.normalize('NFKC', value).split())
    if not name or len(name) > 32 or re.search(r'[/\\>→|\x00-\x1f\x7f]', name):
        raise ValueError('分类名称须为1至32字且不能包含路径分隔符或控制字符')
    return name


def path_key(path):
    return path['level1Id'], path['level2'].casefold(), path['level3'].casefold()


def _path(value):
    if not isinstance(value, dict) or not isinstance(value.get('level1Id'), str) or value['level1Id'] not in CATEGORIES:
        raise ValueError('模型返回未知一级分类，未改变文档分类')
    key = value['level1Id']
    if value.get('level1Name', CATEGORIES[key]) != CATEGORIES[key]:
        raise ValueError('一级分类 ID 与名称不匹配')
    second, third = _name(value.get('level2')), _name(value.get('level3'))
    if second.casefold() == CATEGORIES[key].casefold() or third.casefold() == CATEGORIES[key].casefold():
        raise ValueError('子分类不能重复一级祖先名称')
    if second != UNCLASSIFIED and second.casefold() == third.casefold():
        raise ValueError('三级分类不能重复二级祖先名称')
    if second == UNCLASSIFIED and third != UNCLASSIFIED:
        raise ValueError('二级未归类时三级也必须为未归类')
    return {'level1Id': key, 'level2': second, 'level3': third}


def validate_paths(value, allowed, max_paths=MAX_PATHS):
    paths = value.get('paths') if isinstance(value, dict) else None
    if not isinstance(paths, list) or not 1 <= len(paths) <= max_paths:
        raise ValueError(f'分类结果需为1至{max_paths}条完整三级路径')
    clean, seen = [], set()
    for path in paths:
        result = _path(path)
        key = path_key(result)
        if key in seen:
            raise ValueError('分类结果包含重复路径')
        seen.add(key)
        if type(path.get('primary')) is not bool:
            raise ValueError('每条分类路径必须明确 primary 布尔值')
        reason = path.get('reason')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
            raise ValueError('分类依据须为1至300字，并说明主题实质内容与比重')
        refs = path.get('blockIds')
        if not isinstance(refs, list) or not refs:
            raise ValueError('每条分类路径的 blockIds 必须为非空数组，选择1至3个最直接的原文块')
        if len(refs) > 3:
            raise ValueError('每条分类路径的 blockIds 最多3个，请只保留1至3个最直接的原文块引用')
        if any(not isinstance(ref, str) for ref in refs):
            raise ValueError('blockIds 数组中的每一项必须为原文块 ID 字符串，不能使用对象或数字')
        if any(ref not in allowed for ref in refs):
            raise ValueError('blockIds 含未知原文块 ID：只能原样复制当前输入的 blockId（汇总时复制候选的 blockIds），不能使用 pieceId、start、end 或自行编造 ID')
        result.update(primary=path['primary'], reason=reason.strip(), blockIds=list(dict.fromkeys(refs)))
        clean.append(result)
    if sum(path['primary'] for path in clean) != 1:
        raise ValueError('每篇文章必须有且只有一条主分类路径')
    clean.sort(key=lambda path: not path['primary'])
    return {'paths': clean}


def _nodes(path):
    result = [{'id': path['level1Id'], 'name': CATEGORIES[path['level1Id']]}]
    for name in (path['level2'], path['level3']):
        signature = result[-1]['id'] + '\0' + name.casefold()
        node_id = 'topic_' + hashlib.sha256(signature.encode('utf-8')).hexdigest()[:24]
        result.append({'id': node_id, 'name': name})
    return result


def canonicalize_manual_paths(value):
    if not isinstance(value, list) or len(value) > MAX_PATHS:
        raise ValueError('人工分类最多保留3条三级路径')
    results, seen = [], set()
    for path in value:
        normalized = _path(path)
        key = path_key(normalized)
        if key in seen:
            raise ValueError('人工分类包含重复路径')
        seen.add(key)
        results.append({'nodes': _nodes(normalized), 'primary': len(results) == 0})
    return results


def path_tags(paths):
    return list(dict.fromkeys(node['name'] for path in paths for node in path['nodes']))


def make_result(validated, taxonomy_version=TAXONOMY_VERSION):
    paths = [{'nodes': _nodes(path), 'primary': path['primary'], 'reason': path['reason'], 'blockIds': path['blockIds'],
              'namingVersion': NAMING_VERSION}
             for path in validated['paths']]
    for path, source in zip(paths, validated['paths']):
        if 'evidenceCoverage' in source:
            path['evidenceCoverage'] = source['evidenceCoverage']
    categories = [{'id': path['nodes'][-1]['id'], 'name': ' › '.join(node['name'] for node in path['nodes']),
                   'reason': path['reason'], 'blockIds': path['blockIds']} for path in paths]
    return {'classificationPaths': paths, 'tags': path_tags(paths), 'categories': categories,
            'taxonomyVersion': taxonomy_version}


def existing_paths(db, max_chars=1800):
    """Reuse current naming-policy outputs, fairly budgeted across top-level topics.

    The version records the naming instruction used, not a semantic quality
    certification. Legacy and human paths remain stored but are not exemplars.
    """
    counts, candidates = Counter(), {}
    for row in db.execute('SELECT classification_paths_json FROM sources ORDER BY id'):
        try:
            for entry in json.loads(row[0]):
                if not isinstance(entry, dict) or entry.get('namingVersion') != NAMING_VERSION:
                    continue
                nodes = entry['nodes']
                if len(nodes) != 3:
                    continue
                path = _path({'level1Id': nodes[0]['id'], 'level2': nodes[1]['name'], 'level3': nodes[2]['name']})
                key = path_key(path)
                counts[key] += 1
                candidates[key] = path
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    ordered = sorted(candidates, key=lambda key: (-counts[key], key))
    groups = [[key for key in ordered if key[0] == level1]
              for level1 in CATEGORIES]
    selected = []
    # Offer one path per top-level category before taking a second from any of
    # them; otherwise a frequent/alphabetically early domain consumes the cap.
    for rank in range(max(map(len, groups), default=0)):
        for keys in groups:
            if rank >= len(keys):
                continue
            candidate = selected + [candidates[keys[rank]]]
            if len(json.dumps(candidate, ensure_ascii=False, separators=(',', ':'))) <= max_chars:
                selected = candidate
    return selected


def prompt_contract(reduce=False):
    task = ('综合各部分候选与原文摘录，判断全文主问题。只选择候选中已经出现的完整三级路径，不能拼接不同候选的层级；blockIds只能原样复制所选完整路径候选的blockIds。'
            '候选已在分块时按一般议题命名；先识别跨一级实质角度，再从同一级包含、相邻或重复候选中保留最能代表中心问题的完整路径，不按分块数累加路径。不得为改粒度而重命名或新造路径。'
            'supportChars只是原始证据片段的字符覆盖上界，并非精确主题字数或主题占比，不得把整个归并组长度算给一条路径。'
            if reduce else '根据标题和当前原文判定主题；长文分块时给当前部分的候选，后续程序会汇总全文。blockIds只能原样复制当前输入blocks数组里的blockId字段，即blocks[].blockId。')
    return (task + '\n' + ANGLE_QUALIFICATION + '\n' + NAMING_RULES + '\n每篇选择1至3条完整的一级-二级-三级路径，必须恰好一条primary=true。不要凑满3条。'
            '先辨认正文回答的问题、分析的机制和结论，再对应主题；主路径选中心问题及主要论述，不能只按涉及的行业、政策对象或关键词决定。'
            '每条路径必须在文章中占相当比重并有独立、实质的展开。独立指可辨认的分析角度，不要求不同事件、对象或互不相关的话题；'
            '同一事件或对象的不同实质分析角度可以形成多路径，两个角度互为原因、共同服务同一目标也不互相排斥。'
            '偶然提及、背景、工具、举例、人物或公司名称不足以形成另一条路径。网站导航、广告、推荐文章及转载尾注不作为主文主题依据。'
            '路径选择先检查跨一级的实质角度，避免事项领域吞掉其他角度；再审核同一级是否确需额外路径，各自须有充分展开的不同问题与机制。'
            '同一级的背景—风险—措施—结果链通常合为一个议题：可见性问题引出监督问责、认证授权风险引出不同保护措施，不能只因段落、小节、二级名称或流程阶段不同就拆路径。'
            '包含、相邻或近义议题先合并；同一级路径若只是语义上位/下位或整体/组成环节，不可并列。同一综合制度或治理方案的不同工作重点先聚合，只有另回答独立问题且有充分展开的不同机制，才增加路径。不能把原文各节换成通用名后仍照小节数输出。跨一级的不同实质机制仍可共同解释同一事件，不因因果关联排除。'
            '合并后若仍超过3个合格角度，按中心性及实质内容取最重要的3个。reason须说明原文具体解释的过程、内容比重及新增问题，不能只说多段提及、可能担责或造成损失。删除证据不足的附加角度后，再检查跨一级是否遗漏；不使用自报置信度替代证据。'
            '引用是代表证据，不是全部内容；不能按块数、引用数或片段覆盖上界直接判断比重，长段落也可能充分展开第二角度，同一块可支持不同实质角度。一级严格使用taxonomy中的id。'
            '先依据原文和边界规则独立判定一级与实质角度，再参考existingPaths命名；existingPaths不是分类证据，也不支持沿用旧归属或旧路径数，已有路径的错误归属可以纠正。'
            '二三级使用简体中文、简短稳定的主题名，逐级缩小主题范围。existingPaths可能含过细旧分类，仅在一级、语义和粒度均合适时优先复用；旧名带事件细节或限定过窄时，在分块分类阶段归并为可复用的一般议题，不照抄旧名，也不另外保留一条旧路径。'
            '不得把多个主题用分隔符塞进一个名称，不用地域、年份、文体、人物或机构作主题层级，不重复完整祖先名称。'
            '证据不能支持更细主题时使用未归类；二级未归类时三级也必须未归类，不能为了三级而臆造细分。'
            '输出严格为{"sourceLanguage":"从allowedSourceLanguages原样选择的原文语言代码","sourceLanguageShares":{"zh":0.7,"en":0.3},"paths":[{"level1Id":"一级id","level2":"二级名称","level3":"三级名称","primary":true,"reason":"依据和比重说明","blockIds":["真实原文块id"]}]}。'
            'sourceLanguage须从输入allowedSourceLanguages原样选择，表示来源原文语言，不是本次中文结果的语言；语言标签由程序生成，不加入主题路径。'
            '简体中文与繁体中文统一识别为zh，不区分、不互译。sourceLanguageShares给出当前原文各语言的实质自然语言正文占比，使用0至1小数、合计为1；单语也给出该语言占比1。'
            '计算语言比重先排除技术术语、代码、实体名字（人名、机构名、产品名等）、网址和孤立缩写；不能把它们当作另一种语言正文。'
            '只有至少两种语言各自占比达到30%（含30%）才识别为mixed，否则选择占比最高的主要语言；多批结果按有效正文量加权汇总，不能因分块语言不同就直接判为mixed。合并阶段原样保留程序提供的sourceLanguage及sourceLanguageShares，不从中文理由重新识别语言。'
            '名称最多32字，理由最多300字，每条blockIds仅保留1至3个最直接原文块ID字符串，不要列出所有相关块。'
            'pieceId、start、end只用于片段定位，绝不能当作blockIds值；不得输出节点id，节点身份由程序生成。\n' + BOUNDARY_RULES)
