/**
 * Answer-surface fixtures (Team B): tool steps with a repair, candidate comparison and verifier
 * checks, an abstain and a clarifying question. All synthetic, no PHI. `abstain`/`clarification`
 * parts and `usage` follow contract v1 (web/src/api/contract.ts); the steps are still legacy
 * `tool_call` parts until the mock adapter replays v1 `step.*` events.
 */
import type { Column, Part } from '../lib/contract';
import type { Fixture } from './index';

const P = (x: unknown) => x as Part;

const provCols: Column[] = [
  { key: 'provider', label: 'Provider', type: 'string' },
  { key: 'claims', label: 'Claims', type: 'integer' },
  { key: 'dup_denials', label: 'Duplicate denials', type: 'integer' },
  { key: 'dup_rate', label: 'Duplicate denial rate', type: 'percent' },
];
const provRows = [
  ['Lakeside Family Medicine', 412, 37, 0.0898],
  ['Riverbend Orthopedics', 268, 19, 0.0709],
  ['Maple Street Pediatrics', 351, 21, 0.0598],
  ['Northgate Imaging Center', 590, 29, 0.0492],
  ['Harborview Cardiology', 184, 7, 0.038],
];

const SQL_BAD = `SELECT provider_name AS provider, month, COUNT(*) AS claims
FROM sem.claim_outcomes
WHERE carc = 'CO-18'
GROUP BY provider, month;`;
const SQL_FIXED = `SELECT
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
const SQL_CAND_B = SQL_FIXED.replace("carc = 'CO-18'", "carc IN ('CO-18', 'OA-18')");
const SQL_CAND_C = SQL_FIXED.replace('HAVING COUNT(*) >= 15\n', '');

export const ANSWER_FIXTURES: Fixture[] = [
  {
    id: 'dup-denials', title: 'Duplicate-claim denials by provider', prompt: 'Which providers have the highest duplicate-claim denial rate?', match: /duplicate|repair|provider/i, queries: 3,
    usage: { tokens: { input: 18420, output: 1260, cacheRead: 9200 }, costUsd: 0.0391, costIsEstimate: true,
      latency: { firstEventMs: 40, firstStepMs: 1400, firstTokenMs: 6100, queueMs: 40, modelMs: 5200, sqlMs: 2100, totalMs: 8400 },
      models: [{ model: 'claude-haiku-4-5', role: 'planner', inputTokens: 12100, outputTokens: 640, costUsd: 0.0153, calls: 4 }, { model: 'claude-sonnet-4-5', role: 'writer', inputTokens: 6320, outputTokens: 620, costUsd: 0.0238, calls: 1 }] },
    steps: [
      { type: 'reasoning', id: 'rs-d', durationMs: 3100, summary: 'Duplicate-claim denials are CARC 18. Rate = CO-18 denials / first-pass claims per provider, Q3 2026. Keep providers with at least 15 claims so tiny practices do not top the list. Probe the code values first.' },
      { type: 'tool_call', id: 'st-probe', tool: 'explore', label: 'Probed CARC values like %18', state: 'ok', input: { sql: "SELECT carc, COUNT(*) FROM sem.claim_outcomes WHERE carc LIKE '%18' GROUP BY 1;" }, output: { rowCount: 2, message: 'CO-18 (1,102) and OA-18 (14).' }, durationMs: 180 },
      { type: 'tool_call', id: 'st-q1', tool: 'run_sql', label: 'Queried sem.claim_outcomes', state: 'error', input: { sql: SQL_BAD }, output: { message: 'Binder Error: Referenced column "month" not found in FROM clause! Candidate bindings: "service_month"' }, durationMs: 42 },
      P({ type: 'tool_call', id: 'st-r1', tool: 'repair', label: 'Repaired the query', state: 'ok', attempt: 2, error: 'Binder Error: Referenced column "month" not found in FROM clause! Candidate bindings: "service_month"', previousSql: SQL_BAD, input: { sql: SQL_FIXED }, output: { rowCount: 5 }, durationMs: 1650 }),
      { type: 'tool_call', id: 'st-c', tool: 'compare_candidates', label: 'Compared 3 candidate queries', state: 'ok', output: { message: 'Candidates agree on the top 3.', candidates: [
        { id: 'a', label: 'A · CO-18, ≥15 claims', sql: SQL_FIXED, rowCount: 5, score: 0.91, chosen: true, note: 'matches the definition' },
        { id: 'b', label: 'B · CO-18 + OA-18', sql: SQL_CAND_B, rowCount: 5, score: 0.84, note: 'OA-18 is rare (14 rows)' },
        { id: 'c', label: 'C · no volume floor', sql: SQL_CAND_C, rowCount: 5, score: 0.62, note: 'tiny practices dominate' },
      ] } as never, durationMs: 820 },
      { type: 'tool_call', id: 'st-v', tool: 'verify', label: 'Ran 4 checks', state: 'ok', output: { checks: [
        { name: 'Row count plausible', passed: true, detail: '5 of 30 eligible providers' },
        { name: 'Rates within [0, 1]', passed: true },
        { name: 'Totals reconcile with sem.claim_outcomes', passed: true, detail: 'Δ 0.0%' },
        { name: 'Small-denominator guard', passed: false, detail: 'Harborview has 184 claims, flagged in caveats' },
      ] } as never, durationMs: 910 },
      { type: 'text', id: 'tx-d1', text: 'In Q3 2026, **Lakeside Family Medicine** has the highest duplicate-claim denial rate (**9.0%**, 37 of 412 claims), followed by **Riverbend Orthopedics** (7.1%).\n\nDuplicate denials are CARC **CO-18**; providers with fewer than 15 claims are excluded.' },
      { type: 'table', id: 'tbl-dup', title: 'Duplicate-claim denial rate by provider, Q3 2026', columns: provCols, rows: provRows, rowCount: provRows.length, sqlPartId: 'st-r1', source: { views: ['sem.claim_outcomes'], elapsedMs: 38 }, sensitivity: 'deidentified' },
      { type: 'callout', id: 'co-d1', variant: 'caveat', title: 'Caveat', items: [{ id: 'cv1', text: 'Harborview Cardiology has only 184 claims; one or two resubmissions move its rate by a full point.' }] },
    ],
  },
  {
    id: 'abstain', title: 'Patient satisfaction by clinic', prompt: 'What is our patient satisfaction score by clinic?', match: /satisfaction|nps|survey|weather/i, queries: 1,
    usage: { tokens: { input: 6120, output: 310 }, costUsd: 0.0071, costIsEstimate: true, latency: { modelMs: 1500, sqlMs: 300, totalMs: 1800 }, models: [{ model: 'claude-haiku-4-5', inputTokens: 6120, outputTokens: 310, costUsd: 0.0071 }] },
    steps: [
      { type: 'tool_call', id: 'ab-1', tool: 'list_views', label: 'Searched the schema for survey data', state: 'ok', output: { message: '0 views mention satisfaction, survey, CAHPS or NPS.' }, durationMs: 120 },
      { type: 'tool_call', id: 'ab-2', tool: 'explore', label: 'Probed sem.providers for clinic fields', state: 'ok', input: { sql: 'DESCRIBE sem.providers;' }, output: { rowCount: 11 }, durationMs: 60 },
      P({ type: 'abstain', id: 'ab-x', category: 'data_unavailable', reason: 'Your workspace holds **claims and remittance data** (837/835) only. Nothing in it measures patient satisfaction, so any number I gave would be invented.', details: '- A survey feed such as CAHPS or a post-visit NPS export\n- A clinic or location id that joins to `sem.providers`', suggestions: [
        { id: 's1', text: 'Which clinics have the highest denial rate?' }, { id: 's2', text: 'Show claim volume by clinic for Q3 2026' }, { id: 's3', text: 'Which clinics have the longest days to payment?' }] }),
    ],
  },
  {
    id: 'clarify', title: 'Who are our top providers?', prompt: 'Who are our top providers?', match: /top providers|clarify|ambiguous/i, queries: 0,
    usage: { tokens: { input: 2310, output: 120 }, costUsd: 0.0024, costIsEstimate: true, models: [{ model: 'claude-haiku-4-5', inputTokens: 2310, outputTokens: 120, costUsd: 0.0024 }] },
    steps: [
      { type: 'reasoning', id: 'rs-cl', durationMs: 1200, summary: '"Top" could mean volume, dollars or quality. The answers differ a lot, so ask before running queries.' },
      P({ type: 'clarification', id: 'cl-1', clarificationId: 'clar-top', question: '“Top” by which measure?', allowFreeText: true, choices: [
        { id: 'o1', label: 'By claim volume' }, { id: 'o2', label: 'By billed dollars' }, { id: 'o3', label: 'By lowest denial rate', description: '≥15 claims' },
      ] }),
    ],
  },
  {
    id: 'clarify-answer', title: 'Top providers (answered)', prompt: 'By billed dollars', match: /^(by claim volume|by billed dollars|by lowest denial rate)/i, queries: 1, hidden: true,
    usage: { tokens: { input: 7400, output: 420 }, costUsd: 0.0088, costIsEstimate: true, latency: { firstTokenMs: 700, modelMs: 900, sqlMs: 300, totalMs: 1200 } },
    steps: [
      { type: 'tool_call', id: 'ca-1', tool: 'run_sql', label: 'Queried sem.claim_outcomes', state: 'ok', input: { sql: 'SELECT provider_name, SUM(billed_amt) AS billed\nFROM sem.claim_outcomes\nWHERE service_quarter = \'2026-Q3\'\nGROUP BY 1 ORDER BY 2 DESC LIMIT 5;' }, output: { rowCount: 5 }, durationMs: 51 },
      { type: 'text', id: 'ca-t', text: 'Ranked that way, **Northgate Imaging Center** leads Q3 2026 with **$1.92M** billed.' },
      { type: 'table', id: 'ca-tbl', title: 'Top providers by billed dollars, Q3 2026', sqlPartId: 'ca-1', rowCount: 5, columns: [{ key: 'p', label: 'Provider', type: 'string' }, { key: 'b', label: 'Billed', type: 'currency' }], rows: [['Northgate Imaging Center', 1920400], ['Riverbend Orthopedics', 1411250], ['Harborview Cardiology', 1203980], ['Lakeside Family Medicine', 688120], ['Maple Street Pediatrics', 402700]] },
    ],
  },
];
