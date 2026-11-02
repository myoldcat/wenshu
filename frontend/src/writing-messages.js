// Keep operational details in the writing console; the writing workspace uses
// messages that explain what happened and what the reader can do next.
export function writingMessage(value, fallback = '操作未完成，请稍后重试。') {
  const message = writingText(value) || writingText(value?.detail);
  if (!message) return fallback;
  if (/Failed to fetch|无法连接本地|本地服务|本地文档服务|NetworkError|fetch failed/i.test(message)) return '暂时无法连接文枢，请稍后重试。未保存的文字请先妥善保留。';
  if (/输出预算|输出 Token|单章输出|最大输出|输出上限/.test(message)) {
    const title = message.match(/“[^”]+”/)?.[0];
    return `${title ? `${title}的` : '本次写作的'}篇幅较长，请减少目标字数，或在系统设置中选择支持长文的写作模型。`;
  }
  if (/输入预算|字符预算|上下文.{0,8}(超|不足|限制)/.test(message)) return '写作要求或参考材料过多，请精简后重试，或在系统设置中选择支持长文的写作模型。';
  if (/UTF.?16|选区.{0,12}(偏移|快照)/i.test(message)) return '选中的文字已变化，请重新选择需要修改的内容。';
  if (/校验值|contentHash|baseContentHash/i.test(message)) return '本章正文已变化，这份建议已过期。请根据当前正文重新生成。';
  if (/候选.{0,12}快照|快照.{0,12}(不一致|变化|过期)/.test(message)) return '写作内容已变化，这份候选已过期。请根据当前文稿重新生成。';
  if (/引文|引用|原文块|证据块|材料白名单|blockId|sourceId|revisionId/i.test(message) && /JSON|冻结|白名单|协议|ID[：:]|不一致|无效|越界|定位|编号|身份|重复|缺少/.test(message)) return '生成内容中的引用无法与所选原文对应，请重新生成，并核对参考材料。当前文稿已保留。';
  if (/JSON|schema|traceback|HTTP\s*\d|API\s*key|token|contextChars|stack|TypeError|ValueError|模型.{0,12}格式|冻结.{0,12}(协议|消息)|程序.{0,8}(异常|检查)|原文块|sourceId|revisionId|blockId|快照|白名单/i.test(message)) return fallback;
  return message.replaceAll('服务端', '已保存文稿').replaceAll('项目版本', '写作内容').replaceAll('章节版本', '章节内容').replaceAll('任务快照', '生成时的写作要求').replaceAll('固定版本原文', '写作时选定的原文').replaceAll('冻结的资料版本', '写作时选定的资料').replaceAll('固定资料范围', '本次选定的资料范围');
}

export function writingText(value) {
  return typeof value === 'string' ? value : typeof value?.message === 'string' ? value.message : '';
}

export function materialNotice(value) {
  const message = writingText(value);
  if (/增强排序|[Rr]erank|重排/.test(message)) return '增强排序暂不可用，已按常规方式推荐，可稍后重试。';
  if (/查询模型|查询扩展|本地检索条件/.test(message)) return '已按主题与写作要求查找材料，可调整检索条件后重新推荐。';
  if (/索引|通道|[Ee]mbeddings|向量/.test(message)) return '部分查找方式暂不可用或仅覆盖部分资料，已继续使用其他方式推荐。可稍后重新检索。';
  if (/预算|Wiki/.test(message)) return '本次推荐覆盖部分相关资料，可调整检索条件补充查找。';
  return writingMessage(message, '部分材料暂时无法参与推荐，可稍后重新检索。');
}
