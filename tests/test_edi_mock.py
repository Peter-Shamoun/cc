"""Mock EDI generator + independent validator: deterministic, valid X12, balanced money, every scenario present."""
import hashlib
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from ccsim import edi_mock
from ccsim.edi_mock import DENIAL_CARCS, Scenario, generate, npi, npi_is_valid
from ccsim.parser import ParseError, parse_file
from ccsim.x12_validate import validate


@pytest.fixture(scope="module")
def stress(tmp_path_factory):
    out = tmp_path_factory.mktemp("stress")
    return out, generate(out, Scenario.stress(days=40, volume_scale=.4))


def _files(out, truth):
    for tc, v in truth["tenants"].items():
        for f in v["files"]:
            yield tc, f, (out / tc / "raw" / f"received_date={f['day']}" / f["kind"] / f["name"]).read_bytes()


def test_deterministic_for_a_seed(tmp_path):
    def digest(d):
        h = hashlib.sha256()
        for p in sorted(Path(d).rglob("*.x12")):
            h.update(p.name.encode() + p.read_bytes())
        return h.hexdigest()
    generate(tmp_path / "a", Scenario(days=8, volume_scale=.2, seed=3))
    generate(tmp_path / "b", Scenario(days=8, volume_scale=.2, seed=3))
    generate(tmp_path / "c", Scenario(days=8, volume_scale=.2, seed=4))
    assert digest(tmp_path / "a") == digest(tmp_path / "b") != digest(tmp_path / "c")


def test_every_file_validates_and_every_corrupt_file_is_caught(stress):
    out, truth = stress
    outcome = Counter()
    for tc, f, body in _files(out, truth):
        errs = validate(body.decode("ascii", "replace"))
        if f["expect"] == "rejected":
            assert errs, f["name"]
        else:
            assert not errs, (f["name"], errs[:3])
        outcome[f["expect"]] += 1
    assert outcome["rejected"] and outcome["duplicate"] and outcome["parsed"]


def test_parser_agrees_with_expectations(stress):
    out, truth = stress
    for tc, f, body in _files(out, truth):
        try:
            rows = parse_file(body.decode("ascii", "replace"), "F", tc, dict(s3_uri="x", s3_bucket="x", s3_key="x",
                              size_bytes=1, sha256="x"), "r", datetime(2026, 1, 1))
        except ParseError:
            assert f["expect"] == "rejected", f["name"]
            continue
        assert f["expect"] != "rejected", f["name"]
        assert len(rows["edi.segment"]) == rows["edi.source_file"][0]["segment_count"]
        assert all(t["count_check_ok"] for t in rows["edi.transaction_set"])


def test_all_three_delimiter_styles_and_multi_st_files_appear(stress):
    out, truth = stress
    seps, multi = set(), 0
    for tc, f, body in _files(out, truth):
        if f["expect"] != "rejected":
            seps.add(chr(body[3]))
            multi += body.count(b"\nST" if b"\n" in body else b"~ST") > 1 or body.count(b"ST" + body[3:4] + b"835") > 1
    assert seps == {"*", "|"} and multi


# --- validator mutation tests: each check must fire ---------------------------------------------
@pytest.fixture(scope="module")
def samples(stress):
    out, truth = stress
    s837 = next(b for _, f, b in _files(out, truth) if f["kind"] == "837" and f["expect"] == "parsed" and b[3:4] == b"*").decode()
    s835 = next(b for _, f, b in _files(out, truth) if f["kind"] == "835" and f["expect"] == "parsed" and b[3:4] == b"*"
                and b"\nCAS*CO" in b and b"\nSVC*" in b and b"\nPLB" not in b and b"\nBPR*I*" in b).decode()
    return s837, s835


def _sub(text, pattern, repl):
    new, n = re.subn(pattern, repl, text, count=1, flags=re.M)
    assert n == 1, pattern
    return new


