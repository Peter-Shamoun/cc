# Frontend CI (`.github/workflows/web.yml`)

> **Install step (owner):** the workflow is checked in at `web/ci/web.yml`. The sprint's GitHub token has no `workflow`
> scope, so pushing to `.github/workflows/` is refused. Someone with that scope needs to run
> `git mv web/ci/web.yml .github/workflows/web.yml` and commit, or copy the file across in the GitHub UI. Until then
> the same steps run locally with `bash web/ci/run_local.sh`.

The workflow runs on pull requests into `grokbot/frontend-sota` and `claude/trusting-heisenberg-8xvazk`, and on pushes to
`grokbot/frontend-sota`, but only when `web/`, `api/` or the workflow itself changed. It runs in demo mode only. It uses no
secrets and makes no paid API calls.

| Step | Command (in `web/`) | Fails when |
|---|---|---|
| Lint: licences | `node scripts/license_audit.mjs` | a package that ships has a licence that isn't permissive (see docs/frontend_licenses.md) |
| Typecheck | `npx tsc -b` | any type error |
| Unit tests | `npx vitest run --maxWorkers=2` | any test fails, including the guard tests below |
| Build | `npx vite build` | the build fails |
| Bundle budget | `node scripts/bundle_budget.mjs` | the gzipped initial JS, initial CSS, largest lazy chunk, total JS or asset count goes over `web/bundle-budget.json`, or any .wasm / WebAssembly-compiling chunk ships (the CSP forbids it) |
| CSP check | `CSP_MODE=mock node scripts/csp_check.mjs` against `vite preview` of a mock build | a CSP violation, a console error, a missing security header, or row data in storage, the URL or the console |
| API tests (separate job) | `pytest api/tests -q` | any API test fails |

Guard tests that run inside `npm test`:
- `src/security/headers.test.ts`: the CSP and header policy. index.html has no inline script or style, and src/ uses no eval, `new Function` or injected `<style>`.
- `src/security/phi.test.tsx`: every fixture that has a table is streamed through the real App. The test then searches these sinks for row values: every console method, localStorage, sessionStorage, cookies, `location.href`, `history.pushState`/`replaceState` (state and URL), `document.title`, `window.name` and fetch URLs. It also checks that PHI columns are masked in the DOM and that only `cc.pref.*` keys are persisted.
- `src/security/licenses.test.ts`: the licence audit and the coverage of THIRD_PARTY_NOTICES.

Current bundle, after #139 (2026-10-25): initial JS is 263 KB gz, initial CSS 13.6, the largest lazy chunk is vega at 178,
total JS 720, 79 assets. Budgets: 300 / 16 / 200 / 800 / 100. Lower a budget whenever the bundle shrinks. A PR that raises one has to say why.

## Demo pacing (QA-P1/P4)

With the scripted demo LLM, the API now paces answers like a real model so the streaming UI is actually exercised:
0.25 s per model call and 0.02 s per text chunk, and at least 0.8 s per model call for the `slow` scenario (so Stop
can be tested). `CC_API_DEMO_PACE=0` turns it off; `api/tests/conftest.py` sets that, so pytest stays fast.
Explicit `CC_API_MODEL_DELAY_SECONDS` / `CC_API_STREAM_DELAY_SECONDS` always win. Pacing never applies to a
non-scripted LLM.
