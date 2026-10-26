# Behavioural fidelity pass (QA, 2026-10-25 22:30–22:50 PT)

This pass compares 9 behaviours against how Claude.ai and ChatGPT look and act today. The references are their public UIs and screenshots, never their source.

- **How it was measured:** `web/e2e/review/fidelity2.mjs`, run in light and dark at 1440 and 390 px.
- **Shots:** `shots/qa/20261025-2231-fid2/`
- **Regression guard:** `web/e2e/fidelity.spec.ts`. Each open finding is a `gap()` ratchet.

| # | Behaviour | CC (measured) | Reference | Verdict |
|---|---|---|---|---|
| 1 | Message hover action rows | The latest answer's row is always visible. Older rows are opacity 0 until hover. Copy, Good, Bad, Regenerate (+ style chevron), More and Show SQL are 32×32 ghost buttons with 8 px radius and 16 px icons. User turns show Copy and Edit on hover. | ChatGPT: the same latest-visible / older-on-hover split, Copy/👍/👎/Regenerate/More. Claude: the same split, with Retry/Edit/Copy on user turns. | ✅ match (e2e) |
| 2 | Copy feedback wording | Message and code Copy swap their own icon and label to "Copied" for about 2 s, with no toast. Ctrl+Shift+C shows the toast "Copied last response". | ChatGPT and Claude: an in-place check-icon swap and no toast. | ✅ match (e2e) |
| 3 | Sidebar chat-row hover / active | 36 px rows, 8 px radius, 14/400. Hover and active share the **same** fill (`bg-hover`, 0.05). "…" appears on hover (always on touch). | ChatGPT: the active row has a visibly stronger fill than a hovered row. Claude: the active row is darker than hover. | 🟡 **QA-S33 P3 → shell** |
| 4 | Composer attach / send sizes | Attach is a 36 px round ghost "+". Send is a 36 px round, solid fg fill, 18 px arrow at stroke 2.5; disabled is 30 % opacity. Before this fix, Send **flashed to 100 % and dropped back to 30 %** after every answer (`cc-pop` on mount), and moved 4 px. | ChatGPT: a 36 px round "+" and a 36 px round black send. The send/stop swap happens in place with no pop. | ✅ fixed by QA (QA-S34, e2e) |
| 5 | Model / strategy picker placement | The header, top-left: "CC Analyst ⌄". | ChatGPT: the model picker sits top-left in the header. (Claude puts it inside the composer, bottom-right.) | ✅ ChatGPT pattern |
| 6 | New-chat empty state | A 28 px "What should we look into?" with the composer centred (heading at 37 % of viewport height). 4 quiet single-line outline chips with small icons. No hero or cards. | ChatGPT: a centred 28 px question plus a centred composer. Claude: a greeting with chips below. | ✅ match |
| 7 | Scroll-to-bottom | "Jump to latest": a 36 px circle with a 16 px ↓, hairline border and bg fill, centred on the column (±0 px). It straddles the composer's top edge (`-top-[18px]`, overlap −18 px), so it sits on the composer border and over the last line of content. | ChatGPT: a ~32 px circle floating about 8–16 px **above** the composer, never touching it. | 🟡 **QA-S32 P3 → shell** |
| 8 | Code block header | The header shows a lowercase language label on the left and an icon-only Copy (+ download for files) on the right. Muted fill, 13 px mono, hairline radius. | Claude: a language label and an icon copy (match). ChatGPT: the language label plus a **visible "Copy code" text** button and Edit. | ✅ Claude pattern; optional ChatGPT text label is **QA-A28 P3 → answer** |
| 9 | Stop button | Replaces Send in the same 36 px circle. Filled 12 px square, label "Stop generating", tooltip "Stop (Esc)". | ChatGPT: a black circle with a filled square, in place of send. Claude: a square stop in the same slot. | ✅ match (after the QA-S34 no-pop fix) |

One more finding from the same shots:
- **QA-A29 (P3, answer):** the "Assumptions" note is an amber filled callout with a left accent bar. That is a second accent hue, and it reads as a card.
  - **Reference:** Claude and ChatGPT both render caveats as plain muted text or a hairline block, never a tinted callout.
