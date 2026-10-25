"""Realism invariants from EDI realism audit 1 (research/edi_realism_audit_1.md): who the patients are, whether the
tests fit them, what a claim scrubber would have caught, how Medicare treats screening orders, and what the files
and envelopes look like. Each test reads the raw files the way an auditor would, or the ledger."""
import re
from collections import Counter, defaultdict
from datetime import date

import pytest

from ccsim import edi_mock as m
from ccsim import edi_reference as R
from ccsim.edi_mock import Scenario, generate
from ccsim.parser import split_segments

START = date(2026, 7, 1)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("realism")
    truth = generate(out, Scenario(start=START, days=30, volume_scale=1.5, seed=17), ["acme_labs"])
    files = []
    for f in truth["tenants"]["acme_labs"]["files"]:
        body = (out / "acme_labs" / "raw" / f"received_date={f['day']}" / f["kind"] / f["name"]).read_bytes()
        files.append((f, body))
    return truth["tenants"]["acme_labs"], files


def _segments(body: bytes):
    segs, *_ = split_segments(body.decode("utf-8", "replace"))
    return [e for _, e, _ in segs]


def _claims_837(files):
    """(pcn, DMG sex, dx list, [(cpt, modifiers, pointers)]) for every 837 claim, from the raw files."""
    out = []
    for f, body in files:
        if f["kind"] != "837" or f["expect"] != "parsed":
            continue
        sex, cur = None, None
        for e in _segments(body):
            if e[0] == "DMG":
                sex = e[3]
            elif e[0] == "CLM":
                cur = dict(pcn=e[1], sex=sex, dx=[], lines=[])
                out.append(cur)
            elif e[0] == "HI":
                cur["dx"] = [re.split("[:>]", x)[1] for x in e[1:]]
            elif e[0] == "SV1":
                parts = re.split("[:>]", e[1])
                cur["lines"].append((parts[1], tuple(parts[2:6]), re.split("[:>]", e[7])))
    return out


def _current_lines(T, pcn):
    c = T["claims"].get(pcn)
    if not c:
        return None
    a = c["adjudications"].get(c["payers"][0])
    if not a or not a["events"]:
        return None
    return ([e for e in a["events"] if not e["duplicate_denial"]] or a["events"])[-1]["lines"]


def test_patients_have_real_name_frequencies_and_local_addresses(run):
    _, files = run
    surnames, places, streets = Counter(), Counter(), Counter()
    for f, body in files:
        if f["kind"] == "837" and f["expect"] == "parsed":
            for e in _segments(body):
                if e[0] == "NM1" and e[1] in ("IL", "QC"):
                    surnames[e[3]] += 1
                elif e[0] == "N4":
                    places[(e[1], e[2], e[3][:5])] += 1
                elif e[0] == "N3":
                    streets[e[1]] += 1
    top = [s for s, _ in surnames.most_common(5)]
    assert top[0] == "SMITH" or "SMITH" in top[:3]
    assert len(surnames) > 600 and not set(surnames) - set(R.SURNAMES)
    valid = {(p.city, p.state, p.zip5) for p in R._PLACE_TABLES["NC"][0]}
    lab = m.TENANTS["acme_labs"].address
    assert all(k in valid or k == (lab[1], lab[2], lab[3][:5]) for k in places)
    assert not re.search(r"\b(MOCK|TEST|SAMPLE|FAKE)\b", " ".join(streets))
    phones = {e[4] for f, b in files if f["kind"] == "837" and f["expect"] == "parsed" for e in _segments(b)
              if e[0] == "PER"}
    assert all(p[:3] in ("336", "743") and p[3:6] != "555" for p in phones)


def test_tests_and_diagnoses_fit_the_patients_sex(run):
    """Sex-specific tests and diagnoses match the patient's DMG sex, except a rare residue of patients whose
    insurance gender marker differs: those lines carry KX (gender-specific service) or the payer denies them
    with a sex edit (CO-16 MA39 / CO-11 M76)."""
    T, files = run
    claims = _claims_837(files)
    mismatched = 0
    for c in claims:
        for i, (cpt, mods, ptrs) in enumerate(c["lines"]):
            sexes = {R.CPT_SEX.get(cpt)} | {R.DX_BY_CODE[c["dx"][int(p) - 1]].sex for p in ptrs}
            sexes -= {None, ""}
            if sexes and sexes != {c["sex"]}:
                mismatched += 1
                lines = _current_lines(T, c["pcn"])
                denied = lines is not None and i < len(lines) and set(lines[i]["co_carcs"]) & {"16", "11"}
                assert "KX" in mods or denied or lines is None, (c["pcn"], cpt, c["sex"], c["dx"])
    assert mismatched <= .002 * len(claims)


