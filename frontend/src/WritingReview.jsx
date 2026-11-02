import React from 'react';
import { ArrowRight, CheckCircle, DownloadSimple, ShieldCheck, WarningCircle } from '@phosphor-icons/react';
import { writingMessage } from './writing-messages';
import { WritingCitationBindingIssues } from './WritingCitations';
import WritingImagesEmpty from './WritingImagesEmpty';

const lengthLabel = status => ({ within_range: '符合范围', below_target: '偏短', above_target: '偏长', not_set: '未设目标' })[status] || '待检查';
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const skipped = review => review?.disposition?.status === 'skipped';
const confirmed = issue => !!(issue.confirmed || issue.acknowledged);
const reviewMessage = issue => ({ citation_incomplete: '引用的来源信息不完整，请重新核对原文。', duplicate_citation: '同一个引用编号对应多条来源，请核对后保留正确引用。', citation_id_invalid: '引用编号不完整，请重新插入引用。', citation_out_of_scope: '这条引用不在本次选定的资料范围，请核对后处理。', citation_anchor_invalid: '无法找到这条引用对应的原文位置，请重新核对来源。', unknown_citation: '这条引用未找到对应来源，请重新插入有效引用。', placeholder_content: '本段仍有待补充内容，请完成后再定稿。', unsupported_control_character: '正文含无法导出的字符，请重新输入异常文字。' })[issue.code] || writingMessage(issue.message, '请核对本段内容与引用。');

export function WritingReviewConfirmAll({ writing, stale = false, disabled = false }) {
  const { project, dirty, pending, remoteChanged, busy, loading } = writing;
  const review = project.review;
  if (!review || !stale && skipped(review)) return null;
  const current = !stale && review.version === project.version && !!review.wordCounts;
  const allConfirmed = current && review.authorConfirmed === true && !skipped(review)
    && !(review.issues || []).some(issue => !confirmed(issue));
  const active = pending || (project.runs || []).some(run => ['queued', 'running'].includes(run.status))
    || ['queued', 'running'].includes(project.automatic?.status);
  const blocked = disabled || !!busy || loading || dirty || remoteChanged || active || !current
    || !project.id || !(project.sections || []).some(section => section.content?.trim())
    || typeof writing.confirmReview !== 'function';
  function confirmAll() {
    if (blocked || allConfirmed) return;
    writing.confirmReview();
  }
  return <button type="button" className="btn primary writing-review-confirm-all" data-writing-review-confirm-all
    aria-label="一键确认全部校验事项" aria-busy={busy === 'review-confirm'} disabled={blocked || allConfirmed}
    title="将当前报告的全部事项记录为人工确认，完成检查校验" onClick={confirmAll}>
    <CheckCircle size={16}/>{busy === 'review-confirm' ? '正在确认…' : allConfirmed ? '已全部确认' : '一键确认'}
  </button>;
}

export function WritingReviewStart({ writing, disabled = false }) {
  const { project, dirty, pending, remoteChanged, busy, loading } = writing;
  const active = pending || (project.runs || []).some(run => ['queued', 'running'].includes(run.status))
    || ['queued', 'running'].includes(project.automatic?.status);
  const blocked = disabled || !!busy || loading || dirty || remoteChanged || active || !project.id
    || !(project.sections || []).some(section => section.content?.trim()) || typeof writing.review !== 'function';
  return <button type="button" className="btn primary" data-writing-review-start disabled={blocked}
    title={dirty ? '请先保存修改，再校验当前文稿。' : undefined} onClick={() => { if (!blocked) writing.review(); }}>
    <ShieldCheck size={16}/>{active ? '正在核对…' : '开始校验'}
  </button>;
}

