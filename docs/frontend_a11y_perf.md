# Chat frontend accessibility and performance

Measured on the ClaimClarity chat UI (`web/`) against the demo API (`api/` in demo mode, ScriptedLLM, no paid calls). The after run is the split shell (`AppShell` / `ThreadView`) on `grokbot/frontend-sota`, with the accessibility and performance fixes replayed onto it. The before run is the same probes on the shell those fixes started from. Two probes:

- **Accessibility.** `web/scripts/a11y_perf_audit.mjs` runs axe-core 4.10.3 in headless Chrome. A local hold-proxy pauses the demo SSE stream so the “Analyzing” state and the first text delta can be scanned. The dev server is pointed at that proxy (`CC_API_URL`, `VITE_ANALYST_BASE=/`, demo token `demo:acme_labs:peter`). Prompt: “What is the denial rate by payer?” Theme and paper style are reached from the header “More options” menu and the Settings dialog.
- **Performance.** `web/scripts/perf_audit.mjs` loads the production build (`vite preview`) at `/?audit=1`. That flag seeds an in-memory thread and a table. It does not call the network. React’s production `<Profiler>` does not record commits, so the shell times each commit with `performance.now()` around render and `useLayoutEffect`. “Child rows” counts message rows the thread visited while rendering, not React Profiler `actualDuration`. Frame gaps come from `requestAnimationFrame`. Long tasks come from `PerformanceObserver` (`longtask`, 50ms threshold). Both the before and after runs include this timer.

Chrome: `/usr/local/bin/google-chrome`, viewport 1360×900 (desktop) and 390×844 (mobile drawer).

## Accessibility

axe impact `serious` or `critical` only. The baseline run logged every screen except `mobile-drawer-dark` (Escape did not close the drawer, so the following click never landed). That one screen was probed on its own. The after run adds the Settings dialog, which the split shell introduced.

| Screen | Before (serious/critical nodes) | After |
| --- | --- | --- |
| empty, light, sidebar open | 2 contrast | 0 |
| empty, dark | 0 | 0 |
| settings, light | — (not a separate screen yet) | 0 |
| empty, paper light | 4 contrast | 0 |
| empty, paper dark | 2 contrast | 0 |
| sidebar search | 2 contrast | 0 |
| sidebar collapsed | 1 contrast | 0 |
| empty, reduced motion | 2 contrast | 0 |
| streaming status, light | 3 contrast | 0 |
| streaming status, dark | 0 | 0 |
| streaming text, dark | 0 | 0 |
| streaming text, light | 5 contrast | 0 |
| table + chart, light | 6 contrast | 0 |
| table + chart, dark | 2 contrast | 0 |
| table + chart, paper dark | 6 contrast | 0 |
| table + chart, paper light | 8 contrast | 0 |
| feedback panel | 6 contrast | 0 |
| artifact table | 10 contrast, 1 critical (separator missing `aria-valuenow`) | 0 |
| artifact chart | 8 contrast, 1 critical (same separator) | 0 |
| artifact chart spec | 9 contrast, 1 critical (same separator) | 0 |
| artifact fullscreen | 11 contrast | 0 |
| mobile drawer, light | 2 contrast | 0 |
| mobile drawer, dark | 0 serious (separate probe; 1 minor `aria-allowed-role` on `<aside role="dialog">`) | 0 |

**Before:** 89 serious contrast nodes and 3 critical separator nodes. Failing pairs were `#8f8f8f` / `#87867f` on the light, dark, and paper surfaces (about 3.1:1 to 4.1:1) and white on the paper clay `#d97757` (3.12:1, the CC mark).

**After:** 0 axe violations of any impact on all 23 screens. The console logged two missing-resource 404s and no page errors. axe is injected with the document CSP nonce, because the app no longer allows inline scripts.

Other checks from the same run:

