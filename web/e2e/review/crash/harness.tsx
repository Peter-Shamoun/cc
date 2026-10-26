// Visual harness for the crash screen (dev server only; not part of the app build).
// Query: ?mode=dark|light &palette=neutral|paper &kind=render|chunk|section. Synthetic PHI only.
import ReactDOM from 'react-dom/client';
import '../../../src/index.css';
import { ErrorBoundary } from '../../../src/components/ErrorBoundary';
import { reportError } from '../../../src/lib/errorReporting';

const q = new URLSearchParams(location.search);
document.documentElement.classList.toggle('dark', q.get('mode') === 'dark');
document.documentElement.dataset.palette = q.get('palette') ?? 'neutral';
history.replaceState(null, '', `${location.pathname}${location.search}#/c/c_synthetic01`);
reportError(new TypeError('earlier synthetic failure'), 'promise');
const kind = q.get('kind') ?? 'render';
const Boom = () => {
  throw new Error(kind === 'chunk' ? 'Failed to fetch dynamically imported module: /assets/embed-abc.js' : 'Patient Jane Q Synthetic MRN 4455667788 SSN 123-45-6789');
};
ReactDOM.createRoot(document.getElementById('root')!).render(
  kind === 'section' ? (
    <div className="mx-auto max-w-2xl p-8 font-sans text-fg"><p>Answer text above the broken part.</p><ErrorBoundary scope="section" name="ArtifactPanel"><Boom /></ErrorBoundary><p>Answer text below.</p></div>
  ) : (
    <ErrorBoundary><Boom /></ErrorBoundary>
  ),
);
