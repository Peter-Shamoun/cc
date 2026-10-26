# Frontend anti-slop + fidelity audit (QA C9, 2026-10-25 22:20 PT)

Directive: PETER 10:06 PM PT. Match Claude.ai and ChatGPT as closely as possible, from observation only, and avoid "AI slop".

## Method
- Script: `web/e2e/review/slop_audit.mjs`.
- Coverage: 14 screens × light/dark × 1440/390, 54 captures. The screens are:
  - empty, answer (table + chart), tool steps, chart, clarify, abstain, error, report panel
  - edit, settings, search, shortcuts, share, phone drawer
- It reads the computed style of every visible element and flags each banned pattern from BRIEF.md:
  - gradients (excluding masks and scroll fades), backdrop or blur filters
  - box-shadow blur of 24 px or more, coloured glows
  - pill buttons, bordered "cards" inside the answer, emoji in chrome
  - Sparkles/Wand icons
  - saturated accent hues (bucketed to 30°)
  - animation durations and overshoot easings
  - type scale, column width, icon size/stroke
- Evidence: `/workspace/cc_fe/shots/qa/20261025-2214-c9/slop/`, with `slop.json` (per screen) and `slop_rollup.json`.

## Banned-list results
| Pattern | Result | Detail |
|---|---|---|
| Purple/blue gradients, gradient text | ✅ none | The only gradients are functional: the sidebar title fade mask, the table scroll-edge fades and the skeleton shimmer. |
| Glassmorphism / blur | ✅ none | — |
| Glowing borders / neon | ✅ none | — |
| Sparkle / ✨ icons | ❌ → ✅ fixed by QA | Escalation note and escalation step used lucide `Sparkles`. QA replaced it with `ArrowUpRight` and added a guard test (`qaA21A22.test.tsx` "no-slop"). |
| Emoji in chrome | ✅ none | — |
| Oversized hero heading | ✅ | Empty-state H1 is 28px/500, the same scale as ChatGPT's greeting. |
| Card-everything | 🟡 | See F2 and F5 below. |
| Heavy drop shadows | 🟡 | See F6 below. |
| Bouncy/gratuitous motion | ✅ mostly | All keyframes are ≤240 ms with no overshoot easing. The exception is `animate-ping` on the running step dot (F7). |
| Pill buttons everywhere | ⚪ by design | Retry, Send, Cancel, Create link and the chips are rounded-full. ChatGPT itself uses pill buttons for these, so this is not a deviation. |
| Too many accent colours | 🟡 | Chrome is neutral, plus green for the De-identified badge (F3), indigo for the avatar (F4), blue for the settings switch (F8), and a 5-hue timing bar (F9). |

## Fidelity deviations (filed)
| # | Screen | Us | ChatGPT / Claude (observed) | Owner | Pri |
|---|---|---|---|---|---|
| F1 | Error turn | A pink-filled card with a red border and a solid black Retry pill. | ChatGPT: muted inline text with a small red icon and a quiet outlined Regenerate. Claude: a muted inline notice. Neither fills the card. | answer QA-A23 | P2 |
| F2 | Answer table | A bordered rounded frame with a "Result" caption row inside, then a separate "View SQL" row. | ChatGPT: a table with a thin header rule and a hover toolbar, no caption. Claude: a plain markdown table. | answer QA-A24 | P3 |
| F3 | Header | A green filled "De-identified" pill in the top bar on every screen. | Neither product puts coloured badges in the header. Status lives in muted text or a menu. | shell QA-S25 | P3 |
| F4 | Sidebar account | An indigo (#4352d6) initials avatar, the only indigo on the page. | ChatGPT: a small neutral or user-picture avatar. Claude: a dark neutral circle. | shell QA-S26 | P3 |
| F5 | Empty state | Four bordered 2×2 starter cards with an icon, a title and a subtitle. | ChatGPT (2026): no cards, a few small text chips or nothing. Claude: one row of small chips under the composer. | shell QA-S27 | P3 |
| F6 | Dialogs, drawer | Modal `0 16px 70px rgba(0,0,0,.25)`; menus `0 8px 30px .16`; phone drawer `shadow-2xl`. | Both use a hairline border plus a soft, short shadow (about 0 4–12px 24–32px, alpha ≤ .12). | shell QA-S28 | P3 |
| F7 | Tool step running | `animate-ping` ripple on a 4px dot. | ChatGPT uses a text shimmer only. Claude uses a subtle spinner. | answer QA-A25 | P3 |
| F8 | Settings | A blue iOS-style switch (the only blue in the dialog); the radio-tab focus ring is blue as well. | ChatGPT's switch is neutral/black in light mode. Claude's uses its single accent. | shell QA-S29 | P3 |
| F9 | Answer meta | The timing bar uses 5 hues (#7c9cbf, #10a37f, #e8b339, #b58cd9, #d97757). | Neither shows multi-colour chrome. Use grey tints of one hue. | answer QA-A26 | P3 |
| F10 | Mobile report | autoOpen pops the report as a full-screen sheet over the chat, and the Preview tab shows a focus ring without any keyboard use. | Claude mobile shows an artifact card that the user taps to open. ChatGPT canvas on mobile opens on tap. | answer QA-A27 | P2 |
| F11 | Icons | lucide at stroke 2 (16 px ×502, 14 px ×210), plus 54 at 18/2.5. | Both products' glyphs read lighter, at about 1.5–1.75 stroke for 16–20 px icons. | shell QA-S30 (global `strokeWidth` 1.75; drop the 2.5s) | P3 |
| F12 | Column | Answer column is 720 px (45rem). | ChatGPT is about 48rem (768 px), which is also BRIEF's target. Claude is similar. | shell QA-S31 | P3 |

## Matches (no action)
- Type is ui-sans-serif 16 px. Prose is 16/27.2 (1.7). The composer is 28 px radius, and the composer focus is a soft shadow. These match ChatGPT.
- Sidebar: 260 px, #f9f9f9 / #181818, quiet text rows, date groups. This matches ChatGPT.
- The user bubble is right-aligned on a muted fill, and the assistant text sits plain on the page background.
- Motion is 120–240 ms with ease-out, plus reduced-motion support.
- Dark mode: neutral greys (#212121 main / #181818 sidebar) with no blue tint.
