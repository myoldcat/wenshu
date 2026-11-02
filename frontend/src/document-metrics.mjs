const countFormat = new Intl.NumberFormat('zh-CN');

export const BODY_WORD_COUNT_DESCRIPTION = '按原始正文统计：汉字按字、外文按词，不含标题、代码、空白和标点。';

export function bodyWordCountLabel(doc, prefix = '') {
  const count = doc?.bodyWordCount;
  return typeof count === 'number' && Number.isSafeInteger(count) && count >= 0
    ? `${prefix}${countFormat.format(count)} 字`
    : '字数未统计';
}
