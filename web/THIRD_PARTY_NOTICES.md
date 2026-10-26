# Third-party notices (web/)

We wrote the CC chat UI ourselves. It copies no source from ChatGPT, Claude, Open WebUI or Lobe Chat.
Every package that ships in the bundle is permissively licensed. `npm test` checks this against `package-lock.json`
(`src/security/licenses.test.ts`), and so does `node scripts/license_audit.mjs`. The full audit is in
`docs/frontend_licenses.md`.

When the app is redistributed, keep each package's licence text (`node_modules/<pkg>/LICENSE`) with it.
Apache-2.0 packages also need their NOTICE file kept, if they have one.

## Direct runtime dependencies

| Package | Licence | Copyright | Use |
|---|---|---|---|
| react, react-dom | MIT | Meta Platforms, Inc. and affiliates | UI runtime |
| streamdown | Apache-2.0 | Vercel, Inc. | Streaming markdown that repairs unfinished markdown |
| remend (dependency of streamdown) | Apache-2.0 | Vercel, Inc. | Repairs incomplete markdown |
| @streamdown/math | Apache-2.0 | Hayden Bleasel / Vercel, Inc. | KaTeX plugin for `$…$` and `$$…$$` |
| katex | MIT | Khan Academy and other contributors | Math layout. Fonts in `katex/dist/fonts` are SIL OFL 1.1 and are self-hosted from the build |
| react-markdown | MIT | Espen Hovlandsdal | Markdown rendering |
| remark-gfm | MIT | Titus Wormer | GitHub-flavoured markdown (tables, strikethrough) |
| shiki | MIT | Pine Wu, Anthony Fu | Code highlighting. Its bundled TextMate grammars and themes keep their upstream licences (see below) |
| vega, vega-lite, vega-embed | BSD-3-Clause | University of Washington Interactive Data Lab | Charts |
| @tanstack/react-virtual | MIT | Tanner Linsley | Virtualised table rows and long threads |
| lucide-react | ISC | Lucide Contributors (Feather icons: MIT, Cole Bemis) | Icons |
| clsx | MIT | Luke Edwards | Class names |
| zod | MIT | Colin McDonnell | Runtime mirror of the contract PlotSpec (`src/contract/plot.ts`) |

## Transitive runtime packages that are not MIT, ISC or BSD

| Package | Licence | Note |
|---|---|---|
| robust-predicates (via vega/d3-delaunay) | Unlicense | Public-domain dedication |
| KaTeX fonts | SIL OFL 1.1 | Font files only. Allowed in bundles. We may not sell them on their own, and if we modify them they must be renamed |
| Shiki grammars and themes (`@shikijs/langs`, `@shikijs/themes`) | MIT packages. Some grammars are MIT or Apache-2.0 upstream (from VS Code / textmate) | Only the grammars for sql, python, json, typescript and bash are loaded. The themes used are github-* and vitesse-* (MIT) |

## Build and test tools only (not shipped)

`lightningcss` (MPL-2.0, through Tailwind 4 and Vite), `axe-core` and `@axe-core/playwright` (MPL-2.0) run during the build
and the a11y tests. We use them unmodified and none of their code ends up in `dist/`. MPL-2.0 is file-level copyleft
and would only bind us if we changed and redistributed those files.
