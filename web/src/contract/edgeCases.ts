/**
 * 1.16.0 edge-case demo scenarios (CONTRACT.md §9.1). Send `scenario` on ChatRequest in demo mode to get each one;
 * the recorded streams are in api/contract/examples/edge/<name>.jsonl. Synthetic data only.
 */
import type { DemoScenario, FinishReason } from '../api/contract';

export interface EdgeScenario {
  scenario: DemoScenario;
  /** finishReason of the (first) stream */
  finish: FinishReason;
  /** what the UI should show / survive */
  covers: string;
}

export const EDGE_SCENARIOS: readonly EdgeScenario[] = [
  { scenario: 'empty', finish: 'stop', covers: 'A query that returns 0 rows: an empty table with columns plus an explanation, no chart.' },
  { scenario: 'multi_table', finish: 'stop', covers: 'Two tables in one answer, each with its own SQL code part (sqlPartId) and chart.' },
  { scenario: 'long_sql', finish: 'stop', covers: 'A 100+ line, 4 KB+ query in step.sql_running and the code part (wrap, scroll, copy).' },
  { scenario: 'many_repairs', finish: 'stop', covers: 'Four failed queries and repair attempts before the answer (long step timeline).' },
  { scenario: 'clarify_nested', finish: 'needs_input', covers: 'Asks, then asks again after the first resume, then answers (3 messages, 2 resumes).' },
  { scenario: 'escalation_abstain', finish: 'abstain', covers: 'The small model gives up, escalates, and the larger model also abstains.' },
  { scenario: 'cancel_plan', finish: 'aborted', covers: 'Stop while planning, before any SQL.' },
  { scenario: 'cancel_sql', finish: 'aborted', covers: 'Stop while a query runs: its sql_result arrives with error.code "cancelled".' },
  { scenario: 'cancel_repair', finish: 'aborted', covers: 'Stop during a repair attempt.' },
  { scenario: 'cancel_candidates', finish: 'aborted', covers: 'Stop while candidate k2 runs: open candidate queries close as cancelled.' },
  { scenario: 'cancel_escalation', finish: 'aborted', covers: 'Stop right after an escalation to the larger model.' },
  { scenario: 'cancel_render', finish: 'aborted', covers: 'Stop while the answer text renders: text block closes, no table.' },
];
