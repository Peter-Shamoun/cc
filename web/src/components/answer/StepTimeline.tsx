import { useEffect, useId, useRef, useState } from 'react';
import { AlertTriangle, BarChart3, Check, ChevronDown, ChevronRight, Database, GitCompare, List, ListChecks, Loader2, Search, Sparkles, Telescope, Wrench, X, type LucideIcon } from 'lucide-react';
import clsx from 'clsx';
import { CodeBlock } from '../parts/CodeBlock';
import { fmtMs, summarize, unresolvedFailures } from './normalize';
import type { CheckResult, Step, StepKind } from './provisional';

/** Reasoning folded into the timeline (QA-A10): one "Thought for 1.2s" row, like ChatGPT/Claude. */
export type TimelineReasoning = { summary?: string; durationMs?: number };

const ICON: Record<StepKind, LucideIcon> = { sql: Database, repair: Wrench, check: ListChecks, explore: Telescope, candidates: GitCompare, escalation: Sparkles, chart: BarChart3, schema: List, search: Search, other: Wrench };
const KIND_LABEL: Record<StepKind, string> = { sql: 'Query', repair: 'Repair', check: 'Check', explore: 'Probe', candidates: 'Candidates', escalation: 'Escalation', chart: 'Chart', schema: 'Schema', search: 'Search', other: 'Step' };

/** Ticks while `live`, for the running timer in the header. */
function useElapsed(live: boolean) {
  const [start] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [live]);
  return now - start;
}

/**
 * Claude/ChatGPT-style activity disclosure: one row for the whole think-and-query phase.
 * Live: "Thinking…" with a spinner and running timer; the body lists each step as it runs
 * (each step is a single row keyed by stepId that turns from running to done; QA-A7). Done:
 * auto-collapses to "Thought for 1.2s"; expanding shows the counts ("Ran 2 queries, 1 check"),
 * the reasoning summary, then the steps (QA-A10).
 */
export function StepTimeline({ steps, live, elapsedMs, reasoning = [] }: { steps: Step[]; live: boolean; elapsedMs?: number; reasoning?: TimelineReasoning[] }) {
  const [open, setOpen] = useState(live);
  const touched = useRef(false);
  const wasLive = useRef(live);
  const [mountedLive] = useState(live);
  useEffect(() => {
    if (wasLive.current && !live && !touched.current) setOpen(false);
    if (!wasLive.current && live && !touched.current) setOpen(true);
    wasLive.current = live;
  }, [live]);
  // Ticks while live, then freezes: the frozen value is how long this answer "thought".
  const ticking = useElapsed(live);
  const bodyId = useId();
  const headId = useId();
  const errors = unresolvedFailures(steps);
  // Agent tags only help when more than one agent/model worked on the answer (QA-A6).
  const multiAgent = new Set(steps.map((s) => s.agent).filter(Boolean)).size > 1 || steps.some((s) => s.kind === 'escalation');
  const repaired = steps.filter((s) => s.kind === 'repair' && s.state === 'ok').length;
  const counts = summarize(steps).replace(/ · .*$/, '');
  const worked = steps.reduce((a, b) => a + (b.durationMs ?? 0), 0) + reasoning.reduce((a, r) => a + (r.durationMs ?? 0), 0);
  // QA-A16: never shorter than the longest step it contains (a 2.4s query under "Thought for 1.8s" reads wrong).
  const longest = Math.max(0, ...steps.map((s) => s.durationMs ?? 0), ...reasoning.map((r) => r.durationMs ?? 0));
  const thoughtMs = Math.max(mountedLive ? Math.max(ticking, 0) : elapsedMs ?? worked, longest);
  const stopped = steps.filter((x) => x.state === 'cancelled').length;
  // Collapsed: "Thought for 1.2s" (plus "1 stopped" if any). Expanded: the counts join the header.
  const header = live ? 'Thinking…' : [`Thought for ${fmtMs(Math.max(thoughtMs, 1000))}`, open && steps.length ? counts : stopped ? `${stopped} stopped` : ''].filter(Boolean).join(' · ');
  const notes = reasoning.map((r) => r.summary?.trim()).filter(Boolean) as string[];

  return (
    // QA-A11: a group named by its own header, not N identical "Analysis steps" landmarks per thread.
    <div role="group" aria-labelledby={headId} className="my-3 font-sans text-sm first:mt-0" data-testid="step-timeline" data-live={live || undefined}>
      <button type="button" id={headId} aria-expanded={open} aria-controls={bodyId} onClick={() => { touched.current = true; setOpen((o) => !o); }}
        className="group inline-flex max-w-full items-center gap-2 rounded-lg py-1 text-left text-fg-muted transition-colors hover:text-fg" data-testid="step-summary">
        {live ? <Loader2 size={14} className="shrink-0 animate-spin" aria-hidden /> : <ListChecks size={14} className="shrink-0" aria-hidden />}
        <span className={clsx('truncate', live && 'shimmer')}>{header}</span>
        {live && <span className="shrink-0 tabular-nums text-xs text-fg-subtle" aria-hidden>{fmtMs(Math.max(ticking, 0))}</span>}
        {/* QA-A22: separators so the accessible name reads "Thought for 1s, 2 failed", not "1s2 failed". */}
        {!live && errors > 0 && <span className="shrink-0 rounded-full bg-danger/10 px-1.5 text-[11px] text-danger"><span className="sr-only">, </span>{errors} failed</span>}
        {!live && !errors && repaired > 0 && <span className="sr-only">, all failures repaired</span>}
        {open ? <ChevronDown size={14} className="shrink-0" aria-hidden /> : <ChevronRight size={14} className="shrink-0" aria-hidden />}
      </button>
      <div id={bodyId} hidden={!open} className="step-body">
        {notes.length > 0 && (
          <div className="ml-[6px] mt-1.5 whitespace-pre-line border-l-2 border-line pl-5 leading-relaxed text-fg-muted" data-testid="reasoning-summary">{notes.join('\n\n')}</div>
        )}
        {steps.length > 0 && (
          <ol className="relative ml-[6px] mt-1 border-l border-line pl-5">
            {steps.map((s, i) => <StepRow key={s.id} step={s} last={i === steps.length - 1} showAgent={multiAgent} />)}
          </ol>
        )}
      </div>
    </div>
  );
}

