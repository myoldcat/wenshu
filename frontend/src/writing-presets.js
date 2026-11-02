// Shared by the template library and writing preparation. Selecting a preset
// changes the draft only; generation and persistence remain explicit actions.
export const DEFAULT_STRUCTURE_TEMPLATE = 'research-report-decision';
export const STRUCTURE_REQUIREMENTS_HINT = '写作要求中的明确结构优先，模板仅补充未指定部分。';
const REPORT_INTRODUCTION_HINT = '引言（开篇，不算独立章节）';
export const structureTemplates = [
  { id: 'research-report-decision', type: '结构模板', name: '调研报告（决策参考型）', label: '研判趋势与风险，形成分期决策建议', length: '按文章长度展开', intro: '开篇引言不单列章节；随后参考现状与趋势、风险研判、意见建议组织内容，建议区分近期与中期；正式章标题根据主题与实际内容完善。', tags: ['决策参考', '风险研判'], background: REPORT_INTRODUCTION_HINT, sections: ['现状与趋势', '风险研判', '意见建议（分近期、中期）'], hint: '引言不计章；现状与趋势 → 风险研判 → 意见建议（分近期、中期），章标题按内容拟定。', tip: '条目仅提示论述方向；风险判断须有依据，近期与中期建议须注明适用条件，不把建议写成已批准的决定。', style: '趋势清楚 · 风险有据 · 建议分期' },
  { id: 'research-report-problem', type: '结构模板', name: '调研报告（问题对策型）', label: '从实际问题出发，分析原因并提出对策', length: '按文章长度展开', intro: '开篇引言不单列章节，也不重复第一章的背景与方法；正文参考调研背景与方法、基本情况、存在问题、原因分析、对策建议展开，正式章标题根据内容完善。', tags: ['问题分析', '对策建议'], background: REPORT_INTRODUCTION_HINT, sections: ['调研背景与方法', '基本情况', '存在问题', '原因分析', '对策建议'], hint: '引言不计章；背景与方法 → 基本情况 → 存在问题 → 原因分析 → 对策建议，标题按内容拟定。', tip: '条目仅提示结构；调研方法、事实与原因分析以实际提供的内容为依据，不虚构调研过程或因果关系。', style: '问题具体 · 原因有据 · 对策对应' },
  { id: 'research-report-experience', type: '结构模板', name: '调研报告（经验总结型）', label: '总结做法与成效，提炼可推广的经验', length: '按文章长度展开', intro: '开篇引言不单列章节；随后参考主要做法、取得成效、经验启示、推广建议展开，区分实际成效与经验归纳；正式章标题根据内容完善。', tags: ['经验总结', '推广建议'], background: REPORT_INTRODUCTION_HINT, sections: ['主要做法', '取得成效', '经验启示', '推广建议'], hint: '引言不计章；主要做法 → 取得成效 → 经验启示 → 推广建议，章标题结合内容完善。', tip: '条目仅提示结构；不夸大成效，不把个别经验写成普遍规律，推广建议说明适用条件与边界。', style: '做法清楚 · 成效真实 · 推广有界' },
  { id: 'research-report', type: '结构模板', legacy: true, name: '调研报告', label: '把握当前态势，研判问题，提出建议', length: '按文章长度展开', intro: '开篇背景不单列章节，正文依次围绕态势、问题隐患和建议展开；正式章标题根据主题与实际内容完善。', tags: ['调研分析', '对策建议'], background: '背景介绍（开篇，不算独立章节）', sections: ['当前态势', '存在问题隐患', '对策建议'], hint: '按态势、问题隐患、建议展开；章标题结合主题与内容完善，开篇背景不单列章。', tip: '条目仅提示结构与写作功能，正式章标题需结合内容拟定；背景不单列章，事实与判断仍遵守所选写作模式。', style: '情况清楚 · 问题具体 · 建议务实' },
  { id: 'opinion', type: '结构模板', name: '观点文章', label: '把一个判断，讲得有理有据', length: '2,000–4,000 字', intro: '从一个值得讨论的问题出发，形成明确观点，用材料展开论证，再回应可能的反对意见。', tags: ['观点表达', '短中篇'], sections: ['从一个具体问题开始', '提出核心观点', '用证据展开论证', '回应另一种声音', '回到问题，留下思考'], tip: '适合评论、专栏与面向大众的观点文章。', style: '观点清晰 · 表达克制 · 有据可循' },
  { id: 'review', type: '结构模板', name: '主题综述', label: '从多篇资料，看见一个主题的全貌', length: '4,000–8,000 字', intro: '围绕主题归纳关键概念、主要观点和不同解释，把分散资料组织成清晰的知识地图。', tags: ['知识梳理', '多源归纳'], sections: ['主题与讨论范围', '关键概念与发展脉络', '主要观点与代表材料', '共识、分歧与证据缺口', '总结与后续问题'], tip: '适合学习笔记、领域入门与专题资料整理。', style: '结构清楚 · 区分来源 · 兼顾不同观点' },
  { id: 'report', type: '结构模板', name: '研究报告', label: '从证据出发，让结论站得住脚', length: '5,000–12,000 字', intro: '以研究问题组织材料，透明说明方法和证据边界，形成可追溯的分析与建议。', tags: ['证据导向', '专业报告'], sections: ['摘要与核心结论', '研究问题与范围', '资料来源与分析方法', '主要发现与证据', '讨论、限制与建议'], tip: '适合专题研究、行业观察和内部分析报告。', style: '结论先行 · 依据透明 · 边界明确' },
  { id: 'book', type: '结构模板', name: '长篇论著', label: '围绕一条主线，逐章展开', length: '12,000–30,000 字', intro: '先建立整体论证地图，再为每章设定问题、证据与结论，保持术语和逻辑的一致。', tags: ['逐章编写', '长文协同'], sections: ['导论：核心问题与论证路线', '第一部分：概念与理论基础', '第二部分：材料与案例分析', '第三部分：比较、讨论与综合', '结语：发现、限制与展望'], tip: '建议逐章确认，并在各章完成后进行全文一致性评审。', style: '主线稳定 · 前后呼应 · 逐层深入' },
  { id: 'explainer', name: '知识解读', sections: [] },
  { id: 'custom', name: '自定章节结构', sections: [] },
];

