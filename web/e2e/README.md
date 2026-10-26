# CC e2e (owned by the QA team)

`npm run e2e` starts the demo API (FastAPI, ScriptedLLM, synthetic DuckDB; no paid APIs) on :8140
and two Vite servers (live :5240 via the API, mock :5241 offline fixtures), then runs the desktop
(1440x900) and mobile (390x844) projects. Running servers on those ports are reused.

- `npm run e2e:perf`: 500-message thread and 10k-row table budgets (numbers in test annotations).
- `npm run e2e:update`: refresh visual baselines in `e2e/__screenshots__` after a reviewed UI change.
- Python: `CC_PYTHON=/path/to/venv/bin/python` (defaults to /workspace/cc_fe/venvA or ./.venv).

Specs: `live` (round trip, table + CSV, SQL, chart, tool steps, refusal, stop), `mock` (streaming
feel, tool states, stop/Esc, edit-and-branch, errors, sidebar, mobile drawer/overflow),
`contract-outcomes` (answer, tool steps, abstain, clarify, escalation, cost/latency, unknown
events, HTTP error), `a11y` (axe WCAG 2.1 AA in light/dark, keyboard, aria-live, reduced motion),
`visual`, `perf`.

Contract outcome tests replay `api/contract/examples/<outcome>.jsonl` (one StreamEvent per line)
and are skipped until the Contract team publishes them. Expected test ids:
`abstain`, `clarify` (options are buttons), `escalation`, `answer-meta` (cost/latency).

`gap('<backlog id>')` marks a known open issue: the test is expected to fail. When it starts
passing, delete the `gap()` line in the same PR as the fix.
