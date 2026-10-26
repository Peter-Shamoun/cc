# Frontend licence audit (web/ and api/)

Audited on 2026-10-25 against `web/package-lock.json` (448 packages), **re-checked 21:52 PDT** (462 packages, see "Re-check" below) and the Python environment used by `api/`.
To re-run the web audit:

```
cd web && node scripts/license_audit.mjs            # summary; exits 1 if a shipped package is not permissive
cd web && node scripts/license_audit.mjs --markdown # the runtime table below
```

`npm test` runs `src/security/licenses.test.ts`, so a dependency change that adds a copyleft or unlicensed package to
the bundle fails CI. The test also fails if a new non-permissive dev tool appears or a direct dependency is missing
from `web/THIRD_PARTY_NOTICES.md`.

## Result

**Nothing that ships is copyleft, unlicensed or of unknown licence.** Breakdown:

| Scope | Count | Licences |
|---|---|---|
| web runtime (bundled) | 245 | MIT 169, BSD-3-Clause 39, ISC 30, Apache-2.0 3, BSD-2-Clause 2, Unlicense 1, 0BSD 1 |
| web dev/build only | 217 | MIT 151, Apache-2.0 28, **MPL-2.0 26**, ISC 4, BSD-2-Clause 2, BSD-3-Clause 2, MIT-0 2, BlueOak-1.0.0 1, CC0-1.0 1 |
| api runtime (Python) | see below | MIT, BSD-3-Clause, Apache-2.0, plus **MPL-2.0 (certifi)** and PSF-2.0 |

## Re-check after the sprint's new dependencies (2026-10-25, 21:52 PDT)

Lockfile diff since the first audit (1b65811, 19:11 PDT): **+22 packages added, all MIT, none shipped in the bundle; 4 lockfile entries removed.**
- **Added (dev only):**
  - `oxlint` 1.89.0, plus 19 `@oxlint/binding-*` platform binaries (lint gate);
  - `@types/node` 22.20.5;
  - `undici-types` 6.21.0.
  - All MIT.
- **Removed:**
  - `@tanstack/react-table` and `@tanstack/table-core`, which the tables no longer use. Their stale line in `web/THIRD_PARTY_NOTICES.md` is removed.
  - A nested `katex`/`commander` copy, deduplicated by the katex override.
  - Runtime packages went 253 → 245.
- **No new runtime dependency:** the boot shell, Trusted Types, section error boundaries and sign-out are our own code. `npm audit` is clean (local gate).
- **Unchanged:**
  - `axe-core` / `@axe-core/playwright` (MPL-2.0, dev).
  - The `katex` and `postcss-selector-parser` overrides (same licences).
  - `api/requirements.txt`. The demo backend uses only the standard library for the new access-log and sign-out work.
- Lighthouse runs as `npx -y lighthouse@12.8.2` and isn't in the lockfile. It's Apache-2.0 and a CI tool only.

`license_audit.mjs` reports **no problems**. The only warnings are the existing MPL-2.0 dev tools in the flags table below.

## Flags (anything that is not plain permissive)

| Package | Licence | Where | Assessment |
|---|---|---|---|
| lightningcss (+ 13 platform binaries, two versions) | MPL-2.0 | web dev: Tailwind 4 / Vite CSS minifier | Build tool, used unmodified; its output (our CSS) is not covered. No action needed |
| axe-core, @axe-core/playwright | MPL-2.0 | web dev: a11y e2e tests | Test-only and never bundled. No action needed |
| certifi | MPL-2.0 | api runtime (via httpx / botocore) | CA bundle used unmodified. MPL is file-level, so shipping it unmodified only requires keeping its licence. No action needed |
| KaTeX fonts | SIL OFL 1.1 | web runtime (emitted into `dist/assets`) | Allowed to bundle with the app. Do not sell them separately; rename them if modified |
| streamdown, remend, @streamdown/math | Apache-2.0 | web runtime | Keep LICENSE (and NOTICE, if present) with any redistribution. Listed in THIRD_PARTY_NOTICES |
| robust-predicates | Unlicense | web runtime (vega → d3-delaunay) | Public domain. No action needed |
| typing_extensions | PSF-2.0 | api runtime | Permissive. No action needed |
| python-dateutil | Apache-2.0 / BSD dual | api runtime (botocore) | Permissive. No action needed |
| `claimclarity-web` (web/package.json) | UNLICENSED | our own package | This is our private package, not a dependency. Excluded from the audit |

