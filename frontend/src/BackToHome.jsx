import React from 'react';
import { ArrowLeft } from '@phosphor-icons/react';
import './back-to-home.css';

export function BackToHome({ onClick, editor = false }) {
  return <button type="button" className={`workspace-home-return${editor ? ' is-editor' : ''}`} onClick={onClick} aria-label="返回主页" title="返回主页"><ArrowLeft size={18}/></button>;
}
