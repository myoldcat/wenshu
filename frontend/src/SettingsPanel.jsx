import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, Check, Info, PencilSimple, Plus, PlugsConnected, Trash, X } from '@phosphor-icons/react';
import { BrandMark, BrandWordmark } from './Brand';
import { isDemoMode } from './demo-mode';
import { modelReasoningOptions } from './model-reasoning';
import RetrievalSettings from './RetrievalSettings';
import './settings-panel.css';

function SettingsDetail({ title, anchor, busy, onClose, children }) {
  const panel = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const [position, setPosition] = useState({ right: 22, bottom: 22, width: 460 });
  useLayoutEffect(() => {
    const parent = anchor.current?.closest('.workspace-popover');
    const place = () => {
      const rect = parent?.getBoundingClientRect();
      const room = rect ? window.innerWidth - rect.right - 34 : 0;
      setPosition(room >= 360 ? { left: rect.right + 12, bottom: 22, width: Math.min(460, room) } : { right: 22, bottom: 22, width: Math.min(460, window.innerWidth - 44) });
    };
    place();
    const observer = new ResizeObserver(place);
    if (parent) observer.observe(parent);
    window.addEventListener('resize', place);
    return () => { observer.disconnect(); window.removeEventListener('resize', place); };
  }, [anchor]);
  useEffect(() => {
    const previous = document.activeElement;
    (panel.current?.querySelector('[data-initial-focus]') || panel.current?.querySelector('[data-detail-close]'))?.focus({ preventScroll: true });
    const keyboard = event => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation(); closeRef.current();
      }
      if (event.key === 'Tab') {
        const targets = [...panel.current.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),summary,a[href],[tabindex="0"]')].filter(node => node.getClientRects().length);
        if (!targets.length) { event.preventDefault(); return; }
        if (event.shiftKey && (document.activeElement === targets[0] || !panel.current.contains(document.activeElement))) { event.preventDefault(); targets.at(-1).focus(); }
        else if (!event.shiftKey && (document.activeElement === targets.at(-1) || !panel.current.contains(document.activeElement))) { event.preventDefault(); targets[0].focus(); }
      }
    };
    document.addEventListener('keydown', keyboard, true);
    return () => {
      document.removeEventListener('keydown', keyboard, true);
      if (previous?.isConnected && previous.getClientRects().length && !previous.matches(':disabled')) previous.focus({ preventScroll: true });
      else anchor.current?.closest('.workspace-popover')?.focus({ preventScroll: true });
    };
  }, []);
  return createPortal(<div className="settings-detail-layer" data-settings-detail-layer onPointerDown={event => {
    if (event.target !== event.currentTarget) return;
    // Consume this interaction: returning to the parent must not activate a control beneath the layer.
    event.preventDefault(); event.stopPropagation(); onClose();
  }}>
    <section ref={panel} className="settings-detail" style={position} role="dialog" aria-modal="true" aria-labelledby="settings-detail-title" aria-busy={busy || undefined}>
      <header className="settings-detail-head"><h2 id="settings-detail-title">{title}</h2><button type="button" className="icon-btn" data-detail-close aria-label={`关闭${title}`} onClick={onClose}><X size={20}/></button></header>
      {children}
    </section>
  </div>, document.body);
}

function Feedback({ settings }) {
  return <>{settings.error && <div className="local-library-message is-error ai-settings-feedback" role="alert"><span>{settings.error}</span></div>}{settings.feedback && <p className="ai-settings-feedback" role="status">{settings.feedback}</p>}</>;
}

