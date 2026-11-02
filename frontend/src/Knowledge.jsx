import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, ArrowUpRight, BookOpen, CaretDown, CaretRight, Check, FileText, Graph, Link, MagnifyingGlass, PencilSimple, Quotes, Stack, X } from '@phosphor-icons/react';
import { initialDocuments } from './data';
import { DEFAULT_STRUCTURE_TEMPLATE, STRUCTURE_REQUIREMENTS_HINT, structureTemplates } from './writing-presets';
import { usePromptLibrary } from './usePromptLibrary';
import { promptDraftChanged } from './prompt-library-state';
import { TemplatePromptEditor } from './TemplatePromptEditor';
import { registerPromptDraftReceiver } from './prompt-draft-transfer';
import './knowledge.css';
import './template-library.css';

// In-memory only: switching the main navigation must not discard an editing session.
const knowledgeSession = { type: '概念', query: '', selected: 'knowledge-work', view: 'wiki', editing: false, draft: '', savedText: {}, expanded: false };
const templateSession = { type: '结构模板', selectedId: DEFAULT_STRUCTURE_TEMPLATE, query: '', favorites: [] };
function useSessionValue(session, key) {
  const [value, setValue] = useState(() => session[key]);
  const updateValue = next => {
    const resolved = typeof next === 'function' ? next(session[key]) : next;
    session[key] = resolved;
    setValue(resolved);
  };
  return [value, updateValue];
}

