import { useState } from 'react';
import { ArrowUp, Check, ChevronRight, CircleSlash, HelpCircle, Info, Lock, Sparkles, RotateCcw, Square } from 'lucide-react';
import clsx from 'clsx';
import type { ChatRequest } from '../../api/contract';
import { Markdown } from '../parts/Markdown';
import { Inline } from '../parts/misc';
import { modelName } from './normalize';
import type { AbstainPart, ClarificationPart, EscalationEvent } from './provisional';
import { useExpired } from '../../lib/useExpired';

export type Resume = NonNullable<ChatRequest['resume']>;
/** Send a new user turn; `resume` answers a pending clarification (CONTRACT.md §6). */
export type OnReply = (text: string, resume?: Resume) => void;

const ABSTAIN_TITLE: Record<string, string> = {
  out_of_scope: "That's outside what I can answer from this data",
  phi_request: "I can't share patient-identifying details",
  data_unavailable: "The data needed for this isn't available",
  ambiguous: 'I need a clearer question to answer this',
  low_confidence: "I'm not confident enough to give you a number",
  unsupported: "I can't do that kind of analysis yet",
  policy: "I can't help with that request",
};

/** "Can't answer" is an outcome, not an error: calm card, reason, details, rephrasings. */
export function AbstainCard({ part, onReply }: { part: AbstainPart; onReply?: OnReply }) {
  const Icon = part.category === 'phi_request' || part.category === 'policy' ? Lock : CircleSlash;
  return (
    <div className="my-4 rounded-2xl border border-line bg-elev px-4 py-3.5 font-sans text-sm" role="note" aria-label="Cannot answer" data-testid="abstain" data-category={part.category}>
      <div className="flex items-center gap-2 font-medium text-fg"><Icon size={16} className="shrink-0 text-fg-muted" aria-hidden /> {ABSTAIN_TITLE[part.category] ?? "I can't answer this from the data I have"}</div>
      <div className="abstain-reason mt-1 text-fg-muted [&_.prose-chat]:text-sm [&_.prose-chat]:leading-relaxed [&_p]:my-1"><Markdown text={part.reason} /></div>
      {part.details && (
        <details className="group/needed mt-1 text-[13px]" data-testid="abstain-details">
          {/* Same chevron disclosure as the timeline and "View SQL", not the browser's ▶ marker. */}
          <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-fg-subtle hover:text-fg [&::-webkit-details-marker]:hidden">
            What would be needed<ChevronRight size={13} className="transition-transform group-open/needed:rotate-90" aria-hidden />
          </summary>
          <div className="mt-1 text-fg-muted [&_.prose-chat]:text-[13px] [&_.prose-chat]:leading-relaxed [&_p]:my-1"><Markdown text={part.details} /></div>
        </details>
      )}
      {part.suggestions.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-medium text-fg-subtle">Try asking</div>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {part.suggestions.map((s) => (
              <button key={s.id} type="button" onClick={() => onReply?.(s.text)} disabled={!onReply} className="rounded-full border border-line bg-bg px-3 py-1.5 text-left text-[13px] text-fg hover:bg-hover disabled:opacity-50" data-testid="rephrase-chip">{s.text}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Clarifying question: choice chips (single or multi) plus a free reply. Answering resumes the run. */
export function ClarifyCard({ part, onReply, active }: { part: ClarificationPart; onReply?: OnReply; active: boolean }) {
  const [answer, setAnswer] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [text, setText] = useState('');
  const expired = useExpired(part.expiresAt);
  const done = answer !== null || !!part.answered || !active || expired;
  const choices = part.choices ?? [];
  const submit = (ids: string[], free?: string) => {
    if (done) return;
    const labels = ids.map((id) => choices.find((c) => c.id === id)?.label ?? id);
    const reply = free?.trim() || labels.join(', ');
    if (!reply) return;
    setAnswer(reply);
    onReply?.(reply, { clarificationId: part.clarificationId, ...(ids.length ? { choiceIds: ids } : {}), ...(free?.trim() ? { text: free.trim() } : {}) });
  };
  const chip = (id: string) => (part.multiSelect ? setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id])) : submit([id]));
  return (
    <div className={clsx('my-4 rounded-2xl border px-4 py-3.5 font-sans text-sm', done ? 'border-line bg-bg' : 'border-line-strong bg-elev shadow-sm')} role="group" aria-label="Clarifying question" data-testid="clarify">
      <div className="flex items-start gap-2 text-fg"><HelpCircle size={16} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden /><span className="font-medium leading-snug [&_code]:rounded [&_code]:bg-hover [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.9em]" data-testid="clarify-question"><Inline text={part.question} /></span></div>
      {part.multiSelect && !done && <div className="mt-1 pl-6 text-xs text-fg-subtle">Choose one or more</div>}
      {choices.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Choices">
          {choices.map((o) => {
            const on = part.multiSelect ? picked.includes(o.id) : answer === o.label;
            return (
              <button key={o.id} type="button" disabled={done} aria-pressed={on} onClick={() => chip(o.id)} title={o.description}
                className={clsx('inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-left text-[13px] transition-colors', on ? 'border-fg bg-fg text-bg' : 'border-line bg-bg text-fg enabled:hover:bg-hover disabled:opacity-50')} data-testid="clarify-chip">
                {part.multiSelect && on && <Check size={12} aria-hidden />}
                {o.label}{o.description && <span className={clsx('ml-1', on ? 'text-bg/70' : 'text-fg-subtle')}>· {o.description}</span>}
              </button>
            );
          })}
          {part.multiSelect && !done && (
            <button type="button" disabled={!picked.length} onClick={() => submit(picked)} className="rounded-full bg-fg px-3 py-1.5 text-[13px] text-bg disabled:opacity-30" data-testid="clarify-submit">Continue</button>
          )}
        </div>
      )}
      {!done && part.allowFreeText && (
        <form className="mt-3 flex items-center gap-2 rounded-full border border-line bg-bg py-1 pl-3.5 pr-1 focus-within:border-fg-subtle" onSubmit={(e) => { e.preventDefault(); submit(part.multiSelect ? picked : [], text); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Answer in your own words" placeholder={choices.length ? 'Or answer in your own words' : 'Type your answer'} className="min-w-0 flex-1 bg-transparent py-1 outline-none" />
          <button type="submit" disabled={!text.trim()} aria-label="Send answer" className="flex h-7 w-7 items-center justify-center rounded-full bg-fg text-bg disabled:opacity-30"><ArrowUp size={15} /></button>
        </form>
      )}
      {answer !== null && <div className="mt-2 text-xs text-fg-subtle" data-testid="clarify-answered">You answered “{answer}”. Continuing…</div>}
      {answer === null && part.answered && <div className="mt-2 text-xs text-fg-subtle" data-testid="clarify-answered">Answered</div>}
      {answer === null && !part.answered && expired && <div className="mt-2 text-xs text-fg-subtle">This question expired. Ask again to continue.</div>}
    </div>
  );
}

const TRIGGER: Record<string, string> = {
  verifier_fail: 'a check failed', low_confidence: 'low confidence', repair_exhausted: 'repairs ran out', complexity: 'a complex question',
  disagreement: 'candidates disagreed', user: 'you asked',
};

/** Small inline pill: the run was handed to a larger model; the reason opens on click. */
export function EscalationNote({ ev }: { ev: EscalationEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-2 font-sans text-xs" data-testid="escalation">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-elev px-2.5 py-1 text-fg-muted hover:text-fg">
        <Sparkles size={12} aria-hidden /> <span className="truncate">Escalated to {modelName(ev.to.model)}</span>
        <span className="hidden text-fg-subtle sm:inline">· {TRIGGER[ev.trigger] ?? ev.trigger}</span> <Info size={12} aria-hidden />
      </button>
      {open && <p className="mt-1.5 max-w-prose pl-1 leading-relaxed text-fg-muted">{modelName(ev.from.model)} → {modelName(ev.to.model)}: {ev.reason}</p>}
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
