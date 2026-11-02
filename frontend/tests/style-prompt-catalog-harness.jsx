import React from 'react';
import { createRoot } from 'react-dom/client';
import { PromptWorkspace } from '../src/PromptWorkspace';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/workspace-panels.css';
import '../src/theme-layout.css';

const theme = new URLSearchParams(location.search).get('theme');
if (['jade', 'glass', 'civic'].includes(theme)) document.documentElement.dataset.theme = theme;

// This page only mounts the production component. The browser runner provides
// an isolated, read-only catalog through an injected fetch implementation.
// Without that injection, refuse every request rather than reading live data.
if (!window.__styleCatalogMock) window.fetch = async () => { throw new Error('未提供隔离智能指令目录，禁止访问实际服务。'); };
createRoot(document.getElementById('style-prompt-catalog-root')).render(
  <div className="workspace-shell" style={{ display: 'block', height: '100vh', boxSizing: 'border-box' }}>
    <main className="app-main view-prompts" style={{ height: '100%', minHeight: 0 }}>
      <div className="workspace-view" style={{ height: '100%', minHeight: 0 }}><PromptWorkspace/></div>
    </main>
  </div>,
);
