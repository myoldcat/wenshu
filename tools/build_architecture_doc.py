from design_doc_layout import DesignDoc, architecture_image, WORK
import json

d=DesignDoc()
d.title('KooHub 技术选型与系统详细设计')
d.subtitle('首版工程方案 V1.0')
d.p('设计日期 2026年10月21日    适用范围 单人本地知识库与写作软件')
d.page('1 选型结论与设计目标')
d.p('推荐采用本地 Web 工作台、模块化 Python 后端、独立后台 Worker、PostgreSQL 与 pgvector，以及本地内容寻址文件库。以不可变证据、章节级写作策略和可追溯修订为核心，支持万级网页和数万字以内创作。')
d.p('产品约束以用户在对话中确认的需求为准。工作区需求与技术架构文档仅用于参考和覆盖核对；本方案独立比较技术路线，已有文档中的选型、分期和默认值均不构成必须沿用的约束。')
d.table(['决策','推荐方案','直接收益'],[
('产品形态','本地浏览器访问的 SPA','编辑与阅读空间充足，服务及数据留在本机。'),
('存储','PostgreSQL 18 与 pgvector','业务版本、引用、筛选和任务结果共享约束。'),
('检索','中文全文与向量召回，RRF 融合','同时处理术语匹配与语义查询，可独立评测。'),
('创作','显式工作流与章节证据包','模式约束贯穿生成、编辑和评审。'),
('导出','统一 ExportIR 与格式适配器','编号、引用和图表来源在多格式间一致。'),
('部署','本地 Compose 四个服务','Web、API、Worker、数据库分别启停与恢复。'),
],[24,66,80])
d.h('选择标准与验证边界')
d.p('优先考虑资料与引用可靠性、中文检索质量、写作可控性，再平衡本地维护成本与扩展能力。PostgreSQL 是当前工程首选，SQLite 与 LanceDB 是有竞争力的轻量备选；两者均能满足业务约束，最终质量需通过相同语料的验证。')
d.p('本文给出明确的推荐基线、数据契约、接口和实施顺序。容量为带假设的规划计算，性能与质量阈值为建议验收目标；未执行软件实现、真实模型调用或负载测试。版本锁定和关键 PoC 是开发入口条件。')

d.page('2 技术路线比较与决策依据')
d.table(['路线','优势','代价与边界','结论'],[
('PG 与 pgvector','关系、版本、全文、向量和任务在同一数据库；复杂资料筛选自然。','本地数据库服务、容器或安装包、备份与扩展升级；中文原生排序不是 BM25。','当前首选'),
('SQLite 与 LanceDB','无需数据库服务；LanceDB 支持 BM25 和中文分词；安装可更轻。','须维护派生索引同步与代际切换，结果回查 SQLite；原生依赖仍需打包验证。','便携方案备选'),
('PG 与 Qdrant','向量及过滤搜索可独立扩展。','增加服务、备份及一致性协议；当前规模收益尚未证实。','检索瓶颈后评估'),
('PG 与 Neo4j','复杂路径查询、图算法与专用图工具。','额外数据库与模型同步；首版主要是证据关联和小子图。','图分析成为核心再评估'),
('复用完整 RAG 平台','可复用部分采集与检索能力。','章节模式、编辑器引用、模板和修订仍需定制；平台升级耦合较深。','作为组件参考'),
],[28,49,65,28])
d.p('这项选择并非“万级网页需要服务器数据库”。SQLite 的 WAL 支持读写并行，但同一时刻只有一个写入者；通过短事务和受控写入仍可承载单人场景。LanceDB 当前具备 BM25 与中文分词能力。以上能力依据 [S04][S05]。')
d.h('可推翻当前选择的条件')
d.p('若便携安装成为最高优先级，或同设备、同语料、同 embedding 的对照中 SQLite 与 LanceDB 明显改善检索质量，同时通过恢复与零越界测试，则切换轻量路线。若仅排序质量不足，先更换 RetrievalAdapter，不迁移文章、证据与引用模型。')
d.p('两种路线都不能依靠数据库自动实现严格写作；应用层仍必须冻结资料范围、校验证据、保护人工版本。PostgreSQL 与本地文件也不是一个原子事务系统，文件提交和一致备份另行设计。')
d.h('关键取舍')
d.p('采用模块化单体而非微服务；独立 Worker 避免模型调用阻塞交互。显式工作流负责状态和恢复，模型只承担受约束的生成任务。首版不引入独立消息队列、独立图数据库和完整自治 Agent 框架。')

d.page('3 推荐技术栈与版本策略')
d.table(['层次','技术选择','使用边界'],[
('前端运行与构建','React 19、TypeScript、Vite；Node 24 LTS 构建','SPA；服务端无 SEO 或 SSR 需求。'),
('界面与状态','Tailwind、shadcn/ui、TanStack Query、Zustand','设计令牌统一主题；服务器缓存与工作台状态分离。'),
('编辑与图谱','Tiptap OSS、ProseMirror；Cytoscape.js','自建引用、修订和评审；不依赖付费协作或 AI 服务。'),
('API 与校验','Python 3.14、FastAPI、Pydantic 2','类型化契约；CPU 密集任务进入 Worker 子进程。'),
('数据库访问','SQLAlchemy 2.1、psycopg 3、Alembic','一次事务一个 Session；迁移单进程执行。'),
('业务与检索','PostgreSQL 18、pgvector 0.8.7；jieba','版本为初始验证线；确切补丁和镜像摘要在 PoC 后锁定。'),
('网页处理','HTTPX、lxml、Trafilatura','HTTP 优先；保留 DOM 和原始素材；可选浏览器降级。'),
('模型与搜索','薄 ProviderAdapter、OpenAI SDK；SearchAdapter','首个适配覆盖兼容 Chat 与 Embeddings，其他协议可扩展。'),
('图表与文档','Vega-Lite、vl-convert；Pandoc；Playwright','声明式图表；独立 ExportIR；本地渲染。'),
('任务与部署','PostgreSQL 任务表、asyncio；Compose、Caddy','API 与 Worker 共享代码包，不共享进程内任务状态。'),
],[27,69,74])
d.p('Node 24 处于 LTS；Python 3.14 仍在缺陷修复阶段；PostgreSQL 18 为本方案核查的稳定基线。版本状态来源 [S22][S23][S24]。选择这些版本并不等于依赖组合已经运行通过。')
d.p('以 uv.lock、pnpm-lock、镜像 digest、Pandoc 和 Chromium 版本共同定义可复现构建。首次 PoC 验证 lxml、psycopg、pgvector、图表渲染与 CJK 字体；若 Python 3.14 的必要依赖不兼容，记录原因后退至 3.13。')
d.p('交付前生成依赖与许可证清单。Tiptap 商业扩展、转换服务和协作服务不作为基础依赖；DOCX 转换在本地执行。开源能力、模板能力与本项目自建能力分别登记，避免误把商业功能当作已具备。[S06][S08]')

d.page('4 运行架构与模块边界')
d.image(architecture_image(),'图 1 本地服务边界及可选外部调用')
d.table(['模块','负责的业务','依赖约束'],[
('ingestion 与 content','采集、解析、翻译、块与素材版本','只通过 BlobStore 和 Repository 保存结果。'),
('knowledge 与 retrieval','分类、图谱、Wiki、索引和查询','输出证据定位；不直接写入文章。'),
('writing 与 editing','纲要、策略、证据包、章节与修订','所有生成读取固定上下文和资料版本。'),
('review 与 verification','引用支持检查、全文评审、可选联网核验','意见作为独立对象，修改通过编辑服务。'),
('export 与 integrations','格式输出、模型和搜索适配','适配器可替换；不绕过来源与权限规则。'),
],[38,65,67])
d.p('API 处理短事务与交互，长任务创建 job 后返回 202。Worker 在事务外执行网络或模型调用，再用短事务提交结果。REST 负责命令和查询，SSE 传递进度与流式候选内容；数据库状态始终是恢复依据。[S18]')