@pytest.mark.parametrize("which,pattern,repl,expect", [
    ("837", r"^IEA\*1\*(\d+)~", r"IEA*1*999999999~", "IEA02"),
    ("837", r"^GE\*(\d+)\*", r"GE*7*", "GE01"),
    ("837", r"^SE\*(\d+)\*", r"SE*3*", "SE01"),
    ("837", r"^(CLM\*[^*]+\*)([0-9.]+)", r"\g<1>1.23", "CLM02"),
    ("837", r"^(NM1\*85\*2\*[^~]*\*XX\*\d{9})\d", r"\g<1>0", "NPI"),
    ("837", r"^(DTP\*472\*D8\*)\d{8}", r"\g<1>20261399", "bad date"),
    ("837", r"^HL\*2\*1\*", r"HL*5*1*", "HL"),
    ("835", r"^(BPR\*I\*)([0-9.]+)", r"\g<1>9999.99", "BPR02"),
    ("835", r"^(SVC\*[^*]+\*[0-9.-]+\*)([0-9.-]+)", r"\g<1>12345", "SVC"),
    ("835", r"^(CAS\*[A-Z]{2}\*\w+\*)([0-9.-]+)", r"\g<1>0.01", "CLP"),
    ("835", r"^(N1\*PE\*[^*]+\*XX\*\d{9})\d", r"\g<1>0", "NPI"),
])
def test_validator_catches_each_mutation(samples, which, pattern, repl, expect):
    text = samples[0] if which == "837" else samples[1]
    assert not validate(text)
    errs = validate(_sub(text, pattern, repl))
    assert errs and any(expect.lower() in e.lower() for e in errs), errs


# --- ledger -------------------------------------------------------------------------------------
def test_ledger_money_balances_per_event(stress):
    _, truth = stress
    for v in truth["tenants"]["acme_labs"]["claims"].values():
        for a in v["adjudications"].values():
            for e in a["events"]:
                if not e["claim_level_only"]:
                    assert round(sum(ln["paid"] for ln in e["lines"]), 2) == e["paid"]


def test_every_scenario_occurs_under_stress(stress):
    _, truth = stress
    claims = list(truth["tenants"]["acme_labs"]["claims"].values())
    ads = [a for c in claims for a in c["adjudications"].values()]
    evs = [e for a in ads for e in a["events"]]
    seen = {
        "recorded denial": any(a["denied_recorded"] for a in ads),
        "signal v3 denial": any(a["denied_signal_v3"] for a in ads),
        "zero pay not denied": any(a["remitted_current"] and a["paid_current"] == 0 and not a["denied"] for a in ads),
        "reversal no correction": any(a["ever_reversed"] and not a["remitted_current"] for a in ads),
        "reversal + correction": any(a["ever_reversed"] and a["remitted_current"] for a in ads),
        "resubmission": any(c["resubmitted"] for c in claims),
        "void": any(c["voided"] for c in claims),
        "secondary payer": any(a["seq"] == 1 for a in ads),
        "dependent patient": any(c["dependent"] for c in claims),
        "claim-level denial": any(a["claim_level_only"] for a in ads),
        "partial line denial": any(a["remitted_current"] and a["paid_current"] > 0 and
                                   any(ln["denied"] for ln in a["events"][-1]["lines"]) for a in ads),
        "timely filing": any("29" in e["carcs"] for e in evs),
        "duplicate submission": any(a["has_duplicate_denial"] for a in ads),
        "never remitted": any(not c["adjudications"] for c in claims),
        "payer re-coded procedure": any(ln["adjudicated_cpt"] != ln["cpt"] for e in evs for ln in e["lines"]),
        "deductible": any("1" in e["carcs"] for e in evs),
        "copay": any("3" in e["carcs"] for e in evs),
        "PLB interest/recoupment/forward balance": {p["code"] for p in truth["tenants"]["acme_labs"]["plb"]} >= {"WO", "L6"},
        "risky provider": any(c["risky_provider"] for c in claims),
    }
    assert all(seen.values()), [k for k, v in seen.items() if not v]


def test_denials_follow_the_drivers(tmp_path):
    """Signals the agent should be able to find: risky providers, screening dx on medical-necessity tests."""
    t = generate(tmp_path, Scenario(days=60, volume_scale=1, seed=11), ["acme_labs"])
    rate = lambda pred: (lambda xs: sum(xs) / len(xs))(
        [a["denied"] for c in t["tenants"]["acme_labs"]["claims"].values() if pred(c)
         for a in c["adjudications"].values() if a["remitted_current"] and a["seq"] == 0])
    assert rate(lambda c: c["risky_provider"]) > 1.3 * rate(lambda c: not c["risky_provider"])
    screening = edi_mock.SCREENING_DX
    mn = {k for k, v in edi_mock.CPTS.items() if v.medical_necessity}
    assert rate(lambda c: set(c["cpts"]) & mn and c["principal_dx"] in screening) > \
        rate(lambda c: set(c["cpts"]) & mn and c["principal_dx"] not in screening)


def test_npis_pass_luhn_and_identities_are_synthetic():
    assert all(npi_is_valid(npi(s)) for s in range(500))
    assert npi_is_valid("1234567893") and not npi_is_valid("1234567890")   # CMS's published example
    assert all(p.name.isupper() for p in edi_mock.PAYERS)
