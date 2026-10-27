# Where the current state lives (as of 2026-10-27)

The newest working version of CC is NOT in this repo. It is on Peter's other machine, uncommitted.

## Locations on the other machine

- `~/Internship/Projects/NBA/cc`: clone of peter-shamoun/cc on branch `core`. Clean copy of `core`, no local work.
- `~/Internship/Projects/NBA/dna.app.claimclarity`: the live working tree. It is a separate (team) repo with
  `origin/main` and `origin/db1/duckdb-backend`. The local branch `sprint` has no commits. The whole `core` tree was
  copied in untracked (`git archive core | tar -x`), about 5,615 files and 28 MB, mostly `mock-data/`. Peter
  chose not to commit it. Do not commit or push there unless he asks.
- All changes after `core` exist only as uncommitted files in that working tree.
  - **Do not run `git archive` from `cc` into it again**: that overwrites the changed files.
- Local run: `python -m api.sim_server --world-dir ...` on :8787, with the env from `/tmp/cc-api.env`.
  - Needs `CC_AWS_REGION=us-east-1`, AWS profile `apiservice` and Bedrock `us.anthropic.claude-haiku-5-5`.
  - Web: Vite on :5173 with `VITE_AUTH=cookie VITE_SIGN_IN_URL=/api/auth/mock-login VITE_ANALYST_BASE=/`.
  - Mock sign-in: ana@xifin.example (acme_labs), dev@xifin.example (acme_diagnostics).
  - The local end-to-end chat script is `/tmp/kpi-e2e/chat.py`.

## What is in that working tree beyond `core`

- Bug fixes 1–9 from the local bug prompts. Peter reports all landed. Bug 3 was never exercised and Bug 5 was not
  visible in the export: both are unverified.
- Standard KPI gate in front of B1, benchmarked here in `research/template_gate_results.md` with
  `bench/gate_bench.py`.
  - `analyzer/kpi_templates.yaml`: the 16 bench families as templates, with `$name` parameters and column aliases.
  - `analyzer/kpi_gate.py`, new:
    - Haiku makes the structured decision `{fits, template_id, values, assumptions, reason}`.
    - The server checks the values; payment and submission months are validated separately.
    - The SQL runs with bound parameters on B1's ToolLayer.
    - Numbers are formatted in code, then a Haiku reply writer turns them into the answer.
    - `display_sql` has the values inlined, so the shown SQL runs as is.
    - Any failure falls back to B1.
  - Also edited:
    - `api/strands_analyzer.py`: the gate runs only with the Bedrock model.
    - `analyzer/strands_agent.py`: `run=` parameter.
    - `analyzer/tools.py`: `parameters`.
    - `api/sql_mask.py`: `$name` support.
    - `api/live.py`: `route_step` with `via=template:<id>@v1|b1`.
    - `api/mapping.py`: history carries the template.
    - `api/sim_server.py`: gate logger at INFO.
  - Verified live: the 4 check questions, the "and for Cigna?" follow-up, "now show it by month" (went to
    payer_monthly_denial_rate), and a B1 follow-up with no `$` placeholders in its SQL. The UI step labels were not
    checked in a browser.

## Open items

1. Get the working tree's changes into git somewhere (port to `core`, or a local commit) before they are lost.
2. Re-measure B1 accuracy on the Strands/Bedrock path. The 85.7% was measured on `claude -p` with the old prompt.
3. The reader session's STS credentials expire after 900 s and are not renewed.
4. Verify Bug 3 and Bug 5. Check the UI step labels in a browser.
5. Run a realistic coverage test (about 50 everyday questions through the gate).
6. Recovery-rate template: needs the PM's definition.
7. `group_by` blank on the templates.
8. A repeatable regression test for the gate, including reworded questions.
9. On `core`: 26 failing old API tests, stale contract examples, and the docs (`docs/frontend_handoff.md` §5.9,
   `docs/api.md`).

## Architecture docs for Confluence (built 2026-10-27, on this branch)

- Location: `docs/architecture/`.
  - `pages/` holds one markdown file per Confluence page.
  - `png/` and `svg/` hold the diagrams. `src/` holds one `.drawio` file per diagram.
  - Bundles: `CC_Architecture.drawio` (every diagram), `.html` and `.pdf`.
- Generators: `tools/l1.py` (overview, the area pages and the analyzer), `tools/a10.py`, `a20.py`, `a30.py` and
  `a40.py` (sub-pages), all built on `tools/dlib.py`.
- Rebuild with `python3 docs/architecture/build.py`.
- `STYLE.md`'s "FINAL STRUCTURE" holds the rules the user approved.
- Decisions the user still has to make: `pages/93-open-decisions.md`.
