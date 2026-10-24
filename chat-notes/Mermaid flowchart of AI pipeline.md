<!-- STATUS: REFERENCE - your messages + what was decided/built/left open | source: chat "Mermaid flowchart of AI pipeline" (2026-10-23) -->
## User messages (verbatim)

### Message 1
current state: 

render:   mermaid
  flowchart TD
      U["User question"] --> I

      subgraph AI1["AI: plan"]
          I["Investigator<br/>picks typed tasks"]
      end

      I -->|"run_tasks"| S
      S["Server checks the plan<br/>against business definitions"] --> P{"Payer name<br/>clear?"}
      P -->|"no"| R["Payer Resolver (AI)<br/>suggests names"] --> C["User picks payers"] --> Q
      P -->|"yes"| Q["Server builds the query<br/>from fixed definitions"]
      Q --> D[("Claims data<br/>Neo4j / DuckDB")]
      D --> E["Results + evidence"]
      E -.->|"short summary,<br/>may plan another step"| I
      E --> F["Server builds tables,<br/>charts, findings"]

      subgraph AI2["AI: write"]
          W["Writer<br/>prose with placeholders"]
      end

      F --> W
      W --> V["Server fills in numbers<br/>and checks each sentence"]
      V --> A["Answer saved, then shown"]

      X["Lane 2: AI-written SQL<br/>(built, not connected)"]:::off -.-> D

      classDef off stroke-dasharray: 5 5,color:#888

  How to read it: AI makes only three kinds of call: the Investigator plans, the Payer Resolver suggests names, and the Writer writes. Everything else is the server, and the server owns every number.

  - The loop back to the Investigator is optional. A simple question is one planning call, and the Investigator only sees short result summaries.
  - The payer branch only runs when a payer name doesn't match a loaded name exactly. The user always makes the final pick.
  - Lane 2 (dashed) is the AI-written SQL path. It's built but nothing calls it yet.
  - Left out on purpose: limits, streaming, the fallback when the Writer fails, the Related follow-ups, and storage details.

### Message 2
rerender, use alternate diagram creation technique if needed

### Message 3
and render this running example, same way you just did for this previous: The running example is #2 from the repo's representative questions (docs/business/QA.md), with a payer added so you can see the Payer Resolver step. The payer names and numbers are made up, which follows the
  repo's rule against real payer names. The tasks, the denial-rate definition and the pause-and-resume behaviour follow what the code does today.

  mermaid
  sequenceDiagram
      autonumber
      actor U as User
      participant S as Server
      participant I as Investigator (AI)
      participant R as Payer Resolver (AI)
      participant D as Claims data
      participant W as Writer (AI)

      U->>S: "Which CPTs have the highest denial rate for Northwind?"
      S->>I: question + what the server can compute
      I->>S: run_tasks: denial rate by CPT, payer = "Northwind", final
      S->>S: check plan: claims from 835s, rate = recorded denials / remitted claims, min 20 claims per CPT
      S->>D: is "Northwind" an exact loaded payer name?
      D-->>S: no
      S->>R: suggest loaded names for "Northwind"
      R-->>S: Northwind Health Plan, Northwind Medicare
      S-->>U: card: which payers did you mean?
      Note over S,U: turn pauses here, no answer yet
      U->>S: ticks both names
      Note over S: resume runs the stored task, no new planning call
      S->>D: compiled, parameterized query
      D-->>S: rows: CPT 81479 = 18.4% (46 of 250), CPT 87798 = 12.1% (29 of 240) ...
      S->>S: build table + chart + finding, save evidence
      S->>W: brief of named facts (no raw records)
      W-->>S: "CPT {{e1_r0_label}} has the highest rate at {{e1_r0_rate}} ..."
      S->>S: fill in values, check each sentence
      S-->>U: answer: prose + table + chart + "how this was counted"

  What to notice:
  - Step 3: the Investigator only names a task. It never writes the query and never does the math.
  - Step 4: the server pins down the meaning:
    - "Denial" means a recorded denial (835 claim status 4), not a $0 payment.
    - CPTs with fewer than 20 claims are left out by default.
  - Steps 5–9: "Northwind" isn't an exact loaded payer name. The AI suggests candidates, but you decide, and the turn stops until you do.
  - Step 11: resuming costs no new planning call. The server reruns the task it saved.
  - Steps 15–17: the Writer sees placeholders like {{e1_r0_rate}}, not raw numbers. The server inserts the real values and checks every sentence before showing it.

  That's three AI calls in total (Investigator, Resolver, Writer). A question that names an exact payer skips steps 6–10 and takes only two.

  The chart is saved as claimclarity-knowledge/agent-example.mmd, next to the first one.

### Message 4
I wanted you to output the saem mermaid chart as the last message, just annotated with a real example.

### Message 5
save some memory of the current state, should be dynamic, with another memory pointer reminding to update, revert, and edit the current state as we progress

### Message 6
so at an abstracted layer, this is what the current workflow is?

## Notes

- Decided
  - Diagrams are drawn as hand-built SVG instead of Mermaid, because the Mermaid widget render did not work well.
  - The running example should be shown on the same flowchart (annotated), not as a separate sequence diagram.
  - The "Current state" section of the ClaimClarity memory note is a living section: edit, revert or update it in place and bump its date, instead of appending history.
- Built
  - Current-state turn flowchart as SVG, then a version annotated with the Northwind denial-rate example (the final diagram).
  - A sequence-diagram SVG of the Northwind example (the user did not want this form).
  - Two project memory files: the ClaimClarity current-state note and a preference to keep it live.
  - A review of an abstracted workflow diagram the user showed: payer should not be "Labcorp" (the customer), the plan is a typed task and not SQL, the Writer step is missing, "runs query" is not a decision, and the label should be "query" not "SQL Query".
- Left open
  - Whether the Payer Resolver is an AI call (the aggregate doc says it is search only, which would make it two AI calls, not three). The diagrams still label it "(AI)".
  - Claude's suggested label fixes (normalized payer match, streaming before save) were not applied to the diagrams.
  - The user's abstracted diagram (message 6) was never redrawn with the corrections.