d.page('5 数据分层与不可变约束')
d.table(['数据层','对象','变更方式'],[
('原始证据','source_revision、blob','采集快照不可变；新网页内容形成新版本。'),
('内容表示','parse_revision、source_block、translation_revision','解析和翻译独立版本化；不覆盖旧证据锚点。'),
('知识派生','assertion、edge、wiki_revision、retrieval_chunk','由证据生成，可重建；人工修正单独保留。'),
('写作快照','source_set_revision、outline_revision、evidence_pack','冻结本次生成边界；变更产生新版本。'),
('文章与修订','section_revision、citation、edit_proposal','正文不可变版本；当前版本指针以乐观锁切换。'),
('导出产物','export_job、export_manifest、blob','固定文章版本、模板版本和全部素材哈希。'),
],[29,76,65])
d.h('必须始终成立的规则')
d.p('I01 来源块只在其不可变解析版本内稳定；检索 chunk 是可重建对象，不能成为永久引用 ID。I02 原文和译文分别定位，跨语言偏移不能直接互用。I03 引用版本更新必须显式发生。')
d.p('I04 禁用或排除的来源不能进入目标章节证据包。I05 结构范文、全书摘要和相邻章节只提供上下文，不自动获得事实依据身份。I06 模型输出不能直接覆盖用户正在编辑的版本。')
d.p('I07 向量模型、维度或归一化方式改变即产生新索引代际。I08 任务结果须通过租约 fencing 和幂等检查才能发布。I09 自动重处理不覆盖人工锁定内容。I10 图谱断言与事实核验状态分开保存。')
d.h('标识时间与存储原则')
d.p('实体使用应用生成 UUID，所有时间保存 UTC timestamptz，界面按 Asia/Shanghai 展示。版本表保存 schema_version、created_at、content_hash、producer_run_id。频繁过滤与关联字段使用显式列及外键，长正文和可演进属性使用 JSONB。')
d.p('本地文件按 SHA-256 寻址，数据库保存 blob_id、mime、字节数和哈希。上传先写临时文件并校验，再原子重命名，最后提交数据库引用；失败留下的孤立文件由延迟清理扫描处理，不能先提交指向缺失文件的引用。')

d.page('6 资料与检索数据模型')
d.table(['表','核心字段','关系和约束'],[
('source_document','id、source_kind、canonical_url、title、current_rev_id、deleted_at','URL 可规范化但不盲删语义参数；标题不是唯一键。'),
('source_revision','id、source_id、raw_blob_id、final_url、retrieved_at、published_at、hash','FK source_id；同一来源版本不可变；缺失日期为 null。'),
('parse_revision','id、source_rev_id、parser_version、config_hash、status','解析器或规则变化产生新表示版本。'),
('source_block','id、parse_rev_id、parent_id、order_no、kind、text、text_hash、dom_path','同一 parse 内顺序唯一；heading_path 与原 DOM 定位保留。'),
('translation_revision','id、source_block_ids、translated_blocks、alignment、glossary_rev_id','多对多块对齐；保留模型运行记录与人工覆盖。'),
('asset 与 table_data','id、source_rev_id、blob_id、caption、cell_grid、quality_flags','表格保存行列跨度和 cell_id；图片保存原 URL 与尺寸。'),
('retrieval_chunk','id、source_spans、translation_rev_id、text、chunker_version','一个 chunk 可对应多个来源片段；重分块不改证据。'),
('search_document','id、chunk_id、generation_id、tokens、tsv、source_rev_id','GIN(tsv)；显式记录分词词典版本及已发布状态。'),
('embedding_profile','id、provider、model、dimension、normalization、fingerprint','配置不可变；相同维度也不代表同一向量空间。'),
('index_generation','id、profile_id、chunker_version、state、watermark','building、validating、active、retired；活动指针原子切换。'),
('embedding_gN','chunk_id、generation_id、vector、input_hash','每代物理表或分区固定维度与索引配置；禁止混比。'),
],[37,76,57])
d.p('source_document 的当前版本用于默认浏览；旧 source_revision、parse_revision 与素材只要被文章、证据包或版本历史引用就保留。删除首先影响可发现性，永久清除必须处理全部保留关系。')
d.p('索引元数据与来源引用在数据库中同事务发布，但远程 embedding 生成在事务外。未完成向量的内容可先用全文检索，状态明确；不将“可阅读”与“全部加工完成”混为一项状态。')

d.page('7 分类图谱与 Wiki 数据模型')
d.table(['表','关键字段与关系','业务规则'],[
('taxonomy_node','id、parent_id、name、aliases、revision、redirect_id','树结构防环；类别合并保留旧 ID 与迁移记录。'),
('document_category','source_id、category_id、origin、locked、confidence','人工指定优先；自动分类直接生效。'),
('knowledge_node','id、kind、canonical_name、type_id、redirect_id','concept 或 entity；别名不是自动合并的充分条件。'),
('node_alias 与 mention','node_id、language、name；block_id、anchor','提及指向证据；同名异义保持不同实体。'),
('knowledge_assertion','id、kind、statement、speaker_id、time_scope、qualifiers','opinion 或 material；保存提出者和范围，不承诺真值。'),
('evidence_span','id、parse_rev_id、block_id、start_cp、end_cp、exact、prefix、suffix','字符偏移采用 Unicode code point；同时保存校验文本。'),
('knowledge_edge','id、subject_id、predicate_id、object_ref、qualifiers','边是带出处的断言；支持、冲突证据可并存。'),
('evidence_link','owner_type、owner_id、evidence_id、support_kind','知识项、关系和 Wiki 段落都可多对多关联证据。'),
('wiki_page 与 wiki_revision','node_id、blocks_json、manual_overrides、base_revision','自动区可增量生成；人工区或锁定段落不被覆盖。'),
('merge_event 与 audit_event','actor、before、after、mapping、created_at','类别与实体归并可撤销；历史引用保留旧版本。'),
],[39,75,56])
d.h('自动分类与类型扩展')
d.p('先检索已有类别和类型，再让模型选择或提出新项。通过结构校验、名称去重和父级合法性检查后自动生效；低把握项可进入“未细分”类别并提示检查，不把人工审核设为入库前置门槛。分类体系合并与拆分是单独事务。')
d.h('图谱与 Wiki 查询')
d.p('以关系表和递归 CTE 查询局部子图，初始限制 1 至 2 跳、300 个节点，均为可调起点。请求携带类别、时间及来源过滤。前端按需展开，不下载全库图谱。复杂路径或图算法成为主要工作负载后再评估专用图存储。')
d.p('Wiki 只聚合有证据的陈述。新证据触发受影响节点的增量任务；候选修订基于 wiki_revision，用差异形式更新自动区。证据被删除或发生冲突时标记段落需复核，不自动写成单一确定结论。')

d.page('8 写作修订与引用数据模型')
d.table(['表','主要字段','约束'],[
('writing_project','id、brief、default_mode、style_rev_id、current_outline_rev','default_mode 为 strict 或 expanded。'),
('outline_revision','id、project_id、tree_json、confirmed_at、hash','确认后可生成；结构变化产生新版本。'),
('source_set_revision','id、project_id、parent_rev、policy、hash','引用允许范围的固定快照，不使用变化中的查询结果。'),
('source_set_member','set_rev_id、source_id、source_rev_id、selection、span_refs','排除优先；整份排除按 source_id 覆盖后续版本。'),
('section 与 section_revision','id、parent_id、mode_override；rev、editor_json、summary、hash','章节模式 inherit 或覆盖；JSON schema 版本独立。'),
('context_revision','brief_rev、outline_rev、glossary、claim_ledger、summaries','摘要指明依赖的正文版本；过期时不能静默复用。'),
('evidence_pack','id、section_id、set_rev_id、items、queries、policy_rev、hash','冻结可引用内容及原文锚点；严格章节仅使用包内证据。'),
('generation_run','id、base_rev、mode、context_id、evidence_pack_id、model_run_id','保存解析后的实际模式，不事后动态重算旧任务。'),
('citation 与 claim_link','id、section_rev、block_id、evidence_ids、status、span','分开记录引用存在性与陈述支持状态。'),
('edit_proposal','id、base_rev、target_ids、base_hashes、patch、status','接受时重新检查版本、范围和锁定内容。'),
('review_issue','id、section_rev、anchor、dimension、reason、suggestion、state','意见定位到具体版本；修改后可失效或重新检查。'),
('template_revision 与 export_job','kind、schema、blob；document_rev、template_rev、manifest','模板不可变版本；导出绑定所有输入快照。'),
],[40,76,54])
d.p('每次用户编辑或接受 AI 建议产生章节新版本；自动保存可先写工作副本，再按短时间窗口合并为持久检查点，避免每次按键生成完整大版本。离开页面前刷新工作副本，崩溃恢复与版本历史分开设计。')
d.p('人工正文允许保存为草稿，即使严格检查发现缺口；系统标出未通过项，不能把人工输入强行删除。导出可选择保留检查提示的草稿；只有检查无未解决问题的版本才能显示“严格证据检查通过”。')