**Recommendation:** none of these block shipping. If the product is ever distributed as a desktop or on-prem bundle, ship
`web/THIRD_PARTY_NOTICES.md` and the collected `LICENSE` files (`license_audit.mjs` lists them) with it.

## api/ (Python) runtime dependencies

The packages `api/` imports, plus what they pull in. Versions are from the dev environment, and `api/requirements.txt`
sets the lower bounds.

| Package | Version | Licence |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| boto3 | 1.43.118 | Apache-2.0 |
| botocore | 1.43.118 | Apache-2.0 |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| duckdb | 2.0.0 | MIT |
| fastapi | 0.162.0 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| idna | 3.20 | BSD-3-Clause |
| jmespath | 1.1.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pyarrow | 26.0.0 | Apache-2.0 |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| s3transfer | 0.19.3 | Apache-2.0 |
| six | 1.17.0 | MIT |
| sqlglot | 30.24.0 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

These are dev/test only and not shipped: pytest, pluggy and iniconfig (MIT), Pygments (BSD-2-Clause), and pillow (MIT-CMU, an optional `tools` extra).

## web/ runtime packages (bundled)

| Package | Version | Licence | Scope |
|---|---|---|---|
| @shikijs/core | 4.5.0 | MIT | runtime |
| @shikijs/engine-javascript | 4.5.0 | MIT | runtime |
| @shikijs/engine-oniguruma | 4.5.0 | MIT | runtime |
| @shikijs/langs | 4.5.0 | MIT | runtime |
| @shikijs/primitive | 4.5.0 | MIT | runtime |
| @shikijs/themes | 4.5.0 | MIT | runtime |
| @shikijs/types | 4.5.0 | MIT | runtime |
| @shikijs/vscode-textmate | 10.1.0 | MIT | runtime |
| @streamdown/math | 1.0.3 | Apache-2.0 | runtime |
| @tanstack/react-virtual | 3.14.22 | MIT | runtime |
| @tanstack/virtual-core | 3.19.7 | MIT | runtime |
| @types/debug | 4.1.13 | MIT | runtime |
| @types/estree | 1.0.9 | MIT | runtime |
| @types/estree-jsx | 1.0.5 | MIT | runtime |
| @types/geojson | 7946.0.16 | MIT | runtime |
| @types/hast | 3.0.5 | MIT | runtime |
| @types/katex | 0.16.8 | MIT | runtime |
| @types/mdast | 4.0.4 | MIT | runtime |
| @types/ms | 2.1.0 | MIT | runtime |
| @types/prop-types | 15.7.15 | MIT | runtime |
| @types/react | 18.3.31 | MIT | runtime |
| @types/unist | 2.0.11 | MIT | runtime |
| @types/unist | 3.0.3 | MIT | runtime |
| @ungap/structured-clone | 1.4.0 | ISC | runtime |
| ansi-regex | 6.4.0 | MIT | runtime |
| ansi-styles | 6.2.3 | MIT | runtime |
| bail | 2.0.2 | MIT | runtime |
| ccount | 2.0.1 | MIT | runtime |
| character-entities | 2.0.2 | MIT | runtime |
| character-entities-html4 | 2.1.0 | MIT | runtime |
| character-entities-legacy | 3.0.0 | MIT | runtime |
| character-reference-invalid | 2.0.1 | MIT | runtime |
| cliui | 9.0.1 | ISC | runtime |
| clsx | 2.1.1 | MIT | runtime |
| comma-separated-tokens | 2.0.3 | MIT | runtime |
| commander | 15.0.0 | MIT | runtime |
| commander | 2.20.3 | MIT | runtime |
| commander | 7.2.0 | MIT | runtime |
| csstype | 3.2.3 | MIT | runtime |
| d3-array | 3.2.4 | ISC | runtime |
| d3-color | 3.1.0 | ISC | runtime |
| d3-delaunay | 6.0.4 | ISC | runtime |
| d3-dispatch | 3.0.1 | ISC | runtime |
| d3-dsv | 3.0.1 | ISC | runtime |
| d3-ease | 3.0.1 | BSD-3-Clause | runtime |
| d3-force | 3.0.0 | ISC | runtime |
| d3-format | 3.1.2 | ISC | runtime |
| d3-geo | 3.1.1 | ISC | runtime |
| d3-geo-projection | 4.0.0 | ISC | runtime |
| d3-hierarchy | 3.1.2 | ISC | runtime |
| d3-interpolate | 3.0.1 | ISC | runtime |
| d3-path | 3.1.0 | ISC | runtime |
| d3-quadtree | 3.0.1 | ISC | runtime |
| d3-scale | 4.0.2 | ISC | runtime |
| d3-scale-chromatic | 3.1.0 | ISC | runtime |
| d3-shape | 3.2.0 | ISC | runtime |
| d3-time | 3.1.0 | ISC | runtime |
| d3-time-format | 4.1.0 | ISC | runtime |
| d3-timer | 3.0.1 | ISC | runtime |
| debug | 4.4.3 | MIT | runtime |
| decode-named-character-reference | 1.3.0 | MIT | runtime |
| delaunator | 5.1.0 | ISC | runtime |
| dequal | 2.0.3 | MIT | runtime |
| devlop | 1.1.0 | MIT | runtime |
| emoji-regex | 10.6.0 | MIT | runtime |
| entities | 6.0.1 | BSD-2-Clause | runtime |
| entities | 6.0.1 | BSD-2-Clause | runtime |
| escalade | 3.2.0 | MIT | runtime |
| escape-string-regexp | 5.0.0 | MIT | runtime |
| estree-util-is-identifier-name | 3.0.0 | MIT | runtime |
| extend | 3.0.2 | MIT | runtime |
| fast-json-patch | 3.1.1 | MIT | runtime |
| get-caller-file | 2.0.5 | ISC | runtime |
| get-east-asian-width | 1.7.0 | MIT | runtime |
| hast-util-from-dom | 5.0.1 | ISC | runtime |
| hast-util-from-html | 2.0.3 | MIT | runtime |
| hast-util-from-html-isomorphic | 2.0.0 | MIT | runtime |
| hast-util-from-parse5 | 8.0.3 | MIT | runtime |
| hast-util-parse-selector | 4.0.0 | MIT | runtime |
| hast-util-raw | 9.1.0 | MIT | runtime |
| hast-util-sanitize | 5.0.2 | MIT | runtime |
| hast-util-to-html | 9.0.5 | MIT | runtime |
| hast-util-to-jsx-runtime | 2.3.6 | MIT | runtime |
| hast-util-to-parse5 | 8.0.1 | MIT | runtime |
| hast-util-to-text | 4.0.3 | MIT | runtime |
| hast-util-whitespace | 3.0.0 | MIT | runtime |
| hastscript | 9.0.1 | MIT | runtime |
| html-url-attributes | 3.0.1 | MIT | runtime |
| html-void-elements | 3.0.0 | MIT | runtime |
| iconv-lite | 0.6.3 | MIT | runtime |
| inline-style-parser | 0.2.7 | MIT | runtime |
| internmap | 2.0.3 | ISC | runtime |
| is-alphabetical | 2.0.1 | MIT | runtime |
| is-alphanumerical | 2.0.1 | MIT | runtime |
| is-decimal | 2.0.1 | MIT | runtime |
| is-hexadecimal | 2.0.1 | MIT | runtime |
| is-plain-obj | 4.1.0 | MIT | runtime |
| js-tokens | 4.0.0 | MIT | runtime |
| json-stringify-pretty-compact | 4.0.0 | MIT | runtime |
| katex | 0.19.1 | MIT | runtime |
| longest-streak | 3.1.0 | MIT | runtime |
| loose-envify | 1.4.0 | MIT | runtime |
| lucide-react | 1.61.0 | ISC | runtime |
| markdown-table | 3.0.4 | MIT | runtime |
| marked | 18.1.1 | MIT | runtime |
| mdast-util-find-and-replace | 3.0.4 | MIT | runtime |
| mdast-util-from-markdown | 2.1.0 | MIT | runtime |
| mdast-util-gfm | 3.1.0 | MIT | runtime |
| mdast-util-gfm-autolink-literal | 2.0.1 | MIT | runtime |
| mdast-util-gfm-footnote | 2.1.0 | MIT | runtime |
| mdast-util-gfm-strikethrough | 2.0.1 | MIT | runtime |
| mdast-util-gfm-table | 2.0.0 | MIT | runtime |
| mdast-util-gfm-task-list-item | 2.0.0 | MIT | runtime |
| mdast-util-math | 3.0.0 | MIT | runtime |
| mdast-util-mdx-expression | 2.0.1 | MIT | runtime |
| mdast-util-mdx-jsx | 3.2.0 | MIT | runtime |
| mdast-util-mdxjs-esm | 2.0.1 | MIT | runtime |
| mdast-util-phrasing | 4.1.0 | MIT | runtime |
| mdast-util-to-hast | 13.2.1 | MIT | runtime |
| mdast-util-to-markdown | 2.2.0 | MIT | runtime |
| mdast-util-to-string | 4.0.0 | MIT | runtime |
| micromark | 4.0.3 | MIT | runtime |
| micromark-core-commonmark | 2.0.4 | MIT | runtime |
| micromark-extension-gfm | 3.0.0 | MIT | runtime |
| micromark-extension-gfm-autolink-literal | 2.1.0 | MIT | runtime |
| micromark-extension-gfm-footnote | 2.1.0 | MIT | runtime |
| micromark-extension-gfm-strikethrough | 2.1.0 | MIT | runtime |
| micromark-extension-gfm-table | 2.1.2 | MIT | runtime |
| micromark-extension-gfm-tagfilter | 2.0.0 | MIT | runtime |
| micromark-extension-gfm-task-list-item | 2.1.0 | MIT | runtime |
| micromark-extension-math | 3.1.0 | MIT | runtime |
| micromark-factory-destination | 2.0.1 | MIT | runtime |
| micromark-factory-label | 2.0.1 | MIT | runtime |
| micromark-factory-space | 2.1.0 | MIT | runtime |
| micromark-factory-title | 2.0.1 | MIT | runtime |
| micromark-factory-whitespace | 2.0.1 | MIT | runtime |
| micromark-util-character | 2.1.1 | MIT | runtime |
| micromark-util-chunked | 2.0.1 | MIT | runtime |
| micromark-util-classify-character | 2.0.1 | MIT | runtime |
| micromark-util-combine-extensions | 2.0.1 | MIT | runtime |
| micromark-util-decode-numeric-character-reference | 2.0.2 | MIT | runtime |
| micromark-util-decode-string | 2.0.1 | MIT | runtime |
| micromark-util-edit-map | 1.0.0 | MIT | runtime |
| micromark-util-encode | 2.0.1 | MIT | runtime |
| micromark-util-html-tag-name | 2.0.1 | MIT | runtime |
| micromark-util-normalize-identifier | 2.0.1 | MIT | runtime |
| micromark-util-resolve-all | 2.0.1 | MIT | runtime |
| micromark-util-sanitize-uri | 2.0.1 | MIT | runtime |
| micromark-util-subtokenize | 2.1.0 | MIT | runtime |
| micromark-util-symbol | 2.0.1 | MIT | runtime |
| micromark-util-types | 2.0.3 | MIT | runtime |
| ms | 2.1.3 | MIT | runtime |
| oniguruma-parser | 0.12.2 | MIT | runtime |
| oniguruma-to-es | 4.3.6 | MIT | runtime |
| parse-entities | 4.0.2 | MIT | runtime |
| parse5 | 7.3.0 | MIT | runtime |
| parse5 | 7.3.0 | MIT | runtime |
| property-information | 7.2.0 | MIT | runtime |
| react | 18.3.1 | MIT | runtime |
| react-dom | 18.3.1 | MIT | runtime |
| react-markdown | 10.1.0 | MIT | runtime |
| regex | 6.1.0 | MIT | runtime |
| regex-recursion | 6.0.2 | MIT | runtime |
| regex-utilities | 2.3.0 | MIT | runtime |
| rehype-harden | 1.1.8 | MIT | runtime |
| rehype-katex | 7.0.1 | MIT | runtime |
| rehype-raw | 7.0.0 | MIT | runtime |
| rehype-sanitize | 6.0.0 | MIT | runtime |
| remark-gfm | 4.0.1 | MIT | runtime |
| remark-math | 6.0.0 | MIT | runtime |
| remark-parse | 11.0.0 | MIT | runtime |
| remark-rehype | 11.1.2 | MIT | runtime |
| remark-stringify | 11.0.0 | MIT | runtime |
| remend | 1.4.0 | Apache-2.0 | runtime |
| robust-predicates | 3.0.3 | Unlicense | runtime |
| rw | 1.3.3 | BSD-3-Clause | runtime |
| safer-buffer | 2.1.2 | MIT | runtime |
| scheduler | 0.23.2 | MIT | runtime |
| semver | 7.8.5 | ISC | runtime |
| shiki | 4.5.0 | MIT | runtime |
| space-separated-tokens | 2.0.2 | MIT | runtime |
| streamdown | 2.7.1 | Apache-2.0 | runtime |
| string-width | 7.2.0 | MIT | runtime |
| stringify-entities | 4.0.4 | MIT | runtime |
| strip-ansi | 7.2.0 | MIT | runtime |
| style-to-js | 1.1.21 | MIT | runtime |
| style-to-object | 1.0.14 | MIT | runtime |
| tailwind-merge | 3.7.0 | MIT | runtime |
| topojson-client | 3.1.0 | ISC | runtime |
| trim-lines | 3.0.1 | MIT | runtime |
| trough | 2.2.0 | MIT | runtime |
| tslib | 2.8.1 | 0BSD | runtime |
| unified | 11.0.5 | MIT | runtime |
| unist-util-is | 6.0.1 | MIT | runtime |
| unist-util-position | 5.0.0 | MIT | runtime |
| unist-util-remove-position | 5.0.0 | MIT | runtime |
| unist-util-stringify-position | 4.0.0 | MIT | runtime |
| unist-util-visit | 5.2.0 | MIT | runtime |
| unist-util-visit-parents | 6.1.0 | MIT | runtime |
| vega | 6.4.0 | BSD-3-Clause | runtime |
| vega-canvas | 2.0.0 | BSD-3-Clause | runtime |
| vega-crossfilter | 5.1.3 | BSD-3-Clause | runtime |
| vega-dataflow | 6.1.3 | BSD-3-Clause | runtime |
| vega-embed | 7.3.0 | BSD-3-Clause | runtime |
| vega-encode | 5.2.2 | BSD-3-Clause | runtime |
| vega-event-selector | 4.0.0 | BSD-3-Clause | runtime |
| vega-expression | 6.1.0 | BSD-3-Clause | runtime |
| vega-expression | 6.2.2 | BSD-3-Clause | runtime |
| vega-force | 5.1.3 | BSD-3-Clause | runtime |
| vega-format | 2.1.3 | BSD-3-Clause | runtime |
| vega-functions | 6.2.0 | BSD-3-Clause | runtime |
| vega-geo | 5.1.3 | BSD-3-Clause | runtime |
| vega-hierarchy | 5.1.3 | BSD-3-Clause | runtime |
| vega-interpreter | 2.3.2 | BSD-3-Clause | runtime |
| vega-label | 2.1.3 | BSD-3-Clause | runtime |
| vega-lite | 6.4.3 | BSD-3-Clause | runtime |
| vega-loader | 5.1.3 | BSD-3-Clause | runtime |
| vega-parser | 7.1.3 | BSD-3-Clause | runtime |
| vega-projection | 2.1.3 | BSD-3-Clause | runtime |
| vega-regression | 2.1.3 | BSD-3-Clause | runtime |
| vega-runtime | 7.1.3 | BSD-3-Clause | runtime |
| vega-scale | 8.1.3 | BSD-3-Clause | runtime |
| vega-scenegraph | 5.3.0 | BSD-3-Clause | runtime |
| vega-schema-url-parser | 3.0.2 | BSD-3-Clause | runtime |
| vega-selections | 6.1.5 | BSD-3-Clause | runtime |
| vega-statistics | 2.0.0 | BSD-3-Clause | runtime |
| vega-themes | 3.0.0 | BSD-3-Clause | runtime |
| vega-time | 3.3.0 | BSD-3-Clause | runtime |
| vega-tooltip | 1.1.0 | BSD-3-Clause | runtime |
| vega-transforms | 5.2.2 | BSD-3-Clause | runtime |
| vega-typings | 2.3.0 | BSD-3-Clause | runtime |
| vega-util | 2.1.3 | BSD-3-Clause | runtime |
| vega-view | 6.2.0 | BSD-3-Clause | runtime |
| vega-view-transforms | 5.2.2 | BSD-3-Clause | runtime |
| vega-voronoi | 5.1.3 | BSD-3-Clause | runtime |
| vega-wordcloud | 5.1.3 | BSD-3-Clause | runtime |
| vfile | 6.0.3 | MIT | runtime |
| vfile-location | 5.0.3 | MIT | runtime |
| vfile-message | 4.0.3 | MIT | runtime |
| web-namespaces | 2.0.1 | MIT | runtime |
| wrap-ansi | 9.0.2 | MIT | runtime |
| y18n | 5.0.8 | ISC | runtime |
| yargs | 18.0.0 | MIT | runtime |
| yargs-parser | 22.0.0 | ISC | runtime |
| zwitch | 2.0.4 | MIT | runtime |
