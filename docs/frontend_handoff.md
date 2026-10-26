
## ANSWER (answer surface — owner: answer team)

Everything that renders one assistant turn: `web/src/components/Message.tsx` (`AssistantTurn`, `PartView`), `web/src/components/parts/*` (renderers), `web/src/components/answer/*` (timeline, clarify, sources, states, follow-ups), `web/src/lib/{pacedText,tableReveal,frameBatch,contract}.ts`, plus `web/src/render/chartSpec.ts`. Data comes as contract v1 SSE events (`api/CONTRACT.md`, `web/src/contract/v1.gen.ts`) that `reduceV1` folds into an `AssistantViewV1` (`parts[]`, `steps[]`, `usage`, `sources`, `escalations`).

### Part renderers (`PartView` in Message.tsx)
| part `type` | renderer | notes |
|---|---|---|
| `text` | `parts/Markdown.tsx` (Streamdown, lazy KaTeX/shiki) | paced while streaming (below); prose tables have no outer border, and their tools show only on hover |
| `reasoning` | inside the step timeline (`answer/StepTimeline.tsx`) | dropped server-side by default (PHI policy, `api/reasoning_policy.py`) |
| `table` | `parts/DataTable.tsx` | inline, no outer border/zebra, toolbar on hover; `sqlPartId` → the query is shown under the table (`TableSql`), not as its own block; `rowCount > rows` → preview note + `file` chip |
| `chart` | `parts/ChartView.tsx` (vega-embed, lazy, AST interpreter, remote loads blocked) | strict `plot` (1.18) is validated before drawing; hover toolbar: Copy as image (PNG to clipboard, ✓), PNG/SVG/CSV, expand. Click a bar → a quiet "Ask about X" row that sends a follow-up (only on the live turn) |
| `code` | `parts/CodeBlock.tsx` | icon-only copy that turns into a ✓; `role:'query'` linked to a table is hidden here |
| `image`, `file`, `artifact`, `callout`, `error` | `parts/misc.tsx` | callout `assumption` items with `editable` get "Change" (pre-fills the composer, never names a data source) |
| `clarification` | `answer/States.tsx` `ClarifyCard`, or `answer/ClarifyForm.tsx` when the part has `questions[]` | see Clarify |
| `abstain` | `answer/States.tsx` `AbstainCard` | |
| `follow_ups` (provisional, not in the contract yet) | `answer/FollowUps.tsx` | ≤3 plain rows under the last finished answer; a click sends the text |
| anything else | `Unsupported` chip | forward-compatible |

Turn chrome: Sources chip ("Looked at sem.claims +N"; only when data was actually consulted), action row (copy, 👍/👎 with reasons, Regenerate + style menu incl. "Ask to change response…", which sends the instruction as the next turn), version nav, "Stopped" line (tiny, muted; Retry only if nothing arrived).

### Streaming and pacing
- **Thinking disclosure** (`StepTimeline.tsx`): collapsed by default. While live it shows one muted line naming the current step (200 ms text fade, no spinner/shimmer/timer). When done it reads "Thought for Ns", and a click expands the steps. It has no live region of its own: the shell's single status region announces "answering…" and "Answer complete" (QA-S16). `prefers-reduced-motion` turns off all animation.
- **Text** (`lib/pacedText.ts`): text that arrives after mount is revealed at backlog×dt/`DRAIN_MS`(200 ms), never slower than 250 chars/s, snapping to word ends. Finished or reopened text shows at once, and reduced motion shows everything at once. There is no caret: the last block's trailing ~3em fades (CSS mask on `.streaming-caret`) until it lands, and an empty turn shows a single pulsing dot.
- **Tables** (`lib/tableReveal.ts`): while streaming, rows appear over ≤24 frames (~400 ms). `table.start/rows` (1.9) and `chart.start/points` (1.18) draw early, and the final `part` with the same id is canonical. If `message.end` comes first, the partial is dropped.
- **Batching** (`lib/frameBatch.ts`): reducer updates paint at most once per animation frame.
- **Stop**: the result is `finishReason:'aborted'`. Content already shown stays.

### How analyzer output maps to parts (server: `api/mapping.py`, `api/live.py`, `api/stream_parts.py`)
- **Steps:** analyzer tool calls are tapped live (`LiveTap`) and sent as `step.*` events. `live.py:_EXPLORE` maps tools to probe kinds: `list_relations→list_views`, `describe_relation→describe_view`, `sample_rows/lookup_values→sample_values`, `profile`, `lookup_glossary/dictionary`, `lookup_examples→search_examples`, `query_metrics→lookup_dictionary`, `request_escalation`. `run_sql` becomes `sql`/`repair` steps with a query. The UI labels them in `answer/normalize.ts`:
  - describe/sample/profile on a schema-qualified relation read "Looked at sem.claims";
  - list_views reads "Found the tables it can read";
  - an explore-only run is summarised as "Looked at a, b +N more".
  - The user never picks a DB, schema or view (BACKEND-FIT), and nothing in the answer implies they did.
- **Outcome** (`outcome_parts`):
  - `answer.kind=="answer"` gives, in order:
    - `text` (streamed as `text.start/delta/end`, ~24-char word chunks);
    - `code` (role `query`, collapsed);
    - `table` (≤50 preview rows, `csvFileId`, `sensitivity` limited/deidentified, `source.views`);
    - `file` if more than 50 rows;
    - a `chart` when `chart_for` finds a time series or categorical breakdown;
    - `callout`s: assumptions (editable), caveats, and `phi_redaction` for PHI columns.
    - finish is `stop`.
  - `clarification` → a `clarification` part (`choices` ≤8, `allowFreeText`), finish `needs_input`. The reply comes back as `resume:{clarificationId, choiceIds?, text?}`, sent from a chip, from the multi-question form, or from the composer (QA-A30, shell).
  - `refusal` → `abstain` (category from `refuse` args, else PHI/out_of_scope), and `abstain` → `abstain` low_confidence; both finish `abstain`.
  - Anything else → `error` (`query_failed`, retryable).
- **Multi-question clarify (Claude model):**
  - The UI is done (`ClarifyForm.tsx`): 1–4 questions one at a time, ≤12-char header tabs, 2–4 options with one-line descriptions, multi-select, "Other…", Skip, and number keys. It docks into `#cc-composer-dock` when the shell provides one.
  - It replies with today's resume fields: `choiceIds` holds all picks, and `text` reads "Header: answer; …".
  - **Needs:** the contract part field `questions:[{id, header, question, options:[{id,label,description?}], multiSelect?}]`, and the analyzer's `ask` tool to emit it. Until then it renders only from the local fixture `src/fixtures/clarifyForm.ts`.
- **Follow-ups:** they need a `follow_ups` part from the server. The real analyzer must produce them without an extra paid call; otherwise leave them off and the UI shows nothing.

### Not built (answer side)
- quote-a-selection (composer quote chip): dropped in finalize; needed a shell counterpart.
- read aloud, Answer now, plan preview: excluded by Peter (12:11).

### Tests
- **vitest:** `src/components/answer/*.test.tsx` (thinkingDisclosure, backendFit, clarifyForm, followUps, quietStream, answer, sources, edgeScenarios…) and `src/components/parts/*.test.tsx` (chartActions, codeChrome, tableEdge…).
- **e2e:** `e2e/answer-edge.spec.ts`, `visual-states.spec.ts`, `contract-outcomes.spec.ts`.
