# Third-party notices (web/)

The ClaimClarity chat UI is written independently; no ChatGPT, Claude, Open WebUI or Lobe Chat
source is copied. It depends on these Apache-2.0 packages, whose license texts ship in
`node_modules/<pkg>/LICENSE` and must be kept with any redistribution:

| Package | License | Copyright | Use |
|---|---|---|---|
| streamdown | Apache-2.0 | Copyright 2023 Vercel, Inc. | Streaming markdown with unfinished-markdown repair |
| remend (dependency of streamdown) | Apache-2.0 | Vercel, Inc. | Incomplete-markdown repair |
| @streamdown/math | Apache-2.0 | Hayden Bleasel / Vercel, Inc. | KaTeX plugin for `$…$` and `$$…$$` |
| katex (dependency of @streamdown/math) | MIT | Khan Academy | Math layout. Fonts are self-hosted via the package CSS |

Other runtime dependencies are MIT/ISC/BSD (React, TanStack Table/Virtual, Shiki, Vega,
lucide-react, clsx, KaTeX (MIT, fonts under SIL OFL 1.1 via katex/dist), remark-math, rehype-katex). Run `npx license-checker --production --summary` in `web/` for the full list.