const knowledgeEntries = [
  { id: 'knowledge-work', type: '概念', name: '知识工作', subtitle: '以判断、创造与知识应用为核心', aliases: ['Knowledge work', '知识劳动'], intro: '知识工作是以信息、专业知识和判断为主要投入，通过理解问题、形成观点与创造新知识来产生价值的工作。它的成果往往难以用单一的数量指标衡量。', paragraphs: ['与重复性任务相比，知识工作更依赖情境理解和经验判断。信息检索、分析、写作与决策并非孤立环节，而是一个不断迭代的过程。', '生成式 AI 正在改变其中部分任务的完成方式。值得关注的不是单项任务是否被自动化，而是人在问题定义、证据判断和最终责任中的角色如何变化。'], relations: [{ id: 'ai', label: '正在被重塑', note: '生成式 AI 改变信息处理与内容生产方式' }, { id: 'collaboration', label: '实践方式', note: '人负责判断，工具辅助探索与表达' }, { id: 'knowledge-management', label: '基础支持', note: '把零散信息积累为可复用的知识' }], quote: '知识的价值，体现在被理解、连接和再次使用的过程里。', sourceIds: ['d1', 'd2', 'd3'] },
  { id: 'ai', type: '概念', name: '生成式 AI', subtitle: '从模式学习到内容生成', aliases: ['Generative AI', '生成式人工智能'], intro: '生成式 AI 是能够依据输入与学到的模式生成文本、图像或其他内容的一类人工智能系统。在知识工作中，它常被用于探索思路、整理信息和辅助表达。', paragraphs: ['生成内容的流畅性不等同于可靠性。尤其在涉及事实、数字和引用时，仍需回到可核验的资料。', '合理的协作流程通常保留人的问题定义、来源选择和最终审阅，把模型作为可调整、可追溯的辅助工具。'], relations: [{ id: 'knowledge-work', label: '应用领域', note: '辅助信息整理、分析与写作' }, { id: 'collaboration', label: '协作机制', note: '在人类判断与模型生成之间分工' }], quote: '流畅的表达，需要可靠证据作为支撑。', sourceIds: ['d1', 'd3'] },
  { id: 'knowledge-management', type: '概念', name: '知识管理', subtitle: '让信息能够被再次理解和使用', aliases: ['Knowledge management', '个人知识库'], intro: '知识管理是对信息进行收集、整理、关联与应用的持续过程。对于个人而言，其目标是让过去获得的知识能够参与当前的思考。', paragraphs: ['收藏只是起点。分类、摘要、概念连接和来源记录，让资料从保存下来的页面转变成可以检索和引用的知识。', '有效的知识体系需要允许调整：新资料可能改变既有分类，也可能让原本分散的观点产生新的联系。'], relations: [{ id: 'knowledge-work', label: '服务于', note: '为判断和表达积累材料' }, { id: 'evidence', label: '使用场景', note: '从知识库检索可追溯的写作证据' }], quote: '收藏是起点，理解与应用才让知识持续生长。', sourceIds: ['d2', 'd3'] },
  { id: 'collaboration', type: '概念', name: '人机协作', subtitle: '让人的判断与工具能力相互补充', aliases: ['Human–AI collaboration'], intro: '人机协作是人与智能工具共同完成任务的过程。协作质量取决于任务如何分配、过程能否被检查，以及最终决策是否保留清晰的责任。', paragraphs: ['人在目标制定、价值判断和复杂情境理解中提供方向；工具可以辅助搜索、归纳与草稿生成。', '协作不意味着交出判断。清楚标记证据、推断与尚待核实的信息，有助于维持对成果的掌控。'], relations: [{ id: 'ai', label: '技术基础', note: '生成式模型提供探索与表达能力' }, { id: 'knowledge-work', label: '应用领域', note: '写作与分析中的具体协作流程' }], quote: '好的工具让判断更清楚，也让创造更从容。', sourceIds: ['d1', 'd3'] },
  { id: 'frontier-source', type: '实体', name: initialDocuments.find(d => d.id === 'd1').source, subtitle: '知识来源 · 示例实体', aliases: ['示例来源'], intro: '知识库中出现的示例来源实体。此页面汇集与该来源关联的资料、主题和引用，帮助追踪观点的来源。', paragraphs: ['当前示例资料涉及生成式 AI 与知识工作。来源名称用于溯源，不代表系统对其所有观点作出认可。'], relations: [{ id: 'ai', label: '讨论主题', note: '生成式 AI 的应用与影响' }, { id: 'knowledge-work', label: '讨论主题', note: '工作任务与专业判断的变化' }], quote: '查看同一来源的相关材料，有助于理解观点的语境。', sourceIds: ['d1'] },
  { id: 'notes-source', type: '实体', name: initialDocuments.find(d => d.id === 'd2').source, subtitle: '知识来源 · 示例实体', aliases: ['示例来源'], intro: '知识库中的示例来源实体。关联材料讨论个人知识管理、信息组织与内容复用。', paragraphs: ['实体页将不同资料中出现的同一来源统一关联，同时保留每条材料的原始来源与上下文。'], relations: [{ id: 'knowledge-management', label: '相关主题', note: '信息组织与个人知识体系' }], quote: '用统一的实体连接资料，同时保留每个来源的边界。', sourceIds: ['d2'] },
  { id: 'value', type: '观点', name: '知识价值来自连接与应用', subtitle: '归纳观点 · 仍需结合具体情境', aliases: ['知识复用', '观点归纳'], intro: '相比单纯增加收藏量，把信息进行连接并用于实际问题，更能体现个人知识库的价值。这是一条基于示例材料归纳的观点，并非对所有情境成立的客观定律。', paragraphs: ['相关材料强调分类、回顾与持续应用的重要性。写作时可将该观点作为论证起点，再补充具体证据与适用条件。'], relations: [{ id: 'knowledge-management', label: '归属概念', note: '知识的整理、连接与使用' }, { id: 'knowledge-work', label: '应用场景', note: '在工作中把资料转化为判断' }], quote: '把信息放进一个问题里，它才开始参与思考。', sourceIds: ['d2', 'd3'] },
  { id: 'judgment', type: '观点', name: 'AI 辅助需要保留人的判断', subtitle: '归纳观点 · 人机协作', aliases: ['人工审阅', '责任边界'], intro: '模型可以协助生成与归纳，但事实核查、价值判断和最终责任仍需要明确的人类参与。这是一条当前示例内容的归纳观点。', paragraphs: ['该观点适用于有准确性要求的知识写作。不同任务的风险与要求并不相同，人工介入的程度也应随情境调整。'], relations: [{ id: 'collaboration', label: '归属概念', note: '任务分工与责任界定' }, { id: 'ai', label: '讨论对象', note: '生成内容的准确性与可验证性' }], quote: '让工具参与创作，让判断保持清晰。', sourceIds: ['d1', 'd3'] },
  { id: 'evidence', type: '素材', name: '证据、推理与表达的写作框架', subtitle: '方法素材 · 可用于文章结构', aliases: ['论证框架', '写作方法'], intro: '将一段论证拆解为证据、推理和表达三个层次：首先说明依据，其次解释依据如何支持结论，最后用清晰的语言表达观点。', paragraphs: ['这份素材适合用于文章提纲、章节检查或写作评审。使用时需回到对应资料确认语境，不应将整理后的框架误认为原文直接引用。'], relations: [{ id: 'knowledge-work', label: '应用场景', note: '分析型文章与知识表达' }, { id: 'judgment', label: '支持观点', note: '区分证据与推断，保留审阅环节' }], quote: '先找到依据，再说清楚它为什么支持结论。', sourceIds: ['d3'] },
  { id: 'workflow', type: '素材', name: '从资料到文章的五步流程', subtitle: '流程素材 · 收集到复用', aliases: ['写作流程', '资料整理'], intro: '围绕一个明确的问题，依次完成资料收集、内容整理、主题连接、文章表达与知识回流。这是一份帮助组织材料与思路的示例流程。', paragraphs: ['流程的关键是允许返回：写作发现的证据缺口可以重新触发检索，新形成的认识也可以更新知识库中的概念说明。'], relations: [{ id: 'knowledge-management', label: '归属概念', note: '让知识在整理与使用之间循环' }, { id: 'evidence', label: '相关方法', note: '论证阶段检查证据与推理' }], quote: '文章的完成，也可以成为下一轮思考的起点。', sourceIds: ['d2', 'd3'] },
];

