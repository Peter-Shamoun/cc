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
| Bundle budget | `node scripts/bundle_budget.mjs` | the gzipped initial JS, initial CSS, largest lazy chunk, total JS or asset count goes over `web/bundle-budget.json` |
| CSP check | `CSP_MODE=mock node scripts/csp_check.mjs` against `vite preview` of a mock build | a CSP violation, a console error, a missing security header, or row data in storage, the URL or the console |
| API tests (separate job) | `pytest api/tests -q` | any API test fails |

Guard tests that run inside `npm test`:
- `src/security/headers.test.ts`: the CSP and header policy. index.html has no inline script or style, and src/ uses no eval, `new Function` or injected `<style>`.
- `src/security/phi.test.tsx`: every fixture that has a table is streamed through the real App. The test then searches these sinks for row values: every console method, localStorage, sessionStorage, cookies, `location.href`, `history.pushState`/`replaceState` (state and URL), `document.title`, `window.name` and fetch URLs. It also checks that PHI columns are masked in the DOM and that only `cc.pref.*` keys are persisted.
- `src/security/licenses.test.ts`: the licence audit and the coverage of THIRD_PARTY_NOTICES.

Current bundle, as of 2026-10-25: initial JS is 528 KB gz and the largest lazy chunk is Shiki's oniguruma wasm at 225 KB gz. The budgets
sit slightly above these numbers. Lower a budget whenever the bundle shrinks. A PR that raises one has to say why.
