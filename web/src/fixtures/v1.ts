/**
 * Contract-v1 demo fixtures (synthetic, no PHI). The mock adapter replays these as real v1
 * events (api/contract.ts): step.* SQL / repair / check / explore / candidates, escalation,
 * abstain and clarification parts, needs_input / abstain finish reasons and rich usage.
 * Hidden from the suggestion chips; reach them by exact prompt (V1_PROMPTS) or their regexes,
 * which are checked after the older V1_FIXTURES.
 */
import type { Column } from '../lib/contract';
import type { FinishReason, Part, StepEvent, EscalationEvent, Usage } from '../api/contract';

/** A v1 step/escalation event as the mock replays it (the adapter assigns `seq`). */
type Ev = StepEvent | EscalationEvent;
type NoSeq<T> = T extends unknown ? Omit<T, 'seq'> : never;
export type V1Step = Part | { status: string } | { event: NoSeq<Ev>; waitMs?: number };
export interface V1Fixture { id: string; title: string; prompt: string; match: RegExp; queries: number; usage?: Partial<Usage>; finishReason?: FinishReason; hidden: true; steps: V1Step[] }
const ev = (event: NoSeq<Ev>, waitMs?: number): V1Step => ({ event, ...(waitMs ? { waitMs } : {}) });

const provCols: Column[] = [
  { key: 'provider', label: 'Billing provider', type: 'string' },
  { key: 'claims', label: 'Claims', type: 'integer' },
  { key: 'denied', label: 'Denied', type: 'integer' },
  { key: 'denial_rate', label: 'Denial rate', type: 'percent' },
  { key: 'top_carc', label: 'Top CARC', type: 'code' },
];
const provs: [string, number, number, string][] = [
  ['Lakeside Family Medicine', 412, 0.211, 'CO-16'],
  ['Riverbend Orthopedics', 638, 0.188, 'CO-197'],
  ['Maple Street Imaging', 297, 0.172, 'CO-50'],
  ['Northgate Physical Therapy', 551, 0.149, 'CO-97'],
  ['Cedar Hollow Cardiology', 344, 0.131, 'CO-16'],
  ['Elm Park Pediatrics', 720, 0.094, 'CO-29'],
];
const provRows = provs.map(([p, c, r, k]) => [p, c, Math.round(c * r), r, k]);

const BAD_SQL = `SELECT billing_npi, month, COUNT(*) AS claims,
       AVG(outcome = 'denied') AS denial_rate
FROM sem.claim_outcomes
WHERE adjudication_seq = 1
GROUP BY billing_npi, month
HAVING COUNT(*) >= 15;`;
const FIXED_SQL = `SELECT p.provider_name            AS provider,
       COUNT(*)                   AS claims,
       COUNT(*) FILTER (WHERE o.outcome = 'denied') AS denied,
       AVG(o.outcome = 'denied')  AS denial_rate,
       MODE(a.carc)               AS top_carc
FROM sem.claim_outcomes o
JOIN sem.providers p USING (billing_npi)
LEFT JOIN sem.claim_adjustments a USING (claim_id)
WHERE o.adjudication_seq = 1
  AND o.service_date >= DATE '2026-01-01'
GROUP BY p.provider_name
HAVING COUNT(*) >= 15
ORDER BY denial_rate DESC
LIMIT 6;`;

const CHECK_SQL = `SELECT COUNT(*) AS claims
FROM sem.claim_outcomes
WHERE adjudication_seq = 1
  AND service_date >= DATE '2026-01-01';`;

const providerAnswer: V1Step[] = [
  { type: 'reasoning', id: 'rs-p', durationMs: 3100, summary: 'Rank billing providers by first-pass denial rate for 2026 year to date. Require at least 15 claims so tiny providers do not top the list, and attach the most common CARC for context.' },
  ev({ type: 'step.explore', stepId: 'ex-p1', probe: 'describe_view', target: 'sem.claim_outcomes', summary: '14 columns; service dates 2025-10-01 to 2026-09-30', elapsedMs: 11 }),
  { status: 'Writing SQL…' },
  ev({ type: 'step.sql_running', stepId: 'q-p1', sql: BAD_SQL, purpose: 'answer', attempt: 1, label: 'Denial rate by provider' }),
  ev({ type: 'step.sql_result', stepId: 'q-p1', ok: false, elapsedMs: 18, error: { code: 'binder_error', message: 'column "month" not found in sem.claim_outcomes' } }, 400),
  ev({ type: 'escalation', from: { model: 'Haiku', tier: 'small' }, to: { model: 'Sonnet', tier: 'medium' }, reason: 'First draft failed to bind; the larger model wrote the final query.', trigger: 'repair_exhausted', stepId: 'esc-p' }),
  ev({ type: 'step.repair_attempt', stepId: 'r-p1', failedStepId: 'q-p1', attempt: 1, maxAttempts: 3, error: { code: 'binder_error', message: 'column "month" not found' }, revisedSql: FIXED_SQL, explanation: 'Joined sem.providers for names and dropped the month grouping.' }),
  ev({ type: 'step.sql_running', stepId: 'q-p2', sql: FIXED_SQL, purpose: 'repair', attempt: 2, repairOf: 'q-p1', label: 'Denial rate by provider' }),
  ev({ type: 'step.sql_result', stepId: 'q-p2', ok: true, elapsedMs: 64, rowCount: 6, columns: provCols.map((c) => ({ name: c.key })) }, 500),
  ev({ type: 'step.sql_running', stepId: 'q-p3', sql: CHECK_SQL, purpose: 'check', attempt: 1, label: 'Reconcile claim totals' }),
  ev({ type: 'step.sql_result', stepId: 'q-p3', ok: true, elapsedMs: 22, rowCount: 1 }, 300),
  ev({ type: 'step.check', stepId: 'ck-p1', check: 'totals_reconcile', verdict: 'pass', message: 'Claim counts reconcile to 2,962 (0 difference).', targetStepId: 'q-p2', action: 'none' }),
  ev({ type: 'step.check', stepId: 'ck-p2', check: 'min_volume', verdict: 'pass', message: 'All 6 providers have at least 297 claims.', targetStepId: 'q-p2', action: 'none' }),
  { type: 'text', id: 'tx-p1', text: `**Lakeside Family Medicine** has the highest first-pass denial rate this year at **21.1%**, followed by **Riverbend Orthopedics** (18.8%) and **Maple Street Imaging** (17.2%).\n\nCO-16 (missing information) is the top reason at two of the six, which usually points to front-end registration gaps rather than coding.` },
  { type: 'callout', id: 'as-p', variant: 'assumption', title: 'Assumptions', items: [
    { id: 'pa1', text: '"This year" = **2026 year to date** by date of service.', editable: true },
    { id: 'pa2', text: 'Providers with fewer than **15 claims** are excluded.', editable: true },
    { id: 'pa3', text: 'Denial = first adjudication only; resubmissions are not counted again.' },
  ] },
  { type: 'code', id: 'sql-p', role: 'query', language: 'sql', title: 'Final query', code: FIXED_SQL },
  { type: 'table', id: 'tbl-prov', title: 'Highest denial rate by billing provider, 2026 YTD', columns: provCols, rows: provRows, rowCount: provRows.length, sqlPartId: 'sql-p', source: { views: ['sem.claim_outcomes', 'sem.providers'], elapsedMs: 64 }, sensitivity: 'deidentified' },
];