| Check | Before | After |
| --- | --- | --- |
| Streaming live region | Assistant turn set `aria-live="polite"` and `aria-busy="true"`. Busy on the live region suppresses the announcement. | Turn has neither attribute. A visually hidden `role="status"` (`aria-live="polite"`, `aria-atomic="true"`) publishes the new suffix about every 180ms, without resetting the timer on each token. Held-stream probe read “ClaimClarity is answering. Denial rate by payer.” Short threads keep `role="log"` on the message list. Threads over 30 messages omit it, so a recycling window is not announced. |
| Mobile drawer keyboard | Escape left the drawer open. Focus stayed on “Open sidebar”. The scrim only blocked the pointer. | Focus moves to “Close sidebar”. Escape closes the drawer and returns focus to “Open sidebar”. The drawer is a `<div role="dialog">` (`role="dialog"` is not allowed on `<aside>`). Focus is trapped and restored by the shell focus trap. |
| Reduced motion | `prefers-reduced-motion` still ran `cc-shimmer` / `cc-pulse` at `0.00001s` forever. Shimmer color computed as transparent because of `background-clip: text`. Scroll used `behavior: "smooth"` unconditionally. | Shimmer `animation-name: none`, duration `0s`, color `rgb(13, 13, 13)`. Caret animation name `none`. Scroll and the jump-to-latest control use `auto` when reduced motion is set. |
| Code and tables | Scrollable regions had no tabindex. | The conversation scroller, table body, and code blocks are focusable. The conversation scroller is `role="region"` named “Conversation”. |
| Artifact resize | Focusable `role="separator"` had no `aria-valuenow`. | `aria-valuemin`, `aria-valuenow`, and `aria-valuemax` are set. Opening the panel moves focus into it and restores focus on close. |
| Images | Toolbar was hover-only. Lightbox Escape worked only while focus was already inside the dialog. | Toolbar shows on `focus-within`. Escape closes the lightbox from a window listener, and the dialog takes focus when it opens. |
| Keyboard send (phone) | — | Tab from the top of the empty phone layout reaches the Message textarea (skip link, Open sidebar, product menu, New chat, More options, Message). |

Token changes that clear 4.5:1 on the darkest and lightest surface of each palette: neutral subtle `#6b6b6b` (light) and `#a3a3a3` (dark); paper subtle `#65645f` (light) and `#aea99c` (dark); paper accent text `#141413` on clay `#d97757`; paper links `#a84b2a` (light) and `#f0b9a4` (dark). The “De-identified” badge uses `#086b53` in light palettes (5.20:1 on the paper green wash `#daeae0`, which was 4.24:1 at `#0b7a5f`). Suggestion chips no longer fade opacity on entrance (axe sampled them mid-fade at about 1.2:1 to 2.6:1). Chart download controls no longer animate opacity, which had the same mid-fade failure in the fullscreen artifact.

## Performance

Production preview, `?audit=1`. Idle heap was about 5 MB on the baseline shell and 8.1 MB on the split shell (the shell itself is larger). The after numbers below are from a run with no other browser work in progress. “React ms” is the sum of the shell commit timer. “Worst” is the slowest of those commits. Scroll updates inside the virtualizer re-render the thread, not the shell, so a scroll walk shows up as child-row visits and frame times more than as shell commits.

### 500-message thread

Seeded in memory (alternating user and assistant text). Every message was mounted before.

| | Before | After |
| --- | --- | --- |
| Commits | 2 | 1 |
| Child rows visited | 2,000 | 73 |
| React time | 226.6 ms | 28.0 ms |
| Worst commit | 205.2 ms | 28.0 ms |
| DOM nodes | 8,341 | 252 |
| Messages in the DOM | 500 | 8 |
| Heap | 15.3 MB | 10.5 MB |
| Frame p50 / p95 / max | 16.7 / 16.8 / 233.3 ms | 16.7 / 16.7 / 16.8 ms |
| Long tasks | 1 (205 ms) | 0 |

The 233 ms frame and the 205 ms long task are gone. The mount stays inside one 16.7 ms frame.

Scrolling an already-bottomed list does not move (`scrollHeight` is already the end), so that scenario is idle frames in both runs: before p95 16.7 ms with 8,341 nodes still mounted; after p95 16.8 ms, 0 commits, 8 messages mounted, heap 10.5 MB.

