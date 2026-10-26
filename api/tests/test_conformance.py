"""Contract 1.12.0: the shared stream-ordering rules (api/contract/stream_rules.json) and the python runner.
web/src/contract/conformance.test.ts runs the same cases against the TS runner."""
import json

import pytest

from api.contract import gen_examples
from api.contract.conformance import RULES, check
from api.contract.conformance_cases.make_cases import CASES
from api.tests.conftest import LABS, events

CASES_JSON = json.loads((gen_examples.EXAMPLES.parent / "conformance_cases" / "cases.json").read_text())


def test_cases_json_is_up_to_date():
    assert CASES_JSON == [{"name": n, "events": e, "expect": sorted(x)} for n, e, x in CASES], \
        "run python -m api.contract.conformance_cases.make_cases"


@pytest.mark.parametrize("case", CASES_JSON, ids=lambda c: c["name"])
def test_case(case):
    assert sorted(v.rule for v in check(case["events"])) == case["expect"]


def test_every_rule_is_tripped_by_a_case():
    tripped = {r for c in CASES_JSON for r in c["expect"]}
    assert {r["id"] for r in RULES["rules"]} <= tripped


@pytest.mark.parametrize("path", sorted(gen_examples.EXAMPLES.glob("*.jsonl")), ids=lambda p: p.name)
def test_golden_examples_conform(path):
    assert check([json.loads(line) for line in path.read_text().splitlines() if line.strip()]) == []


@pytest.mark.parametrize("scenario", ["table", "chart", "repair", "abstain", "clarify", "escalation", "error",
                                      "candidates", "report"])
def test_live_demo_streams_conform(client, scenario):
    r = client.post("/api/chat", headers=LABS, json={"scenario": scenario, "messages": [
        {"id": "u1", "role": "user", "text": "Paid amount by month"}]})
    assert check(events(r)) == []


@pytest.mark.parametrize("garbage", [None, 1, "x", {}, [None], [[]], [{"type": 1}], [{"type": "part", "part": None}],
                                     [{"type": "message.end", "finishReason": {}}], [{"type": "message.start", "seq": True}]])
def test_never_raises(garbage):
    assert isinstance(check(garbage), list)
