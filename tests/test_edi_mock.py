"""Mock EDI generator + independent validator: deterministic, valid X12, balanced money, every scenario present."""
import hashlib
import shutil
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
    return out, generate(out, Scenario.stress(days=75, volume_scale=.3))   # 75 days: corrected claims come back, PLB L6


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
        errs = validate(body.decode("utf-8", "replace"))
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
            rows = parse_file(body.decode("utf-8", "replace"), "F", tc, dict(s3_uri="x", s3_bucket="x", s3_key="x",
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
            seps.add(chr(body.removeprefix(b"\xef\xbb\xbf")[3]))
            multi += body.count(b"\nST" if b"\n" in body else b"~ST") > 1 or body.count(b"ST" + body[3:4] + b"835") > 1
    assert seps == {"*", "|"} and multi


# --- validator mutation tests: each check must fire ---------------------------------------------
@pytest.fixture(scope="module")
def samples(stress):
    out, truth = stress
    s837 = next(b for _, f, b in _files(out, truth) if f["kind"] == "837" and f["expect"] == "parsed" and _plain(b)).decode()
    s835 = next(b for _, f, b in _files(out, truth) if f["kind"] == "835" and f["expect"] == "parsed" and _plain(b)
                and b"\nCAS*CO" in b and b"\nSVC*" in b and b"\nPLB" not in b and b"\nBPR*I*" in b).decode()
    return s837, s835


def _plain(body: bytes) -> bool:
    """One '*'-delimited interchange, one segment per line (no BOM, wrapping, padding or concatenation)."""
    return body[:4] == b"ISA*" and body.count(b"~\n") == body.count(b"~") and body.count(b"ISA*") == 1


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
    ("835", r"^(DTM\*472\*)\d{8}", r"\g<1>20260231", "bad date"),
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
                                   any(ln["denied"] for ln in _current_event(a)["lines"])
                                   for a in ads),
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


def _current_event(a):
    """The latest event that is not a duplicate-claim denial (bench/ledger.py _cur_event)."""
    return ([e for e in a["events"] if not e["duplicate_denial"]] or a["events"])[-1]


def test_denials_follow_the_drivers(tmp_path):
    """Signals the agent should be able to find: risky providers (claim denials for missing information / PECOS),
    screening dx on medical-necessity tests (mostly line-level CO-50/11/167 on an otherwise paid claim)."""
    t = generate(tmp_path, Scenario(days=60, volume_scale=1, seed=11), ["acme_labs"])

    def rate(pred, any_line=False):
        xs = [a["denied"] or (any_line and any(ln["denied"] for ln in _current_event(a)["lines"]))
              for c in t["tenants"]["acme_labs"]["claims"].values() if pred(c)
              for a in c["adjudications"].values() if a["remitted_current"] and a["seq"] == 0]
        return sum(xs) / len(xs)
    assert rate(lambda c: c["risky_provider"]) > 1.3 * rate(lambda c: not c["risky_provider"])
    screening = edi_mock.SCREENING_DX
    mn = {k for k, v in edi_mock.CPTS.items() if v.medical_necessity}
    assert rate(lambda c: set(c["cpts"]) & mn and c["principal_dx"] in screening, any_line=True) > \
        1.3 * rate(lambda c: set(c["cpts"]) & mn and c["principal_dx"] not in screening, any_line=True)


def test_npis_pass_luhn_and_identities_are_synthetic():
    assert all(npi_is_valid(npi(s)) for s in range(500))
    assert npi_is_valid("1234567893") and not npi_is_valid("1234567890")   # CMS's published example
    assert all(p.name.isupper() for p in edi_mock.PAYERS)


def test_validator_status_and_cob_rules(stress):
    out, truth = stress
    files = list(_files(out, truth))
    cob = next(b.decode() for _, f, b in files if f["kind"] == "837" and b"\nSVD*" in b and _plain(b))
    assert not validate(cob)
    assert any("SVD02" in e for e in validate(_sub(cob, r"^(SVD\*\w+\*)([0-9.]+)", r"\g<1>99999")))
    assert any("SBR02" in e for e in validate(_sub(cob, r"^(SBR\*P\*)(\d\d)(\*\w+\*{6}\w+~\r?\n(?:CAS|AMT))", r"\g<1>\g<3>")))
    rem = next(b.decode() for _, f, b in files if f["kind"] == "835" and f["expect"] != "rejected" and _plain(b)
               and re.search(rb"^CLP\*\w+\*4\*[0-9.]+\*0\*", b, re.M) and b"\nPLB" not in b)
    m = re.search(r"^CLP\*(\w+)\*4\*([0-9.]+)\*0\*", rem, re.M)
    assert m
    bad = rem.replace(m.group(0), f"CLP*{m.group(1)}*4*{m.group(2)}*5*", 1)
    assert any("status 4" in e for e in validate(bad))


def test_validator_and_parser_accept_delivery_noise(samples):
    s837 = samples[0]
    variants = {
        "bom": "\ufeff" + s837,
        "leading newline": "\r\n" + s837,
        "nul padding": s837 + "\x00" * 64,
        "space padding": s837 + " " * 80,
        "wrapped at 80": "\n".join(s837.replace("\n", "")[i:i + 80] for i in range(0, len(s837.replace("\n", "")), 80)),
        "two interchanges": s837 + s837.replace("000000", "000009"),
    }
    for name, text in variants.items():
        assert not validate(text), (name, validate(text)[:2])
        rows = parse_file(text, "F", "t", dict(s3_uri="x", s3_bucket="x", s3_key="x", size_bytes=1, sha256="x"), "r",
                          datetime(2026, 1, 1))
        assert rows["c837.claim"], name


def test_generator_regressions_from_review(stress, tmp_path):
    out, truth = stress
    claims = truth["tenants"]["acme_labs"]["claims"].values()
    # a payer is never secondary to itself
    assert all(len(set(c["payers"])) == len(c["payers"]) for c in claims)
    # a replacement claim's remit echoes frequency 7 (CLP09)
    body = b"".join(b for _, f, b in _files(out, truth) if f["kind"] == "835" and f["expect"] == "parsed")
    assert re.search(rb"CLP\*[^*~]+\*(1|19)\*[^~]*\*7~", body)
    # secondary claims report the primary's payment date (DTP*573), a business day that is some 835's BPR16
    # (or a payment made before the window opened, during the generator's burn-in)
    paid_on = {d.decode() for d in re.findall(rb"BPR[*|][^~]*[*|](\d{8})~", body)}
    start = truth["scenario"]["start"].replace("-", "")
    for m in re.finditer(rb"DTP\*573\*D8\*(\d{8})", b"".join(b for _, f, b in _files(out, truth) if f["kind"] == "837")):
        d = m.group(1).decode()
        assert datetime.strptime(d, "%Y%m%d").weekday() < 5 and (d in paid_on or d < start)
    # duplicate resends: denied before or after the original pays; either way the original adjudication stays current
    orders = Counter()
    for c in claims:
        for a in c["adjudications"].values():
            if a["has_duplicate_denial"]:
                last = a["events"][-1]
                orders["after" if last["duplicate_denial"] else "before"] += 1
                original = _current_event(a)
                only_duplicates = all(e["duplicate_denial"] for e in a["events"])   # original not paid yet
                assert original["duplicate_denial"] == only_duplicates and a["paid_current"] == (
                    original["paid"] if a["remitted_current"] else 0.0)
    assert orders["before"] and orders["after"], orders
    # generator instances do not share state
    g1, g2 = edi_mock.Generator(Scenario(days=5, volume_scale=.1)), edi_mock.Generator(Scenario(days=5, volume_scale=.1))
    assert g1.plb_log is not g2.plb_log


def test_default_benchmark_scenario_exercises_money_features(tmp_path):
    """The benchmark's dataset must contain the cases its questions ask about."""
    from bench.dataset import DEFAULT
    t = generate(tmp_path, Scenario(**DEFAULT), ["acme_labs"])["tenants"]["acme_labs"]
    evs = [e for c in t["claims"].values() for a in c["adjudications"].values() for e in a["events"]]
    assert any(e["status"] == "22" and e["paid"] < 0 for e in evs), "no reversal of a paid claim"
    assert {p["code"] for p in t["plb"]} >= {"WO", "L6"}, {p["code"] for p in t["plb"]}
    assert any(c["voided"] for c in t["claims"].values())
    assert any(a["seq"] == 1 and a["paid_current"] > 0 for c in t["claims"].values() for a in c["adjudications"].values())


def test_every_parsed_value_survives_into_the_lossless_tables(stress):
    """Parser rows -> the batch's constrained staging tables (as the Fargate task stages them): no field dropped."""
    from ccsim.parser import TABLES
    from ccsim.silver_staging import RawFile, SilverBatch
    out, truth = stress
    tc, f, body = next((tc, f, b) for tc, f, b in _files(out, truth) if f["kind"] == "837" and b"\nSVD*" in b)
    meta = dict(s3_uri="x", s3_bucket="x", s3_key="x", size_bytes=1, sha256="x")
    rows = parse_file(body, "F1", tc, meta, "r1", datetime(2026, 1, 1))
    batch = SilverBatch("r1", datetime(2026, 1, 1), triggered_by="t", notes=None, parser_version="test")
    assert [ok for _, ok in batch.add_raw_files([RawFile("x", body, "F1", meta)], tc, {})] == [True]
    con = batch.finish()
    for t in TABLES:
        if t == "edi.segment":                          # tuples: the segment's location in bronze
            assert con.execute("SELECT count(*), count(byte_length) FROM edi.segment").fetchone() == (len(rows[t]),) * 2
            continue
        for col in {k for r in rows.get(t, []) for k, v in r.items() if v not in (None, [], "")}:
            n_rows = sum(1 for r in rows[t] if r.get(col) not in (None, [], ""))
            n_tab = con.execute(f'SELECT count(*) FROM {t} WHERE "{col}" IS NOT NULL').fetchone()[0]
            assert n_tab == n_rows, f"{t}.{col}: {n_rows} parsed, {n_tab} stored"


# --- volume, streaming, ledger layout ------------------------------------------------------------
def _tree_digest(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def test_target_mb_per_day_sets_volume_from_measured_bytes(tmp_path):
    """Steady-state raw volume (once remits for earlier claims have ramped up) lands within 15% of the target over
    20 business days, and still varies day to day."""
    target = .15
    sc = Scenario(start=edi_mock.CAL_START, days=edi_mock.CAL_WARMUP_DAYS + 28, seed=5, target_mb_per_day=target)
    sizes = [dd.bytes for dd in edi_mock.generate_days(tmp_path, sc, ["acme_labs"])]
    last = sizes[-20:]
    mean = sum(last) / len(last)
    assert abs(mean / (target * 1e6) - 1) < .15, mean
    assert max(last) > 1.2 * min(last)                                    # weekday pattern + noise
    assert edi_mock.claims_per_day(Scenario(volume_scale=.5), "acme_labs") == 60      # volume_scale still works
    assert edi_mock.claims_per_day(sc, "acme_labs") == edi_mock.claims_per_day(
        Scenario(target_mb_per_day=target, seed=9), "acme_labs")           # deterministic, seed-independent


def test_output_is_identical_for_any_worker_count(tmp_path):
    sc = Scenario.stress(days=12, volume_scale=.3, seed=21)
    generate(tmp_path / "w1", sc, workers=1, return_claims=False)
    generate(tmp_path / "w2", sc, workers=2, return_claims=False)
    assert _tree_digest(tmp_path / "w1") == _tree_digest(tmp_path / "w2")
    # a tenant's data does not depend on which other tenants are generated with it
    generate(tmp_path / "solo", sc, ["acme_diagnostics"], return_claims=False)
    assert _tree_digest(tmp_path / "solo" / "acme_diagnostics") == _tree_digest(tmp_path / "w1" / "acme_diagnostics")


def test_generate_days_streams_day_dirs_that_can_be_deleted(tmp_path):
    sc = Scenario(days=10, volume_scale=.2, seed=4)
    generate(tmp_path / "ref", sc)
    seen = []
    for workers in (1, 2):
        out = tmp_path / f"days{workers}"
        order = []
        for dd in edi_mock.generate_days(out, sc, workers=workers):
            assert dd.path.is_dir() and dd.bytes == sum(p.stat().st_size for p in dd.path.rglob("*.x12"))
            assert _tree_digest(dd.path) == _tree_digest(tmp_path / "ref" / dd.tenant / "raw" / f"received_date={dd.day}")
            order.append((dd.day, dd.tenant))
            shutil.rmtree(dd.path)                  # the pipeline ingests day N, then drops the local copy
        assert order == sorted(order, key=lambda k: (k[0], list(edi_mock.TENANTS).index(k[1])))
        assert edi_mock.load_truth(out) == edi_mock.load_truth(tmp_path / "ref")
        seen.append(order)
    assert seen[0] == seen[1]


def test_load_truth_round_trips_the_in_memory_ledger(tmp_path):
    sc = Scenario.stress(days=15, volume_scale=.3)
    mem = generate(tmp_path / "a", sc)
    disk = edi_mock.load_truth(tmp_path / "a")
    assert disk == mem
    pcns = list(disk["tenants"]["acme_labs"]["claims"])
    assert pcns == sorted(pcns, key=lambda p: int(p[8:]))                  # submission order
    for tc, T in disk["tenants"].items():
        assert T["n_claims"] == len(T["claims"]) == sum(1 for _ in edi_mock.iter_claims(tmp_path / "a", tc))
    assert generate(tmp_path / "gz", sc, truth_gzip=True) == dict(mem, tenants={
        tc: dict(T, claims_file=T["claims_file"] + ".gz") for tc, T in mem["tenants"].items()})
    assert edi_mock.load_truth(tmp_path / "gz")["tenants"]["acme_labs"]["claims"] == mem["tenants"]["acme_labs"]["claims"]
    sub = dict(mem, customer="acme_labs", tenants={"acme_labs": mem["tenants"]["acme_labs"]})
    edi_mock.write_truth(tmp_path / "w", sub)
    back = edi_mock.load_truth(tmp_path / "w")
    assert back["tenants"]["acme_labs"]["claims"] == mem["tenants"]["acme_labs"]["claims"]
    assert back["customer"] == "acme_labs" and list(back["tenants"]) == ["acme_labs"]


def test_memory_stays_flat_while_streaming(tmp_path):
    """Retained memory must not grow with the claims written: what remains is claims in flight, queued events and
    bounded state (deductibles reset each plan year, a capped recoupment window). Measured: ~0.5 KB per claim while
    those fill up; keeping the ledger in memory instead costs ~5.4 KB per claim. Average over two 15-day windows
    (daily volume is noisy) and stop early, so events falling past the window end do not shrink the second one."""
    import tracemalloc
    sc = Scenario(days=365, volume_scale=.3, seed=8)
    win: dict = {"a": [], "b": []}
    tracemalloc.start()
    days = edi_mock.generate_days(tmp_path, sc, ["acme_labs"])
    try:
        for i, dd in enumerate(days):
            shutil.rmtree(dd.path)
            for k, (lo, hi) in {"a": (30, 45), "b": (75, 90)}.items():
                if lo <= i < hi:
                    with open(tmp_path / "truth" / "acme_labs" / "claims.jsonl") as fh:
                        win[k].append((tracemalloc.get_traced_memory()[0], sum(1 for _ in fh)))
            if i == 90:
                break
    finally:
        days.close()
        tracemalloc.stop()
    (m1, n1), (m2, n2) = [[sum(x) / len(x) for x in zip(*win[k])] for k in "ab"]
    assert n2 - n1 > 1500
    assert (m2 - m1) / (n2 - n1) < 1500, ((m2 - m1) / (n2 - n1), m1, m2)
