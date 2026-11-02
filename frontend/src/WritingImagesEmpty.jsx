import React from 'react';

export default function WritingImagesEmpty({ title = '本文尚未配图', children, description, artwork }) {
  return <div className="writing-images-empty writing-images-empty-illustrated" data-writing-images-empty>
    <svg className="writing-images-empty-art" width="180" height="114" viewBox="0 0 180 114" fill="none" aria-hidden="true" focusable="false">
      {artwork || <><rect x="25" y="16" width="112" height="78" rx="7" transform="rotate(-8 25 16)" fill="var(--surface)" stroke="currentColor" opacity=".32"/>
      <rect x="40" y="18" width="116" height="79" rx="7" fill="var(--surface)" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="124" cy="40" r="8" fill="currentColor" opacity=".2"/>
      <path d="m51 84 28-31 24 23 15-15 27 23H51Z" fill="currentColor" opacity=".12"/>
      <path d="m51 84 28-31 24 23 15-15 27 23" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M17 94h12m-6-6v12m129-88h10m-5-5v10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity=".35"/></>}
    </svg>
    <strong>{title}</strong>{description && <p>{description}</p>}{children}
  </div>;
}
