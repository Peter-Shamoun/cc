import { useState, type ReactNode } from 'react';
import { Braces, Copy, FileText, Link2, Lock, Printer } from 'lucide-react';
import { Dialog } from './primitives';

export interface ShareDialogProps {
  title: string;
  /** First question, shown in the preview card (synthetic data in the demo; never put in a URL). */
  firstQuestion?: string;
  messageCount: number;
  onClose: () => void;
  onCopyMarkdown: () => void;
  onDownloadMarkdown: () => void;
  /** Server JSON export (contract 1.8.0); hidden when the chat only lives in this tab. */
  onDownloadJson?: () => void;
  onPrint: () => void;
}

/**
 * Share & export, in the ChatGPT/Claude shape: a preview of what is being shared, a link section, and export actions.
 * Link sharing is a stub: the demo backend has no share endpoint, so "Create link" stays disabled and says why.
 */
export function ShareDialog(p: ShareDialogProps) {
  const [busy, setBusy] = useState(false);
  const run = (f: () => void | Promise<void>) => async () => { setBusy(true); try { await f(); } finally { setBusy(false); } };
  return (
    <Dialog title="Share chat" onClose={p.onClose} testId="share-dialog" className="max-w-[520px]">
      <div className="space-y-5 px-5 pb-5 pt-3 text-sm">
        <div className="rounded-xl border border-line bg-bg p-4" data-testid="share-preview">
          <div className="truncate font-medium">{p.title}</div>
          {p.firstQuestion && <div className="mt-1 line-clamp-2 text-fg-muted">{p.firstQuestion}</div>}
          <div className="mt-2 text-xs text-fg-subtle">{p.messageCount} {p.messageCount === 1 ? 'message' : 'messages'} · de-identified demo data</div>
        </div>

        <section aria-labelledby="share-link-h">
          <h3 id="share-link-h" className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-subtle">Link</h3>
          <div className="flex items-center gap-3 rounded-xl border border-line px-3 py-2.5">
            <Lock size={16} aria-hidden className="shrink-0 text-fg-subtle" />
            <div className="min-w-0 flex-1">
              <div>Only people in your organization</div>
              <div id="share-link-note" className="text-xs text-fg-subtle">Link sharing isn't available in this demo. Messages sent after you create a link won't be shared.</div>
            </div>
            <button type="button" aria-disabled="true" aria-describedby="share-link-note" data-testid="create-link" onClick={(e) => e.preventDefault()}
              className="flex shrink-0 cursor-not-allowed items-center gap-1.5 rounded-full bg-fg/10 px-3 py-1.5 text-fg-subtle">
              <Link2 size={14} aria-hidden /> Create link
            </button>
          </div>
        </section>

        <section aria-labelledby="share-export-h">
          <h3 id="share-export-h" className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-subtle">Export</h3>
          <div className="grid grid-cols-2 gap-2 max-sm:grid-cols-1">
            <Action icon={<Copy size={16} />} label="Copy as Markdown" onClick={p.onCopyMarkdown} />
            <Action icon={<FileText size={16} />} label="Download Markdown" hint=".md" onClick={p.onDownloadMarkdown} />
            {p.onDownloadJson && <Action icon={<Braces size={16} />} label="Download JSON" hint="Full transcript" disabled={busy} onClick={run(p.onDownloadJson)} />}
            <Action icon={<Printer size={16} />} label="Print or save as PDF" onClick={p.onPrint} />
          </div>
        </section>
      </div>
    </Dialog>
  );
}

function Action({ icon, label, hint, onClick, disabled }: { icon: ReactNode; label: string; hint?: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className="flex items-center gap-2.5 rounded-xl border border-line px-3 py-2.5 text-left hover:bg-hover disabled:opacity-60">
      <span aria-hidden className="shrink-0 text-fg-muted">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {hint && <span aria-hidden className="shrink-0 text-xs text-fg-subtle">{hint}</span>}
    </button>
  );
}