d.page('9 系统表约束与事务边界')
d.table(['表或约束','设计'],[
('job','id、kind、object_id、input_hash、state、priority、attempt、run_after、lease_owner、lease_until、fence、cancel_requested。'),
('job_dependency 与 stage_result','父任务、依赖阶段、input_fingerprint；产物版本、output_hash、published_at。'),
('model_profile 与 model_invocation','协议、endpoint、secret_ref、capabilities；每次调用的模型、提示词版本、token、费用、结果和错误。'),
('outbox_event 与 event_log','业务事务写 outbox；单序发布器分配持久 event_seq。不得用普通 sequence 充当提交顺序，详见第 23 节。'),
('blob 与 blob_reference','哈希、大小、mime、路径、ready 状态；所有版本对象对文件的保留引用。'),
('settings 与 audit_event','版本化设置、操作者、对象、变更摘要；密钥不进入普通审计内容。'),
('document_revision','id、project_id、有序 section_revision_ids、outline_rev、style_rev、policy_refs、hash；一致快照中创建，全文评审与导出只读此冻结清单。'),
('section_policy_revision','id、section_id、effective_mode、inheritance_refs、source_set_rev、scope、hash；保存继承解析结果。策略变化后，旧运行结果为 stale 候选。'),
],[48,122])
d.h('索引与唯一性约束')
d.code('UNIQUE stage_result(stage, object_id, input_fingerprint)\nUNIQUE source_block(parse_rev_id, order_no)\nUNIQUE source_set_member(set_rev_id, scope_key, selection)\nINDEX job(state, run_after, priority) WHERE state = \'queued\'\nINDEX evidence_span(block_id), citation(section_rev_id)\nGIN search_document(tsv)\nBTREE source_document(source_domain, id)')
d.p('上述为逻辑索引示意，最终迁移需补齐外键、CHECK、实际字段名和执行计划验证。频繁组合筛选按实际查询分布设置索引，避免对所有组合盲目建索引。JSONB 仅为需要查询的路径建立表达式索引。')
d.h('事务提交原则')
d.p('资料版本与后续任务同事务完成；外部调用在事务外。发布结果须满足第 23 节完整租约 CAS、输入与策略版本、目标 base_revision；不满足时不能发布。scope_key 规范化整份、单版或片段范围，空值不能绕过唯一约束。')
d.p('网络失败后允许任务再次执行，但同一阶段输入只发布一个成功结果。本地事务幂等不能保证外部服务只计费一次。业务状态不依赖内存回调或浏览器长连接，API 重启后可从数据库重建任务进度。')

d.page('10 入库流水线与内容表示')
d.table(['阶段','处理','持久输出'],[
('接收','创建批次；保留文件名、URL、来源类型和幂等键。','import_batch 与 import_item。'),
('抓取','HTTPX 流式下载；检查重定向、大小、类型与目标地址。','原始响应 blob 与 source_revision。'),
('结构解析','lxml 保存 DOM；Trafilatura 辅助正文判定；独立解析表格图片。','parse_revision、source_block、asset。'),
('内容加工','语种识别、块级翻译、摘要与自动分类。','翻译、摘要、归类结果各有版本。'),
('知识加工','抽取观点和素材，消歧、关系归并、Wiki 更新。','带 evidence_span 的知识派生对象。'),
('索引发布','生成检索 chunk、中文词元与 embedding。','已发布索引代际与阶段状态。'),
],[25,83,62])
d.p('Trafilatura 的正文判定不能替代原始 DOM 存档；复杂表格、图注和位置由 lxml 分支保留。[S10] 普通公开网页并不等于仅静态页面：HTTP 正文不足时可按用户设置启用隔离浏览器补抓，保存渲染 DOM 与原始响应的派生关系。')
d.h('ContentIR 的最小契约')
d.code('{\n  "parse_revision_id": "uuid", "schema_version": 1,\n  "blocks": [{"id":"b1", "kind":"paragraph",\n    "text":"...", "heading_path":["..."], "dom_path":"..."}],\n  "tables": [{"id":"t1", "cells":[{"id":"c1",\n    "row":0,"col":0,"rowspan":2,"colspan":1,"raw":"..."}]}],\n  "assets": [{"id":"a1","blob_id":"sha256","caption":"..."}],\n  "quality_flags": ["missing_image"]\n}')
d.p('URL 规范化仅删除明确无语义的跟踪项；内容哈希去重共享 blob，但不同来源身份仍保留。相似内容只作为重复建议，不因相似就合并出处。相对图片路径以文档来源为基准解析，本地上传只能读取用户本次导入范围内的资源。')

d.page('11 翻译抽取与知识维护')
d.h('翻译与摘要')
d.p('按来源块及标题上下文分批翻译，输入明确 block_id 和术语表版本，输出携带对应 ID。校验块数量、数值、单位与名称保留情况。允许多对多块对齐；无法精确定位句子时退回块级，不虚构跨语言字符位置。摘要每个主要要点关联来源块。')
d.h('结构化知识抽取')
d.p('模型输出分为概念、实体、观点、素材和关系。Pydantic 校验类型与字段，再校验引用 ID 属于本次输入、证据片段能在原块中找到。证据位置无效的项进入失败或待检查结果，不能发布为已证实知识。')
d.p('模型自评分只作为检查排序信号，字段完整和片段存在性作为独立质量指标，不把合成分数当作经过校准的正确率。观点记录主体和条件，素材保留来源的数字、时间和适用范围；关系中的因果表述也保留“来源主张”属性。')
d.h('消歧与自动分类')
d.p('候选通过名称、别名、类型、上下文和语义相似度查找。证据充分且无明显属性冲突时可自动归并；否则保持独立节点，显示候选关联。新类别自动创建必须经过命名去重和层级防环，用户手改的归类不被重跑覆盖。')
d.table(['变化事件','需重算的派生数据','应保持的内容'],[
('源网页新版本','新解析、翻译、知识和索引。','旧引用及当时的证据版本。'),
('术语表更新','受影响翻译和依赖它的索引。','原始块和旧译文版本。'),
('分类合并或拆分','筛选映射、相关视图及检索元数据。','文章正文和永久引用 ID。'),
('知识项合并','别名、关系入口及 Wiki 自动区。','原断言、证据和人工历史。'),
('正文或 Wiki 手改','摘要、评审和关联检查标记为过期。','人工当前版本与锁定段落。'),
],[37,69,64])
d.p('派生依赖通过 dependency_ref(owner_revision, input_revision) 记录。事件发布只标记受影响对象并创建去重任务；在固定输入版本上生成候选结果，避免一次资料更新触发全库无边界重算。')

