import React, { useEffect, useState } from 'react';
import { ArrowUpRight, FileText, Star } from '@phosphor-icons/react';

const savedAssetUrl = /^\/api\/assets\/[A-Za-z0-9_-]+$/;
const localImageUrl = value => typeof value === 'string' && savedAssetUrl.test(value) ? value : null;

/** Read current list metadata only; never request or start image selection. */
export function homeRecentCardImage(doc = {}) {
  const cover = doc.cardCover;
  if (doc.revisionId && cover?.revisionId === doc.revisionId && cover.status === 'completed') {
    // A completed empty choice is intentional. Invalid URLs are also hidden.
    return localImageUrl(cover.url);
  }

  const candidates = Array.isArray(doc.coverCandidates) ? doc.coverCandidates : [];
  const usable = candidates.filter(candidate => localImageUrl(candidate?.url));
  const selected = usable.find(candidate => candidate.url === doc.ruleCoverImage) || usable[0];
  return selected?.url || null;
}

export function HomeRecentCard({ doc, title, date, copy = 'original', onOpen }) {
  const [failedImage, setFailedImage] = useState(null);
  const scope = JSON.stringify([doc.id, doc.revisionId]);
  const imageUrl = homeRecentCardImage(doc);
  const imageKey = JSON.stringify([scope, imageUrl]);
  const showImage = !!imageUrl && failedImage !== imageKey;
  const original = copy === 'original';

  useEffect(() => { setFailedImage(null); }, [scope]);

  return <button type="button" className={`home-flow-card${showImage ? ' has-image' : ''}`}
    onClick={() => onOpen()} onPointerDown={!original ? event => event.preventDefault() : undefined}
    draggable={false} data-flow-original={original ? '' : undefined} data-flow-copy={!original ? copy : undefined}
    aria-hidden={!original ? true : undefined} tabIndex={!original ? -1 : undefined}
    aria-label={original ? `阅读资料：${title}` : undefined}>
    <span className="home-document-type"><FileText size={15}/>{doc.kind || '资料'}{doc.favorite && <Star size={13} weight="fill" aria-label="已收藏"/>}</span>
    <span className="home-flow-card-content">
      <span className="home-flow-card-copy"><h3 title={title}>{title}</h3><p>{doc.summary || '打开资料，继续阅读。'}</p></span>
      {showImage && <span className="home-flow-card-image"><img key={imageKey} src={imageUrl} alt="" loading="lazy" decoding="async" draggable={false} onError={() => setFailedImage(imageKey)}/></span>}
    </span>
    <span className="home-flow-card-footer"><span title={doc.source}>{doc.source || '本地导入'}</span><span>{date}</span><ArrowUpRight size={14}/></span>
  </button>;
}