// Retain an explicitly saved or unsaved historical choice without presenting
// the retired template as a new-draft option.
export const structureTemplateOptions = (...projects) => {
  const retained = new Set(projects.map(project => project?.structureTemplate));
  return structureTemplates.filter(template => !template.legacy || retained.has(template.id));
};

export const writingStyles = [
  { id: 'official', name: '政府公文', description: '庄重平实、简明规范，情况、问题和建议层次清楚。' },
  { id: 'technical', name: '技术论述', description: '概念准确、术语一致，讲清原理、条件与技术边界。' },
  { id: 'news', name: '新闻报道', description: '客观准确、重点前置，交代来源并区分事实与观点。' },
];

export function structureTemplatePatch(id) {
  const preset = structureTemplates.find(item => item.id === id);
  if (!preset) return {};
  return { structureTemplate: id, template: preset.name, templateSections: [...preset.sections], sampleReferenceText: '', sampleStructureApplied: false };
}

export function writingStylePatch(id) {
  const preset = writingStyles.find(item => item.id === id);
  return preset ? { writingStyle: id, style: preset.name } : {};
}

export const structureTemplateLabel = project => structureTemplates.find(item => item.id === project.structureTemplate)?.name || project.template || '自定章节结构';
export const writingStyleLabel = project => writingStyles.find(item => item.id === project.writingStyle)?.name || project.style || '原文风';

export function restoreLegacyWritingPreset(project, saved, kind) {
  const keys = kind === 'structure' ? ['structureTemplate', 'template', 'templateSections', 'sampleReferenceText', 'sampleStructureApplied'] : ['writingStyle', 'style'];
  const next = { ...project };
  for (const key of keys) {
    if (Object.hasOwn(saved, key)) next[key] = saved[key];
    else delete next[key];
  }
  return next;
}
