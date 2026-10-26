import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import clsx from 'clsx';
import { Check } from 'lucide-react';
import type { ClarificationPart, ClarifyAnswer, ClarifyQuestion } from '../../api/contract';
import { clarifyRequest, clarifySummary } from '../../contract/clarify';
import { Inline } from '../parts/misc';
import { useExpired } from '../../lib/useExpired';
import type { OnReply } from './States';

/**
 * Multi-question clarification, modelled on Claude's interactive inputs / AskUserQuestion (Peter 12:14 AM):
 * 1–4 questions, ONE AT A TIME, docked at the bottom of the chat just above the composer. Each question has a short
 * header (≤12 chars, shown as a quiet tab), 2–4 options with a one-line description, multi-select where allowed, an
 * "Other" free-text row and Skip. Typing in the composer still works (shell routes it as the answer, QA-A30).
 * Neutral by rule: one hairline-bordered panel, no colour accents, no animation beyond the app's focus ring.
 * Contract 1.23.0 (CONTRACT.md §6.1): `ClarificationPart.questions`; the reply is `resume.answers` (one
 * ClarifyAnswer per question) and the visible user turn is `clarifySummary` ("Metric: …; Period: no preference").
 * Placement: portals into `#cc-composer-dock` when the shell provides it; until then it renders at the end of the
 * last answer, sticky to the bottom of the thread, which reads the same when the reader is at the bottom.
 */
export type MultiClarification = ClarificationPart & { questions: ClarifyQuestion[] };

export const isMultiClarification = (p: ClarificationPart): p is MultiClarification =>
  Array.isArray((p as { questions?: unknown }).questions) && (p as MultiClarification).questions.length > 0;

export type Ans = { picked: string[]; other: string; skipped: boolean };
const empty = (): Ans => ({ picked: [], other: '', skipped: false });
export const DOCK_ID = 'cc-composer-dock';
/** Claude caps a call at 4 questions and 4 options; headers are chips of ≤12 characters. */
const MAX_Q = 4, MAX_OPT = 4, MAX_HEADER = 12;
export const shortHeader = (h: string) => (h.length > MAX_HEADER ? `${h.slice(0, MAX_HEADER - 1)}…` : h);

export function answerLabel(q: ClarifyQuestion, a: Ans | undefined): string {
  if (!a || a.skipped) return 'skipped';
  const labels = a.picked.map((id) => q.options.find((o) => o.id === id)?.label ?? id);
  if (a.other.trim()) labels.push(a.other.trim());
  return labels.join(', ') || 'skipped';
}

/** The contract answers (one per question, in order). Single-select: Other replaces a pick. Nothing chosen = skipped. */
export function toAnswers(qs: readonly ClarifyQuestion[], answers: Record<string, Ans>): ClarifyAnswer[] {
  return qs.map((q) => {
    const a = answers[q.id];
    const other = a?.other.trim() ?? '';
    const picked = a && !a.skipped ? (q.multiSelect ? a.picked : other ? [] : a.picked.slice(0, 1)) : [];
    if (!a || a.skipped || (!picked.length && !other)) return { questionId: q.id, skipped: true };
    return { questionId: q.id, ...(picked.length ? { choiceIds: picked } : {}), ...(other ? { otherText: other } : {}) };
  });
}

function useDock(active: boolean) {
  const [el, setEl] = useState<HTMLElement | null>(null);
  useEffect(() => { setEl(active ? document.getElementById(DOCK_ID) : null); }, [active]); // eslint-disable-line react-hooks/set-state-in-effect -- reads a DOM node the shell owns, once it exists
  return el;
}

