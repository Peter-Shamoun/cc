# ClaimClarity architecture docs: project brief

## Goal

A Confluence-ready architecture set that the company's IT and security teams can build, secure and operate from.
The set has three parts:
- one main board, the overview;
- five drill-downs (00–04), one per app area;
- a short document with one section per board (image plus a 3–8 row spec table).

Formats:
- diagrams as PNG and SVG, with an editable source for each;
- one markdown file;
- HTML and PDF.

Middle-ground length: about 15–20 PDF pages. No 100-page sets.

## What exists

- **Main board, keep it.** `docs/architecture/drilldowns/main.json`, rendered to `drilldowns/main.png` and `.svg`. It
  was converted on 2026-10-27 from the enhanced original icon drawio (`diagrams/CC_Data_Backend_uncompressed.drawio`)
  so the user can edit it in the diagram editor like the drill-downs. The user said "the general diagram is fine".
  Don't redesign it.
- **The user's reference for every drill-down.** `/root/.claude/uploads/0e6bb8ac-ae6e-51d1-adaa-a5d39fba27c7/a72a3212-agent_analyzer.excalidraw`,
  the analyzer excalidraw. Read its JSON and render it. Copy its look and structure, then add detail.
- **Code facts.** `/home/user/cc-strands` is the core branch: `ccsim/` holds the AWS design (`infra.py` covers IAM,
  KMS and bucket policy), `api/`, `analyzer/` and `web/`. The KPI gate and preset queries exist only on the user's
  other machine. Their facts are below.

## The drill-downs (numbers match the zoom badges on the main board)

| # | Board | Must show |
|---|---|---|
| 00 | Storage & isolation | One bucket, the `customer_b/` prefixes (raw, parquet/silver, semantic_layer/gold, quarantine, audit), the customer's roles, keys and bucket policy, and Customer A denied |
| 01 | Ingestion & parse | Customer → SFTP intake → raw → `_SUCCESS` → EventBridge rule → ECS RunTask → Fargate parser task (start, parse, silver, gold, exit) → gold publish (new CURRENT version). Gates 1, 2 and 3. |
| 02 | Login & customer mapping | User → Microsoft Entra ID → assigned group → Cognito (group → one customer) → token naming the customer → gate 4 → chat API. No session details. |
| 03 | Query path | Run worker: Claim Query Agent → ClaimClarity CLI (`cli.py`) → SQL guard → over Quack → DuckDB query service. The query service exchanges the token at STS (gate 5) and reads `customer_b/semantic_layer/` (gate 6); `lossless.*` only after a reasoned escalation. Customer A is blocked by the API and by IAM. |
| 04 | Analyzer | The user's excalidraw: frontend ⇄ Claim Query Agent. The agent's inputs: the solved example bank (K = 3), references (prompt cached: rules, EDI glossary, semantic schema + allowed values), the tool and CLI reference (prompt cached), the system prompt + history (cached) and the newest prompt. It runs Claude Haiku, first decides to answer, clarify or escalate, then calls `cli.py` → SQL guard. The full lossless schema is reachable only after escalation. **Add the KPI gate** in front: a preset fits → preset KPI queries (the gate fills the parameters); otherwise → the agent. |

## Hard rules: the user's feedback, in their words where it matters

1. **"ClaimClarity", never "CC"**, anywhere visible, including the CLI ("ClaimClarity CLI", `cli.py`). The only
   exceptions are real wire names IT configures (the `cc_session` cookie); keep those out of diagrams.
2. **AWS permission gates must be obvious and sit ON the arrow they guard:** IAM, KMS, STS trust, PassRole,
   EventBridge rule, token check. "It's okay if they take up more space. Just allocate more space." Number them 1–6
   with the same meaning everywhere:
   1. the EventBridge rule matches the customer;
   2. iam:PassRole;
   3. IAM + KMS for the parser;
   4. token valid;
   5. the role trust policy allows this token (STS);
   6. IAM + KMS for the reader.
   Deny paths (Customer A) are drawn as visibly denied.