A top-to-bottom walk was added for the virtual list (no before number, because the old list did not recycle). 1.2 s of scrolling: 2,266 child-row visits, shell commit time 0.7 ms (the list recycles without re-rendering the shell), frame p95 16.8 ms, frame max 16.8 ms, 0 long tasks, DOM stays at 8 messages / 252 nodes, heap 13.5 MB.

### Streaming 240 tokens onto the 500-message thread

One `setState` per token before. Tokens now coalesce to one animation frame, and only the virtual window renders. The burst appends a user turn plus an assistant turn onto the existing 500 so the cost is the long thread, not a 2-message replacement.

| | Before | After |
| --- | --- | --- |
| Commits | 242 | 137 |
| Child rows visited | 127,006 | 1,124 |
| React time | 2,686.1 ms | 174.1 ms |
| Worst commit | 25.1 ms | 3.4 ms |
| Messages in the DOM | 502 | 8 |
| DOM nodes | 8,366 | 248 |
| Heap | 96.3 MB | 15.8 MB |
| Frame p50 / p95 / max | 16.7 / 16.7 / 16.8 ms | 16.7 / 16.7 / 16.8 ms |
| Frames over 16.7 ms | 34 / 186 | 11 / 121 |
| Long tasks | 0 | 0 |

Before, each commit walked every message (~525 rows) and React held the main thread for about 2.7 s of a ~3 s stream. After, a commit walks about 8 rows and React uses about 174 ms of a ~2 s stream. Frame p95 stays on the 16.7 ms vsync. Earlier turns are memoized, so a token updates the streaming row. The virtual list pins with `behavior: "auto"` so the streaming row stays mounted. Text and reasoning deltas share `frameBatch` (one paint per animation frame).

### 10,000-row table

The table was already virtualized at 200 rows (about 24 DOM rows). The mount long task was building a TanStack row model for every row.

| | Before | After |
| --- | --- | --- |
| Commits (shell) | 1 | 1 |
| React time | 76.7 ms | 20.2 ms |
| Worst commit | 76.7 ms | 20.2 ms |
| DOM rows | 24 | 24 |
| DOM nodes | 265 | 289 |
| Heap | 45.4 MB | 12.5 MB |
| Frame max | 66.6 ms | 16.7 ms |
| Long tasks | 1 (85 ms) | 0 |

Scrolling the table: before p95 16.7 ms, 38 rows, heap 54.5 MB, 0 long tasks. After p95 16.7 ms, frame max 16.8 ms, 24 rows, heap 13.1 MB, 0 shell commits, 0 long tasks. Sorting the Claims column stays inside the table, so the shell commit counter stays at 0 in both runs. After: p95 16.7 ms, frame max 16.8 ms, 23 DOM rows, heap 14.1 MB, 0 long tasks.

## What changed

- Subtle, link, and accent-foreground tokens were darkened or lightened per palette until each pair clears WCAG AA on that palette’s surfaces. Shimmer animates `color` instead of clipping a gradient to transparent text. `prefers-reduced-motion: reduce` disables animation, transition, and smooth scrolling. The suggestion entrance moves without fading text through a failing contrast. Chart download controls appear at full opacity instead of fading through one.
- Threads longer than 30 messages virtualize (`@tanstack/react-virtual`, overscan 6). Shorter threads stay a plain list so unit tests and small chats are unchanged, and they keep `role="log"`. Rows are memoized. Token deltas flush once per animation frame; the opening and closing frames of a turn still commit immediately.
- Tables over 200 rows still virtualize, but the body sorts and filters the raw rows. It no longer builds a TanStack row model for all 10k rows. `@tanstack/react-table` is no longer a dependency.
- Streaming text is announced from a single polite status region on a 180ms timer, not from the virtualized scroller.

## Reproduce

Demo API on `127.0.0.1:8787` (`uvicorn api.app:app`, demo mode). From `web/`:

```bash
npm test
npm run build
npx vite preview --host 127.0.0.1 --port 4173 --strictPort
PERF_OUT=/tmp/perf-after.json node scripts/perf_audit.mjs http://127.0.0.1:4173
node scripts/a11y_perf_audit.mjs
```

`npm test` (202 tests), `tsc -b`, and `vite build` pass on this change.