const knowledgeTypes = ['概念', '实体', '观点', '素材'];

export function KnowledgeWorkspace({ onToast = () => {}, onOpenDoc = () => {} }) {
  const [type, setType] = useSessionValue(knowledgeSession, 'type');
  const [query, setQuery] = useSessionValue(knowledgeSession, 'query');
  const [selected, setSelected] = useSessionValue(knowledgeSession, 'selected');
  const [view, setView] = useSessionValue(knowledgeSession, 'view');
  const [editing, setEditing] = useSessionValue(knowledgeSession, 'editing');
  const [draft, setDraft] = useSessionValue(knowledgeSession, 'draft');
  const [savedText, setSavedText] = useSessionValue(knowledgeSession, 'savedText');
  const [expanded, setExpanded] = useSessionValue(knowledgeSession, 'expanded');
  const entries = useMemo(() => knowledgeEntries.filter(e => e.type === type && `${e.name} ${e.subtitle} ${e.aliases.join(' ')}`.toLowerCase().includes(query.toLowerCase())), [type, query]);
  const current = knowledgeEntries.find(e => e.id === selected) || knowledgeEntries[0];
  const switchEntry = entry => { setSelected(entry.id); setType(entry.type); setEditing(false); setExpanded(false); };
  const changeType = next => { setType(next); setQuery(''); switchEntry(knowledgeEntries.find(e => e.type === next)); };
  const content = savedText[current.id] ?? [current.intro, ...current.paragraphs].join('\n\n');
  const relations = current.relations.map(r => ({ ...r, node: knowledgeEntries.find(e => e.id === r.id) }));
  const secondary = useMemo(() => {
    const direct = new Set([current.id, ...current.relations.map(r => r.id)]);
    const found = [];
    current.relations.forEach(relation => {
      const node = knowledgeEntries.find(e => e.id === relation.id);
      node?.relations.forEach(r => { if (!direct.has(r.id) && !found.some(e => e.id === r.id)) found.push({ ...r, through: node.name, node: knowledgeEntries.find(e => e.id === r.id) }); });
    });
    return found;
  }, [current]);
  return <section className="knowledge-workspace">
    <header className="knowledge-heading">
      <div><h1>让知识，彼此相连</h1><p>沿着一个概念，找到相关的观点与依据。</p></div>
      <span className="knowledge-sample"><Stack size={15} /> 示例知识网络</span>
    </header>
    <div className="knowledge-type-tabs" role="tablist" aria-label="知识类型">
      {knowledgeTypes.map(t => <button type="button" role="tab" aria-selected={type === t} key={t} className={type === t ? 'active' : ''} onClick={() => changeType(t)}>{t}<span>{knowledgeEntries.filter(e => e.type === t).length}</span></button>)}
    </div>
    <div className="knowledge-layout">
      <aside className="knowledge-index" aria-label={`${type}索引`}>
        <label className="knowledge-search"><MagnifyingGlass size={18} /><input placeholder={`搜索${type}`} aria-label={`搜索${type}`} value={query} onChange={e => setQuery(e.target.value)} />{query && <button className="icon-btn" aria-label="清空搜索" onClick={() => setQuery('')}><X size={14} /></button>}</label>
        <div className="knowledge-index-caption">{query ? `找到 ${entries.length} 项` : `全部${type}`}</div>
        <div className="knowledge-entry-list">
          {entries.map(entry => <button className={`knowledge-entry ${selected === entry.id ? 'selected' : ''}`} key={entry.id} onClick={() => switchEntry(entry)}><span><strong>{entry.name}</strong><small>{entry.sourceIds.length} 篇关联资料</small></span><CaretRight size={15} /></button>)}
          {entries.length === 0 && <div className="knowledge-empty"><MagnifyingGlass size={24} /><p>还没有匹配的{type}</p><button className="btn quiet" onClick={() => setQuery('')}>清除搜索</button></div>}
        </div>
        <p className="knowledge-index-note">自动归纳，持续生长。<br />你可以随时调整内容。</p>
      </aside>
      <article className="knowledge-detail">
        <div className="knowledge-detail-top"><span className="knowledge-kind">{current.type} WIKI</span><div className="knowledge-view-switch" role="tablist" aria-label="知识查看方式"><button role="tab" aria-selected={view === 'wiki'} className={view === 'wiki' ? 'active' : ''} onClick={() => setView('wiki')}><BookOpen size={15} />概览</button><button role="tab" aria-selected={view === 'relations'} className={view === 'relations' ? 'active' : ''} onClick={() => setView('relations')}><Graph size={16} />关联</button></div></div>
        <h2>{current.name}</h2><div className="knowledge-aliases">{current.aliases.join(' / ')}</div>
        {view === 'wiki' ? <>
          <div className="knowledge-section-label"><h3>{editing ? '编辑知识说明' : '理解这个' + current.type}</h3>{!editing && <button className="btn quiet knowledge-edit-btn" onClick={() => { setDraft(content); setEditing(true); }}><PencilSimple size={15} />编辑</button>}</div>
          {editing ? <div className="knowledge-editor"><textarea aria-label="知识说明" value={draft} onChange={e => setDraft(e.target.value)} autoFocus /><div className="knowledge-editor-actions"><span>修改仅在本次使用期间保留</span><button className="btn quiet" onClick={() => setEditing(false)}>取消</button><button className="btn primary" disabled={!draft.trim()} onClick={() => { setSavedText({ ...savedText, [current.id]: draft.trim() }); setEditing(false); onToast('知识说明已保存'); }}><Check size={15} />保存</button></div></div> : <div className="knowledge-prose">{content.split('\n').filter(Boolean).map((p, i) => <p key={i}>{p}</p>)}</div>}
          <blockquote className="knowledge-insight"><Quotes size={21} weight="fill" /><p>{current.quote}</p><span>整理笔记 · 非原文引语</span></blockquote>
          <div className="knowledge-section-label"><h3>与它相连</h3><button className="btn quiet" onClick={() => setView('relations')}>查看关联<ArrowRight size={15} /></button></div>
          <div className="knowledge-related-inline">{relations.map(r => <button key={r.id} onClick={() => { switchEntry(r.node); setQuery(''); }}><Link size={16} /><span>{r.node.name}</span><ArrowUpRight size={14} /></button>)}</div>
        </> : <div className="knowledge-relationships"><div className="knowledge-section-label"><h3>围绕「{current.name}」</h3><span>{relations.length} 个直接关联</span></div><p className="knowledge-relationship-intro">每一个连接，都保留它的含义与来源。</p>{relations.map(r => <button className="knowledge-relation-row" key={r.id} onClick={() => { switchEntry(r.node); setQuery(''); }}><span className="knowledge-relation-icon"><Link size={20} /></span><span className="knowledge-relation-main"><small>{r.label}</small><strong>{r.node.name}</strong><span>{r.note}</span></span><CaretRight size={18} /></button>)}{secondary.length > 0 && <><button className="btn quiet knowledge-expand" onClick={() => setExpanded(!expanded)}><CaretDown size={16} style={{ transform: expanded ? 'rotate(180deg)' : undefined }} />{expanded ? '收起间接关联' : `再探索一步 · ${secondary.length} 个间接关联`}</button>{expanded && <div className="knowledge-secondary">{secondary.map(r => <button className="knowledge-relation-row" key={r.id} onClick={() => { switchEntry(r.node); setQuery(''); }}><span className="knowledge-relation-main"><small>通过「{r.through}」连接</small><strong>{r.node.name}</strong><span>{r.note}</span></span><ArrowUpRight size={17} /></button>)}</div>}</>}</div>}
        <section className="knowledge-provenance"><div className="knowledge-section-label"><h3>回到来源</h3><span>{current.sourceIds.length} 篇资料</span></div>{current.sourceIds.map((id, i) => { const source = initialDocuments.find(s => s.id === id); return <button className="knowledge-source" key={id} onClick={() => onOpenDoc(id)}><span className="knowledge-source-number">{String(i + 1).padStart(2, '0')}</span><span><strong>{source.title}</strong><small>{source.source}<span>·</span>{source.date}</small></span><ArrowUpRight size={16} /></button>; })}<p className="knowledge-provenance-note">此处为示例内容，请以原始资料为准。</p></section>
      </article>
    </div>
  </section>;
}

