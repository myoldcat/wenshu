const GENERIC_EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
const DEEPSEEK_ALIASES = { minimal: 'low', medium: 'high', xhigh: 'high' };

// Other compatible endpoints, including local DeepSeek models, define their
// own capabilities. A model name alone does not identify the official API.
function usesDeepSeekAPI(baseUrl) {
  try {
    return new URL(baseUrl).hostname.toLowerCase() === 'api.deepseek.com';
  } catch {
    return false;
  }
}

export function modelReasoningOptions(model = {}) {
  const value = model.reasoningEffort || '';
  if (!usesDeepSeekAPI(model.baseUrl)) {
    return {
      options: [{ value: '', label: '服务默认' }, ...GENERIC_EFFORTS.map(value => ({ value, label: value }))],
      hint: '思考力度与 JSON 输出仅在服务支持时启用。',
    };
  }
  const options = [
    { value: '', label: '服务默认（high）' },
    { value: 'none', label: '关闭思考' },
    ...['low', 'high', 'max'].map(value => ({ value, label: value })),
  ];
  const mapped = DEEPSEEK_ALIASES[value];
  // Preserve the saved value and draft until the author explicitly chooses a
  // different option. Even saving another field must not migrate old aliases.
  if (mapped) options.push({ value, label: `${mapped}（原值 ${value}）` });
  return {
    options,
    hint: `DeepSeek 思考力度为 low、high、max，服务默认 high；“关闭思考”单独关闭推理。${mapped ? `当前原值 ${value} 实际按 ${mapped} 执行；未选择新档位时保留原值。` : ''}`,
  };
}
