# Frontend dependency audit

Last run: 2026-10-25, re-checked at 22:20 PDT after the sprint's new dependencies (oxlint, @types/node):
- `npm audit`: **0** vulnerabilities across 462 packages.
- `pip-audit -r api/requirements.txt`: **none**.
- Full-env `pip-audit`: only `pip` itself, as described below.

CI runs both audits: `npm run audit` in the web job and `pip-audit -r api/requirements.txt` in the api job. Locally, `web/ci/run_local.sh` runs both, the pip one whenever pip-audit or uvx is available.

## npm (web/)

Before this change, `npm audit` reported 7 findings (5 low, 2 moderate, 0 high/critical):

| Package | Path | Advisory | Runtime? | Resolution |
|---|---|---|---|---|
| katex 0.16.47 | @streamdown/math → rehype-katex / remark-math | GHSA-238p-pmpm-9mq7 (prototype pollution can bypass `trust` restrictions), fixed in 0.18.2 | **Yes.** It renders model output. | `overrides.katex = "$katex"` pins the whole tree to our direct katex ^0.19.1. That leaves one katex copy instead of two. |
| postcss-selector-parser 6.0.10 | @tailwindcss/typography | GHSA-rj75-hqrm-r3gf (quadratic selector parsing, CPU DoS), fixed in 7.1.6 | No. It's build-time only and parses our own CSS. | `overrides["postcss-selector-parser"] = "^7.1.6"`. The built CSS is **byte-identical** before and after. |

The other 5 findings (@streamdown/math, rehype-katex, remark-math, micromark-extension-math, @tailwindcss/typography) were the same advisories, inherited. After the change: **0 vulnerabilities**. vitest, build and the bundle budget are all green.

Policy:
- The CI audit fails on any severity (`--audit-level=low`).
- If an advisory can't be fixed (no patched version), add a row here explaining why it's unreachable. Then change the CI step to `--audit-level` of the next higher severity, with a dated TODO, and raise it in NOTES.md.
- Don't use `npm audit fix --force`. Use a targeted `overrides` entry and verify the build output.

## Python (api/)

`pip-audit -r api/requirements.txt` (fastapi, uvicorn, httpx, pytest, jsonschema): **no known vulnerabilities**.

A full-env `pip-audit` in the dev venv flags only `pip` 25.1.1 itself (PYSEC-2026-*, fixed in 26.2). `pip` is installer tooling and isn't shipped. CI uses the runner's current pip. `cc-design` (the repo's own package) isn't on PyPI, so pip-audit skips it.