const extraStructureCards = {
  explainer: { type: '结构模板', label: '循序解释概念、原理与应用', length: '按文章长度展开', intro: '按读者理解顺序介绍概念、原理和应用，章节由实际主题与写作要求确定。', tags: ['知识解读', '循序展开'], tip: '没有固定参考部分；作者明确的章节安排优先。', style: '概念清楚 · 逐步展开 · 结合应用' },
  custom: { type: '结构模板', label: '按作者明确的章节安排组织', length: '按写作要求展开', intro: '沿用作者已保存的结构要求和章节安排，不预设固定参考部分。', tags: ['作者结构', '自主安排'], tip: '在写作要求中说明章节数量、层级和顺序；模板仅补充未指定部分。', style: '作者安排优先 · 保留已确认结构' },
};
const templates = [
  ...structureTemplates.filter(template => !template.legacy && (template.type === '结构模板' || extraStructureCards[template.id])).map(template => ({ ...extraStructureCards[template.id], ...template })),
  { id: 'reading', type: '输出模板', name: '简约阅读', label: '留白舒展，适合在屏幕上阅读', length: 'HTML · PDF', intro: '清楚的标题层级、舒适的行距与简洁的引文样式，让读者把注意力放在内容上。', tags: ['轻盈留白', '屏幕友好'], sections: ['文章标题与副标题', '作者、日期与阅读信息', '正文与分级标题', '图表标题与来源说明', '文末参考资料'], tip: 'A4 页面；正文 11 pt；行距 1.6；图表随文排布。', style: '版式预览 · 不改变正文结构' },
  { id: 'professional', type: '输出模板', name: '专业报告', label: '严谨有序，方便打印与分享', length: 'Word · PDF', intro: '加入封面、目录、章节编号与统一的图表说明，适合需要清晰层级与正式交付的文档。', tags: ['章节编号', '自动目录'], sections: ['独立封面', '摘要与目录', '编号章节与正文', '图表目录与来源', '参考文献与附录'], tip: 'A4 页面；正文 11 pt；2 cm 页边距；页脚显示页码。', style: '版式预览 · 导出时统一排版' },
  { id: 'academic', type: '输出模板', name: '学术阅读', label: '突出论证层级与参考依据', length: 'Word · PDF', intro: '采用节制的正文版式，强化摘要、关键词、层级标题和参考文献，便于审阅与查证。', tags: ['参考文献', '规范层级'], sections: ['标题、摘要与关键词', '分级标题与正文', '表格、图注与注释', '文内引用标记', '参考文献列表'], tip: 'A4 页面；正文 10.5 pt；数字顺序引用；可在导出前调整。', style: '通用学术版式 · 非特定期刊格式' },
  { id: 'sample-one', type: '范文参考', name: '从问题到洞见', label: '学习一篇好文章如何层层展开', length: '示例范文 · 5 段结构', intro: '参考开篇设问、材料展开、观点转折与结尾回扣的组织方式。仅提取结构，不把范文内容当作知识依据。', tags: ['问题驱动', '层层递进'], sections: ['一个常见但未被深究的现象', '解释现象背后的机制', '引入另一种理解视角', '用例子检验新的解释', '回扣问题与实际意义'], tip: '使用后会转成可修改的写作纲要，不复制范文原句。', style: '示例结构 · 供体验范文工作流' },
  { id: 'sample-two', type: '范文参考', name: '两种视角的对话', label: '让不同观点在同一篇文章里相遇', length: '示例范文 · 5 段结构', intro: '将不同观点放回各自的条件与证据之中，通过比较形成更有解释力的综合判断。', tags: ['观点比较', '辩证表达'], sections: ['提出共同讨论的问题', '第一种观点及其依据', '第二种观点及其依据', '解释分歧产生的条件', '形成有边界的综合判断'], tip: '适合有争议的主题，强调公平呈现和证据对照。', style: '示例结构 · 供体验范文工作流' },
  { id: 'sample-three', type: '范文参考', name: '由小见大的观察', label: '从一个细节，走向更广阔的认识', length: '示例范文 · 5 段结构', intro: '以具体情境引起共鸣，再逐步扩展至机制、趋势和可讨论的判断，保留叙事的温度。', tags: ['叙事观察', '案例切入'], sections: ['呈现一个具体场景', '提出场景里的疑问', '补充背景与相关材料', '从个案转向整体分析', '以新的理解返回场景'], tip: '适合知识随笔与观察文章，个案不直接代表普遍结论。', style: '示例结构 · 供体验范文工作流' },
];