function StepRow({ step, last, showAgent }: { step: Step; last: boolean; showAgent: boolean }) {
  const hasDetail = !!(step.sql || step.error || step.checks?.length || step.candidates?.length || step.message);
  const [open, setOpen] = useState(false);
  const Icon = ICON[step.kind];
  const meta = [step.rowCount !== undefined && `${step.rowCount.toLocaleString()}${step.truncated ? '+' : ''} rows`, step.durationMs !== undefined && fmtMs(step.durationMs)].filter(Boolean).join(' · ');
  const dot = step.state === 'running' || step.state === 'cancelled' ? 'border-fg-subtle bg-bg' : step.state === 'error' ? 'border-danger bg-danger' : step.state === 'warn' ? 'border-[#e8b339] bg-[#e8b339]' : step.kind === 'repair' ? 'border-[var(--callout-bar)] bg-[var(--callout-bar)]' : 'border-line-strong bg-bg';
  return (
    <li className={clsx('relative step-enter', !last && 'pb-1')} data-testid="step" data-kind={step.kind} data-state={step.state}>
      <span className={clsx('absolute -left-[26.5px] top-[9px] flex h-[11px] w-[11px] items-center justify-center rounded-full border-2', dot)} aria-hidden>
        {step.state === 'running' && <span className="h-1 w-1 animate-ping rounded-full bg-fg-subtle" />}
      </span>
      <button type="button" disabled={!hasDetail} aria-expanded={hasDetail ? open : undefined} onClick={() => setOpen((o) => !o)}
        className="flex w-full max-w-full items-center gap-2 rounded-md py-1 text-left text-fg-muted enabled:hover:text-fg">
        <Icon size={14} className="shrink-0" aria-hidden />
        <span className="sr-only">{KIND_LABEL[step.kind]}:</span>
        <span className={clsx('min-w-0 truncate', step.state === 'running' && 'shimmer')}>{step.label}</span>
        {step.attempt !== undefined && step.attempt > 1 && <span className="shrink-0 rounded-full border border-line px-1.5 text-[11px] text-fg-subtle">attempt {step.attempt}</span>}
        {step.state === 'ok' && <Check size={13} className="shrink-0 text-[#10a37f]" aria-label="done" />}
        {step.state === 'error' && <X size={13} className="shrink-0 text-danger" aria-label="failed" />}
        {step.state === 'warn' && <AlertTriangle size={13} className="shrink-0 text-[#b7791f] dark:text-[#e8b339]" aria-label="warning" />}
        {step.state === 'cancelled' && <span className="shrink-0 text-xs text-fg-subtle">stopped</span>}
        {meta && <span className="shrink-0 tabular-nums text-xs text-fg-subtle">{meta}</span>}
        {showAgent && step.agent && <span className="shrink-0 rounded-full border border-line px-1.5 text-[11px] text-fg-subtle">{step.agent}</span>}
        {hasDetail && (open ? <ChevronDown size={13} className="shrink-0" /> : <ChevronRight size={13} className="shrink-0" />)}
      </button>
      {!open && step.kind === 'repair' && step.error && (
        <div className="-mt-0.5 mb-1 truncate pl-[22px] font-mono text-[11.5px] text-danger/80" data-testid="repair-preview">{step.error}</div>
      )}
      {open && hasDetail && (
        <div className="mb-2 mt-0.5 space-y-2 text-fg-muted">
          {step.error && (
            <div className="flex gap-2 rounded-lg border border-danger/25 bg-danger/5 px-3 py-2 text-[13px] text-fg" role="note" data-testid="repair-error">
              <AlertTriangle size={14} className="mt-0.5 shrink-0 text-danger" aria-hidden />
              <span className="min-w-0 break-words font-mono text-xs leading-relaxed">{step.error}</span>
            </div>
          )}
          {step.message && (!step.error || step.kind === 'repair') && <div className="text-[13px]">{step.message}</div>}
          {step.checks && <ChecksList checks={step.checks} />}
          {step.candidates && <CandidateTable candidates={step.candidates} />}
          {step.previousSql && <details className="text-xs"><summary className="cursor-pointer text-fg-subtle hover:text-fg">Previous SQL</summary><CodeBlock code={step.previousSql} language="sql" title="before repair" /></details>}
          {step.sql && <CodeBlock code={step.sql} language="sql" title={step.kind === 'repair' ? 'revised' : 'query'} />}
        </div>
      )}
    </li>
  );
}

