# Frontend QA: blind side-by-side heuristics (10 key moments)

QA cycle C8→C9, 2026-10-25. This compares how ChatGPT and Claude behave, as seen from the outside in their public web apps, with ClaimClarity at sota `773854a`.
We did not use or copy any source code, CSS or assets. Our evidence is in `/workspace/cc_fe/shots/qa/20261025-2129-c8/`.

**Gap scale.**

| Score | Meaning |
|---|---|
| 0 | At parity or ahead |
| 1 | Polish only |
| 2 | A user would notice |
| 3 | Clearly behind |

**Blind test.** Crop the frame to the message column, hide the brand, and ask: "which product is this?" If a reviewer can tell us apart only by our domain content (claims or SQL), we score 0.

| # | Moment | ChatGPT (observed convention) | Claude (observed convention) | ClaimClarity now | Gap | Owner / item |
|---|---|---|---|---|---|---|
| 1 | Empty state | One-line greeting with a centred composer that drops to the bottom after the first send. Tool chips sit inside the composer. | Time-of-day greeting with a large centred composer and a row of quick-action chips under it. | Centred greeting and composer, 4 starter cards and a scope chip. The composer moves to the bottom after the first send. | 0 | — |
| 2 | First token | A pulsing dot appears within about 100 ms. Reasoning models show "Thinking" with a shimmer, then text streams in. | An animated mark appears at once, then text streams with a soft fade. | The "Thinking…" row and timer appear at once (A15). Text arrives with a steady cadence and no layout jump. **However, the ticking timer and streaming text also land in the role=log live region (S16).** | 1 | shell QA-S16 |
| 3 | Tool step | A collapsible "Thought for Ns" header. The steps are hidden by default, and the summary line names the action. | A collapsible tool-use block per call. It shows the tool name and expands to input and output. | A StepTimeline with named steps, "Thought for Ns", repair counts and SQL behind a disclosure. **There is a summary-name concatenation bug: "Thought for 1sall failures repaired" (A22).** | 1 | answer QA-A22 |
| 4 | Table | Markdown tables have a copy button. File analysis gives interactive tables with expand and download. | Plain markdown tables, or full tables inside artifacts. | Sortable and resizable columns, tabular numerals, CSV, copy, expand, and a row-count footer. | 0 (ahead) | — |
| 5 | Chart | Interactive charts with hover values, a static/interactive toggle and download. | Charts render as code artifacts in a side panel. | SVG charts with an expand view, SVG/PNG/CSV export, a spec view and the 1.9 artifact panel. Hover tooltips exist. | 0–1 | answer (verify the 1.9 panel) |
| 6 | Clarify | Asks in prose (deep research asks 2–4 questions) and the user answers in free text. | Asks in prose. | A structured clarify card with options plus free text. It resumes in the same turn. | 0 (ahead) | — |
| 7 | Abstain | A polite plain-prose refusal, often with an alternative. | A plain-prose refusal with an explanation. | An abstain card with the reason, details and "Try asking" suggestions. Copy includes the suggestions. | 0 | — |
| 8 | Error | A muted inline "Something went wrong" with a Regenerate/Retry button. No feedback buttons appear on the failed turn. | An inline error with Retry; the partial text is kept. | A quiet error card with Retry (#222). **The full action bar (thumbs, copy, share) still shows on the failed turn (A22).** | 1 | answer QA-A22 |
| 9 | Edit | In-place edit inside the bubble with Cancel/Send. Creates "< 1/2 >" versions. Focus comes back to the bubble on cancel. | Same pattern (Save/Cancel), with version arrows. | In-place edit with versions (#216). **Focus is lost to BODY after Esc or submit and after version navigation (A21).** | 1–2 | answer QA-A21 |
| 10 | Mobile (390) | Drawer nav, a compact header, the composer pinned above the safe area, no autofocus on touch. | Same, with swipe-to-open. | Drawer with swipe, safe-area composer, autofocus off on touch (fixed in #238), 44 px targets. | 0 | — |

**Mean gap:** 0.55 / 3. The remaining gaps are all already filed: S16, A21 and A22. Clearing them brings moments 2, 3, 8 and 9 to 0. Cross-engine issues are tracked separately (see QA findings C9-prep/xb).
