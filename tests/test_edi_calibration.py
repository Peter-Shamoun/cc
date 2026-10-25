"""The generator lands within tolerance of the calibration spec (research/edi_realism_calibration.yaml) on a
moderate run: lines per claim, payer mix, denial rate and reason mix, days to pay, allowed / CLFS, patient share,
crossovers, calendar. Tolerances are wide where the spec's own confidence is low."""
import re
from collections import Counter, defaultdict
from datetime import date
from statistics import median

import pytest

from ccsim import edi_mock as m
from ccsim.edi_mock import Scenario, generate


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    out = tmp_path_factory.mktemp("calibration")
    truth = generate(out, Scenario(start=date(2026, 2, 2), days=150, volume_scale=1.5, seed=31), ["acme_labs"])
    T = truth["tenants"]["acme_labs"]
    return T, [c for c in T["claims"].values() if c["submitted"] >= "2026-02-02"]   # not the burn-in's claims


def _primary(claims):
    for c in claims:
        a = c["adjudications"].get(c["payers"][0])
        if a and a["events"]:
            yield c, a


def _initial_denials(claims):
    """XiFin's definition: a claim with a denial (any line, or a duplicate) on its first adjudication."""
    n, out = 0, []
    for c, a in _primary(claims):
        evs = [e for e in a["events"] if e["status"] != "22"]
        if not evs:
            continue
        n += 1
        real = [e for e in evs if not e["duplicate_denial"]]
        e = real[0] if real else evs[0]
        denied = [ln for ln in e["lines"] if ln["paid"] == 0 and set(ln["co_carcs"]) & set(m.DENIAL_CARCS)]
        full = e["status"] == "4" or e["claim_level_only"] or (e["lines"] and len(denied) == len(e["lines"]))
        if full or denied:
            out.append((c, e["category"] or "units_mue", "full" if full else "partial"))
        elif any(x["duplicate_denial"] for x in evs):
            out.append((c, "duplicate", "full"))
    return n, out


def test_lines_per_claim(ledger):
    _, claims = ledger
    lines = [len(c["cpts"]) for c in claims]
    assert 4.4 <= sum(lines) / len(lines) <= 5.4                         # spec mean 4.9 (3.5-6.5)
    assert sum(n == 1 for n in lines) / len(lines) <= .25                 # spec 17% single-line
    assert sum(n >= 9 for n in lines) / len(lines) >= .08                 # spec 13% with 9+
    by = defaultdict(list)
    for c in claims:
        by[c["payer_class"]].append(len(c["cpts"]))
    mean = {k: sum(v) / len(v) for k, v in by.items()}
    assert 5.3 <= mean["medicare"] <= 6.7 and 3.8 <= mean["commercial"] <= 4.9    # spec 6.0 / 4.3
    draws = [c for c in claims if c["order_type"] in ("routine_blood", "mixed_blood_plus_urine")]
    assert .68 <= sum("36415" in c["cpts"] for c in draws) / len(draws) <= .82        # venipuncture on ~75% of draws
    units = [u for c in claims for code, u in zip(c["cpts"], c["units"]) if code == "87798" and c["order_type"] == "pcr_panels"]
    assert 8 <= sum(units) / len(units) <= 14                             # spec mean 10.3 units


def test_every_code_is_on_the_2026_clfs(ledger):
    _, claims = ledger
    codes = {code for c in claims for code in c["cpts"]}
    assert "0240U" not in codes and "0241U" not in codes
    assert all(code in m.CLFS_2026 or code in m.NOT_ON_CLFS for code in codes)
    assert len(codes) > 300                                               # long tail of CLFS codes


def test_payer_mix(ledger):
    _, claims = ledger
    seg = Counter(c["segment"] for c in claims)
    for s, share in m.SEGMENT_SHARE.items():
        assert abs(seg[s] / len(claims) - share) <= max(.02, share * .2), (s, seg[s] / len(claims))
    payers = Counter(c["payers"][0] for c in claims)
    assert .6 <= sum(n for _, n in payers.most_common(10)) / len(claims) <= .82     # spec: top 10 ids ~70%
    assert len(payers) > 150                                              # long tail of payer ids