export function WritingReviewReport({ review, stale, sections = [], onLocate, locateLabel = '前往正文编辑', onConfirm, busy }) {
  if (!review) return <p className="writing-review-empty">尚未进行检查校验。可以开始校验，也可以跳过此步。</p>;
  if (!stale && skipped(review)) return <div className="writing-review-report" role="status" data-writing-review-skipped-empty>
    <WritingImagesEmpty title="本次已跳过校验" description="可随时点击“开始校验”，核对当前文稿。" artwork={<>
      <rect x="43" y="20" width="70" height="80" rx="6" transform="rotate(-8 43 20)" fill="var(--surface)" stroke="currentColor" opacity=".32"/>
      <rect x="58" y="13" width="70" height="84" rx="6" fill="var(--surface)" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M72 34h40M72 46h31M72 58h36M72 70h20" stroke="currentColor" strokeWidth="2" strokeLinecap="round" opacity=".35"/>
      <circle cx="123" cy="78" r="19" fill="var(--surface)" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="123" cy="78" r="12" fill="currentColor" opacity=".08"/>
      <path d="m137 92 14 14M23 82h12m-6-6v12m117-66h10m-5-5v10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity=".55"/>
    </>}/>
  </div>;
  const words = review.wordCounts;
  return <div className={'writing-review-report ' + (stale ? 'is-stale' : '')}>
    <p className="writing-review-summary" role="status">{stale ? '文稿有变化，以下为此前校验记录' : skipped(review) ? '本次已跳过校验' : formatTime(review.checkedAt) + ' · 当前保存文稿的校验报告'}<span>{review.issues?.filter(issue => !confirmed(issue)).length || 0} 项待确认{review.authorConfirmed ? ' · 已人工确认' : ''}</span></p>
    {words && <section className="writing-review-length" aria-label="全文字数校验"><div><strong>{words.total?.toLocaleString()} 字</strong><span className={words.status === 'within_range' ? '' : 'attention'}>{lengthLabel(words.status)}</span></div><p>目标 {words.target?.toLocaleString()} 字</p><details><summary>逐章字数 · {words.sections?.length || 0} 章</summary><div className="writing-review-length-list">{(words.sections || []).map(item => <button key={item.sectionId} disabled={stale || !onLocate} onClick={() => onLocate?.({ sectionId: item.sectionId })}><span>{item.title}</span><small>{item.actual} / {item.target} 字 · {lengthLabel(item.status)}</small></button>)}</div></details></section>}
    <div className="writing-review-results">{(review.issues || []).map((issue, index) => <article key={issue.id || index} className={confirmed(issue) ? 'is-confirmed' : issue.severity === 'error' ? 'is-error' : ''}>{confirmed(issue) ? <CheckCircle size={17}/> : <WarningCircle size={17}/>}<div><h4>{issue.sectionId ? sections.find(section => section.id === issue.sectionId)?.title || '章节检查' : '全文检查'}{issue.paragraphIndex ? ' · 第 ' + issue.paragraphIndex + ' 段' : ''}{confirmed(issue) ? ' · 已人工确认' : ''}</h4><p>{reviewMessage(issue)}</p>{issue.sectionId && <button className="writing-review-locate" disabled={stale || !onLocate} onClick={() => onLocate?.(issue)}>{locateLabel}<ArrowRight size={13}/></button>}{issue.id && !confirmed(issue) && onConfirm && <button type="button" className="btn quiet" disabled={busy || stale} onClick={() => onConfirm(issue.id)}>已核对，此项无误</button>}</div></article>)}{!review.issues?.length && !skipped(review) && <article><CheckCircle size={17}/><div><h4>本次未发现待核对事项</h4><p>请结合文章内容与来源判断事实和观点是否准确。</p></div></article>}</div>
    <p className="writing-review-notice">只核对事实、观点和明确引用；总结、过渡、重复提及与常识性介绍可无需逐句对应资料。人工确认作为处理记录保留。</p>
  </div>;
}

export function WritingParagraphChecks({ section, review, stale, busy, onConfirm }) {
  if (!section?.paragraphs?.length || !stale && skipped(review)) return null;
  return <section className="writing-paragraph-checks" aria-label="逐段核对结果"><header><ShieldCheck size={17}/><h3>段落核对结果</h3>{stale && <span>正文已变化，请保存后重新核对或确认处理事项</span>}</header>{section.paragraphs.map(paragraph => {
    const issues = (review?.issues || []).filter(issue => issue.sectionId === section.id && issue.paragraphIndex === paragraph.index + 1);
    const supported = paragraph.tokens.filter(token => token.type === 'citation' && token.bindingStatus === 'supported').length;
    return <article data-review-paragraph={paragraph.index + 1} key={paragraph.id}><h4>第 {paragraph.index + 1} 段{supported > 0 ? ' · ' + supported + ' 处引用支持已核对' : ''}</h4><blockquote>{paragraph.text.replace(/\[E\d+\]/g, '')}</blockquote><WritingCitationBindingIssues paragraph={paragraph}/>{issues.map((issue, index) => <div key={issue.id || index} className="writing-paragraph-issue"><p>{reviewMessage(issue)}</p>{confirmed(issue) ? <span><CheckCircle size={14}/>已人工确认</span> : issue.id && <button type="button" className="btn quiet" disabled={busy || stale} onClick={() => onConfirm?.(issue.id)}>已核对，此项无误</button>}</div>)}{!issues.length && !paragraph.diagnostics.length && !paragraph.unplacedCitations && <p className="writing-paragraph-clear">{stale ? '正文已变化，原核对结果仅供参考。' : section.bindingStatus !== 'current' || !review?.citationReviewCompleted ? review?.authorConfirmed ? '当前文章已人工确认；本段尚无完整自动核对结论。' : '本段尚未完成引用核对。' : '本次核对没有发现本段待处理的引用事项。'}</p>}</article>;
  })}</section>;
}

export default function WritingReview({ writing, onLocate }) {
  const { project, dirty, pending, remoteChanged, busy } = writing;
  const review = project.review, stale = !!review && (dirty || remoteChanged || pending || review.version !== project.version);
  const blocked = !!busy || pending || !project.id || remoteChanged || !project.sections.some(section => section.content?.trim());
  return <div className="writing-review"><ShieldCheck size={26} weight="light"/><h3>检查校验</h3><p>核对事实、观点、明确引用与文章长度，生成独立校验报告。也可以跳过。</p><div className="writing-review-actions"><WritingReviewConfirmAll writing={writing} stale={stale}/><WritingReviewStart writing={writing}/><button type="button" className="btn quiet" disabled={blocked} onClick={() => writing.skipReview()}>跳过校验</button>{review && <button type="button" className="btn" disabled={blocked || stale} onClick={() => writing.exportReview()}><DownloadSimple size={16}/>导出校验报告</button>}</div>{pending && <p className="writing-review-empty" role="status">核对正在进行，结果会保存到文稿，可稍后返回查看。</p>}<WritingReviewReport review={review} stale={stale} sections={project.sections} onLocate={onLocate} onConfirm={issueId => writing.confirmReview([issueId])} busy={blocked}/>{review && stale && <button type="button" className="btn primary" disabled={blocked || dirty} onClick={() => writing.confirmReview()}>{dirty ? '请先保存修改，再确认' : stale ? '修改已处理，确认当前文章' : '已核对，全部事项无误'}</button>}</div>;
}
