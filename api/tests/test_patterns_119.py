"""1.19.0 edge fix: no id or pattern accepts a trailing newline. Python's `re` `$` also matches before a final "\\n"
and `\\d` matches any Unicode digit, so the schema anchors with `(?![\\s\\S])` and uses `[0-9]`; plain-`re` checks in
api/ use ``fullmatch``. The shared cases in api/contract/pattern_cases.json are also run by
web/src/contract/patterns.test.ts (ECMAScript RegExp), so both validators agree."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api import app as app_mod, titles, transcripts
from api.contract import schema, validate_event
from api.tests.conftest import LABS

CASES = json.loads((Path(__file__).parents[1] / "contract" / "pattern_cases.json").read_text())["cases"]


def _pattern(path: str) -> str:
    node = schema()
    for k in path.split("."):
        node = node[k]
    return node if isinstance(node, str) else node["pattern"]


def _patterns(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("pattern", "x-temporal") and isinstance(v, str):
                yield path + ("" if k == "pattern" else ".x-temporal"), v
            else:
                yield from _patterns(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _patterns(v, f"{path}.{i}")


@pytest.mark.parametrize("case", CASES, ids=[c["path"] for c in CASES])
def test_shared_pattern_case(case):
    v = Draft202012Validator({"type": "string", "pattern": _pattern(case["path"])})
    for s in case["ok"]:
        assert v.is_valid(s), (case["path"], s)
    for s in case["bad"]:
        assert not v.is_valid(s), (case["path"], s)


def test_every_schema_pattern_is_portable_and_has_a_trailing_newline_case():
    covered = {_pattern(c["path"]): c for c in CASES}
    for path, pat in _patterns(schema()):
        assert not pat.endswith("$"), f"{path}: use (?![\\s\\S]); Python's $ accepts a trailing newline"
        assert "\\d" not in pat, f"{path}: use [0-9]; Python's \\d matches any Unicode digit"
        assert pat in covered, f"{path} has no case in pattern_cases.json"
        assert any(b.endswith("\n") for b in covered[pat]["bad"]), path


def test_events_with_a_newline_in_an_id_are_off_contract():
    ok = {"type": "status", "seq": 0, "label": None}
    validate_event(ok)
    with pytest.raises(Exception):
        validate_event({"type": "text.delta", "seq": 1, "partId": "p_1\n", "delta": "x"})
    with pytest.raises(Exception):
        validate_event({"type": "message.start", "seq": 0, "messageId": "m_1", "conversationId": "c_1",
                        "contractVersion": "1.19.0\n"})


def test_python_side_id_checks_use_fullmatch():
    for rx in (app_mod._SAFE_ID, transcripts._SAFE):
        assert rx.fullmatch("c_1") and not rx.fullmatch("c_1\n")
    assert titles._IDENT.fullmatch("paid_amount") and not titles._IDENT.fullmatch("paid_amount\n")


@pytest.mark.parametrize("cid", ["c_1%0A", "c_1%0D%0A", "c_1%00"])
def test_routes_treat_a_newline_id_as_not_found(cid):
    client = TestClient(app_mod.create_app(settings=app_mod.Settings(heartbeat_seconds=5)))
    for url in (f"/api/conversations/{cid}", f"/api/conversations/{cid}/messages/m_1/export?format=md"):
        r = client.get(url, headers=LABS)
        assert r.status_code in (404, 422), (url, r.status_code)
    r = client.post("/api/chat", json={"conversationId": "c_1\n", "messages": [{"id": "u1", "role": "user",
                                                                               "text": "How many claims?"}]},
                    headers=LABS)
    assert r.status_code == 422