d.page('12 中文混合检索与范围约束')
d.p('检索链路：解析查询与筛选 → 确定允许资料版本集合 → 并行全文与向量召回 → 合并与去重 → RRF 融合 → 可选重排 → 来源范围二次校验 → 返回带原文定位的结果。项目选材、章节证据检索和普通搜索共享 RetrievalService。')
d.h('全文通道')
d.p('原文、中文译文和术语别名分别记录。默认使用 jieba 精确分词及领域词典，将词元送入 PostgreSQL simple 配置并构建 GIN；标题与术语提高权重。索引与查询使用相同词典版本。搜索模式的重叠词用于额外召回，避免混入短语位置。')
d.p('PostgreSQL 的 ts_rank 与 ts_rank_cd 不是 BM25。先以中文标注集评估词元、短语、混合排序，再决定是否加 LanceDB 等派生检索后端；不能仅因数据库有全文功能就推断中文质量足够。[S02][S04][S25]')
d.h('向量与融合通道')
d.p('在同一 embedding_profile 下生成查询向量，初始全文与向量各取 80 个候选，按证据片段去重后使用 RRF；k 常量先设 60，均为可调工程起点。前 20 个候选可由独立重排器或受约束模型重排，重排不得增补未召回的来源。')
d.code('rrf_score(item) = sum(1 / (60 + rank_channel(item)))\nallowed = source_set - exclusions\nresult = rerank(fuse(fts(query, allowed), vector(query, allowed)))\nreturn [x for x in result if source_policy_allows(x)]')
d.h('过滤 ANN 的正确处理')
d.p('过滤条件参与查询，但 ANN 索引扫描后仍可能因过滤不足而少返回结果。启用 pgvector iterative_scan；小型项目资料集优先精确距离扫描。复杂过滤下先测执行计划和召回，再决定候选预算或分区；不足时返回实际命中数，绝不放宽排除条件补足 Top K。[S01]')
d.p('整份排除按 source_id 持久生效，涵盖新抓取版本；仅排除某版时才使用 source_rev_id。允许版本与 span_refs 通过参数化查询和关系表执行。片段选择还须约束块、字符范围、asset 与 table_cell；跨界 chunk 先裁剪，重排与 EvidencePack 只读取允许片段，不能放行同源全文。图谱结果亦回查原始证据。')

d.page('13 向量索引代际与迁移')
d.p('模型名称、供应商、维度、归一化、截断策略、分块版本和输入语言策略共同定义索引代际。即使两模型维度相同，也不能混合比较。embedding_profile 在首次使用后不可原地修改，新的配置产生新代际。')
d.table(['步骤','实现规则'],[
('建立代际','创建新的物理向量表或分区；表固定维度、距离算子与索引配置。'),
('历史回填','按资料快照水位批量生成；记录成功数、输入哈希和失败项。'),
('追赶增量','逐项确认 outbox 的新增、更新和删除；安全水位按已确认连续进度推进，不使用最大 sequence。'),
('验证','检查覆盖率、维度、有限数值、抽样一致性、中文召回与过滤范围。'),
('原子切换','事务切换 active_generation 指针；请求在开始时固定代际。'),
('回退与清理','保留旧代际和兼容配置到回退窗口结束；无活跃任务引用后删除。'),
],[32,138])
d.h('维度与精度选择')
d.p('pgvector 的 vector 存储能力不等于 ANN 索引维度上限。当前 HNSW 的 vector 索引上限为 2000 维，halfvec 为 4000 维；0.8.0 起提供迭代扫描。[S01] 推荐优先评测 1024 或 1536 维多语种 embedding；具体模型由可用服务和中文语料效果决定。')
d.p('若用户选择 3072 维，先检查模型是否正式支持输出降维；否则验证 halfvec 索引并保留必要的全精度数据复排。超过支持范围时阻止创建不兼容索引并说明替代方案；不能无记录截断向量。')
d.h('检索后端的替换接口')
d.code('RetrievalAdapter.upsert(revision_batch, generation)\nRetrievalAdapter.delete(source_ids, generation)\nRetrievalAdapter.search(query, allowed_set, generation, k)\nRetrievalAdapter.validate_coverage(watermark)\nRetrievalAdapter.activate(generation)')
d.p('若引入 LanceDB，只把它作为可重建派生索引；PostgreSQL 仍保存事实、版本和引用。每条结果回查主库的有效版本与允许范围。切换整个主库到 SQLite 是独立决策，不应与替换排序引擎捆绑。')

d.page('14 项目规划与三类模板')
d.table(['对象','字段','处理规则'],[
('StructureTemplate','section_roles、hierarchy、required_parts、length_weights、constraints','定义组织结构，不携带事实权威。'),
('ExampleReference','source_ref、structure_analysis、paragraph_roles、analysis_revision','提取结构与论述顺序；默认不启用正文事实引用。'),
('OutputTemplate','format、style_tokens、reference_docx、css、csl_style、capabilities','定义输出外观与编号样式；导入后生成兼容报告。'),
('ProjectBrief','topic、audience、purpose、length、language、style、default_mode','初始以中文输出设计；语言字段保留扩展。'),
('OutlineRevision','node_id、parent、title、goal、word_budget、source_scope、mode','确认后成为生成输入；可局部重新生成。'),
],[35,80,55])
d.h('规划流程')
d.p('建立写作目标后，先提取模板或范文结构，再将章节任务与本地资料覆盖情况匹配。纲要条目显示拟用资料、预计篇幅与证据缺口。用户可调整层级、顺序、章节模式和资料范围，确认后冻结 outline_revision。')
d.p('自动选材、人工选材和推荐后确认共享 source_set_revision。默认采用哪一种属于界面设置，不把“每次必须确认资料”设为硬性要求。用户已确认纲要后，一次成文可连续执行后台流程，不反复申请操作许可。')
d.h('模板兼容策略')
d.p('首版结构模板采用受控 JSON schema，并支持从可读取文档中提取结构候选。输出模板采用受控 CSS、样式参数和已验证的 reference.docx。导入时解析可支持项、忽略项与风险，展示一次兼容报告；不能承诺任意外部 Word 模板原样复刻。')
d.p('结构模板、文风、证据策略之间有明确优先级：已确认纲要决定结构，项目文风决定表达，章节模式和证据范围约束事实。模板中的示例段落只作为范例，不能绕过证据规则进入严格章节。')

d.page('15 章节严格模式与证据包')
d.p('实际模式取最近一级章节覆盖，否则继承项目默认值。首次生成、润色、缩写、扩写、改写和评审调用同一个策略解析器。任务开始时固定 policy_revision、source_set_revision 与 base_revision。')
d.h('EvidencePack 最小结构')
d.code('{\n  "id":"ep1", "project_id":"p1", "section_id":"s1",\n  "source_set_revision":"set7", "effective_mode":"strict",\n  "policy_revision":"pol2", "content_hash":"...",\n  "items":[{"id":"ev1", "source_revision":"src3",\n    "parse_revision":"parse2", "block_id":"b9",\n    "original_excerpt":"...", "translated_excerpt":"...",\n    "translation_revision":"tr4", "anchor":{"start_cp":0},\n    "asset_ids":[], "table_cell_ids":[], "metadata":{}}]\n}')
d.table(['检查层','检查内容','失败处理'],[
('确定性结构','证据 ID 存在、属于资料范围、版本一致、引用片段可定位。','拒绝发布损坏引用；展示可修复错误。'),
('数值与引文','直接引文、数字、单位、日期、主体与来源的一致性。','标出对应句子，要求修改或补证据。'),
('语义支持','引用是否支持结论，是否扩大样本范围或把相关当因果。','支持、部分支持、冲突、证据不足。'),
('全章检查','无来源事实、来源冲突、上下文偷换与范文事实混入。','保留草稿，未解决项阻止“严格检查通过”状态。'),
],[29,82,59])
d.p('严格模式以引用、概括、比较和有依据的归纳为主。生成输出按事实陈述、归纳、归属观点和连接性文字标记，并为需要证据的陈述保存 claim_link。普通承接句无需机械附注；事实性或解释性结论必须有依据。')
d.p('JSON Schema 合规不代表内容正确，语义评审也可能出错。[S11] 因此“严格检查通过”表示已执行本方案检查且无未解决问题，不表示客观真理保证。人工文本仍可保存；用户可导出带未解决提示的草稿。')