def test_every_line_points_at_a_diagnosis_that_justifies_it(run):
    _, files = run
    claims = _claims_837(files)
    distinct = Counter(d for c in claims for d in c["dx"])
    assert all(d in R.DX_BY_CODE for d in distinct)              # all from the FY2026 billable code list
    assert len(distinct) > 400                                    # a Zipf tail, not a short fixed list
    bad = sum(1 for c in claims for cpt, _, ptrs in c["lines"]
              if cpt != "36415" and not any(R.dx_fits_cpt(c["dx"][int(p) - 1], cpt) for p in ptrs))
    assert bad <= .002 * sum(len(c["lines"]) for c in claims)


def test_scrubber_strips_ncci_components_and_the_residue_is_denied(run):
    T, files = run
    claims = _claims_837(files)
    with_pairs = [c for c in claims if R.ncci_conflicts([l[0] for l in c["lines"]])]
    assert len(with_pairs) <= .012 * len(claims)
    for c in with_pairs:
        lines = _current_lines(T, c["pcn"])
        if lines is None or len(lines) != len(c["lines"]):
            continue
        comps = {comp for comp, _ in R.ncci_conflicts([l[0] for l in c["lines"]])}
        for (cpt, _, _), ln in zip(c["lines"], lines):
            if cpt in comps and ln["paid"] > 0:
                pytest.fail(f"{c['pcn']}: NCCI component {cpt} paid with its panel")


def test_no_single_source_proprietary_codes():
    codes = set(m.CPTS)
    assert not {"81528", "0037U"} & codes and not any(c.endswith("U") for c in codes)
    assert not any("81500" <= c <= "81599" for c in codes)


def _service_lines_835(files, payer_id):
    """(modifiers, paid, [(group, carc)], rarc) for each SVC in this payer's 835s."""
    out = []
    for f, body in files:
        if f["kind"] != "835" or f["expect"] != "parsed" or f.get("payer_id") != payer_id:
            continue
        for e in _segments(body):
            if e[0] == "SVC":
                out.append(dict(mods=re.split("[:>]", e[1])[2:], paid=float(e[3]), cas=[], rarc=None))
            elif e[0] in ("CLP", "SE"):
                out.append(None)
            elif out and out[-1] is not None and e[0] == "CAS":
                out[-1]["cas"] += [(e[1], e[i]) for i in range(2, len(e), 3) if e[i]]
            elif out and out[-1] is not None and e[0] == "LQ":
                out[-1]["rarc"] = e[2]
    return [s for s in out if s]


def test_medicare_screening_orders_follow_ncd_and_abn_rules(run):
    """GA (ABN signed) -> PR-50 (patient liable); GZ -> CO-50; GY -> PR-96/204 with N425; never paid."""
    _, files = run
    seen = Counter()
    for svc in _service_lines_835(files, "11502"):
        for mod in ("GA", "GZ", "GY"):
            if mod in svc["mods"]:
                seen[mod] += 1
                assert svc["paid"] == 0
                want = {"GA": {("PR", "50")}, "GZ": {("CO", "50")}, "GY": {("PR", "96"), ("PR", "204")}}[mod]
                assert set(svc["cas"]) & want, svc
                if mod == "GY":
                    assert svc["rarc"] == "N425"
    assert seen["GA"] and seen["GY"]


def test_npis_are_valid_spread_and_unrelated(run):
    T, _ = run
    npis = {c["referring_npi"] for c in T["claims"].values()} | {m.npi(t.npi_seed) for t in m.TENANTS.values()}
    assert all(m.npi_is_valid(n) and "1003000000" <= n <= "1993999999" for n in npis)
    prefixes = Counter(n[:6] for n in npis)
    assert max(prefixes.values()) == 1
    clia = {t.clia for t in m.TENANTS.values()}
    assert "34D0655059" not in clia and all(re.fullmatch(r"\d\dD\d{7}", c) for c in clia)


