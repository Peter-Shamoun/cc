import { useState } from 'react';
import { ArrowUp, CircleSlash, HelpCircle, Info, Sparkles, RotateCcw, Square } from 'lucide-react';
import clsx from 'clsx';
import type { AbstainPart, ClarifyPart, EscalationPart } from './provisional';

/** "Can't answer" is an outcome, not an error: calm card, reason, what's missing, rephrasings. */
export function AbstainCard({ part, onSend }: { part: AbstainPart; onSend?: (t: string) => void }) {
  return (
    <div className="my-4 rounded-2xl border border-line bg-elev px-4 py-3.5 font-sans text-sm" role="note" aria-label="Cannot answer" data-testid="abstain">
      <div className="flex items-center gap-2 font-medium text-fg"><CircleSlash size={16} className="text-fg-muted" aria-hidden /> {part.title ?? "I can't answer this from the data I have"}</div>
      <p className="mt-1.5 leading-relaxed text-fg-muted">{part.reason}</p>
      {part.needed && part.needed.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-medium uppercase tracking-wide text-fg-subtle">What would be needed</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-fg-muted">{part.needed.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
      {part.suggestions && part.suggestions.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Try asking</div>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {part.suggestions.map((s) => (
              <button key={s} type="button" onClick={() => onSend?.(s)} disabled={!onSend} className="rounded-full border border-line bg-bg px-3 py-1.5 text-left text-[13px] text-fg hover:bg-hover disabled:opacity-50" data-testid="rephrase-chip">{s}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Clarifying question: choice chips plus a free reply. Answering resumes the run. */
export function ClarifyCard({ part, onSend, active }: { part: ClarifyPart; onSend?: (t: string) => void; active: boolean }) {
  const [answer, setAnswer] = useState<string | null>(part.answered ?? null);
  const [text, setText] = useState('');
  const done = answer !== null || !active;
  const pick = (v: string) => { if (done || !v.trim()) return; setAnswer(v); onSend?.(v); };
  return (
    <div className={clsx('my-4 rounded-2xl border px-4 py-3.5 font-sans text-sm', done ? 'border-line bg-bg' : 'border-line-strong bg-elev shadow-sm')} role="group" aria-label="Clarifying question" data-testid="clarify">
      <div className="flex items-start gap-2 text-fg"><HelpCircle size={16} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden /><span className="font-medium leading-snug">{part.question}</span></div>
      <div className="mt-3 flex flex-wrap gap-2" role="list">
        {part.options.map((o) => {
          const v = o.value ?? o.label;
          const chosen = answer === v;
          return (
            <button key={o.id} type="button" role="listitem" disabled={done} aria-pressed={chosen} onClick={() => pick(v)} title={o.description}
              className={clsx('rounded-full border px-3 py-1.5 text-left text-[13px] transition-colors', chosen ? 'border-fg bg-fg text-bg' : 'border-line bg-bg text-fg enabled:hover:bg-hover disabled:opacity-50')} data-testid="clarify-chip">
              {o.label}{o.description && <span className={clsx('ml-1', chosen ? 'text-bg/70' : 'text-fg-subtle')}>· {o.description}</span>}
            </button>
          );
        })}
      </div>
      {!done && part.allowFreeText !== false && (
        <form className="mt-3 flex items-center gap-2 rounded-full border border-line bg-bg py-1 pl-3.5 pr-1 focus-within:border-fg-subtle" onSubmit={(e) => { e.preventDefault(); pick(text); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Answer in your own words" placeholder="Or answer in your own words" className="min-w-0 flex-1 bg-transparent py-1 outline-none" />
          <button type="submit" disabled={!text.trim()} aria-label="Send answer" className="flex h-7 w-7 items-center justify-center rounded-full bg-fg text-bg disabled:opacity-30"><ArrowUp size={15} /></button>
        </form>
      )}
      {answer !== null && <div className="mt-2 text-xs text-fg-subtle" data-testid="clarify-answered">You answered “{answer}”. Continuing…</div>}
    </div>
  );
}

/** Small inline pill: the run was handed to a larger model, with the reason on hover/focus. */
export function EscalationNote({ part }: { part: EscalationPart }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-2 font-sans text-xs" data-testid="escalation">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-elev px-2.5 py-1 text-fg-muted hover:text-fg">
        <Sparkles size={12} aria-hidden /> Escalated to a larger model <span className="text-fg-subtle">· {part.from} → {part.to}</span> <Info size={12} aria-hidden />
      </button>
      {open && <p className="mt-1.5 max-w-prose pl-1 leading-relaxed text-fg-muted">{part.reason}</p>}
    </div>
  );
}

/** Stopped by the user: subtle line with Retry, like ChatGPT's "You stopped this response". */
export function StoppedNote({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="my-2 flex items-center gap-2 font-sans text-sm text-fg-subtle" data-testid="stopped">
      <Square size={12} aria-hidden /> You stopped this response
      {onRetry && <button type="button" onClick={onRetry} className="inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-0.5 text-xs text-fg-muted hover:bg-hover hover:text-fg"><RotateCcw size={11} /> Retry</button>}
    </div>
  );
}

/** Placeholder while a query runs and its table hasn't arrived. */
export function TableSkeleton() {
  return (
    <div className="my-4 overflow-hidden rounded-xl border border-line" aria-hidden data-testid="table-skeleton">
      <div className="h-9 border-b border-line bg-muted/60" />
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-4 border-b border-line px-3 py-2.5 last:border-0">
          {[40, 18, 18, 14].map((w, j) => <div key={j} className="skeleton h-3 rounded" style={{ width: `${w - i * 2 + j}%` }} />)}
        </div>
      ))}
    </div>
  );
}