export function ClarifyForm({ part, onReply, active }: { part: MultiClarification; onReply?: OnReply; active: boolean }) {
  const qs = part.questions.slice(0, MAX_Q).map((q) => ({ ...q, options: q.options.slice(0, MAX_OPT) }));
  const [answers, setAnswers] = useState<Record<string, Ans>>(() => Object.fromEntries(qs.map((q) => [q.id, empty()])));
  const [step, setStep] = useState(0);
  const [sent, setSent] = useState<string | null>(null);
  const expired = useExpired(part.expiresAt);
  const done = sent !== null || !!part.answered || !active || expired;
  const dock = useDock(!done);
  const panel = useRef<HTMLDivElement>(null);
  const q = qs[Math.min(step, qs.length - 1)];
  const a = answers[q.id];

  const finish = (all: Record<string, Ans>) => {
    const list = toAnswers(qs, all);
    const text = clarifySummary(qs, list);
    setSent(text);
    onReply?.(text, clarifyRequest(part.clarificationId, list));
  };
  /** Record this question's answer, then move on (or send after the last one). */
  const commit = (next: Ans) => {
    const all = { ...answers, [q.id]: next };
    setAnswers(all);
    if (step >= qs.length - 1) finish(all);
    else setStep(step + 1);
  };
  // Focus follows the question, like Claude's panel (first option of the new question).
  useEffect(() => { if (!done) panel.current?.querySelector<HTMLElement>('[data-testid="clarify-option"]')?.focus({ preventScroll: true }); }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (id: string) => {
    if (q.multiSelect) setAnswers((all) => ({ ...all, [q.id]: { ...a, skipped: false, picked: a.picked.includes(id) ? a.picked.filter((x) => x !== id) : [...a.picked, id] } }));
    else commit({ picked: [id], other: '', skipped: false });
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).tagName === 'INPUT') return;
    const n = Number(e.key);
    if (n >= 1 && n <= q.options.length) { e.preventDefault(); pick(q.options[n - 1].id); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const rows = [...(panel.current?.querySelectorAll<HTMLElement>('[data-testid="clarify-option"], [data-testid="clarify-other"]') ?? [])];
      const i = rows.indexOf(document.activeElement as HTMLElement);
      const to = rows[(i + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length];
      if (to) { e.preventDefault(); to.focus(); }
    }
  };

  if (done) {
    // Settled: one muted line in the answer, no controls (Claude collapses the inputs to what you chose).
    const text = sent ?? (part.answered ? 'Answered' : expired ? 'These questions expired. Ask again to continue.' : null);
    return (
      <div className="my-3 font-sans text-sm" data-testid="clarify-form" data-done="true">
        {part.question && <p className="text-fg" data-testid="clarify-question"><Inline text={part.question} /></p>}
        {text && <p className="mt-1 text-xs text-fg-subtle" data-testid="clarify-answered">{text}</p>}
      </div>
    );
  }

  const ui = (
    // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- number keys and arrows for the option list (Claude's panel keys)
    <div ref={panel} role="group" aria-label="Clarifying questions" onKeyDown={onKey} data-testid="clarify-form" data-docked={dock ? 'true' : undefined}
      className={clsx('rounded-2xl border border-line bg-bg font-sans text-sm', !dock && 'sticky bottom-2 z-[2] my-3')}>
      {qs.length > 1 && (
        <div className="flex items-center gap-1 border-b border-line px-3 pt-2" role="tablist" aria-label="Questions">
          {qs.map((x, i) => {
            const got = i < step;
            return (
              <button key={x.id} type="button" role="tab" aria-selected={i === step} disabled={i > step} onClick={() => setStep(i)}
                className={clsx('-mb-px inline-flex items-center gap-1 border-b px-2 pb-1.5 text-xs', i === step ? 'border-fg text-fg' : 'border-transparent text-fg-subtle enabled:hover:text-fg')}
                data-testid="clarify-tab">
                {got && <Check size={11} aria-hidden />}{shortHeader(x.header)}
              </button>
            );
          })}
          <span className="ml-auto pb-1.5 text-xs tabular-nums text-fg-subtle" aria-hidden>{step + 1} of {qs.length}</span>
        </div>
      )}
      <div className="px-3 pb-2 pt-3" role="tabpanel" aria-label={q.header}>
        <p className="mb-2 px-1 font-medium text-fg" id={`cq-${part.id}-${q.id}`} data-testid="clarify-question">
          <Inline text={q.question} />{q.multiSelect && <span className="ml-1.5 text-xs font-normal text-fg-subtle">Choose any</span>}
        </p>
        <div role={q.multiSelect ? 'group' : 'radiogroup'} aria-labelledby={`cq-${part.id}-${q.id}`} className="space-y-0.5">
          {q.options.map((o, i) => {
            const on = a.picked.includes(o.id);
            return (
              <button key={o.id} type="button" role={q.multiSelect ? 'checkbox' : 'radio'} aria-checked={on} onClick={() => pick(o.id)}
                className={clsx('flex w-full items-start gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-hover', on && 'bg-hover')} data-testid="clarify-option">
                <span className="mt-px w-4 shrink-0 text-xs tabular-nums text-fg-subtle" aria-hidden>{on ? <Check size={13} className="mt-0.5 text-fg" /> : i + 1}</span>
                <span className="min-w-0">
                  <span className="block text-fg">{o.label}</span>
                  {o.description && <span className="block truncate text-xs text-fg-subtle">{o.description}</span>}
                </span>
              </button>
            );
          })}
          <form className="flex items-center gap-3 rounded-lg px-2 py-1 focus-within:bg-hover" onSubmit={(e) => { e.preventDefault(); if (a.other.trim()) commit({ ...a, picked: q.multiSelect ? a.picked : [], other: a.other, skipped: false }); }}>
            <span className="w-4 shrink-0" aria-hidden />
            <input value={a.other} onChange={(e) => setAnswers((all) => ({ ...all, [q.id]: { ...a, other: e.target.value } }))} placeholder="Other…" aria-label={`Other answer: ${q.header}`}
              className="min-w-0 flex-1 bg-transparent py-0.5 text-fg outline-none placeholder:text-fg-subtle" data-testid="clarify-other" />
          </form>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 pb-3">
        <span className="text-xs text-fg-subtle">Or type a reply below</span>
        <button type="button" onClick={() => commit({ picked: [], other: '', skipped: true })} className="ml-auto rounded-lg px-2.5 py-1 text-[13px] text-fg-muted hover:bg-hover hover:text-fg" data-testid="clarify-skip">Skip</button>
        {(q.multiSelect || a.other.trim()) && (
          <button type="button" disabled={!a.picked.length && !a.other.trim()} onClick={() => commit(a)} className="rounded-lg bg-fg px-3 py-1 text-[13px] font-medium text-bg disabled:opacity-30" data-testid="clarify-next">
            {step >= qs.length - 1 ? 'Submit' : 'Next'}
          </button>
        )}
      </div>
    </div>
  );
  return (
    <>
      {part.question && <p className="my-3 font-sans text-fg" data-testid="clarify-intro"><Inline text={part.question} /></p>}
      {dock ? createPortal(ui, dock) : ui}
    </>
  );
}
