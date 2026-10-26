import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { installGlobalErrorHandlers } from './lib/errorReporting';
import { loadMath, preloadMarkdown } from './components/parts/Markdown';
import { preloadCharts } from './components/parts/ChartView';
import { scheduleIdlePrefetch } from './lib/idlePrefetch';
import { adoptBootDraft, restoreBootFocus } from './lib/bootShell';
import { installTrustedTypes } from './security/trustedTypes';

installGlobalErrorHandlers();
// Before anything renders: the CSP's Trusted Types `default` policy (accepts only '').
installTrustedTypes();
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

// The answer renderer, KaTeX and Vega are lazy chunks; warm them at idle once the shell has painted, so the first
// answer never waits on them or compiles them mid-stream (lib/idlePrefetch.ts).
scheduleIdlePrefetch([preloadMarkdown, loadMath, preloadCharts], { conn: (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection });
