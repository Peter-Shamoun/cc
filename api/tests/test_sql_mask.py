"""Contract 1.14.0: shown-SQL literal masking (CONTRACT.md §10, api/sql_mask.py). Synthetic values only."""
import copy
import json
import pathlib
import random

import pytest
from fastapi.testclient import TestClient

from api.analyzer_adapter import Recording, events_for_recording
from api.app import Settings, create_app
from api.contract.conformance import check
from api.sql_mask import UNPARSEABLE, iter_literals, mask_event, mask_part, mask_sql, policy_from_env
from api.tests.conftest import LABS, ask, parts
from api.tests.test_titles import _validator

FIX = pathlib.Path(__file__).with_name("fixtures") / "analyzer_runs"
# invented values, the same family as fixtures/analyzer_runs/phi_reasoning.json
PHI = ["Testperson", "00123456", "1980-01-02", "XYZ123456789", "1234567893", "123-45-6789", "jane.t@example.com",
       "555-123-4567", "CLM-0004411"]

CASES = [  # (sql, phi-masked, all-masked)
    ("SELECT payer_name, COUNT(*) FROM sem.claims WHERE status = 'paid' GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
     "SELECT payer_name, COUNT(*) FROM sem.claims WHERE status = 'paid' GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
     "SELECT payer_name, COUNT(*) FROM sem.claims WHERE status = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 10"),
    ("SELECT 1 FROM c WHERE service_date BETWEEN '2026-01-01' AND '2026-03-31'",
     "SELECT 1 FROM c WHERE service_date BETWEEN '2026-01-01' AND '2026-03-31'",
     "SELECT 1 FROM c WHERE service_date BETWEEN ? AND ?"),
    ("SELECT 1 FROM c WHERE c.patient_id = 'P1' AND mrn IN ('00123456', '00999') AND dob BETWEEN '1980-01-02' AND '1980-12-31'",
     "SELECT 1 FROM c WHERE c.patient_id = ? AND mrn IN (?, ?) AND dob BETWEEN ? AND ?", None),
    ("SELECT 1 FROM c WHERE lower(last_name) LIKE 'testp%' AND CAST(dob AS date) = DATE '1980-01-02'",
     "SELECT 1 FROM c WHERE lower(last_name) LIKE ? AND CAST(dob AS date) = DATE ?", None),
    ("SELECT 1 FROM c WHERE 'Testperson' = family_name", "SELECT 1 FROM c WHERE ? = family_name", None),
    ("SELECT 1 FROM c WHERE note = 'call 555-123-4567 re 123-45-6789'", "SELECT 1 FROM c WHERE note = ?", None),
    ("SELECT 1 FROM c WHERE \"Member_ID\" = 'XYZ123456789' AND x = 'it''s'",
     "SELECT 1 FROM c WHERE \"Member_ID\" = ? AND x = 'it''s'", "SELECT 1 FROM c WHERE \"Member_ID\" = ? AND x = ?"),
    ("SELECT 1 FROM c WHERE claim_id = 'CLM-0004411' -- 'Testperson' in a comment is hidden too",
     "SELECT 1 FROM c WHERE claim_id = ? -- ?", None),
    ("SELECT 1 /* patient 123-45-6789 */ FROM c", "SELECT 1 /* ? */ FROM c", "SELECT 1 /* ? */ FROM c"),
    # DuckDB dollar-quoted strings are string literals like '…'
    ("SELECT 1 FROM c WHERE patient_last_name = $$Testperson$$", "SELECT 1 FROM c WHERE patient_last_name = ?", None),
    ("SELECT 1 FROM c WHERE note = $t$123-45-6789$t$ AND status = $$paid$$",
     "SELECT 1 FROM c WHERE note = ? AND status = $$paid$$", "SELECT 1 FROM c WHERE note = ? AND status = ?"),
    ("SELECT 1 FROM c WHERE amount > 100 AND quarter = '2026-Q1'",
     "SELECT 1 FROM c WHERE amount > 100 AND quarter = '2026-Q1'", "SELECT 1 FROM c WHERE amount > ? AND quarter = ?"),
]


@pytest.mark.parametrize("sql,phi,all_", CASES)
def test_policies(sql, phi, all_):
    assert mask_sql(sql, "phi")[0] == phi
    if all_ is not None:
        assert mask_sql(sql, "all")[0] == all_
    assert mask_sql(sql, "off") == (sql, 0)
    once, n = mask_sql(sql, "phi")
    assert mask_sql(once, "phi") == (once, 0), "idempotent"
    assert n == once.count("?") - sql.count("?")


def test_unparseable_sql_fails_closed():
    assert mask_sql("SELECT 1 WHERE mrn = '00123456", "phi") == (UNPARSEABLE, 1)
    assert mask_sql("SELECT 1 WHERE last_name = $$Testperson", "phi") == (UNPARSEABLE, 1)  # unterminated $$
    assert mask_sql("SELECT 1 WHERE mrn = '00123456", "off")[1] == 0


def test_env_typo_never_turns_masking_off():
    assert policy_from_env({}) == "phi" and policy_from_env({"CC_API_SQL_LITERAL_MASKING": "of"}) == "phi"
    assert policy_from_env({"CC_API_SQL_LITERAL_MASKING": " ALL "}) == "all"
    assert policy_from_env({"CC_API_SQL_LITERAL_MASKING": "off"}) == "off"


COLUMNS = ["patient_id", "c.member_id", "mrn", "dob", "last_name", "patient_first_name", "ssn", "subscriber_zip",
           "email", "phone", "npi", "accession_number"]