const providerUsage = (firstTokenMs: number, totalMs: number): Partial<Usage> => ({
  costIsEstimate: true,
  tokens: { input: 24630, output: 1000 },
  costUsd: 0.0703,
  latency: { firstStepMs: 420, firstTokenMs, sqlMs: 115, totalMs },
  models: [
    { model: 'Haiku', role: 'draft', inputTokens: 6210, outputTokens: 88, costUsd: 0.0016, calls: 1 },
    { model: 'Sonnet', role: 'repair + answer', inputTokens: 18420, outputTokens: 912, costUsd: 0.0687, calls: 2 },
  ],
});

export const V1_FIXTURES: V1Fixture[] = [
  {
    id: 'v1-providers-repair', title: 'Which providers get denied most?', prompt: 'Which billing providers have the highest denial rate this year?', match: /billing providers?.*(denial|denied)/i, hidden: true, queries: 3,
    usage: providerUsage(2140, 6480), steps: providerAnswer,
  },
  {
    id: 'v1-clarify-resume', title: 'Denial rate (resumes after clarify)', hidden: true, prompt: 'Last 12 months', match: /^(last quarter|last 12 months|year to date|2026 ytd)\b/i, queries: 3,
    usage: providerUsage(1890, 6120), steps: providerAnswer,
  },
  {
    id: 'v1-clarify', title: 'Denial rate for a period (asks which period)', prompt: 'Show me the denial rate trend', match: /^show me the denial rate trend$/i, hidden: true, queries: 0, finishReason: 'needs_input',
    usage: { costIsEstimate: true, tokens: { input: 6210, output: 88 }, costUsd: 0.0016, latency: { firstTokenMs: 640, totalMs: 1310 }, models: [{ model: 'Haiku', inputTokens: 6210, outputTokens: 88, costUsd: 0.0016 }] },
    steps: [
      { type: 'reasoning', id: 'rs-q', durationMs: 900, summary: '"Top providers" and the time window are both ambiguous. Ask before running anything.' },
      { type: 'clarification', id: 'cl-1', clarificationId: 'clr_demo_1', question: 'Which period should I use?', choices: [{ id: 'c1', label: 'Last quarter' }, { id: 'c2', label: 'Last 12 months' }, { id: 'c3', label: 'Year to date', description: 'Jan 1 2026 to today' }], allowFreeText: true },
    ],
  },
  {
    id: 'v1-abstain', title: 'Forecast next year (abstains)', prompt: "What will Cascade's denial rate be next year?", match: /next year|forecast|predict/i, hidden: true, queries: 0, finishReason: 'abstain',
    usage: { costIsEstimate: true, tokens: { input: 7400, output: 140 }, costUsd: 0.0021, latency: { firstTokenMs: 820, totalMs: 1650 }, models: [{ model: 'Haiku', inputTokens: 7400, outputTokens: 140, costUsd: 0.0021 }] },
    steps: [
      { type: 'reasoning', id: 'rs-a', durationMs: 1400, summary: 'The question asks for a forecast. The data covers Oct 2025 to Sep 2026 and the analyst only reports observed history.' },
      ev({ type: 'step.explore', stepId: 'ex-a1', probe: 'describe_view', target: 'sem.claim_outcomes', summary: 'Service dates 2025-10-01 to 2026-09-30', elapsedMs: 9 }),
      { type: 'abstain', id: 'ab-1', category: 'unsupported', reason: "I can't forecast future denial rates. The data only covers **Oct 2025 – Sep 2026**, and I report what happened rather than predict it.", details: 'A projection here could look precise without being reliable, so I would rather not give one.', suggestions: [
        { id: 's1', text: "What was Cascade's monthly denial rate over the last 12 months?" },
        { id: 's2', text: "Is Cascade's denial rate trending up or down?" },
        { id: 's3', text: 'Which CARC codes drive Cascade Medicaid denials?' },
      ] },
    ],
  },
];