def test_835_banking_addresses_and_contacts(run):
    _, files = run
    banks, receivers, addresses = defaultdict(set), set(), set()
    for f, body in files:
        if f["kind"] != "835" or f["expect"] != "parsed":
            continue
        bank, per_bl = None, 0
        for e in _segments(body):
            if e[0] == "BPR":
                bank = (e[7], e[9]) if e[4] == "ACH" else None
                if bank:
                    assert m.aba_is_valid(e[7]) and m.aba_is_valid(e[13])
                    receivers.add((e[13], e[15]))
            elif e[0] == "TRN" and bank:
                banks[e[3]].add(bank)                             # TRN03 = 1 + the payer's EIN
            elif e[0] == "N4":
                addresses.add(tuple(e[1:4]))
            elif e[0] == "PER" and e[1] == "BL":
                per_bl += 1
            elif e[0] == "N1" and e[1] == "PE":
                assert per_bl == 1, f"{f['name']}: PER*BL missing in loop 1000A"
                per_bl = 0
    assert len(receivers) == 1                                    # the lab's own account never changes
    assert all(len(v) == 1 for v in banks.values())               # one originating account per payer
    assert len({next(iter(v)) for v in banks.values()}) > .8 * len(banks)
    assert len(addresses) > 30                                    # remittance addresses are not a few cities


def test_envelopes_files_and_redeliveries_look_real(run):
    T, files = run
    by_pair = defaultdict(list)
    names = [f["name"] for f, _ in files]
    assert len(names) == len(set(names))
    assert not any(w in n.lower() for n in names for w in ("redeliver", "batch", "truncated", "not_x12", "envelope"))
    for f, body in files:
        if f["expect"] == "rejected":
            continue
        for e in _segments(body):
            if e[0] == "ISA":
                by_pair[(e[6].strip(), e[8].strip())].append((f["day"], int(e[13]), e[10]))
    assert {("ACMELABS", "CLAIMCLARITY"), ("CLAIMCLARITY", "ACMELABS")} <= set(by_pair)
    lab = sorted(by_pair[("ACMELABS", "CLAIMCLARITY")])
    assert [n for _, n, _ in lab] == sorted(n for _, n, _ in lab) and len({n for _, n, _ in lab}) == len(lab)
    assert len({t for _, _, t in lab}) > 5 and not all(t == "1200" for _, _, t in lab)
    starts = {pair: min(n for _, n, _ in v) for pair, v in by_pair.items()}
    assert starts[("ACMELABS", "CLAIMCLARITY")] != starts[("CLAIMCLARITY", "ACMELABS")]
    dups = [(f, b) for f, b in files if f["expect"] == "duplicate"]
    originals = {f["name"]: b for f, b in files}
    assert all(b == originals[f["redelivery_of"]] and f["name"] != f["redelivery_of"] for f, b in dups)
    sizes = Counter(f["day"] for f, _ in files if f["kind"] == "837")
    assert max(sizes.values()) > 1


def test_window_opens_in_steady_state_with_trailing_recoupments(run):
    T, files = run
    first_day = min(f["day"] for f, _ in files)
    day1 = [f for f, _ in files if f["day"] == first_day and f["kind"] == "835"]
    assert any(f.get("payer_id") == "11502" for f in day1) and len(day1) >= 8
    billed = lambda pcn: date(2000 + int(pcn[2:4]), int(pcn[4:6]), int(pcn[6:8]))   # PCN = prefix + yymmdd + n
    lags = sorted((date.fromisoformat(p["day"]) - billed(p["target_pcn"])).days
                  for p in T["plb"] if p["code"] == "WO" and p["amount"] > 0 and p.get("target_pcn"))
    assert lags[0] >= 20 and lags[len(lags) // 2] >= 60           # recoupments trail payment by weeks to months
    burn_in_billed = [c for c in T["claims"].values() if c["submitted"] < str(START)]
    assert burn_in_billed and all(c["adjudications"] or c["submissions"] for c in burn_in_billed)
    assert any(c["resubmitted"] for c in burn_in_billed)          # corrected claims for earlier denials
    assert {p["code"] for p in T["plb"]} >= {"WO", "L6"}
