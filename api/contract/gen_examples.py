"""Publish canonical contract example streams at api/contract/examples/<outcome>.jsonl.

Each file is one ``StreamEvent`` per line, captured from the demo backend (ScriptedLLM on synthetic data,
no paid APIs) and normalised so regenerating gives a stable diff: random ids become readable sequential ids
(``m_1``, ``s_3``, ``p_2``...); timings come from a demo run with simulated model latency. QA e2e and the frontend replay
these files; the contract tests validate every line against v1.schema.json.

    python -m api.contract.gen_examples          # (re)write the examples
    python -m api.contract.gen_examples --check  # exit 1 if the event-type sequence of any example drifted
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EXAMPLES = Path(__file__).with_name("examples")

#: outcome -> (question, scenario). Outcome names are what QA e2e asks for (web/e2e/contract-outcomes.spec.ts).
OUTCOMES: dict[str, tuple[str, str | None]] = {
    "answer": ("Paid amount by payer", "table"),
    "tool_steps": ("Top denial reason codes", "repair"),
    "abstain": ("How many appeals did we win?", "abstain"),
    "clarify": ("How bad are denials?", "clarify"),
    "escalation": ("What's our net collection rate by payer?", "escalation"),
    "chart": ("Paid amount trend by month", "chart"),
    "candidates": ("Average paid per claim by payer", "candidates"),
    "error": ("simulate an error please", "error"),
    "report": ("Paid amount by month as a report", "report"),
}
#: extra example: the answer that follows picking the first clarify choice (message.start.resumeOf).
RESUME = "clarify_resume"
REGENERATE = "regenerate_concise"  # 1.10.0: "More concise" on the `answer` example (CONTRACT.md §8.1)
#: simulated per-turn model latency (s) when writing examples, so usage/latency look like a real run.
REALISTIC_MODEL_DELAY = 0.35

_ID = re.compile(r'"((?:[a-z]{1,3})_[0-9a-z]{16,32})"')
_HEADERS = {"Authorization": "Bearer demo:acme_labs:qa@xifin.example"}


def _client(model_delay: float = 0.0):
    from fastapi.testclient import TestClient
    from api.app import Settings, create_app
    return TestClient(create_app(settings=Settings(heartbeat_seconds=30, model_delay_seconds=model_delay)))


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _post(client, body: dict) -> list[dict]:
    r = client.post("/api/chat", json=body, headers=_HEADERS)
    if r.status_code != 200:
        raise RuntimeError(f"/api/chat {r.status_code}: {r.text[:200]}")
    return [e for e in _events(r) if e.get("type") != "heartbeat"]


def normalise(events: list[dict]) -> list[dict]:
    """Replace random opaque ids with stable ``<prefix>_<n>`` ids (per prefix, in order of appearance)."""
    raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    seen: dict[str, str] = {}
    counters: dict[str, int] = {}

    def sub(m: re.Match) -> str:
        old = m.group(1)
        if old not in seen:
            prefix = old.split("_", 1)[0]
            counters[prefix] = counters.get(prefix, 0) + 1
            seen[old] = f"{prefix}_{counters[prefix]}"
        return f'"{seen[old]}"'

    return [json.loads(line) for line in _ID.sub(sub, raw).splitlines()]


def capture(model_delay: float = 0.0) -> dict[str, list[dict]]:
    """``model_delay`` simulates model latency so the published examples carry realistic timings."""
    client = _client(model_delay)
    out: dict[str, list[dict]] = {}
    for outcome, (question, scenario) in OUTCOMES.items():
        body = {"messages": [{"id": "u_1", "role": "user", "text": question}]}
        if scenario:
            body["scenario"] = scenario
        evs = _post(client, body)
        out[outcome] = evs
        if outcome == "clarify":
            start = evs[0]
            clar = next(e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == "clarification")
            choice = (clar.get("choices") or [{}])[0]
            reply = {"messages": [{"id": "u_1", "role": "user", "text": question},
                                  {"id": "u_2", "role": "user", "text": choice.get("label", "All denials")}],
                     "conversationId": start["conversationId"],
                     "resume": {"clarificationId": clar["clarificationId"],
                                **({"choiceIds": [choice["id"]]} if "id" in choice else {})}}
            out[RESUME] = _post(client, reply)
        if outcome == "answer":
            out[REGENERATE] = _post(client, {"messages": [], "conversationId": evs[0]["conversationId"],
                                             "regenerate": {"messageId": evs[0]["messageId"], "style": "concise"}})
    # Normalise the clarify pair together so resumeOf ids line up with the asking message.
    pair = normalise(out["clarify"] + out[RESUME])
    out["clarify"], out[RESUME] = pair[: len(out["clarify"])], pair[len(out["clarify"]):]
    pair = normalise(out["answer"] + out[REGENERATE])  # so regenerateOf points at the answer's message id
    out["answer"], out[REGENERATE] = pair[: len(out["answer"])], pair[len(out["answer"]):]
    return {k: (v if k in ("clarify", RESUME, "answer", REGENERATE) else normalise(v)) for k, v in out.items()}


def capabilities_example() -> dict:
    """GET /api/capabilities as the demo backend serves it (web/src/contract/capabilities.ts mirrors it offline)."""
    return _client().get("/api/capabilities").json()


def feedback_example() -> dict:
    """PUT .../messages/{id}/feedback on a demo answer (1.7.0): request + response, ids and time normalised."""
    client = _client()
    evs = _post(client, {"messages": [{"id": "u_1", "role": "user", "text": "Paid amount by payer"}],
                         "conversationId": "c_1", "scenario": "table"})
    mid = evs[0]["messageId"]
    request = {"rating": "down", "reasons": ["wrong_numbers"], "comment": "Totals look high for March."}
    response = client.put(f"/api/conversations/c_1/messages/{mid}/feedback", json=request,
                          headers=_HEADERS).json()
    response.update(messageId="m_1", updatedAt="2026-10-25T20:00:00Z")
    return {"method": "PUT", "path": "/api/conversations/c_1/messages/m_1/feedback", "request": request,
            "response": response, "clear": {"method": "DELETE", "response": {"cleared": True}}}


def share_example() -> dict:
    """PUT .../share then GET /api/shared/{id} by a colleague in the same org (1.11.0); ids and times normalised."""
    client = _client()
    _post(client, {"messages": [{"id": "u_1", "role": "user", "text": "Paid amount by service month"}],
                   "conversationId": "c_1", "scenario": "chart"})
    link = client.put("/api/conversations/c_1/share", json={}, headers=_HEADERS).json()
    colleague = {**_HEADERS, "Authorization": _HEADERS["Authorization"].rsplit(":", 1)[0] + ":colleague"}
    view = client.get(f"/api/shared/{link['shareId']}", headers=colleague).json()
    raw = json.dumps({"link": link, "view": view}).replace(link["shareId"], "sh_1")
    raw = re.sub(r'"\d{4}-\d\d-\d\dT[0-9:.]+Z"', '"2026-10-25T20:00:00Z"', raw)
    out = json.loads(json.dumps(normalise([json.loads(raw)])[0]))
    return {"share": {"method": "PUT", "path": "/api/conversations/c_1/share", "request": {}, "response": out["link"]},
            "view": {"method": "GET", "path": "/api/shared/sh_1", "response": out["view"]},
            "revoke": {"method": "DELETE", "path": "/api/conversations/c_1/share", "response": {"revoked": True}}}


def export_example() -> str:
    """GET /api/conversations/{id}/export?format=markdown for a demo answer (1.8.0); the timestamp is normalised."""
    client = _client()
    _post(client, {"messages": [{"id": "u_1", "role": "user", "text": "Paid amount by payer"}],
                   "conversationId": "c_1", "scenario": "table"})
    text = client.get("/api/conversations/c_1/export?format=markdown", headers=_HEADERS).text
    return re.sub(r"on \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "on 2026-10-25T20:00:00Z", text)


def dump(events: list[dict]) -> str:
    return "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in events)


def shape(events: list[dict]) -> list[str]:
    return [e["type"] + (f":{e['part']['type']}" if e["type"] == "part" else "") for e in events]


def load(outcome: str) -> list[dict]:
    path = EXAMPLES / f"{outcome}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    captured = capture(0.0 if check else REALISTIC_MODEL_DELAY)
    if check:
        drift = [k for k, v in captured.items()
                 if not (EXAMPLES / f"{k}.jsonl").exists() or shape(load(k)) != shape(v)]
        if drift:
            print("contract examples drifted (run python -m api.contract.gen_examples):", ", ".join(drift))
            return 1
        print(f"contract examples up to date ({len(captured)})")
        return 0
    EXAMPLES.mkdir(exist_ok=True)
    (EXAMPLES / "capabilities.json").write_text(json.dumps(capabilities_example(), indent=2) + "\n")
    print("wrote examples/capabilities.json")
    (EXAMPLES / "feedback.json").write_text(json.dumps(feedback_example(), indent=2) + "\n")
    print("wrote examples/feedback.json")
    (EXAMPLES / "share.json").write_text(json.dumps(share_example(), indent=2, ensure_ascii=False) + "\n")
    print("wrote examples/share.json")
    (EXAMPLES / "export.md").write_text(export_example())
    print("wrote examples/export.md")
    for k, v in captured.items():
        (EXAMPLES / f"{k}.jsonl").write_text(dump(v))
        print(f"wrote examples/{k}.jsonl ({len(v)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
