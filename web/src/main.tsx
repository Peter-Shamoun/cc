import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { installGlobalErrorHandlers } from './lib/errorReporting';
import { preloadMarkdown } from './components/parts/Markdown';
import { adoptBootDraft, restoreBootFocus } from './lib/bootShell';

installGlobalErrorHandlers();
// Text typed into the static boot composer before JS arrived becomes the new-chat draft (QA-P8).
adoptBootDraft();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
requestAnimationFrame(() => requestAnimationFrame(() => restoreBootFocus()));

// The answer renderer is its own chunk; fetch it once the shell has painted so the first answer never waits.
const idle = (cb: () => void) => ('requestIdleCallback' in window ? window.requestIdleCallback(cb, { timeout: 3000 }) : setTimeout(cb, 1500));
idle(() => { preloadMarkdown().catch(() => undefined); });