d.page('16 数万字写作的编排与上下文')
d.p('“一次成文”定义为一次启动后自动完成多阶段工作流；它与“逐章编写”共用相同引擎。区别是章节之间是否等待用户继续，而不是是否在单个模型请求中塞入全书。')
d.code('outline_confirmed -> source_scope_frozen -> section_plan\n  -> evidence_pack -> context_pack -> candidate_generation\n  -> citation_checks -> section_review -> checkpoint\n  -> next_section -> whole_document_review -> export_ready')
d.table(['上下文部分','内容与约束'],[
('全书约定','目标读者、中心问题、主论点、文风、术语、统一数据口径。'),
('章节契约','本章任务、不能重复的内容、承接关系、目标篇幅与实际模式。'),
('证据部分','仅当前允许的 EvidencePack；保留必要原文而非只给摘要。'),
('相邻上下文','前文摘要、必要原段落、章节结论账本和未解决问题。'),
('编辑保护','当前 base_revision、锁定块 ID 和哈希、可修改范围。'),
],[34,136])
d.h('预算与执行策略')
d.p('使用模型支持的 tokenizer 或保守估算，预留输出和安全余量；汉字数不等于 token 数。上下文超限时先减少无关上下文和重复证据，不能截掉来源限定条件。超长章节按小节生成，最后进行衔接与整体审查。')
d.p('默认顺序生成有依赖的章节。只有明确独立的章节才允许并行，完成后统一核对术语、重复和结论。每次成功阶段保存输入与产物哈希，重启后从最近检查点恢复；已接受章节不因后续失败而重写。')
d.h('修改影响传播')
d.p('维护 dependency_ref、claim_ledger 和章节摘要版本。某章正文变更后，其摘要、评审、引用检查及依赖章节标为 stale；后台生成修订建议而不覆盖正文。修改全书术语或纲要时，展示受影响范围，允许分批处理。')
d.p('模型切换只替换 ProviderAdapter 和能力配置，继续使用相同的项目契约、证据包和版本模型。若新模型上下文或视觉能力不足，只限制相关任务，不静默改变资料范围或写作模式。')

d.page('17 编辑器锚点与修订应用')
d.p('采用 Tiptap OSS 与 ProseMirror JSON 保存正文，应用自建引用、素材、图表和 AI 修订扩展。协作、评论、转换等商业平台功能不作为首版依赖。[S06] 文章 JSON 与各节点均有 schema_version。')
d.table(['节点或标记','核心属性','行为'],[
('paragraph 与 heading','block_id、schema_version、locked','拆分产生新 ID；合并记录重定向，复制必须生成新 ID。'),
('citationMark','citation_id、evidence_ids、support_status','渲染时编号；存储中不把 [1] 当作永久标识。'),
('sourceFigure 与 sourceTable','asset_id、source_rev、caption、object_id','引用稳定素材；题注和交叉引用通过 object_id。'),
('generatedChart','chart_rev、dataset_rev、spec_hash、object_id','图表数据与规格可追溯。'),
('aiSupplement 与 reviewAnchor','origin、run_id；block_id、quote、context','区分补充内容，意见固定到被评审版本。'),
],[39,76,55])
d.h('AI 建议的接受流程')
d.p('选区请求带 section_revision、block_ids、selection_range、目标操作与锁定哈希。模型返回独立 edit_proposal。接受时后端再次检查章节版本、选区内容哈希、锁定范围、章节策略和证据包；失败返回 409 或 422，并保留建议供比较。')
d.p('同一版本步骤链中的位置变化可使用 ProseMirror Mapping。[S07] 跨任意历史快照不能直接套用偏移；使用 block_id、exact、prefix、suffix 重新定位，无法唯一定位则标记 orphaned 或 conflict。来源锚点使用 Unicode code point，编辑器 UTF-16 偏移在边界转换并测试 emoji。')
d.h('引用失效与自动保存')
d.p('正文修改后引用分为 valid、needs_review、orphaned、source_unavailable。句子还在并不表示语义仍被原来源支持，关键陈述变更必须重新审查。撤销是编辑事务，版本回退则创建指向历史内容的新版本，保留审计。')
d.p('本地工作副本可写入 IndexedDB 以恢复未同步内容，但服务器版本是长期事实源。自动保存使用 ETag 和 If-Match，多个标签页并发也按乐观锁处理，不能以“单人”假设没有写入冲突。')

d.page('18 互联网检索与核验')
d.p('SearchAdapter 与 VerificationService 分离。search_enabled 和 verification_enabled 为独立项目设置，建议默认关闭。互联网搜索用于发现候选资料；核验用于对已存在陈述寻找支持或冲突证据。模型服务可用不代表带互联网检索能力。')
d.table(['阶段','处理','边界'],[
('查询规划','从主题、章节缺口或待核验陈述生成检索词。','只发送必要查询，不发送整个知识库。'),
('候选检索','SearchAdapter 返回标题、URL、摘要、时间与提供方。','搜索摘要只是线索，不能直接成为严格证据。'),
('抓取与入集','通过标准采集流水线获取来源并保存快照。','按项目选材方式自动纳入或等待用户选择。'),
('证据对照','比对主体、时间、口径、数值和结论范围。','保留不同证据，重复转载不算独立支持。'),
('结果发布','保存 VerificationResult 及修改建议。','不直接覆盖正文，不把资料不足判为事实错误。'),
],[26,79,65])
d.h('核验结果契约')
d.code('{"claim_id":"c1", "section_revision":"r8",\n "status":"partially_supported", "checked_at":"UTC",\n "evidence_ids":["ev7"], "queries":["..."],\n "differences":[{"dimension":"time_scope","detail":"..."}],\n "limitations":["..."], "suggested_edit_id":"proposal9"}')
d.p('首个搜索提供方可选择有正式接口的 Brave Web Search，但必须验证用户网络可达性、中文召回和成本；提供方可替换，不写死为唯一平台。[S17] 严格章节只有在新增资料进入其固定允许集合并生成新证据包后才能引用。')
d.h('公开网页与可信模型端点分开管理')
d.p('网页抓取仅允许 HTTP(S) 公网目标，对 DNS 结果、每次重定向和子资源检查私网、回环、链路本地及元数据地址；浏览器补抓通过受控出站策略执行。用户主动配置的本地模型 URL 使用独立可信端点允许列表，不能被网页抓取规则误伤。[S16]')

d.page('19 模型网关能力与费用控制')
d.table(['接口','作用','关键校验'],[
('generate_text 与 stream_text','正文、翻译及流式候选生成。','完成原因、截断、拒绝、空结果。'),
('generate_structured','分类、抽取、纲要、评审。','JSON Schema 与 Pydantic；无效 ID 和多余字段拒收。'),
('describe_image','图片内容理解与说明。','模型视觉能力、输入大小、来源图像哈希。'),
('embed','分批生成文本向量。','维度、数量、有限数值、profile 指纹。'),
('probe_capabilities','文本、流式、结构化、视觉和向量分别探测。','记录 supported、unsupported、unknown 及时间。'),
],[43,65,62])
d.p('首个 ProviderAdapter 实现 OpenAI-compatible Chat Completions 与 Embeddings；这是一项成本可控的实现选择，不把“任意 Base URL 完全兼容”作为承诺。Responses、其他原生协议通过额外适配器加入。模型列表接口缺失时仍允许手工指定模型。[S11][S12]')
d.h('结构化输出与失败语义')
d.p('优先使用提供方正式支持的 JSON Schema；否则使用提示生成 JSON、本地校验和最多一次修复。认证错误或不支持参数应停止并提示，不能无限重试。结构校验与事实核验分开；视觉输入、最大上下文和费用不能从一次文本连接成功推断。')
d.p('重试只由网关一层负责，关闭 SDK 内层重复重试。使用指数退避、抖动、Retry-After 与任务总截止时间。提交后读取超时记为 unknown_outcome：服务端可能已完成并收费；重试建立新的 attempt，不能声称远端恰好执行一次。')
d.h('流式内容预算与密钥')
d.p('流式增量写入候选缓冲，不直接追加到当前正式章节。断线后按任务事件恢复，不重复拼接。调用前预留估算预算，返回 usage 后结算；缺失 usage 记为未知或估算，不记为零。价格由用户配置并保留生效日期与币种。')
d.p('密钥以本地主密钥加密，主密钥不进入数据库备份同包明文；备份恢复单独处理密钥材料。模型配置与任务记录只保存 secret_ref。自动切换提供方须符合该项目的数据出站策略，不能悄悄换到未允许的服务。')