def test_denial_rate_and_reason_mix(ledger):
    _, claims = ledger
    n, den = _initial_denials(claims)
    assert .09 <= len(den) / n <= .135                                    # XiFin 2025: 11.2% of claims
    real = [d for d in den if d[1] != "duplicate"]
    assert .5 <= sum(d[2] == "partial" for d in real) / len(real) <= .75   # spec: ~60% of denials are line-level
    cats = Counter(d[1] for d in den)
    ranked = [k for k, _ in cats.most_common()]
    assert ranked[:2] == ["bundling", "duplicate"], cats
    assert .50 <= cats["bundling"] / len(real) <= .68                     # 59.4% of clinical denials
    assert .10 <= cats["duplicate"] / len(den) <= .21                     # 15.2%
    for k in ("non_covered", "prior_auth", "modifier", "medical_necessity", "information", "cob"):
        assert .005 <= cats[k] / len(den) <= .09, (k, cats[k] / len(den))
    rate = defaultdict(lambda: [0, 0])
    for c, a in _primary(claims):
        rate[c["payer_class"]][0] += 1
    for c, _, _ in den:
        rate[c["payer_class"]][1] += 1
    r = {k: d / n for k, (n, d) in rate.items()}
    assert r["medicare"] < r["commercial"] < r["medicaid_mco"]           # spec multipliers .6 / 1.0 / 1.5
    carcs = Counter(k for c, a in _primary(claims) for e in a["events"] for k in e["co_carcs"] if k in m.DENIAL_CARCS)
    assert {"97", "18", "236", "50", "16", "197", "151", "183", "B7"} <= set(carcs)
    assert "45" not in m.DENIAL_CARCS and "253" not in m.DENIAL_CARCS


def test_days_to_pay_by_payer_type(ledger):
    _, claims = ledger
    days = defaultdict(list)
    for c, a in _primary(claims):
        paid = [e for e in a["events"] if e["paid"] > 0 and e["status"] in ("1", "19")]
        if paid:
            e = paid[0]
            days[c["segment"]].append((date.fromisoformat(e["date"]) - date.fromisoformat(e["received"])).days)
    assert min(days["medicare_ffs"]) >= 14                                # 13-day payment floor
    assert 14 <= median(days["medicare_ffs"]) <= 18                       # spec typical 15
    assert 9 <= median(days["national_commercial"]) <= 19                 # spec typical 12
    assert 11 <= median(days["blues"]) <= 20                              # spec typical 14
    assert median(days["medicaid_mco"]) > median(days["national_commercial"])   # spec typical 22
    tail = [d for v in days.values() for d in v]
    assert .01 <= sum(d > 60 for d in tail) / len(tail) <= .09            # spec pended tail: 5% past 60 days (censored)


def test_allowed_amounts_follow_the_clfs(ledger):
    _, claims = ledger
    ratio = defaultdict(list)
    for c, a in _primary(claims):
        for ln in a["events"][0]["lines"]:
            if ln["allowed"] > 0 and ln["units"] == 1 and ln["cpt"] in m.CLFS_2026 and ln["adjudicated_cpt"] == ln["cpt"]:
                ratio[a["payer_class"]].append(ln["allowed"] / m.CLFS_2026[ln["cpt"]])
    assert all(abs(x - 1) < .001 for x in ratio["medicare"])              # Medicare allows the CLFS rate
    assert .9 <= median(ratio["medicare_advantage"]) <= 1.1
    assert max(ratio["medicaid"] + ratio["medicaid_mco"]) <= 1.0005       # SSA 1903(i)(7): capped at the CLFS
    assert .7 <= median(ratio["medicaid"] + ratio["medicaid_mco"]) <= .9
    assert 1.0 <= median(ratio["commercial"] + ratio["blues"]) <= 1.3      # spec typical 1.15
    charge = [ch / m.CLFS_2026[code] for c in claims for code, ch, u in zip(c["cpts"], c["line_charges"], c["units"])
              if code in ("80053", "85025", "80061", "83036", "84443") and u == 1]
    assert 4 <= median(charge) <= 11                                      # chargemaster 4-11x CLFS on routine tests