export function SettingsPanel({ settings }) {
  const anchor = useRef(null);
  const [detail, setDetail] = useState(null);
  const [idleStart, setIdleStart] = useState(settings.preferences.idleStart);
  const [idleEnd, setIdleEnd] = useState(settings.preferences.idleEnd);
  const [timeError, setTimeError] = useState('');
  useEffect(() => {
    setIdleStart(settings.preferences.idleStart); setIdleEnd(settings.preferences.idleEnd); setTimeError('');
  }, [settings.preferences.idleStart, settings.preferences.idleEnd]);
  const item = settings.drafts.find(model => model.id === settings.selectedId);
  const saved = settings.models.find(model => model.id === settings.selectedId);
  const reasoning = modelReasoningOptions(item);
  const orderedModels = [...settings.models].sort((a, b) => Number(b.id === settings.preferences.defaultModelId) - Number(a.id === settings.preferences.defaultModelId));
  const dirty = item && (item.draft || item.apiKey || item.clearApiKey || ['name','baseUrl','model','contextChars','maxOutputTokens','timeoutSeconds','reasoningEffort','jsonMode'].some(key => String(item[key]) !== String(saved?.[key])));
  const update = changes => settings.changeModel(item.id, changes);
  const openEditor = adding => {
    settings.clearFeedback();
    if (adding) settings.addModel();
    setDetail('model');
  };
  const closeDetail = () => {
    if (!settings.busy) {
      if (detail === 'model') settings.discardModel();
      else settings.clearFeedback();
    }
    setDetail(null);
  };
  const save = async event => {
    event.preventDefault(); await settings.saveModel();
  };
  const saveIdleTime = async event => {
    event.preventDefault();
    if (!idleStart || !idleEnd || idleStart === idleEnd) { setTimeError('开始时间和结束时间不能相同，请选择一个有效时段。'); return; }
    setTimeError(''); await settings.savePreferences({ idleStart, idleEnd });
  };
  const toggleIdle = enabled => {
    if (enabled && (!idleStart || !idleEnd || idleStart === idleEnd)) { setTimeError('开始时间和结束时间不能相同，请选择一个有效时段。'); return; }
    setTimeError('');
    return settings.savePreferences(enabled ? { idleProcess: true, idleStart, idleEnd } : { idleProcess: false });
  };
  const timeChanged = idleStart !== settings.preferences.idleStart || idleEnd !== settings.preferences.idleEnd;
  return <div className="settings-overview" ref={anchor}>
    <div className="settings-model-heading"><h3>模型设置</h3><span>{settings.models.length} 个模型</span></div>
    <div className="settings-model-list" role="radiogroup" aria-label="模型列表" aria-busy={settings.loading}>
      {orderedModels.map(model => <label className={`settings-model-row ${settings.selectedId === model.id ? 'is-selected' : ''}`} key={model.id}>
        <input type="radio" name="settings-model" value={model.id} checked={settings.selectedId === model.id} disabled={!!settings.busy} onChange={() => { settings.setSelectedId(model.id); settings.clearFeedback(); }}/>
        <span className="settings-model-copy"><strong>{model.name}</strong><small title={model.model}>{model.model}</small></span>
        {settings.preferences.defaultModelId === model.id && <span className="settings-default-badge">默认</span>}
      </label>)}
      {!settings.models.length && <p className="settings-model-empty" role="status">{settings.loading ? '正在读取模型…' : settings.error ? '模型列表读取失败' : '尚未添加模型'}</p>}
    </div>
    <div className="settings-model-toolbar">
      <button type="button" className="btn" disabled={!!settings.busy || settings.loading} onClick={() => openEditor(true)}><Plus size={15}/>添加模型</button>
      <button type="button" className="btn" disabled={!!settings.busy || !saved} onClick={() => openEditor(false)}><PencilSimple size={15}/>修改</button>
      <button type="button" className="btn" disabled={!!settings.busy || !saved} onClick={() => { settings.clearFeedback(); setDetail('delete'); }}><Trash size={15}/>删除</button>
      <button type="button" className="btn" disabled={!!settings.busy || !saved || settings.preferences.defaultModelId === saved?.id} onClick={() => settings.savePreferences({ defaultModelId: saved.id })}>设为默认</button>
    </div>
    {!settings.preferences.defaultModelId && <p className="settings-default-hint">请设置默认模型以启用 AI 处理。</p>}
    <section className="settings-processing" aria-label="自动处理设置">
      <label className="settings-processing-option"><span><strong>自动翻译为中文</strong><small>新文件识别到外语后自动翻译，保留原文{!settings.preferences.defaultModelId ? '；等待配置默认模型' : ''}</small></span><input type="checkbox" role="switch" checked={settings.preferences.autoTranslate !== false} disabled={!!settings.busy || settings.loading} onChange={event => settings.savePreferences({ autoTranslate: event.target.checked })}/></label>
      <label className="settings-processing-option"><span><strong>自动处理新文件</strong><small>新文件入库后生成中文摘要与主题分类</small></span><input type="checkbox" role="switch" checked={settings.preferences.autoProcess} disabled={!!settings.busy || !settings.preferences.defaultModelId} onChange={event => settings.savePreferences({ autoProcess: event.target.checked })}/></label>
      <label className="settings-processing-option"><span><strong>闲时处理旧文件</strong><small>没有新文件任务和写作活动时补充缺失结果</small></span><input type="checkbox" role="switch" checked={settings.preferences.idleProcess} disabled={!!settings.busy || !settings.preferences.defaultModelId} onChange={event => toggleIdle(event.target.checked)}/></label>
      <form className="settings-idle-time" onSubmit={saveIdleTime}>
        <div className="settings-idle-time-heading"><span>闲时时段</span>{timeChanged && <button className="settings-time-save" type="submit" disabled={!!settings.busy}>保存时段</button>}</div>
        <div className="settings-idle-time-inputs"><label>开始<input type="time" required value={idleStart} disabled={!!settings.busy} onChange={event => { setIdleStart(event.target.value); setTimeError(''); }}/></label><span aria-hidden="true">—</span><label>结束<input type="time" required value={idleEnd} disabled={!!settings.busy} onChange={event => { setIdleEnd(event.target.value); setTimeError(''); }}/></label></div>
        {timeError ? <p className="settings-time-error" role="alert">{timeError}</p> : (idleStart > idleEnd || timeChanged) && <p className="settings-time-hint">{idleStart > idleEnd ? '跨午夜至次日。' : ''}{timeChanged ? '时段尚未保存。' : ''}</p>}
      </form>
    </section>
    {!detail && <Feedback settings={settings}/>}
    {settings.error && !settings.models.length && !detail && <button type="button" className="btn quiet" onClick={settings.refresh}>重新读取</button>}
    <button type="button" className="settings-about-button" disabled={!!settings.busy} onClick={() => { settings.clearFeedback(); setDetail('retrieval'); }}><PlugsConnected size={18}/><span>知识库检索配置</span><ArrowRight size={17}/></button>
    {!isDemoMode() && <button type="button" className="settings-about-button" disabled={!!settings.busy} onClick={() => { settings.clearFeedback(); setDetail('about'); }}><Info size={18}/><span>关于文枢</span><ArrowRight size={17}/></button>}

    {detail && <SettingsDetail title={detail === 'model' ? item?.draft ? '添加模型' : '修改模型' : detail === 'delete' ? '删除模型' : detail === 'retrieval' ? '知识库检索配置' : '关于文枢'} anchor={anchor} busy={!!settings.busy} onClose={closeDetail}>
      {detail === 'retrieval' && <RetrievalSettings models={settings.models}/>}
      {detail === 'model' && item && <form className="settings-editor" onSubmit={save}>
        <div className="settings-detail-body"><fieldset className="settings-editor-fields" disabled={!!settings.busy}>
          <label className="field">配置名称<input data-initial-focus required maxLength={100} value={item.name} onChange={event => update({ name: event.target.value })} placeholder="例如：日常阅读与写作"/></label>
          <label className="field">模型名称<input required maxLength={200} value={item.model} onChange={event => update({ model: event.target.value })} placeholder="服务提供方的模型 ID"/></label>
          <label className="field">接口地址<input required type="url" value={item.baseUrl} onChange={event => update({ baseUrl: event.target.value })} placeholder="http://127.0.0.1:11434/v1"/></label>
          <label className="field">API Key<input type="password" autoComplete="new-password" value={item.apiKey || ''} onChange={event => update({ apiKey: event.target.value, clearApiKey: false })} placeholder={item.hasApiKey ? '已保存密钥，留空保留；输入可替换' : '无密钥服务可留空'}/></label>
          {item.hasApiKey && <label className="ai-inline-check"><input type="checkbox" checked={!!item.clearApiKey} onChange={event => update({ clearApiKey: event.target.checked, apiKey: '' })}/>保存时清除已存密钥</label>}
          <details className="ai-model-options"><summary>请求参数</summary><div className="ai-model-numbers">
            <label className="field">单次输入上限（字符）<input required type="number" min="2000" max="1000000" step="1" value={item.contextChars} onChange={event => update({ contextChars: event.target.value })}/><span className="field-hint">包含智能指令与正文；这是应用输入预算，与模型的 token 上下文长度不同。</span></label>
            <label className="field">最大输出 Token<input required type="number" min="128" max="65536" step="1" value={item.maxOutputTokens} onChange={event => update({ maxOutputTokens: event.target.value })}/></label>
            <label className="field">超时（秒）<input required type="number" min="5" max="300" step="1" value={item.timeoutSeconds} onChange={event => update({ timeoutSeconds: event.target.value })}/></label>
            <label className="field">思考力度<select aria-describedby="model-reasoning-hint" value={item.reasoningEffort || ''} onChange={event => update({ reasoningEffort: event.target.value })}>{reasoning.options.map(option => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>
            <label className="ai-inline-check"><input type="checkbox" checked={!!item.jsonMode} onChange={event => update({ jsonMode: event.target.checked })}/>请求 JSON 输出</label>
          </div><p className="field-hint" id="model-reasoning-hint">{reasoning.hint}</p></details>
          {dirty && <p className="field-hint">保存模型后即可测试连接。</p>}
          {item.lastTest && <p className="field-hint">上次测试：{item.lastTest.embeddings ? `向量响应通过 · ${item.lastTest.dimensions} 维` : item.lastTest.textGeneration ? '文本响应通过' : '未通过'}{item.lastTest.jsonParseable ? ' · JSON 可解析' : ''}</p>}
          <Feedback settings={settings}/>
        </fieldset></div>
        <footer className="settings-detail-footer"><button type="button" className="btn quiet" disabled={!!settings.busy} onClick={closeDetail}>关闭</button><button type="button" className="btn" disabled={!!settings.busy || !saved || !!dirty} onClick={settings.testModel}><PlugsConnected size={15}/>{settings.busy === 'test' ? '测试中…' : '测试连接'}</button><button type="submit" className="btn primary" disabled={!!settings.busy}><Check size={15}/>{['save','preferences'].includes(settings.busy) ? '保存中…' : '保存模型'}</button></footer>
      </form>}
      {detail === 'delete' && saved && <><div className="settings-detail-body settings-delete-body"><p>删除模型“{saved.name}”？</p><p className="field-hint">{settings.preferences.defaultModelId === saved.id ? '这是当前默认模型。删除后需要重新指定默认模型，自动处理和闲时处理将关闭。' : '删除后将从模型列表移除。'}已有文档和生成结果会保留。</p><Feedback settings={settings}/></div><footer className="settings-detail-footer"><button type="button" className="btn quiet" disabled={!!settings.busy} onClick={closeDetail}>取消</button><button type="button" className="btn settings-delete-confirm" disabled={!!settings.busy} onClick={async () => { if (await settings.deleteModel()) setDetail(null); }}><Trash size={15}/>{settings.busy === 'delete' ? '删除中…' : '确认删除'}</button></footer></>}
      {detail === 'about' && <div className="settings-detail-body about-prototype">
        <div className="about-brand" role="img" aria-label="文枢"><BrandMark/><BrandWordmark/></div>
        <h3>留给思考的空间</h3>
        <p>文枢把资料、理解与表达放在同一张工作台上。</p>
        <div className="settings-about-features"><p><strong>资料与阅读</strong>导入 HTML、收集网页、全文检索，保留原始快照与正文版本；通过默认模型生成摘要、中文译文与主题分类。</p><p><strong>知识与写作</strong>构建带原文证据的 Wiki 与关系图谱，创建写作项目、生成纲要与章节，支持选区修改、比较采纳、原文引用和 Word、PDF、HTML 导出。</p><p><strong>数据保存</strong>资料、模型配置与处理结果保存在文枢中；执行 AI 任务时，相关正文会发送到你配置的模型服务。</p></div>
      </div>}
    </SettingsDetail>}
  </div>;
}
