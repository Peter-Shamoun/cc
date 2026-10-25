# Third-party notices (web/)

The CC chat UI is written independently; no ChatGPT, Claude, Open WebUI or Lobe Chat
source is copied. It depends on these Apache-2.0 packages, whose license texts ship in
`node_modules/<pkg>/LICENSE` and must be kept with any redistribution:

| Package | License | Copyright | Use |
|---|---|---|---|
| streamdown | Apache-2.0 | Copyright 2023 Vercel, Inc. | Streaming markdown with unfinished-markdown repair |
| remend (dependency of streamdown) | Apache-2.0 | Vercel, Inc. | Incomplete-markdown repair |

Other runtime dependencies are MIT/ISC/BSD (React, TanStack Table/Virtual, Shiki, Vega,
lucide-react, clsx). Run `npx license-checker --production --summary` in `web/` for the full list.
