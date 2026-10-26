"""Contract 1.13.0 audit: machine-readable open enums, error-code registry, timestamps, descriptions, and the
error envelope on every 4xx (no exceptions left)."""
import json
import pathlib
import re

import jsonschema

from api.contract import CONTRACT_VERSION
from api.tests.conftest import LABS as DX, ask

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "api" / "contract" / "v1.schema.json").read_text())
DEFS = SCHEMA["$defs"]
CONTRACT_MD = (ROOT / "api" / "CONTRACT.md").read_text()

#: Enums that stay closed: a new value is a major change (security, outcome or protocol semantics).
CLOSED = {"Sensitivity", "MessageEnd.finishReason", "StoredMessage.finishReason", "StoredMessage.role",
          "ChatRequest.messages.role", "ChatRequest.scenario", "RegenerateStyle", "FeedbackRating",
          "ImagePart.mime", "TranscriptsInfo.mode",
          "Source.role", "ToolCallPart.state", "StepCheck.verdict",
          "ConversationArchivedFilter",
          # 1.18.0: the PlotSpec is strict by design; a client drops a plot it does not know (the table stays)
          "PlotKind", "PlotFieldType", "PlotFormat", "PlotSort",
          "IdentityScopedKey"}


def _walk(node, path):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.get("properties", {}).items():
            yield from _walk(v, f"{path}.{k}")
        if isinstance(node.get("items"), dict):
            yield from _walk(node["items"], path)
        for k in ("oneOf", "anyOf", "allOf"):
            for x in node.get(k, []):
                yield from _walk(x, path)


def every_node():
    for name, d in DEFS.items():
        yield from _walk(d, name)


def test_version_is_at_least_1_13_and_consistent():
    assert CONTRACT_VERSION == SCHEMA["version"]
    assert tuple(map(int, CONTRACT_VERSION.split("."))) >= (1, 13, 0)
    assert f"**Version: {CONTRACT_VERSION}**" in CONTRACT_MD


def test_every_def_is_documented():
    assert [k for k, v in DEFS.items() if not v.get("description")] == []


def test_every_enum_is_explicitly_open_or_closed():
    undecided = sorted(p for p, n in every_node() if "enum" in n and len(n["enum"]) > 1
                       and not n.get("x-open") and p not in CLOSED)
    assert undecided == [], "mark new enums x-open (with x-fallback) or add them to CLOSED"


def test_open_enums_have_a_valid_fallback_and_open_strings_list_known_values():
    for path, n in every_node():
        if not n.get("x-open"):
            continue
        if "enum" in n:
            assert n.get("x-fallback") in n["enum"], path
        else:
            assert n.get("type") == "string" and n.get("x-known") and len(set(n["x-known"])) == len(n["x-known"]), path
        assert "pen" in n.get("description", ""), path   # "Open…" stated in prose too


def test_timestamps_share_one_definition():
    assert DEFS["Timestamp"] == {"type": "string", "format": "date-time",
                                 "description": DEFS["Timestamp"]["description"]}
    loose = [p for p, n in every_node() if re.search(r"(At|\.at)$", p) and n.get("type") == "string"]
    assert loose == []


def test_error_code_registry_matches_the_server():
    known = set(DEFS["ErrorCode"]["x-known"])
    src = "\n".join(p.read_text() for p in (ROOT / "api").glob("*.py"))
    used = set(re.findall(r'error_json\("([a-z_]+)"', src)) | \
        set(re.findall(r'"error": \{"code": "([a-z_]+)"', src)) | \
        set(re.findall(r'"code":"([a-z_]+)"', src)) | {"not_found", "method_not_allowed"}
    used -= {"cancelled", "query_failed"}   # step/part codes, not HttpError
    assert used <= known, used - known
    for code in known:
        assert f"`{code}`" in CONTRACT_MD, code


def test_every_4xx_body_has_the_envelope(client):
    _, evs = ask(client, "Denials by payer last quarter")
    cid = evs[0]["conversationId"]
    r = client.post("/api/chat/m_nope/stop", headers=DX)
    assert r.status_code == 404 and r.json()["stopped"] is False and r.json()["error"]["code"] == "not_found"
    r = client.delete("/api/conversations/c_nope", headers=DX)
    assert r.status_code == 404 and r.json()["deleted"] is False and r.json()["error"]["code"] == "not_found"
    r = client.post("/api/chat", headers=DX, json={"conversationId": cid, "messages": [{"role": "user", "text": "x"}],
                                                   "regenerate": {"messageId": "m_nope"}})
    assert r.status_code == 404 and r.json()["error"] == {"code": "not_found", "message": "Nothing to regenerate.",
                                                           "legacyCode": "validation"}
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/HttpError", "$defs": DEFS})
    for resp in (client.get("/api/nope", headers=DX), client.put("/api/capabilities", headers=DX), r):
        validator.validate(resp.json())


def test_server_timestamps_are_iso_utc(client):
    _, evs = ask(client, "Denials by payer last quarter")
    cid = evs[0]["conversationId"]
    conv = client.get(f"/api/conversations/{cid}", headers=DX).json()
    stamps = [conv["updatedAt"]] + [m["createdAt"] for m in conv["messages"]]
    iso = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
    assert stamps and all(iso.match(s) for s in stamps), stamps