3. **DuckDB runs as its own service: the DuckDB query service**, a DuckDB Quack server (https://duckdb.org/quack/:
   DuckDB's client–server protocol; the server runs `quack_serve`, and clients `ATTACH 'quack:host'` with a token)
   on its own Fargate service. The run worker sends SQL over Quack with a per-run token. The query service holds
   the customer's 15-minute STS credentials. The agent never holds AWS credentials.
4. **"Stop making the analyzer seem like a different process."** The Claim Query Agent IS the run worker (Fargate),
   not a separate service. Model: Claude Haiku on Amazon Bedrock via the Strands SDK.
5. **Real shapes:** official AWS icons for AWS services; databases look like databases; tables like tables; files
   like files; compute like compute. Plain boxes for everything were rejected: "so hard to follow".
6. **Too much subtext was rejected.** Names plus at most a short qualifier. Detail goes in the spec tables. Don't go
   into analyzer internals that don't matter to IT (effort levels, rule counts, benchmark numbers).
7. **Sign-in order: user → Entra ID → Cognito → token.**
8. **Compute is ECS on Fargate.** No EC2. Don't mention AgentCore.
9. No build-status words ("simulated", "mock", "planned", "not built"), no regions or account ids, no model ids,
   no internal names ("B1", "claude -p").
10. **Not a single horizontal chain (user correction).** Drill-downs 02–05 came back as one left-to-right line,
    which the user rejected. Lay each board out the way the user's excalidraw does:
    - a few large zone containers side by side: "Frontend", "Claim Query Agent", the tools, and "Core principle"
      with the customer's S3 prefix nested inside;
    - inside each zone, a two-dimensional composition: a tall input column on the left (the example bank), a row of
      inputs above (the references) feeding DOWN into a central node, a side column (the prompts), and dashed
      optional parts below (the lossless schema);
    - nested trust boundaries: account ⊃ service ⊃ customer prefix, with gates on the arrows that cross them;
    - parallel labelled arrows for a two-way exchange ("writes CLI calls with SQL" →, ← "iterative querying and
      result streaming", and a red "final query + answer");
    - short annotations beside the arrows they explain (for example "SQL over a per-run channel; the agent never
      holds credentials").
    The overall flow can still run left to right between zones. No crossing arrows, no arrows through text, white
    space. Verify by looking at every render.
11. Keep the main board. Enhance it, don't replace it. The user said: "you abandoned the original drawio instead of
    enhancing".

## Settled facts

- **Scope (user decision): we own the app, not the platform.** Network & security (VPC, endpoints, NAT, firewall) and
  operations (deploy, monitoring, alarms, log retention) are owned by IT: no boards 06/07. The set is the main board
  plus drill-downs 00–04. Board 05 (chat backend) was dropped (user decision 2026-10-27).

- **Query audit (user decision: keep it, and show it with its record structure).** The DuckDB query service writes one
  JSON Lines record per SQL statement to `customer_b/audit/` (write-once, platform audit key, kept 6 years, each
  record chained by hash). Show this record as an evidence artifact on 00, 03 and the main board:
  `{"ts":"2026-10-27T14:03:11Z","customer":"customer_b","user":"ana@xifin.example","run_id":"r_7f3c","seq":3,`
  `"sql":"SELECT payer_name, avg(is_denied::INT) … (first 500 chars)","tables":["sem.remit_claims"],"rows":12,`
  `"ms":184,"escalation_reason":null,"prev_hash":"9c1e…","hash":"4b7a…"}`

- **Out of scope (user decision): chat transcripts and conversation storage.** No `conversations/` or `run-events/`
  prefix, no transcript role and no transcript key. They would need their own ids and database, which is a later
  design. The chat streams answers live over SSE.
- **Two KMS keys (user decision):** one **customer key** per customer (raw, silver, gold, quarantine) and one
  **platform audit key** (`audit/`, one key-policy statement per customer). IAM prefix policies keep roles apart.
- **No landing zone and no staging zone (user decision).** SFTP intake writes straight into `raw/`. The batch lease
  is a lock object owned by the parser, drawn as a small lock on the parser and not as a prefix or zone. Prefixes per
  customer: `raw/`, `parquet/` (silver), `semantic_layer/` (gold), `quarantine/` and `audit/`.

- **Bucket:** one bucket holds all customer data, one prefix per customer. The web app and the access logs have their
  own buckets.
- **Roles and keys:** per customer, roles for SFTP copy, parser, reader and audit. Keys: see the two-key decision above.
- **Raw:** write-once (Object Lock, 7 years). `_SUCCESS` is written last. Bad files go to `quarantine/` (kept 1 year).
- **Parser:** EventBridge retries the launch 3 times. Nothing else: no dead-letter queue, no alarm (user decision: "fallbacks for the sake of fallbacks"). The task needs 4–8 GB of memory. Gold publishes a
  new version, and `CURRENT` moves by compare-and-set; old versions stay readable for sessions already open.
- **Reader:** STS credentials last 15 minutes, renewed with the same token when fewer than 3 minutes remain. Read-only.
  Only the current gold version. `lossless.*` holds PHI-stripped tables published next to gold.
- **SQL guard:** read-only SELECT; blocks queries that would fail against the schema; 25 s per query, 60 s per
  question, 1,000 rows; refuses keyless joins over 100 million row pairs.
- **KPI gate:** one Haiku call with structured output. 16 preset queries with fixed SQL. The parameters (payer,
  payer class, month, CPT, CARC) are checked against the data. A preset that doesn't fit, or anything else wrong,
  goes to the agent.
- **Login scope (user decision 2026-10-27):** propose only Entra ID → group → Cognito lookup → token naming the customer.
  Session handling (cookie, lifetime, sign-out) is IT's design: don't draw it. Say "single sign-on", not "federate".
  No DynamoDB.
- **Chat:** the API checks the token on every request. 3 unfinished runs per user, 110 s run deadline. The answer
  streams live over SSE. The server re-runs the final SQL, so the numbers come from
  the database.
- **Audit:** CloudTrail is kept 6 years. The agent audit is hash-chained, one record per SQL, write-once. App logs are
  PHI-redacted and kept 1 year.
- **Brand tokens** (from the main board): navy `#1C2B57`, blue `#4A6BB0`, tint `#EAF0F9`, ink `#1A1D23`, line `#C9CBCE`.
  Fonts: Barlow Condensed for headings and Barlow for text. Official AWS category colours on icons.

## Tooling in this container

- **Playwright and Chromium** are preinstalled (`require('playwright')`). Don't install another copy.
- **Python 3.13, PIL, pandoc.**
- **Board sources:** `docs/architecture/drilldowns/main.json` and `<nn>-<name>.json`, the files `diagram-editor.html`
  opens and saves. `tools/build.py` renders them (set `PYTHON` to a Python with Playwright), rebuilds the editor, and
  builds the HTML and PDF.
- **The current build:** `python3 docs/architecture/tools/build.py`. It builds the HTML and PDF from
  `ClaimClarity_Architecture.md`.
