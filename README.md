# CC

A chat agent that answers questions about a clinical lab's claims (837P) and remittances (835). Each customer's
data lives in its own S3 prefix; the agent reads only that customer's curated tables.

This is the `core` branch: the working system only. Research runs, diagrams, drafts and earlier designs are on
`claude/trusting-heisenberg-8xvazk`.

## What's here

| Path | What it is |
|---|---|
| `ccsim/` | The AWS backend simulated locally: S3, STS, IAM, KMS, EventBridge, ECS, CloudTrail, plus Entra ID → Cognito login. Also the mock EDI generator (`edi_mock.py`), X12 validator, parser and lake loader (raw → silver → gold). See `ccsim/README.md`. |
| `claude/schema/` | The data model: lossless EDI tables (silver) and the `sem.*` semantic layer (gold). |
| `analyzer/` | The agent analyzer. Shipped bundle **B1**: one Haiku agent, effort low, free SQL over `sem.*`, briefed with the playbook (`analyzer/playbook/frozen_playbook.md`), glossary, column dictionary, allowed values, value lookup and 3 similar solved questions. It can ask clarifying questions and can escalate to `lossless.*` with a stated reason. |
| `analyst/` | Older helper modules the analyzer still imports (example retrieval, prompts). |
| `bench/` | Benchmark questions, splits, glossaries, the SQL guard (`bench/guard.py`) and the `claude -p` runner the analyzer uses. |
| `api/` | Chat API (FastAPI, SSE). Contract in `api/CONTRACT.md` (1.24.0). Conversations and run events in S3-style objects, no DynamoDB. |
| `web/` | React chat app. |
| `docs/` | `frontend_handoff.md` (how the pieces connect), `api.md`, `data-backend/` (design, S3 layout, lake results), frontend docs. |
| `research/` | Only the final results (stages 1–4, clarify, named metrics) and the files tests read. |
| `diagrams/` | `CC_Backend.drawio` (data and chat backend) and `agent_analyzer.excalidraw`. |

## Setup

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" -r api/requirements.txt
cd web && npm ci && cd ..
pytest                      # tests/, api/tests, analyzer
cd web && npm test -- --run && npm run build
```

## Run it

Demo (scripted model, synthetic data, no login):

```sh
python -m api --port 8787
cd web && VITE_ANALYST_BASE=/ VITE_ANALYST_DEMO_TOKEN=demo:acme_labs:ana@xifin.example npm run dev   # :5173
```

With the real analyzer on the simulated AWS login and two customers (acme_labs, acme_diagnostics). It uses local
headless Claude Code (`claude -p`) on your subscription; no API key. The first start builds the world (~7 min).

```sh
SNAPSHOT=$(python -c "from bench.dataset import snapshot; print(snapshot())")   # the example library (built once)
CC_API_ANALYZER_INTERACTION=claude_code_cli CC_API_ANALYZER_SNAPSHOT=$SNAPSHOT \
CC_TRANSCRIPT_MODE=persisted CC_TRANSCRIPT_DIR=/tmp/cc-chat/blobs CC_TRANSCRIPT_KEYS_DIR=/tmp/cc-chat/keys \
python -m api.sim_server --world-dir /tmp/cc-world --tokens-file /tmp/cc-world/tokens.json --port 8787
```

Send `Authorization: Bearer <idToken from tokens.json>`. Details and the other settings: `docs/frontend_handoff.md` §5.9.

Data only:

```sh
python -m ccsim.edi_mock /tmp/mock --days 30          # mock 837P/835 + ground-truth ledger
python -m ccsim.x12_validate /tmp/mock/*/raw/*/*/*.x12
python -m ccsim.infra --export /tmp/policies          # IAM/KMS/bucket policy JSON for Terraform
```

## Not built yet

Production AWS (real buckets, roles, keys, ECS/SQS), Entra → Cognito for real, and Bedrock under the BAA in place
of `claude -p`. B1's numbers were measured on the `claude -p` path; check it again on Bedrock first.