export function TemplateWorkspace({ onUseTemplate = () => {}, onToast = () => {}, active = true }) {
  const promptLibrary = usePromptLibrary(active);
  useEffect(() => registerPromptDraftReceiver(payload => promptLibrary.adoptDraftState(payload)),
    [promptLibrary.adoptDraftState, promptLibrary.drafts, promptLibrary.conflicts, promptLibrary.savingId]);
  const [type, setType] = useSessionValue(templateSession, 'type');
  const [selectedId, setSelectedId] = useSessionValue(templateSession, 'selectedId');
  const [query, setQuery] = useSessionValue(templateSession, 'query');
  const [favorites, setFavorites] = useSessionValue(templateSession, 'favorites');
  const filtered = templates.filter(t => t.type === type && `${t.name} ${t.label} ${t.tags.join(' ')}`.includes(query));
  const current = templates.find(t => t.id === selectedId) || templates[0];
  const copied = favorites.includes(current.id);
  const promptDraftCount = Object.entries(promptLibrary.drafts).filter(([id, draft]) => id.startsWith('writing_structure_') && promptDraftChanged(draft)).length;
  const changeType = next => { setType(next); setSelectedId(templates.find(t => t.type === next).id); setQuery(''); };
  return <section className="template-workspace template-library">
    <div className="template-main">
      <header className="knowledge-heading"><div><h1>让表达，有章可循</h1><p>借鉴结构，保留自己的声音。</p></div></header>
      {promptDraftCount > 0 && <p className="template-prompt-draft-notice" role="status">有 {promptDraftCount} 个模板提示词未保存。切换模板或页面会保留，关闭或刷新前请保存。</p>}
      <div className="template-detail-scroll" role="region" aria-label="模板详情" tabIndex={0}>
      <article className="template-preview"><div className="template-preview-top"><span className="knowledge-kind">{current.type}预览</span><span>{current.length}</span></div><h2>{current.name}</h2><p className="template-intro">{current.intro}</p><div className="template-tags">{current.tags.map(tag => <span className="pill" key={tag}>{tag}</span>)}</div><div className="template-paper"><div className="template-paper-caption">{type === '输出模板' ? '文档组成' : type === '结构模板' ? '结构提示' : '结构参考'}<span>{type === '结构模板' ? '正式标题按内容拟定' : '可在写作前调整'}</span></div><>{current.background && <p className="template-paper-background">{current.background}</p>}<ol>{current.sections.map((section, index) => <li key={section}><span>{String(index + 1).padStart(2, '0')}</span>{section}</li>)}</ol></><div className="template-paper-style">{current.style}</div></div>{current.type === '结构模板' && <TemplatePromptEditor template={current} library={promptLibrary} active={active} onToast={onToast}/>}<p className="template-tip"><BookOpen size={17} />{current.tip}</p><div className="template-actions"><button className="btn quiet" onClick={() => { setFavorites(list => copied ? list.filter(id => id !== current.id) : [...list, current.id]); onToast(copied ? '已取消收藏模板' : '已收藏模板，本次使用期间有效'); }}>{copied && <Check size={16} />}{copied ? '已收藏' : '收藏模板'}</button><button className="btn primary" onClick={() => onUseTemplate(current.name, current)}>{type === '输出模板' ? '使用此输出模板' : type === '范文参考' ? '参考这个结构' : '使用这个模板'}<ArrowRight size={17} /></button></div></article>
      </div>
    </div>
    <aside className="template-navigation" aria-label="模板选择">
      <div className="template-navigation-heading"><h2>模板选择</h2><span>{filtered.length} 个模板</span></div>
      <div className="knowledge-type-tabs template-tabs" role="tablist" aria-label="模板类型">{['结构模板', '输出模板', '范文参考'].map(t => <button role="tab" aria-selected={type === t} className={type === t ? 'active' : ''} key={t} onClick={() => changeType(t)}>{t}</button>)}</div>
      <p className="template-type-hint">{type === '结构模板' ? STRUCTURE_REQUIREMENTS_HINT + '正式章标题根据主题与实际内容拟定。' : type === '输出模板' ? '决定文档如何呈现。排版样式与写作结构独立设置。' : STRUCTURE_REQUIREMENTS_HINT + '参考范文的组织方式，内容来自你的思考与资料。'}</p>
      <label className="knowledge-search"><MagnifyingGlass size={18} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="查找合适的模板" aria-label="搜索模板" />{query && <button className="icon-btn" aria-label="清空模板搜索" onClick={() => setQuery('')}><X size={14} /></button>}</label>
      <div className="template-list" role="region" aria-label="模板列表" tabIndex={0}>{filtered.map(template => <button key={template.id} className={`template-item ${current.id === template.id ? 'selected' : ''}`} aria-pressed={current.id === template.id} onClick={() => { setSelectedId(template.id); }}><span className="template-item-icon">{type === '范文参考' ? <Quotes size={23} /> : <FileText size={23} />}</span><span className="template-item-content"><strong>{template.name}</strong><span>{template.label}</span><small>{template.length}{promptDraftChanged(promptLibrary.drafts[`writing_structure_${template.id}`]) && <em className="template-prompt-draft-badge">未保存</em>}</small></span><CaretRight size={17} /></button>)}{!filtered.length && <div className="knowledge-empty"><MagnifyingGlass size={24} /><p>没有找到这个模板</p><button className="btn quiet" onClick={() => setQuery('')}>查看全部模板</button></div>}</div>
      <p className="template-list-footer">模板提供起点，好的表达由你决定。</p>
    </aside>
  </section>;
}



