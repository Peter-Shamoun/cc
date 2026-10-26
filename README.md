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
| `bench/` | Benchmark questions, splits, glossaries, the SQL guard (`bench/guard.py`) and the lake benchmarks. |
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

With the real analyzer (B1 on a Strands agent, Claude on Amazon Bedrock) on the simulated AWS login and two customers
(acme_labs, acme_diagnostics). The first start builds the world (~7 min).

```sh
aws sso login --profile apiservice                  # or any credentials the apiservice profile resolves
SNAPSHOT=$(python -c "from bench.dataset import snapshot; print(snapshot())")   # B1's example library (built once)
CC_API_ANALYZER_MODEL=bedrock CC_API_ALLOW_PAID_API=i-understand-this-calls-a-paid-model-api \
CC_API_ANALYZER_SNAPSHOT=$SNAPSHOT CC_AWS_PROFILE=apiservice CC_AWS_REGION=us-east-1 \
CC_STRANDS_MODEL_ID=us.anthropic.claude-haiku-5-5 CC_API_DEMO_PACE=0 \
CC_TRANSCRIPT_MODE=persisted CC_TRANSCRIPT_DIR=/tmp/cc-chat/blobs CC_TRANSCRIPT_KEYS_DIR=/tmp/cc-chat/keys \
python -m api.sim_server --world-dir /tmp/cc-world --tokens-file /tmp/cc-world/tokens.json --port 8787
cd web && VITE_ANALYST_BASE=/ VITE_ANALYST_DEMO_TOKEN=$(python -c "import json; print(json.load(open('/tmp/cc-world/tokens.json'))['ana@xifin.example']['idToken'])") npm run dev
```

One question without the server: `CC_API_ALLOW_PAID_API=i-understand-this-calls-a-paid-model-api python -m api.strands_smoke "Denial rate by payer last month?"`
(bench snapshot data, Bedrock via profile `apiservice`; prints steps, answer and token/cache usage).
`CC_STRANDS_MODEL_ID` defaults to `us.anthropic.claude-haiku-5-5`, `CC_AWS_PROFILE` to `apiservice`, `CC_AWS_REGION` to
the profile's region, `CC_API_TIME_ZONE` to `America/Los_Angeles`. B1's accuracy was measured on the old `claude -p`
shell route with a different prompt (no schema in the prompt, no cc tool, no date line); re-check it on the validation
set on this route before relying on the numbers.

Data only:

```sh
python -m ccsim.edi_mock /tmp/mock --days 30          # mock 837P/835 + ground-truth ledger
python -m ccsim.x12_validate /tmp/mock/*/raw/*/*/*.x12
python -m ccsim.infra --export /tmp/policies          # IAM/KMS/bucket policy JSON for Terraform
```

## Not built yet

Production AWS (real buckets, roles, keys, ECS/SQS), Entra → Cognito for real, the BAA for Bedrock, and a Strands-route
validation runner (the old `claude -p` benchmark runner was removed).
