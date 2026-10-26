/**
 * Contract v1 event scripts (Team B) for the answer surface: the duplicate-denials run as real
 * `step.*` / `escalation` events (web/src/api/contract.ts). Synthetic, no PHI. Used by tests now
 * and by the mock adapter once it replays raw v1 events.
 */
import type { StreamEvent } from '../api/contract';

const SQL_PROBE = "SELECT carc, COUNT(*) FROM sem.claim_outcomes WHERE carc LIKE '%18' GROUP BY 1;";
const SQL_BAD = `SELECT provider_name AS provider, month, COUNT(*) AS claims
FROM sem.claim_outcomes
WHERE carc = 'CO-18'
GROUP BY provider, month;`;
export const SQL_FIXED = `SELECT
  provider_name                                   AS provider,
  COUNT(*)                                        AS claims,
  COUNT(*) FILTER (WHERE carc = 'CO-18')          AS dup_denials,
  ROUND(dup_denials / claims, 4)                  AS dup_rate
FROM sem.claim_outcomes
WHERE adjudication_seq = 1
  AND service_quarter = '2026-Q3'
GROUP BY provider_name
HAVING COUNT(*) >= 15
ORDER BY dup_rate DESC
LIMIT 5;`;
const SQL_B = SQL_FIXED.replace("carc = 'CO-18'", "carc IN ('CO-18', 'OA-18')");
const BINDER = 'Binder Error: Referenced column "month" not found in FROM clause! Candidate bindings: "service_month"';

type NoSeq<T> = T extends unknown ? Omit<T, 'seq'> : never;
const seqd = (evs: NoSeq<StreamEvent>[]): StreamEvent[] => evs.map((e, i) => ('type' in e && e.type === 'heartbeat' ? e : { ...e, seq: i }) as StreamEvent);

/** Everything up to (not including) the answer parts. */
export const DUP_V1_STEPS = seqd([
  { type: 'message.start', messageId: 'm-v1', conversationId: 'c-v1', contractVersion: '1.0.0', model: 'claude-haiku-4-5' },
  { type: 'step.explore', stepId: 'x1', probe: 'describe_view', target: 'sem.claim_outcomes', summary: '24 columns; carc, service_month, adjudication_seq.', elapsedMs: 40 },
  { type: 'step.sql_running', stepId: 'q0', sql: SQL_PROBE, purpose: 'explore', attempt: 1, label: 'Probed CARC values like %18' },
  { type: 'step.sql_result', stepId: 'q0', ok: true, elapsedMs: 180, rowCount: 2, columns: [{ name: 'carc' }, { name: 'count' }] },
  { type: 'step.sql_running', stepId: 'q1', sql: SQL_BAD, purpose: 'answer', attempt: 1 },
  { type: 'step.sql_result', stepId: 'q1', ok: false, elapsedMs: 42, error: { code: 'binder_error', message: BINDER } },
  { type: 'step.repair_attempt', stepId: 'r1', failedStepId: 'q1', attempt: 2, maxAttempts: 3, error: { code: 'binder_error', message: BINDER }, revisedSql: SQL_FIXED, explanation: 'month is service_month' },
  { type: 'step.sql_running', stepId: 'q2', sql: SQL_FIXED, purpose: 'repair', attempt: 2, repairOf: 'r1' },
  { type: 'step.sql_result', stepId: 'q2', ok: true, elapsedMs: 38, rowCount: 5, columns: [{ name: 'provider' }, { name: 'claims' }, { name: 'dup_denials' }, { name: 'dup_rate' }] },
  { type: 'step.sql_running', stepId: 'cb', sql: SQL_B, purpose: 'candidate', attempt: 1, candidateId: 'b' },
  { type: 'step.sql_result', stepId: 'cb', ok: true, elapsedMs: 41, rowCount: 5 },
  { type: 'step.candidates', stepId: 'cand', candidates: [
    { candidateId: 'a', label: 'A · CO-18, ≥15 claims', sql: SQL_FIXED, ok: true, rowCount: 5, score: 0.91, agreesWith: ['b'], stepId: 'q2' },
    { candidateId: 'b', label: 'B · CO-18 + OA-18', sql: SQL_B, ok: true, rowCount: 5, score: 0.84, agreesWith: ['a'], stepId: 'cb' },
  ], chosenId: 'a', method: 'execution_agreement', rationale: 'Both agree on the top 3; A matches the CO-18 definition.' },
  { type: 'step.check', stepId: 'k1', check: 'row_count', verdict: 'pass', message: '5 of 30 eligible providers', targetStepId: 'q2' },
  { type: 'step.check', stepId: 'k2', check: 'date_range', verdict: 'pass', message: 'All rows in 2026-Q3', targetStepId: 'q2' },
  { type: 'step.check', stepId: 'k3', check: 'verifier', verdict: 'warn', message: 'Harborview has 184 claims; small denominator', targetStepId: 'q2', action: 'caveat' },
  { type: 'escalation', from: { model: 'claude-haiku-4-5', tier: 'small' }, to: { model: 'claude-sonnet-4-5', tier: 'large' }, reason: 'A verifier warning needs a careful caveat in the write-up.', trigger: 'verifier_fail' },
]);

const n = DUP_V1_STEPS.length;
/** The answer parts and message.end, continuing the seq. */
export const DUP_V1_ANSWER = [
  { type: 'text.start', partId: 't1' },
  { type: 'text.delta', partId: 't1', delta: 'In Q3 2026, **Lakeside Family Medicine** has the highest duplicate-claim denial rate (**9.0%**).' },
  { type: 'text.end', partId: 't1' },
  { type: 'part', part: { type: 'code', id: 'sql1', language: 'sql', role: 'query', code: SQL_FIXED, stepId: 'q2' } },
  { type: 'part', part: { type: 'table', id: 'tb1', title: 'Duplicate-claim denial rate by provider, Q3 2026', stepId: 'q2', sqlPartId: 'sql1', rowCount: 2,
    columns: [{ key: 'p', label: 'Provider', type: 'string' }, { key: 'r', label: 'Rate', type: 'percent' }], rows: [['Lakeside Family Medicine', 0.0898], ['Riverbend Orthopedics', 0.0709]] } },
  { type: 'message.end', messageId: 'm-v1', finishReason: 'stop', usage: {
    queries: 4, elapsedMs: 8400, tokens: { input: 18420, output: 1260 }, costUsd: 0.0391, costIsEstimate: true,
    latency: { firstTokenMs: 6100, modelMs: 5200, sqlMs: 300, queueMs: 40, totalMs: 8400 },
    models: [{ model: 'claude-haiku-4-5', role: 'planner', inputTokens: 12100, outputTokens: 640, costUsd: 0.0153 }, { model: 'claude-sonnet-4-5', role: 'writer', inputTokens: 6320, outputTokens: 620, costUsd: 0.0238 }] } },
].map((e, i) => ({ ...e, seq: n + i })) as unknown as StreamEvent[];