def test_patient_share_cob_and_crossover(ledger):
    _, claims = ledger
    prim = list(_primary(claims))
    assert .22 <= sum(a["events"][0]["pr"] > 0 for _, a in prim) / len(prim) <= .38    # XiFin 2025: 30%
    medicare = [(c, a) for c, a in prim if a["payer_class"] == "medicare"]
    pr_on_clfs = [ln for c, a in medicare for e in a["events"][:1] for ln in e["lines"]
                  if ln["cpt"] in m.CLFS_2026 and ln["paid"] > 0]
    assert pr_on_clfs and not any(k in e["cas"] for c, a in medicare for e in a["events"] for k in ("PR-1", "PR-2", "PR-3")
                                  if all(code in m.CLFS_2026 for code in c["cpts"]))   # no cost sharing on CLFS tests
    paid = [e for _, a in medicare for e in a["events"] if e["status"] in ("1", "19") and e["paid"] > 0]
    assert .14 <= sum(e["status"] == "19" for e in paid) / len(paid) <= .26           # COBA crossover ~20%
    assert all(e["crossover"] == (e["status"] == "19") for e in paid)
    secondary_billed = sum(any(s["seq"] == 1 for s in c["submissions"]) for c in claims)
    assert .02 <= secondary_billed / len(claims) <= .07                   # spec: ~4% billed to a secondary by 837


def test_commercial_deductibles_match_kff():
    sim = m._TenantRun(Scenario(), "acme_labs", 3000)
    deds = []
    for lo, size, seg, rep in sim.pools:
        if seg in ("blues", "national_commercial", "regional_tpa") and not rep:
            deds += [d for d in (sim._patient(lo + i).deductible for i in range(0, size, 7)) if d > 0]
    assert abs(sum(deds) / len(deds) / 1886 - 1) < .12                    # KFF EHBS 2025 average single deductible
    assert .26 <= sum(d >= 2000 for d in deds) / len(deds) <= .45         # 34% at $2,000 or more


def test_calendar_and_remittance_cadence(ledger):
    T, claims = ledger
    for f in T["files"]:
        d = date.fromisoformat(f["day"])
        assert d.weekday() < 5 and d not in m.federal_holidays(d.year), f["name"]
    days_835 = Counter(f["day"] for f in T["files"] if f["kind"] == "835" and f["expect"] == "parsed")
    mac = [f["day"] for f in T["files"] if f["kind"] == "835" and f.get("payer_id") == "11502"]
    assert len(set(mac)) >= .9 * len([d for d in days_835 if d >= min(mac)])    # the MAC remits every business day
    medicaid = {date.fromisoformat(f["day"]).weekday() for f in T["files"] if f.get("payer_id") == "NCMCD"}
    assert len(medicaid) <= 2                                             # weekly checkwrite (holidays shift it)
    assert median(days_835.values()) >= 10
    sizes = Counter(c["submitted"] for c in claims)
    mondays = [n for d, n in sizes.items() if date.fromisoformat(d).weekday() == 0]
    fridays = [n for d, n in sizes.items() if date.fromisoformat(d).weekday() == 4]
    assert median(mondays) > 1.2 * median(fridays)                       # Monday carries Saturday's service dates


def test_respiratory_season(tmp_path):
    def resp_share(start):
        t = generate(tmp_path / str(start), Scenario(start=start, days=28, volume_scale=1, seed=3), ["acme_labs"])
        claims = [c for c in t["tenants"]["acme_labs"]["claims"].values() if c["submitted"] >= str(start)]
        return sum(c["order_type"] == "respiratory" for c in claims) / len(claims)
    assert resp_share(date(2026, 1, 5)) > 3 * resp_share(date(2026, 6, 1))   # spec: Jan 3.2x vs Jun 0.5x


def test_tenants_never_share_pcns_or_identifiers(tmp_path):
    t = generate(tmp_path, Scenario(days=30, volume_scale=.2, seed=2))
    pcns = {tc: set(T["claims"]) for tc, T in t["tenants"].items()}
    assert not pcns["acme_labs"] & pcns["acme_diagnostics"]
    assert {p[:2] for p in pcns["acme_labs"]} == {"LB"} and {p[:2] for p in pcns["acme_diagnostics"]} == {"DX"}
    body = b"".join(p.read_bytes() for p in (tmp_path / "acme_labs").rglob("*.x12") if "/837/" in str(p))
    assert b"PRV*BI*PXC*291U00000X" in body and b"REF*X4*34D2187436" in body and b"*81:B:1*" in body
    remit = b"".join(p.read_bytes() for p in (tmp_path / "acme_labs").rglob("*.x12") if "/835/" in str(p))
    assert b"*ZZ*CLAIMCLARITY   *" in remit and re.search(rb"TRN\*1\*\d+\*1\d{9}~", remit)     # TRN03 = 1 + payer EIN
    assert re.search(rb"CAS\*CO\*[^~]*\b253\*", remit)                     # Medicare sequestration