d.page('20 图文素材与统一导出表示')
d.p('表格保留原始 HTML 和结构化 cell_grid；每个单元格记录原文本、可解析值、单位、行列跨度及脚注。图片保留本地原文件、来源、图注和相邻正文。视觉模型描述是派生内容，不能覆盖原图或原始说明。')
d.h('图表生成')
d.p('模型只生成受控的 Vega-Lite 规格与数据选择建议。应用验证字段存在、数据类型、单位、汇总口径和转换白名单，绑定冻结 dataset_revision，再用同一规格在前端和服务端渲染。流程和概念图可用受控 Mermaid 子集。')
d.p('Vega-Lite 允许通过 URL 读取数据，因此本产品必须主动禁止模型任意设置 data.url，并使用已解析的本地数据值或登记的数据集。[S13] 统计图的数值和汇总由确定性代码计算；示意图标明性质；首版不运行模型生成的任意 Python 或 JavaScript。')
d.h('ExportIR 契约')
d.code('{"schema_version":1,"document_revision":"dr9",\n "template_revision":"ot3","csl_style":"style-id",\n "blocks":[{"kind":"paragraph","id":"b1","inlines":[]},\n   {"kind":"table","object_id":"t1","cells":[],"caption":"..."}],\n "citations":[{"id":"ci1","evidence_id":"ev1",\n   "source_revision_id":"sr4","parse_revision_id":"pr2",\n   "translation_revision_id":null,"locator":"block:b2"}],\n "assets":[{"id":"a1","sha256":"...","mime":"image/png"}],\n "numbering_manifest":{},"source_manifest":{},"warnings":[]}')
d.h('编号与引用统一解析')
d.p('先遍历整篇 ExportIR 生成 NumberingManifest，再输出图表题注、正文交叉引用和文献编号。稳定 object_id 与 citation_id 不随顺序变化；编号由序列化器产生，不能由模型写死。全文只使用一次统一 CSL 处理，避免逐章编号冲突。')
d.p('网页引用记录标题、作者、URL、日期和可用定位；未提供页码的网页不编造页码。原文引文和译文引文保存不同表示版本。文献条目与证据片段分开，多个片段可以共享一条参考文献。')

d.page('21 输出模板与格式适配器')
d.table(['格式','主线路','必要验证'],[
('HTML','ExportIR → 受控 HTML 与 CSS → 本地资源包。','相对资源完整、目录锚点、引用跳转、无外链脚本依赖。'),
('PDF','同源 HTML 与 CSS → 本地 Chromium 与 Playwright。','中文字体、打印样式、分页、长表格、页眉页脚和目录。'),
('Word','ExportIR → Pandoc AST 与 CSL → reference.docx → 有限 OOXML 后处理。','合并表格、样式、题注、书签、交叉引用、中文字体。'),
],[22,88,60])
d.p('Pandoc 的 reference.docx 使用样式与文档属性，并忽略其正文。因此封面内容、章节结构和占位符填充必须来自本项目模板模型，不能承诺任意 Word 范文直接变成版式模板。[S08] 表格从含 rowspan 和 colspan 的 IR 转换为 AST，不能经 Markdown 管道丢失结构。')
d.h('目录与页码')
d.p('HTML 目录采用可点击锚点。DOCX 生成目录域并提供可点击的静态目录兜底；有页码目录需要真实布局引擎更新。PDF 若要求目录页码，使用受控分页方案或双遍排版，并在 PoC 中验证；Playwright 的 page.pdf 本身不自动解决目录页码。[S08][S09]')
d.p('产品 DOCX 页码刷新优先使用已验证的本地 LibreOffice Worker，显式刷新字段与目录索引后保存，检查最终缓存页码；转换成功本身不证明目录已刷新。本方案文档 QA 可用本机 Word，但不能据此推断容器排版相同。')
d.h('DOCX 后备路线')
d.p('先以包含合并单元格、图片、题注、引用和中文目录的样稿验证 Pandoc。如果必需模板属性仍无法正确产出，且 OOXML 补丁已接近重建主体结构，再替换为 python-docx writer；其脚注、域和复杂编号也需自建和测试，不能视为无成本替代。ExportIR、CSL 与其余格式适配器保留。')
d.h('导出清单与错误处理')
d.p('每个导出产物记录文章版本、模板版本、字体、渲染器版本、全部素材哈希、警告及文件哈希。输出失败保留文章和输入快照；修复素材后重试同一导出任务。候选版本或未解决引用的导出状态须显式显示。')

d.page('22 HTTP 接口设计')
d.p('接口统一使用 /api/v1 前缀、UUID 标识和 ISO 8601 UTC 时间。列表使用游标分页；命令以 Idempotency-Key 去重。长任务返回 202 与 job_id，查询返回 200，更新冲突返回 409，契约错误返回 422。')
d.table(['方法与路径','请求关键字段','结果或动作'],[
('POST /imports/files','multipart files、options、batch_id','创建导入批次与任务。'),
('POST /imports/urls','urls、fetch_policy、options','公开网页采集任务。'),
('GET /documents','filters、cursor、page_size','元数据、阶段状态、下一游标。'),
('GET /documents/{id}/revisions/{rev}','representation、include_assets','固定原文、译文、素材与知识。'),
('POST /search','query、filters、source_set_rev、k','带原文锚点的结果与检索配置版本。'),
('POST /categories/{id}/merge','target_id、base_revision','合并影响预览或原子提交。'),
('GET /graph/subgraph','node_id、hops、filters、limit','有证据的局部节点与边。'),
('PATCH /wiki/{id}','base_revision、block_patch','新 Wiki 版本或冲突。'),
('POST /projects','brief、mode、style、template_refs','写作项目。'),
('PUT /projects/{id}/source-set','base_revision、members、policy','新参考资料集合版本。'),
('POST /projects/{id}/outline','source_set_rev、template_rev','纲要生成任务。'),
('POST /outlines/{rev}/confirm','base_project_revision','确认纲要及固定结构。'),
('PATCH /sections/{id}/policy','base_revision、mode、source_scope','新章节策略，旧正文触发检查。'),
('POST /sections/{id}/generate','base_rev、outline_rev、policy_rev','写作任务与候选版本。'),
('POST /projects/{id}/generate','outline_rev、source_set_rev、policy_refs、execution_mode','连续成文或逐章暂停。'),
('PUT /sections/{id}/draft','If-Match、base_rev、editor_json','人工保存；返回新修订或冲突。'),
('POST /sections/{id}/edit-proposals','selection、operation、instruction','独立 AI 修订建议。'),
('POST /edit-proposals/{id}/accept','If-Match、expected_hashes','事务校验后创建正文新版本。'),
('POST /reviews 与 /verifications','target_revision、scope、dimensions','意见或核验任务。'),
('POST /exports','document_rev、format、template_rev','导出任务与可下载产物。'),
],[69,55,46],cell_pad=60)
d.p('资料删除、恢复、任务取消、模型探测和备份接口遵循相同约定。所有范围和版本均由后端验证；界面隐藏按钮不是约束的唯一实现。文件下载通过受控 ID 映射，不接受任意本地路径。')

d.page('23 任务状态租约与事件接口')
d.h('任务状态与领取')
d.code('queued -> running -> succeeded\n                 -> retry_wait -> queued\n                 -> failed | cancelled | superseded\nPOST /jobs/{id}/cancel\nGET  /jobs/{id}\nGET  /events?after_seq=123')
d.p('以 SELECT FOR UPDATE SKIP LOCKED 在短事务内领取任务，设置 owner、lease_until 并递增 fence。[S03] 续租和结果提交均以 job_id、owner、fence、running、lease_until > 数据库时钟、cancel=false 作 CAS；过期不可续租复活，零行更新必须停止发布。')
d.table(['机制','初始设计参数','规则'],[
('租约与心跳','租约 120 秒，心跳 30 秒','长模型请求期间仍续租；数值为可调初始值。'),
('过期回收','定时检查 lease_until','重新领取递增 fence；旧 Worker 无权发布结果。'),
('幂等提交','阶段与输入指纹唯一','结果、完成状态和 outbox 事件同事务提交。'),
('取消','cancel_requested 标记','停止后续步骤；提交前复查；外部请求可能已收费。'),
('重试','按错误类型与总时间预算','配置错误立即失败；暂态错误有界退避。'),
('通知','持久 event_seq 与 SSE','断线按序恢复；事件过期时重新查询对象快照。'),
],[29,56,85])
d.h('事件数据与交互恢复')
d.code('{"seq":124,"job_id":"j1","type":"generation.delta",\n "object_id":"s1","revision":"candidate7",\n "payload":{"offset":120,"text":"..."},"created_at":"UTC"}')
d.p('业务事务写 outbox。单序发布器持锁读取已提交且未发布项，在同一事务分配 event_seq、写 event_log 并确认 outbox；发布器事务串行，不能以普通 sequence 推断提交顺序。索引消费者逐项确认，安全水位不可跨越未完成项。')
d.p('前端以 seq 和候选 revision 去重；SSE 不是任务队列。增量合并后持久化，重启读取检查点及后续事件。事件过期则取带游标的一致对象快照再续订；游标须对应完整处理的业务变更，不能跳过尚未发布的 outbox 项。')
d.p('API 与 Worker 默认共享应用代码而独立进程；网络调用用受控异步并发，图像和导出等重任务使用有限子进程。每个 SQLAlchemy AsyncSession 只服务一个任务或事务，不跨并发协程共享。[S19]')

