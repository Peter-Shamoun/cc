import { useEffect, useId, useState, type ReactNode } from 'react';
import { Braces, Check, Copy, FileText, Link2, Lock, Printer } from 'lucide-react';
import { Dialog } from './primitives';
import type { ShareExpiryDays, ShareLink } from '../contract/v1.gen';
import { shareDaysLeft } from '../contract/share';
import { serverNow } from '../lib/serverClock';

/** Contract 1.11.0 §17 share calls for the open chat (absent when the chat isn't on a server, e.g. the offline mock). */
export interface ShareLinkApi {
  get: () => Promise<ShareLink | null>;
  /** `expiresInDays` (1.15.0) restarts the lifetime; omit it to keep the current expiry. */
  set: (includeData: boolean, expiresInDays?: ShareExpiryDays) => Promise<ShareLink>;
  revoke: () => Promise<boolean>;
  /** Copies the absolute link; resolves false when the clipboard is unavailable. */
  copy: (url: string) => Promise<boolean>;
}

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
  link?: ShareLinkApi;
  /** `Capabilities.shareExpiry` (1.15.0). Absent from older servers, whose links never expire. */
  expiry?: { options: ShareExpiryDays[]; default: ShareExpiryDays };
}

/**
 * Share & export, in the ChatGPT/Claude shape: a preview of what is being shared, a link section, and export actions.
 * The link is read-only and org-only (contract 1.11.0). Result data is withheld unless the owner opts in. Without a
 * server (offline mock), "Create link" stays disabled and says why.
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

        {p.link ? <LinkSection api={p.link} expiry={p.expiry} /> : (
          <section aria-labelledby="share-link-h">
            <h3 id="share-link-h" className="mb-2 text-sm font-medium text-fg-muted">Link</h3>
            <div className="flex items-center gap-3 rounded-xl border border-line px-3 py-2.5">
              <Lock size={16} aria-hidden className="shrink-0 text-fg-subtle" />
              <div className="min-w-0 flex-1">
                <div>Only people in your organization</div>
                <div id="share-link-note" className="text-xs text-fg-subtle">Links need the analyst server. This chat only lives in this tab.</div>
              </div>
              <button type="button" aria-disabled="true" aria-describedby="share-link-note" data-testid="create-link" onClick={(e) => e.preventDefault()}
                className="flex shrink-0 cursor-not-allowed items-center gap-1.5 rounded-full bg-fg/10 px-3 py-1.5 text-fg-subtle">
                <Link2 size={14} aria-hidden /> Create link
              </button>
            </div>
          </section>
        )}

        <section aria-labelledby="share-export-h">
          <h3 id="share-export-h" className="mb-2 text-sm font-medium text-fg-muted">Export</h3>
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

type LinkState = { kind: 'loading' } | { kind: 'none' } | { kind: 'shared'; link: ShareLink } | { kind: 'error'; message: string };

/** Create, copy, scope (with/without data) and revoke the org-only link. Never logs or displays more of the id than the URL. */
function LinkSection({ api, expiry }: { api: ShareLinkApi; expiry?: ShareDialogProps['expiry'] }) {
  const [state, setState] = useState<LinkState>({ kind: 'loading' });
  const [includeData, setIncludeData] = useState(false);
  const [days, setDays] = useState<ShareExpiryDays | undefined>(expiry?.default);
  /** 1.15.0: turning data on issued a new id and revoked the old one; say so until the dialog closes. */
  const [rotated, setRotated] = useState(false);
  const [working, setWorking] = useState(false);
  const [copied, setCopied] = useState(false);
  const id = useId();
  useEffect(() => {
    let live = true;
    api.get().then(
      (l) => { if (!live) return; setState(l ? { kind: 'shared', link: l } : { kind: 'none' }); if (l) { setIncludeData(l.includeData); if (l.expiresInDays) setDays(l.expiresInDays as ShareExpiryDays); } },
      () => { if (live) setState({ kind: 'error', message: "Couldn't check this chat's link." }); },
    );
    return () => { live = false; };
  }, [api]);
  const act = async (f: () => Promise<void>, failure: string) => {
    setWorking(true);
    try { await f(); } catch { setState({ kind: 'error', message: failure }); } finally { setWorking(false); }
  };
  const absolute = (l: ShareLink) => new URL(l.url, window.location.origin).href;
  const copy = async (l: ShareLink) => { const ok = await api.copy(absolute(l)); setCopied(ok); if (ok) window.setTimeout(() => setCopied(false), 2000); };
  const create = () => act(async () => { const l = await api.set(includeData, days); setRotated(false); setState({ kind: 'shared', link: l }); await copy(l); }, "Couldn't create the link. Try again.");
  const update = (next: boolean, d?: ShareExpiryDays) => act(async () => {
    const l = await api.set(next, d);
    if (l.rotated) { setRotated(true); setCopied(false); }
    setState({ kind: 'shared', link: l });
  }, "Couldn't update the link. Try again.");
  // Widening rotates the link, and a rotated share would fall back to 30 days, so carry the chosen lifetime over.
  const toggle = (next: boolean) => {
    setIncludeData(next);
    if (state.kind === 'shared') void update(next, next && !state.link.includeData ? days : undefined);
  };
  const changeDays = (d: ShareExpiryDays) => { setDays(d); if (state.kind === 'shared') void update(includeData, d); };
  const revoke = () => act(async () => { await api.revoke(); setState({ kind: 'none' }); setCopied(false); setRotated(false); }, "Couldn't stop sharing. Try again.");
  const shared = state.kind === 'shared' ? state.link : null;
  // Server clock, so a skewed device clock doesn't misstate how long the link has left.
  const left = shared ? shareDaysLeft(shared, serverNow()) : null;
  const fmt = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return (
    <section aria-labelledby="share-link-h" data-testid="share-link" aria-busy={state.kind === 'loading' || working}>
      <h3 id="share-link-h" className="mb-2 text-sm font-medium text-fg-muted">Link</h3>
      <div className="rounded-xl border border-line">
        <div className="flex items-center gap-3 px-3 py-2.5">
          <Lock size={16} aria-hidden className="shrink-0 text-fg-subtle" />
          <div className="min-w-0 flex-1">
            <div>Only people in your organization</div>
            <div id={`${id}-note`} className="text-xs text-fg-subtle">
              {shared ? 'Anyone signed in to your organization with the link can read this chat, including later messages. Read-only.' : 'Read-only. Viewers must be signed in to your organization.'}
            </div>
          </div>
          {!shared && (
            <button type="button" onClick={create} disabled={working || state.kind === 'loading'} aria-describedby={`${id}-note`} data-testid="create-link"
              className="flex shrink-0 items-center gap-1.5 rounded-full bg-fg px-3 py-1.5 text-bg hover:opacity-90 disabled:opacity-50">
              <Link2 size={14} aria-hidden /> {state.kind === 'loading' ? 'Checking…' : 'Create link'}
            </button>
          )}
        </div>
        {shared && (
          <div className="flex items-center gap-2 border-t border-line px-3 py-2.5">
            <input readOnly value={absolute(shared)} aria-label="Share link" data-testid="share-url" onFocus={(e) => e.currentTarget.select()}
              className="min-w-0 flex-1 truncate rounded-lg bg-muted px-2.5 py-1.5 font-mono text-[12px] text-fg-muted outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]" />
            <button type="button" onClick={() => void copy(shared)} data-testid="copy-link"
              className="flex shrink-0 items-center gap-1.5 rounded-full border border-line px-3 py-1.5 hover:bg-hover">
              {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />} {copied ? 'Copied' : 'Copy link'}
            </button>
          </div>
        )}
        {shared && rotated && (
          <p className="border-t border-line px-3 py-2 text-xs text-fg-muted" data-testid="share-rotated">
            This is a new link. The old link no longer works, so share this one again.
          </p>
        )}
        <div className="relative flex items-start gap-3 border-t border-line px-3 py-2.5">
          <span className="min-w-0 flex-1">
            <label htmlFor={`${id}-sw`} className="block cursor-pointer after:absolute after:inset-0">Include result data</label>
            <span id={`${id}-data`} className="block text-xs text-fg-subtle">
              {includeData ? 'Table previews (up to 50 rows), charts and SQL are shared. CSV downloads never are.' : shared ? 'Off: questions, answers and steps only. SQL is not shared. Turning this on replaces the link.' : 'Off: questions, answers and steps only. SQL is not shared, and tables show their size, not their rows.'}
            </span>
          </span>
          <span className="relative mt-0.5 flex shrink-0">
          <input id={`${id}-sw`} type="checkbox" role="switch" aria-checked={includeData} checked={includeData} disabled={working} onChange={(e) => toggle(e.target.checked)} aria-describedby={`${id}-data`} data-testid="share-include-data"
            className="peer absolute inset-0 z-10 m-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-default" />
          <span aria-hidden className="relative h-5 w-9 shrink-0 rounded-full bg-fg/20 transition-colors peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-[var(--focus)] peer-disabled:opacity-50 after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow-sm after:transition-transform peer-checked:after:translate-x-4 peer-checked:after:bg-accent-fg" />
          </span>
        </div>
        {expiry && expiry.options.length > 0 && (
          <div className="flex items-center justify-between gap-3 border-t border-line px-3 py-2.5">
            <label htmlFor={`${id}-exp`}>Link expires after</label>
            <select id={`${id}-exp`} value={days} disabled={working} onChange={(e) => changeDays(Number(e.target.value) as ShareExpiryDays)} data-testid="share-expiry"
              className="rounded-md border border-line bg-bg px-2 py-1 text-sm text-fg hover:bg-hover focus-visible:outline-2 focus-visible:outline-[var(--focus)] disabled:opacity-50">
              {expiry.options.map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </div>
        )}
        {shared && (
          <div className="flex items-center justify-between gap-3 border-t border-line px-3 py-2 text-xs text-fg-subtle">
            <span data-testid="share-dates">Created {fmt(shared.createdAt)}{shared.expiresAt && left !== null ? (left === 0 ? ' · Expired' : ` · Expires ${fmt(shared.expiresAt)}, in ${left} ${left === 1 ? 'day' : 'days'}`) : ''}</span>
            <button type="button" onClick={revoke} disabled={working} data-testid="revoke-link" className="rounded-md px-2 py-1 font-medium text-[var(--danger)] hover:bg-hover disabled:opacity-50">Stop sharing</button>
          </div>
        )}
      </div>
      <p role="status" className={state.kind === 'error' ? 'mt-2 text-xs text-[var(--danger)]' : 'sr-only'}>
        {state.kind === 'error' ? state.message : copied ? 'Link copied' : rotated ? 'New link created. The old link no longer works.' : shared ? 'Link active' : ''}
      </p>
    </section>
  );
}
