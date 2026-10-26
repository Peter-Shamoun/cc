/**
 * Synthetic fixture conversations. Every payer, number and id below is
 * invented; no real claims data and no PHI. CARC meanings paraphrase the
 * public X12 code list.
 */
import type { Column, Part } from '../lib/contract';
import { ANSWER_FIXTURES } from './answer';
import type { UsageDetail } from '../components/answer/provisional';

export type FixtureStep = Part | { status: string };
export interface Fixture { id: string; title: string; prompt: string; match: RegExp; queries: number; steps: FixtureStep[]; usage?: Partial<UsageDetail>; hidden?: boolean }

const payers: [string, number, number][] = [
  ['Northwind Health Plan', 18432, 0.142],
  ['Bluebird Mutual', 12210, 0.097],
  ['Cascade Medicaid MCO', 9876, 0.183],
  ['Summit Commercial PPO', 7654, 0.064],
  ['Harbor Medicare Adv.', 6021, 0.121],
  ['Pinecrest Workers Comp', 1488, 0.231],
];
const denialCols: Column[] = [
  { key: 'payer', label: 'Payer', type: 'string' },
  { key: 'claims', label: 'Claims', type: 'integer' },
  { key: 'denied', label: 'Denied', type: 'integer' },
  { key: 'denial_rate', label: 'Denial rate', type: 'percent' },
  { key: 'denied_amt', label: 'Denied $', type: 'currency' },
];
const denialRows = payers.map(([p, c, r]) => [p, c, Math.round(c * r), r, Math.round(c * r * 412.37)]);

function csvOf(cols: Column[], rows: unknown[][]) {
  const esc = (v: unknown) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  return [cols.map((c) => esc(c.label)).join(','), ...rows.map((r) => r.map(esc).join(','))].join('\r\n');
}

const denialCsv = csvOf(denialCols, denialRows);

// Claim-line detail with a PHI-flagged column, to exercise masking (synthetic member ids).
const lineCols: Column[] = [
  { key: 'claim_id', label: 'Claim', type: 'code' },
  { key: 'member_id', label: 'Member ID', type: 'string', phi: true },
  { key: 'dos', label: 'Service date', type: 'date' },
  { key: 'cpt', label: 'CPT', type: 'code' },
  { key: 'carc', label: 'CARC', type: 'code' },
  { key: 'billed', label: 'Billed', type: 'currency' },
];
const cpts = ['99213', '99214', '97110', '80053', '93000', '71046'];
const carcs = ['CO-16', 'CO-197', 'CO-29', 'CO-16', 'CO-50', 'CO-197'];
const lineRows = Array.from({ length: 60 }, (_, i) => [
  `SYN-${(410000 + i * 37).toString()}`,
  `ZZ${(900000 + i * 7919) % 1000000}`.padEnd(8, '0'),
  `2026-0${7 + (i % 3)}-${String(1 + ((i * 5) % 28)).padStart(2, '0')}`,
  cpts[i % cpts.length],
  carcs[(i * 7) % carcs.length],
  Math.round(80 + ((i * 53) % 900) + (i % 7) * 0.37 * 100) / 1,
]);

const months = ['2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09'];
const trendValues = months.flatMap((m, i) => [
  { month: m, payer: 'Northwind Health Plan', denial_rate: +(0.11 + i * 0.003 + (i % 3) * 0.004).toFixed(3) },
  { month: m, payer: 'Cascade Medicaid MCO', denial_rate: +(0.16 + Math.sin(i / 2) * 0.015 + i * 0.002).toFixed(3) },
  { month: m, payer: 'Summit Commercial PPO', denial_rate: +(0.07 - i * 0.001).toFixed(3) },
]);