d.page('24 前端页面与状态组织')
d.table(['路由或工作区','核心组件','交互重点'],[
('/library','检索栏、筛选器、列表或卡片、批量任务','组合条件可保存；数据量大时游标分页或虚拟列表。'),
('/documents/:id','原译对照、素材、知识条目、来源侧栏','点击证据定位原段落；缺失素材和加工状态可见。'),
('/knowledge','图谱、Wiki、归并与冲突视图','按需展开小子图，区分来源主张与核验状态。'),
('/projects/:id','主题、选材、模板、纲要与章节状态','模式标记可见，证据不足在写作前提示。'),
('/write/:id','左目录、中编辑器、右参考与评审','面板可折叠；选区工具栏；引用点击不丢光标。'),
('/settings 与 /tasks','模型、搜索、存储、任务与费用','能力分项探测、阶段失败原因、取消和恢复。'),
],[39,71,60])
d.h('状态边界')
d.p('TanStack Query 管理服务器对象、分页缓存和失效；Zustand 管理面板、选区和本地工作台偏好；ProseMirror 管理编辑事务。避免把整篇正文同时复制到多个全局状态仓库。SSE 更新对应查询和候选缓冲，不直接绕过编辑器应用正文。')
d.h('设计系统与可访问性')
d.p('以颜色、字体、间距、圆角和阴影令牌统一深浅主题。正文是视觉中心，资料和评审面板保留宽度调节。关键操作有键盘入口、焦点反馈和明确状态；严格模式、引用失效和失败状态同时使用文字与图标，不只依赖颜色。')
d.h('页面反馈')
d.p('资料解析失败与向量未就绪分别提示，部分可用内容可先阅读。生成、重写和核验显示目标章节与资料范围。接受建议前展示差异，产生冲突时保留双方内容。长任务不阻塞搜索、阅读和手动编辑；批处理可暂停派发新任务。')
d.h('前后端契约维护')
d.p('由后端 Pydantic/OpenAPI 生成 TypeScript DTO，编辑器节点另有独立 JSON Schema。契约变更必须提供 schema_version 和迁移策略；服务端再次校验客户端数据，不能把 TypeScript 类型检查当作运行时验证。')

d.page('25 本地部署安全与备份')
d.p('推荐 Compose 运行 web、api、worker、postgres 四个服务。web 只绑定 127.0.0.1:8787，反向代理 API 与 SSE；数据库不发布宿主机端口。API 与 Worker 共用应用镜像，镜像包含验证过的导出工具和中文字体，全部运行于本地。')
d.p('Windows 采用 WSL 2 支持的 Linux 容器；Docker Desktop 的安装与资源要求需要在实际设备确认。[S21] 本次没有验证该设备的 Docker 可用性或硬件容量。若需便携安装，采用前述 SQLite 与 LanceDB 路线重新评估，不能假设容器运行时已经存在。')
d.h('访问与内容安全')
d.p('本机使用启用随机会话秘密、HttpOnly 和 SameSite Cookie、Origin/Host 校验和受控 CORS。原始 HTML 只作为存档，阅读视图经过清洗或隔离沙箱；脚本、表单和外链执行受限。上传和归档解包校验路径、数量与解压后体积，避免越出本地数据目录。')
d.p('模型看到的网页内容是资料，不是系统指令。工具权限、模型 endpoint、文件写入位置和资料范围由程序控制，网页正文不能触发密钥读取或任意请求。图表和输出模板使用受控配置，禁止来自资料的任意脚本执行。')
d.h('一致备份与恢复')
d.p('采用短维护窗口：关闭全局写入闸门，阻止 API、已有 Worker 和发布器提交，并暂停文件清理；等待已进入事务结束。冻结期间执行 pg_dump 并导出 blob 清单，复制不可变文件后生成 manifest 与哈希，再开放闸门。写入事务均须持闸门共享锁，备份持独占锁；不能只停止新任务。[S20]')
d.p('恢复到新目录和数据库，校验 schema、文件哈希、引用与索引代际，抽样阅读和导出后切换。全部 running 租约失效、递增 fence，再按幂等结果安全重排；远程未明结果不假定未收费。密钥单独恢复。可重建索引允许低频备份，但须保存完整重建配置。')
d.h('升级')
d.p('升级前备份，单实例执行 Alembic 迁移，应用与 Worker 检查 schema 兼容后启动。破坏性迁移采用扩展、回填、验证、切换、清理步骤；失败通过备份恢复，不假设所有数据库迁移都可直接降级。')

d.page('26 容量预算与性能目标')
d.p('以下是规划假设和建议目标，不是实际设备测试结果。参考环境为 4 至 8 个 CPU 核、16 GB 内存起步、32 GB 更适合同时批处理与导出、至少 100 GB 可用 SSD；模型推理主要走外部接口，不把本地 GPU 设为必需条件。')
d.table(['项','假设与计算','解释'],[
('网页与检索块','10000 网页 × 20 块 = 200000 块','翻译、图片说明和版本增长另计。'),
('1024 维向量','200000 × (4 × 1024 + 8) = 0.821 GB','约 0.764 GiB，仅 vector 载荷，不含行与索引。'),
('1536 维向量','200000 × (4 × 1536 + 8) = 1.230 GB','HNSW、GIN、WAL 和副本占用另测。'),
('原始 HTML','每页 0.3 MB，约 3 GB','网页体积假设，真实网页须抽样。'),
('图片','每页 3 张 × 0.25 MB，约 7.5 GB','原图大、版本多时会显著增加。'),
('磁盘余量','同时保留活动索引、重建代际与备份','100 GB 是参考预算，不是容量保证。'),
],[31,81,58])
d.h('建议验收目标')
d.table(['指标','初始目标','测量边界'],[
('筛选与本地检索','热态 p95 分别不高于 0.8 秒和 2 秒','固定 20 万块；排除远程 embedding 与重排。'),
('完整语义查询','分解并记录本地、embedding、重排耗时','外部服务延迟单独报告，不伪装成本地指标。'),
('写作交互','编辑不中断；自动保存可恢复','页面交互与服务器保存分别测量。'),
('检索质量','标注集 Recall@20 ≥ 0.85 为初始目标','至少 100 条中文问题，含范围过滤与不应命中项。'),
('范围隔离','所有测试中越界来源数为 0','章节排除、旧版本、删除来源和交叉章节污染。'),
],[34,68,68])
d.p('批量吞吐、单篇加工时延、每次查询延迟和故障恢复分别记录。抽取与写作质量需人工标注样本，模拟向量只能测吞吐和索引行为，不能证明语义召回。目标不达标时优先定位瓶颈，再按第 2 节替代方案调整。')