SHAPES = ["{c} = {v}", "{v} = {c}", "{c} IN ({v}, {v})", "{c} BETWEEN {v} AND {v}", "upper({c}) LIKE {v}",
          "{c} <> {v}", "CAST({c} AS text) = {v}", "{c} = DATE {v}"]


def test_property_no_phi_literal_survives_on_phi_columns():
    rnd = random.Random(1402)
    for _ in range(400):
        preds = []
        for _ in range(rnd.randint(1, 4)):
            v = "'" + rnd.choice(PHI).replace("'", "''") + "'" if rnd.random() < 0.8 else str(rnd.randint(10, 99))
            preds.append(rnd.choice(SHAPES).format(c=rnd.choice(COLUMNS), v=v))
        joiner = rnd.choice([" AND ", " OR ", "\n  AND "])
        sql = f"SELECT COUNT(*) FROM sem.claims WHERE status = 'denied' AND {joiner.join(preds)} GROUP BY 1 LIMIT 50"
        shown, n = mask_sql(sql, "phi")
        assert n >= 1 and "'denied'" in shown, sql
        assert not any(p in shown for p in PHI), (sql, shown)
        assert all(lit in ("'denied'", "1", "50") for lit in iter_literals(shown)), shown


def test_event_and_part_helpers_mark_counts_and_copy():
    ev = {"type": "step.sql_running", "stepId": "s1", "sql": "SELECT 1 FROM c WHERE mrn = '00123456'", "attempt": 1,
          "purpose": "answer"}
    out = mask_event(ev, "phi")
    assert out["maskedLiterals"] == 1 and "00123456" in ev["sql"], "input not mutated"
    cands = mask_event({"type": "step.candidates", "stepId": "s", "chosenId": "k1", "method": "vote",
                        "candidates": [{"candidateId": "k1", "sql": ev["sql"], "ok": True},
                                       {"candidateId": "k2", "sql": "SELECT 1", "ok": True}]}, "phi")
    assert cands["candidates"][0]["maskedLiterals"] == 1 and "maskedLiterals" not in cands["candidates"][1]
    code = mask_part({"type": "code", "id": "p", "language": "sql", "role": "query", "code": ev["sql"]})
    assert code["maskedLiterals"] == 1 and "00123456" not in code["code"]
    assert mask_part({"type": "code", "id": "p", "language": "python", "role": "snippet", "code": "x = '00123456'"}
                     )["code"].endswith("'00123456'"), "non-SQL code is untouched"
    tc = mask_part({"type": "tool_call", "id": "t", "tool": "run_sql", "state": "done", "input": {"sql": ev["sql"]}})
    assert "00123456" not in tc["input"]["sql"]
    art = mask_part({"type": "artifact", "id": "a", "kind": "code", "title": "SQL", "version": 1,
                     "content": {"type": "code", "id": "c", "language": "sql", "code": ev["sql"]}})
    assert "00123456" not in art["content"]["code"]
    patch = mask_event({"type": "part.patch", "partId": "p", "patch": {"code": ev["sql"]}})
    assert patch["patch"]["maskedLiterals"] == 1
    for e in (out, cands):
        _validator(e["type"] == "step.sql_running" and "StepSqlRunning" or "StepCandidates").validate({**e, "seq": 1})
    _validator("CodePart").validate(code)


def _phi_recording():
    data = json.loads((FIX / "answer.json").read_text())
    bad = "SELECT payer_name, COUNT(*) AS claims FROM sem.claims WHERE member_id = 'XYZ123456789' GROUP BY 1"
    for call in data["calls"]:
        for tc in call["completion"].get("tool_calls") or []:
            if "sql" in tc["arguments"]:
                tc["arguments"]["sql"] = bad
    data["answer"]["sql"] = bad
    return data


@pytest.mark.parametrize("policy", ["phi", "all"])
def test_adapter_stream_never_shows_the_literal(policy):
    data = _phi_recording()
    evs = events_for_recording(Recording.from_json(copy.deepcopy(data)), sql_masking=policy)
    assert "XYZ123456789" not in json.dumps(evs)
    assert any(e.get("maskedLiterals") for e in evs if e["type"] == "step.sql_running")
    assert check(evs) == []
    off = events_for_recording(Recording.from_json(data), sql_masking="off")
    assert "XYZ123456789" in json.dumps(off), "the fixture really carries the literal"


def test_demo_default_is_phi_and_leaves_demo_sql_readable(client):
    caps = client.get("/api/capabilities", headers=LABS).json()
    assert caps["sqlLiteralMasking"] == "phi"
    _, evs = ask(client, "Top denial reason codes")
    runs = [e for e in evs if e["type"] == "step.sql_running"]
    assert runs and all("maskedLiterals" not in e for e in runs) and any("'denied'" in e["sql"] for e in runs)


def test_demo_all_policy_masks_stream_transcript_and_still_answers():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5, sql_literal_masking="all")))
    assert client.get("/api/capabilities", headers=LABS).json()["sqlLiteralMasking"] == "all"
    _, evs = ask(client, "Top denial reason codes")
    runs = [e for e in evs if e["type"] == "step.sql_running"]
    assert any(e.get("maskedLiterals") for e in runs) and not any("'denied'" in e["sql"] for e in runs)
    assert evs[-1]["finishReason"] == "stop" and any(p["type"] == "table" for p in parts(evs)), \
        "the executed SQL is unchanged, so the answer still has rows"
    cid = evs[0]["conversationId"]
    stored = json.dumps(client.get(f"/api/conversations/{cid}", headers=LABS).json())
    assert "'denied'" not in stored and "maskedLiterals" in stored
    assert check(evs) == []