function ChecksList({ checks }: { checks: CheckResult[] }) {
  return (
    <ul className="space-y-1 text-[13px]" data-testid="checks">
      {checks.map((c) => (
        <li key={c.name} className="flex items-start gap-2">
          {c.passed ? <Check size={14} className="mt-0.5 shrink-0 text-[#10a37f]" aria-label="passed" /> : c.verdict === 'warn' ? <AlertTriangle size={14} className="mt-0.5 shrink-0 text-[#b7791f] dark:text-[#e8b339]" aria-label="warning" /> : <X size={14} className="mt-0.5 shrink-0 text-danger" aria-label="failed" />}
          <span><span className="text-fg">{c.name}</span>{c.detail && <span className="text-fg-subtle"> · {c.detail}</span>}</span>
        </li>
      ))}
    </ul>
  );
}

function CandidateTable({ candidates }: { candidates: { id: string; label: string; rowCount?: number; score?: number; note?: string; chosen?: boolean; sql?: string }[] }) {
  const [show, setShow] = useState<string | null>(null);
  const shown = candidates.find((c) => c.id === show);
  return (
    <div data-testid="candidates">
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-[13px]">
          <thead className="text-left text-xs text-fg-subtle"><tr><th className="px-3 py-1.5 font-medium">Candidate</th><th className="px-3 py-1.5 text-right font-medium">Rows</th><th className="px-3 py-1.5 text-right font-medium">Score</th><th className="px-3 py-1.5 font-medium">Note</th></tr></thead>
          <tbody>
            {candidates.map((c) => (
              <tr key={c.id} className={clsx('border-t border-line', c.chosen && 'bg-hover')}>
                <td className="px-3 py-1.5">
                  <button type="button" className="text-left text-fg underline-offset-2 hover:underline disabled:no-underline" disabled={!c.sql} onClick={() => setShow(show === c.id ? null : c.id)} aria-pressed={show === c.id}>{c.label}</button>
                  {c.chosen && <span className="ml-2 rounded-full bg-[#10a37f]/12 px-1.5 py-0.5 text-[11px] font-medium text-[#0b7a5f] dark:text-[#4fd1a5]">chosen</span>}
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{c.rowCount?.toLocaleString() ?? '–'}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{c.score !== undefined ? c.score.toFixed(2) : '–'}</td>
                <td className="px-3 py-1.5 text-fg-subtle">{c.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown?.sql && <CodeBlock code={shown.sql} language="sql" title={shown.label} />}
    </div>
  );
}