d.page('27 实施顺序与技术验证')
d.table(['阶段','内容','完成条件'],[
('P0 技术验证','中文检索对照；HTML 表格图像；编辑锚点；DOCX/PDF 模板；模型能力探测。','保留版本、样本和结果；据结果锁定依赖与必要替代。'),
('P1 基础闭环','项目结构、数据库与文件版本、任务、模型设置、导入解析、阅读。','失败重试和恢复可用；原文和素材可定位。'),
('P2 知识与检索','翻译摘要、自动分类、知识抽取、Wiki、图谱、全文向量检索。','覆盖中文查询、人工修正保留和索引代际切换。'),
('P3 写作与编辑','参考集合、三类模板、纲要、章节模式、逐章生成与人工编辑。','严格模式无越界，过期 AI 建议不覆盖正文。'),
('P4 输出与评审','图表、引用、评审、联网检索核验、三格式导出。','来源可追溯；图表编号、目录和中文排版验证。'),
('P5 首版验收','万级数据、数万字文章、故障恢复、备份迁移与安装。','逐项完成需求 A01 至 A20，记录遗留问题。'),
],[27,82,61])
d.p('阶段是实施顺序，不是删减范围。图谱、Wiki、模板、图文引用、逐章创作、评审和三格式导出均保留在完整首版目标中。工期需在 P0 结果和实际开发资源明确后估算。')
d.h('必须先验证的难点')
d.p('检索：相同 10000 网页与 embedding，对比 PG 与 SQLite/LanceDB，包含高选择性过滤；回收后结果重新校验来源。编辑：含中文、emoji、跨段引用、复制拆并、AI 生成期间人工修改与恢复。')
d.p('导出：一份包含长合并表格、图片、统计图、网页参考文献、目录和跨章编号的样稿，实际生成并逐页查看 PDF 与 DOCX。写作：三万字样稿在第 3 章中断后恢复，修改前文再检查影响传播。')
d.p('恢复：Worker 崩溃、租约过期、旧 Worker 迟到、远程超时、文件提交中断、索引切换中断、数据库恢复。每项保留日志、输入哈希和断言，不能只展示一次正常运行截图。')

d.page('28 需求覆盖与风险决策')
d.table(['需求编号','对应设计章节','关键验收'],[
('F01 至 F06','5、6、9、10、11','采集解析、原译对照、版本与重处理。'),
('F07 至 F11','7、11','自动分类、人工优先、图谱及 Wiki 证据。'),
('F12 至 F15','6、12、13、14','混合检索、组合筛选、范围与选材。'),
('F16 至 F21','8、14、15、17','模板纲要、章节模式、引用稳定。'),
('F22 至 F27','8、16、17、23','长文编排、人工编辑、评审和恢复。'),
('F28 至 F33','10、20、21','图表数据血缘、模板与三格式导出。'),
('F34 至 F39','18、19、25','可选联网、多模型能力、费用与密钥。'),
('F40 与 N01 至 N04','23、24、25、26、27','页面流畅、万级性能、备份与运行记录。'),
],[31,46,93])
d.h('风险与决策出口')
d.table(['风险','检测方式','调整方向'],[
('中文全文排序不足','同语料标注集与失败查询分析。','调整词典、融合、重排；必要时换 RetrievalAdapter。'),
('本地安装维护过重','真实 Windows 安装、启动与恢复演练。','优化打包；若便携优先，改 SQLite 与 LanceDB。'),
('模型兼容或质量不足','分任务能力探测与中文任务样本。','增加适配器、替换任务模型；保留证据规则。'),
('导出模板不达标','复杂样稿的实际布局与引用验证。','限定支持集、改 writer；保留 ExportIR。'),
('成本与耗时失控','每阶段调用、token、重试和等待分解。','合并批次、缓存、降低无效抽取；用户设置预算。'),
],[37,66,67])
d.p('下一步交付物应是通过 P0 的版本锁定清单、可执行数据库迁移、OpenAPI 契约、编辑器节点 Schema、检索评测集和导出模板样稿。选择是否调整技术路线应引用这些验证证据，而不是沿用工作区参考文档中的结论。')

sources=[
('S01','pgvector 官方仓库','https://github.com/pgvector/pgvector','向量类型、索引维度、过滤、迭代扫描及存储计算。'),
('S02','PostgreSQL 全文检索控制','https://www.postgresql.org/docs/18/textsearch-controls.html','词元、权重和 ts_rank 系列排序语义。'),
('S03','PostgreSQL SELECT 锁定','https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE','SKIP LOCKED 在队列表中的适用边界。'),
('S04','LanceDB 全文检索','https://docs.lancedb.com/search/full-text-search','BM25、中文分词及全文检索能力。'),
('S05','SQLite WAL','https://www.sqlite.org/wal.html','读写并行、单写入者和运行约束。'),
('S06','Tiptap 开源与平台说明','https://tiptap.dev/open-source','开源编辑器与商业平台能力边界。'),
('S07','ProseMirror 参考手册','https://prosemirror.net/docs/ref/','Schema、事务、Step 和 Mapping。'),
('S08','Pandoc 用户手册','https://pandoc.org/MANUAL.html','DOCX reference、CSL、目录与输出选项。'),
('S09','Playwright PDF 接口','https://playwright.dev/docs/api/class-page#page-pdf','打印 CSS、页眉页脚和 PDF 输出能力。'),
('S10','Trafilatura 核心接口','https://trafilatura.readthedocs.io/en/latest/corefunctions.html','正文、元数据、图片表格与输出选项。'),
('S11','OpenAI Structured Outputs','https://developers.openai.com/api/docs/guides/structured-outputs','结构化输出、拒绝和内容正确性的边界。'),
('S12','OpenAI SDK 文档','https://developers.openai.com/api/docs/libraries','官方 SDK；第三方兼容性仍需独立验证。'),
('S13','Vega Lite 数据定义','https://vega.github.io/vega-lite/docs/data.html','内联数据、URL 数据及命名数据集。'),
('S14','Cytoscape 官方文档','https://js.cytoscape.org/','图模型、布局、事件和交互接口。'),
('S15','lxml HTML','https://lxml.de/lxmlhtml.html','HTML 结构和链接处理能力。'),
('S16','OWASP SSRF 防护','https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html','URL、重定向和网络边界设计依据。'),
('S17','Brave Web Search API','https://api-dashboard.search.brave.com/app/documentation/web-search/get-started','搜索接口与返回字段；中文效果待测。'),
('S18','FastAPI 容器部署','https://fastapi.tiangolo.com/deployment/docker/','容器进程、资源和初始化步骤。'),
('S19','SQLAlchemy 异步 ORM','https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html','AsyncSession 与并发任务边界。'),
('S20','PostgreSQL 备份与恢复','https://www.postgresql.org/docs/18/backup.html','逻辑备份、文件备份及连续归档的区别。'),
('S21','Docker Desktop Windows 安装','https://docs.docker.com/desktop/setup/install/windows-install/','WSL 2 与 Windows 运行前提。'),
('S22','Node.js 版本周期','https://nodejs.org/en/about/previous-releases','Node 24 LTS 版本线。'),
('S23','Python 版本状态','https://devguide.python.org/versions/','3.14 和其他版本的支持阶段。'),
('S24','PostgreSQL 18 发布说明','https://www.postgresql.org/docs/18/release-18.html','稳定版本基线和大版本迁移要求。'),
('S25','jieba 官方仓库','https://github.com/fxsjy/jieba','精确和搜索模式、自定义词典。'),
('S26','Pandoc 合并单元格支持记录','https://github.com/jgm/pandoc/issues/4672','作者确认行跨度与列跨度支持，须锁定版本实测。'),
('S27','python docx 能力说明','https://python-docx.readthedocs.io/en/latest/user/comments.html','脚注和尾注的当前支持边界。'),
]
for i in range(3):
    d.page(f'{29+i} 技术来源与核查记录')
    if i==0:d.p('以下官方文档和项目维护者资料于 2026年10月21日核查。来源支撑能力与限制；本方案的组合、参数、默认值和取舍属于工程判断，未代替真实运行测试。')
    for s in sources[i*9:(i+1)*9]:d.source(*s)
    if i==2:
        d.p('需求对照参考：工作区《知识库与写作软件 首版产品需求规格 V1.0》用于需求编号映射；《技术架构.pdf》和需求梳理稿用于方案参考。用户已明确这些文件仅供参考，技术决策以本方案的独立比较与后续验证为准。')
        d.p('本次交付为详细设计文档。系统实现、数据库迁移、模型连接、部署和性能验收均尚未执行。')

d.save()
