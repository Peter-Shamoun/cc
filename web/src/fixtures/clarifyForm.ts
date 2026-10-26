import type { MultiClarification } from '../components/answer/ClarifyForm';

/** Local fixture for the multi-question clarification until the contract publishes its parts. Synthetic only. */
export const CLARIFY_FORM: MultiClarification = {
  type: 'clarification', id: 'cf1', clarificationId: 'clf_demo1', allowFreeText: true,
  question: 'A few details so the numbers match what you need.',
  questions: [
    { id: 'metric', header: 'Metric', question: 'Which denial measure?', options: [
      { id: 'count_rate', label: 'Claim count rate', description: 'Denied claims ÷ adjudicated claims' },
      { id: 'dollar_rate', label: 'Dollar rate', description: 'Denied billed amount ÷ billed amount' },
    ] },
    { id: 'period', header: 'Period', question: 'Which period?', options: [
      { id: 'q3', label: 'Q3 2026', description: 'Jul 1 – Sep 30' },
      { id: 'ytd', label: 'Year to date', description: 'Jan 1 – today' },
      { id: 'l12m', label: 'Last 12 months', description: 'Rolling, by service month' },
    ] },
    { id: 'split', header: 'Split by', question: 'Break it down by', multiSelect: true, options: [
      { id: 'payer', label: 'Payer', description: 'One row per payer' },
      { id: 'provider', label: 'Provider', description: 'One row per billing provider' },
      { id: 'carc', label: 'Denial reason', description: 'Grouped CARC codes' },
    ] },
  ],
};