function plotSvg(): string {
  const bars = [['CO-45', 31], ['CO-97', 22], ['CO-16', 17], ['PR-1', 12], ['CO-197', 9], ['CO-50', 6]] as const;
  const w = 640, h = 320, left = 60, bottom = 40, top = 40, max = 35;
  const bw = (w - left - 20) / bars.length;
  const rects = bars.map(([k, v], i) => {
    const bh = ((h - top - bottom) * v) / max, x = left + i * bw + 10, y = h - bottom - bh;
    return `<rect x="${x}" y="${y}" width="${bw - 20}" height="${bh}" rx="3" fill="#4C78A8"/>` +
      `<text x="${x + (bw - 20) / 2}" y="${h - bottom + 18}" font-size="12" text-anchor="middle" fill="#555">${k}</text>` +
      `<text x="${x + (bw - 20) / 2}" y="${y - 6}" font-size="11" text-anchor="middle" fill="#333">${v}%</text>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" font-family="Helvetica,Arial,sans-serif">` +
    `<rect width="100%" height="100%" fill="#fff"/><text x="${left}" y="24" font-size="15" font-weight="600" fill="#222">Share of denied dollars by CARC, Q3 2026 (synthetic)</text>` +
    `<line x1="${left}" y1="${h - bottom}" x2="${w - 10}" y2="${h - bottom}" stroke="#ccc"/>${rects}</svg>`;
}

/** Opaque blob ids -> content, served by MockAnalystAdapter.fetchBlob. */
export const BLOBS: Record<string, { mime: string; content: string }> = {
  'blob_carc_plot': { mime: 'image/svg+xml', content: plotSvg() },
  'file_denial_q3': { mime: 'text/csv', content: denialCsv },
  'file_lines_q3': { mime: 'text/csv', content: csvOf(lineCols, lineRows.map((r) => r.map((v, j) => (lineCols[j].phi ? '[masked]' : v)))) },
};

const SQL_DENIAL = `-- scoped DuckDB session · sem.* views only
SELECT
  payer_name                                         AS payer,
  COUNT(*)                                           AS claims,
  COUNT(*) FILTER (WHERE outcome = 'denied')         AS denied,
  ROUND(denied / claims, 3)                          AS denial_rate,
  SUM(billed_amt) FILTER (WHERE outcome = 'denied')  AS denied_amt
FROM sem.claim_outcomes
WHERE adjudication_seq = 1
  AND service_quarter = '2026-Q3'
GROUP BY payer_name
ORDER BY denial_rate DESC;`;

const denialTable: Part = {
  type: 'table', id: 'tbl-denial', title: 'Denial rate by payer, Q3 2026', columns: denialCols, rows: denialRows, rowCount: denialRows.length,
  csvFileId: 'file_denial_q3', sqlPartId: 'sql-denial', source: { views: ['sem.claim_outcomes'], elapsedMs: 41 }, sensitivity: 'deidentified',
};

export const FIXTURES: Fixture[] = [
  {
    id: 'denial-by-payer', title: 'Denial rate by payer, last quarter', prompt: 'What was our denial rate by payer last quarter?', match: /denial rate by payer|payer|deny/i, queries: 1,
    steps: [
      { type: 'reasoning', id: 'rs-1', durationMs: 4200, summary: 'First-pass denial rate per payer for Q3 2026. Use sem.claim_outcomes and keep adjudication_seq = 1 so resubmissions do not double count. Report claims, denied count and denied dollars, then flag outliers by volume.' },
      { status: 'Querying sem.claim_outcomes…' },
      { type: 'tool_call', id: 'tc-1', tool: 'run_sql', label: 'Queried sem.claim_outcomes', state: 'ok', input: { sql: SQL_DENIAL }, output: { rowCount: 6 }, durationMs: 41 },
      { status: 'Checking results…' },
      { type: 'text', id: 'tx-1', text: `## Denial rate by payer, Q3 2026\n\nOverall, **13.7%** of 55,681 claims were denied on first adjudication (Jul 1 – Sep 30, by date of service).\n\n- **Pinecrest Workers Comp** has the highest rate (**23.1%**), but low volume.\n- **Cascade Medicaid MCO** (**18.3%**) is second and drives the most denied dollars after Northwind.\n- **Summit Commercial PPO** is the cleanest payer at **6.4%**.\n` },
      { type: 'callout', id: 'co-1', variant: 'assumption', title: 'Assumptions', items: [
        { id: 'a1', text: '"Last quarter" = calendar **Q3 2026** by date of service, not paid date.', editable: true },
        { id: 'a2', text: 'Denied = claim status 4 on the first 835 (`CLP02`); partial denials excluded.' },
        { id: 'a3', text: 'Corrected and void claims (frequency code 7/8) are excluded.' },
      ] },
      denialTable,
      { type: 'code', id: 'sql-denial', language: 'sql', role: 'query', title: 'denial_rate_by_payer.sql', code: SQL_DENIAL, collapsed: true },
      { type: 'file', id: 'fl-1', fileId: 'file_denial_q3', name: 'cc_denial_rate_by_payer_2026-10-25.csv', mime: 'text/csv', sizeBytes: denialCsv.length, rowCount: denialRows.length, previewable: true },
      { type: 'text', id: 'tx-2', text: `Want me to break Cascade's denials down by reason code?` },
    ],
  },
  {
    id: 'denial-trend', title: 'Plot the monthly denial trend', prompt: 'Plot the monthly denial rate trend for our top 3 payers', match: /trend|plot|chart|month/i, queries: 2,
    steps: [
      { status: 'Planning…' },
      { type: 'tool_call', id: 'tc-t1', tool: 'describe_view', label: 'Described sem.claim_outcomes', state: 'ok', input: { args: { view: 'sem.claim_outcomes' } }, output: { message: '14 columns' }, durationMs: 12 },
      { type: 'tool_call', id: 'tc-t2', tool: 'run_sql', label: 'Queried sem.claim_outcomes by month', state: 'ok', input: { sql: "SELECT date_trunc('month', service_date) AS month, payer_name, AVG(outcome='denied') AS denial_rate\nFROM sem.claim_outcomes WHERE adjudication_seq = 1 GROUP BY ALL;" }, output: { rowCount: 36 }, durationMs: 58 },
      { type: 'tool_call', id: 'tc-t3', tool: 'make_chart', label: 'Built a line chart', state: 'ok', output: { message: 'Vega-Lite · 36 points' }, durationMs: 4 },
      { type: 'text', id: 'tx-t1', text: `Monthly first-pass denial rate, **Oct 2025 – Sep 2026**:` },
      { type: 'chart', id: 'ch-1', library: 'vega-lite', title: 'Monthly denial rate, top 3 payers', alt: 'Line chart of monthly denial rate for three payers; Cascade rises from about 16% to 19%, Summit falls slightly below 7%.',
        data: { name: 'trend', values: trendValues },
        spec: {
          $schema: 'https://vega.github.io/schema/vega-lite/v5.json', width: 'container', height: 280,
          mark: { type: 'line', point: true, strokeWidth: 2 },
          encoding: {
            x: { field: 'month', type: 'temporal', timeUnit: 'yearmonth', title: null },
            y: { field: 'denial_rate', type: 'quantitative', title: 'Denial rate', axis: { format: '.0%' } },
            color: { field: 'payer', type: 'nominal', title: null, legend: { orient: 'bottom' } },
            tooltip: [{ field: 'payer', type: 'nominal' }, { field: 'month', type: 'temporal', timeUnit: 'yearmonth' }, { field: 'denial_rate', type: 'quantitative', format: '.1%' }],
          },
        } },
      { type: 'text', id: 'tx-t2', text: `**Cascade Medicaid MCO** climbs from ~16% to ~19% with a Q1 bump; **Summit Commercial PPO** trends gently down; Northwind rises about +0.3 pts a month.\n\nHere is the denied-dollar mix by reason code, rendered by the analyst as an image:` },
      { type: 'image', id: 'im-1', blobId: 'blob_carc_plot', mime: 'image/svg+xml', width: 640, height: 320, alt: 'Bar chart: CO-45 31%, CO-97 22%, CO-16 17%, PR-1 12%, CO-197 9%, CO-50 6% of denied dollars.', caption: 'Figure 1 · Share of denied dollars by CARC (synthetic)' },
      { type: 'callout', id: 'co-t1', variant: 'caveat', title: 'Caveat', items: [{ id: 'c1', text: 'September 2026 is not fully adjudicated yet; expect its rate to move by up to 1 pt.' }] },
    ],
  },
  {
    id: 'carc', title: 'Which CARC codes drive Cascade denials?', prompt: 'Which CARC codes drive Cascade Medicaid denials?', match: /carc|reason|code|why|cascade/i, queries: 2,
    steps: [
      { type: 'reasoning', id: 'rs-c', durationMs: 2600, summary: 'Group CAS adjustments for Cascade Medicaid MCO denials by group + reason code and rank by denied dollars. Pull a claim-line sample so the user can inspect examples; member ids are PHI and must stay masked.' },
      { type: 'tool_call', id: 'tc-c1', tool: 'run_sql', label: 'Queried sem.claim_adjustments', state: 'ok', input: { sql: "SELECT group_code || '-' || reason_code AS carc, SUM(adj_amt) FROM sem.claim_adjustments WHERE payer_name = 'Cascade Medicaid MCO' GROUP BY 1 ORDER BY 2 DESC;" }, output: { rowCount: 8 }, durationMs: 33 },
      { type: 'tool_call', id: 'tc-c2', tool: 'verify', label: 'Verified totals against sem.claim_outcomes', state: 'error', output: { message: 'Totals differ by 0.4% (late 835s). Proceeding with a caveat.' }, durationMs: 21 },
      { type: 'text', id: 'tx-c1', text: `For **Cascade Medicaid MCO** in Q3 2026, three reason codes account for ~70% of denied dollars:\n\n| Rank | CARC | Meaning (paraphrased) | Share of denied $ |\n|---:|---|---|---:|\n| 1 | CO-16 | Claim lacks information needed for adjudication | 31% |\n| 2 | CO-197 | Precertification or authorization absent | 24% |\n| 3 | CO-29 | Time limit for filing has expired | 15% |\n\n### Suggested next steps\n\n1. Audit CO-16 claims for a missing referring-provider NPI (loop \`2310A\`).\n2. Check the prior-auth workflow for Cascade plan ids.\n3. Tighten timely-filing alerts: Cascade's window is 90 days.\n\nA sample of the denied claim lines is below. Member IDs are masked under your access level.` },
      { type: 'table', id: 'tbl-lines', title: 'Denied claim lines, Cascade Medicaid MCO (sample)', columns: lineCols, rows: lineRows, rowCount: 1284, csvFileId: 'file_lines_q3', source: { views: ['sem.claim_lines'], elapsedMs: 77 }, sensitivity: 'limited' },
      { type: 'callout', id: 'co-c1', variant: 'phi_redaction', title: 'PHI masked', items: [{ id: 'p1', text: '1 column (**Member ID**) was masked under your access level and is excluded from copy and CSV.' }] },
      { type: 'callout', id: 'co-c2', variant: 'data_quality', title: 'Data quality', items: [{ id: 'd1', text: 'Verifier found a 0.4% gap against claim-level totals, likely 835s that arrived after month close.' }] },
      { type: 'artifact', id: 'af-1', artifactId: 'art_carc_sql', version: 1, kind: 'code', title: 'CARC breakdown query', content: { type: 'code', id: 'af-1-code', language: 'sql', title: 'carc_breakdown.sql', code: "SELECT group_code || '-' || reason_code AS carc,\n       SUM(adj_amt)                      AS denied_amt,\n       SUM(adj_amt) / SUM(SUM(adj_amt)) OVER () AS share\nFROM sem.claim_adjustments\nWHERE payer_name = 'Cascade Medicaid MCO'\n  AND service_quarter = '2026-Q3'\nGROUP BY 1\nORDER BY denied_amt DESC;" } },
    ],
  },
  {
    id: 'error', title: 'Show me an error', prompt: 'Run the query against the raw 837 tables', match: /error|raw|fail|837/i, queries: 0,
    steps: [
      { type: 'tool_call', id: 'tc-e1', tool: 'run_sql', label: 'Queried raw.x12_837_loop2300', state: 'error', input: { sql: 'SELECT * FROM raw.x12_837_loop2300 LIMIT 100;' }, output: { message: 'Blocked by guard: only sem.* views are allowed.' }, durationMs: 3 },
      { type: 'error', id: 'er-1', code: 'access_denied', title: 'Access denied', message: 'Raw EDI tables are outside your scoped session. I can answer this from the sem.* semantic views instead.', details: 'guard=run_semantic_sql; relation=raw.x12_837_loop2300; policy=sem_only', retryable: true },
      { type: 'text', id: 'tx-e1', text: 'Try asking for the same metric, and I will use `sem.claim_lines`, which carries the 2300-loop fields without identifiers.' },
    ],
  },
];

const ALL: Fixture[] = [...FIXTURES, ...ANSWER_FIXTURES];

export function pickFixture(prompt: string): Fixture {
  return ALL.find((f) => f.prompt === prompt) ?? ANSWER_FIXTURES.find((f) => f.match.test(prompt)) ?? FIXTURES.find((f) => f.match.test(prompt)) ?? FIXTURES[0];
}

/** Main demos plus the answer-surface demos (Team B: repair/candidates/checks, abstain, clarify). */
export const SUGGESTIONS = ALL.filter((f) => !f.hidden).map((f) => ({ title: f.title, prompt: f.prompt }));
