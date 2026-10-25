"""Realism invariants from EDI realism audits 1-3 (research/edi_realism_audit_*.md): who the patients are, whether the
tests fit them, what a claim scrubber would have caught, how Medicare treats screening orders, and what the files
and envelopes look like. Each test reads the raw files the way an auditor would, or the ledger."""
import re
from collections import Counter, defaultdict
from datetime import date

import pytest

from ccsim import edi_mock as m
from ccsim import edi_reference as R
from ccsim.edi_mock import Scenario, generate
from ccsim.parser import iter_segment_chunks

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
    chunks = iter_segment_chunks(body.decode("utf-8", "replace"))
    next(chunks)                                                  # the delimiters
    return [e for segments in chunks for _, _, e, _ in segments]


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
                if not set(svc["cas"]) & want:                    # a whole-claim outcome took precedence
                    assert any(g in ("CO", "OA") and (k in m.DENIAL_CARCS or k == "45") for g, k in svc["cas"]), svc
                    continue
                if mod == "GY":
                    assert svc["rarc"] == "N425"
    assert seen["GA"] and seen["GY"]


def test_npis_are_valid_spread_and_unrelated(run):
    T, _ = run
    npis = {c["referring_npi"] for c in T["claims"].values()} | {m.npi(t.npi_seed) for t in m.TENANTS.values()}
    assert all(m.npi_is_valid(n) and "1003000000" <= n <= "1993999999" for n in npis)
    prefixes = Counter(n[:7] for n in npis)
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


# --- audit 2 --------------------------------------------------------------------------------------------
def _claim_rows_837(files):
    """Per claim: payer id, member id, lines (code, SV101-7), 2310C facility, from the raw 837s."""
    out = []
    for f, body in files:
        if f["kind"] != "837" or f["expect"] != "parsed":
            continue
        payer = member = None
        cur = None
        for e in _segments(body):
            if e[0] == "NM1" and e[1] == "IL":
                member = e[9]
            elif e[0] == "NM1" and e[1] == "PR":
                payer = e[9]
            elif e[0] == "CLM":
                cur = dict(pcn=e[1], payer=payer, member=member, lines=[], facility=None)
                out.append(cur)
            elif e[0] == "SV1" and cur is not None:
                parts = re.split("[:>]", e[1])
                cur["lines"].append((parts[1], parts[6] if len(parts) > 6 else ""))
            elif e[0] == "NM1" and e[1] == "77" and cur is not None:
                cur["facility"] = e[3]
            elif e[0] == "N3" and cur is not None and cur["facility"] and "facility_street" not in cur:
                cur["facility_street"] = e[1]
    return out


def test_moldx_zcodes_and_germline_prior_auth(run):
    """Palmetto (MolDX): molecular lines carry the DEX Z-code in SV101-7, or are denied CO-16 (M20/N350).
    Commercial plans deny a large share of germline genetic tests (prior authorization)."""
    T, files = run
    molecular = {c for c, cpt in m.CPTS.items() if cpt.category == "molecular"}
    rows = _claim_rows_837(files)
    medicare = [(c, code, desc) for c in rows if c["payer"] == "11502" for code, desc in c["lines"] if code in molecular]
    assert medicare and sum(desc.startswith("Z") for _, _, desc in medicare) >= .8 * len(medicare)
    for c, code, desc in medicare:
        lines = _current_lines(T, c["pcn"])
        if not desc.startswith("Z") and lines and len(lines) == len(c["lines"]):
            i = [l[0] for l in c["lines"]].index(code)
            assert lines[i]["paid"] == 0, c["pcn"]
    germline = [c for c in T["claims"].values() if c["submissions"] and set(c["cpts"]) & m.GERMLINE_TESTS
                and c["payer_class"] in ("commercial", "blues")]
    denied = [c for c in germline if any(a["denied"] or any(ln["denied"] for ln in a["events"][-1]["lines"])
                                         for a in c["adjudications"].values() if a["remitted_current"])]
    remitted = [c for c in germline if any(a["remitted_current"] for a in c["adjudications"].values())]
    assert remitted and .2 <= len(denied) / len(remitted) <= .65


def test_member_ids_and_claim_numbers_follow_each_payers_format(run):
    T, files = run
    ids = defaultdict(set)
    for c in _claim_rows_837(files):
        ids[c["payer"]].add(c["member"])
    fmt = {"60054": r"W\d{9}", "61473": r"[A-Z]{3}\d{9}", "FEPNC": r"R\d{8}", "NCMCD": r"\d{9}[A-Z]",
           "11502": r"\d[A-Z][A-Z0-9]\d[A-Z][A-Z0-9]\d[A-Z]{2}\d{2}", "87726": r"\d{9}"}
    for payer, pattern in fmt.items():
        assert ids.get(payer), payer
        assert all(re.fullmatch(pattern, i) for i in ids[payer]), (payer, sorted(ids[payer])[:3])
    assert len({i[:3] for i in ids["61473"]}) >= 10                 # Blues prefixes vary by group
    shapes = defaultdict(set)
    checked = 0
    for f, body in files:
        if f["kind"] != "835" or f["expect"] != "parsed":
            continue
        icn = status = None
        for e in _segments(body):
            if e[0] == "CLP":
                icn, status = e[7], e[2]
                shapes[f.get("payer_id")].add(re.sub(r"\d", "9", re.sub(r"[A-Z]", "A", icn)))
            elif e[0] == "DTM" and e[1] == "050" and f.get("payer_id") == "11502" and status != "22":
                received = date(int(e[2][:4]), int(e[2][4:6]), int(e[2][6:8]))
                assert icn[2:7] == f"{received:%y}{received.timetuple().tm_yday:03d}", (icn, e[2])
                checked += 1
    assert checked > 100
    assert len({next(iter(v)) for v in shapes.values() if len(v) == 1}) >= 8   # formats differ by payer


def test_tail_payers_are_geographically_coherent():
    """Each lab's tail is its own market: places in the lab's state and its neighbours, offices there (a TPA may sit
    at a national hub); no payer serves two markets."""
    all_tail = [p for payers in m.TAIL_PAYERS.values() for p in payers]
    assert len({p.payer_id for p in all_tail}) == len({p.name for p in all_tail}) == len(all_tail)
    assert not any("WORKERS COMP" in p.name for p in m.PAYERS)
    for region, payers in m.TAIL_PAYERS.items():
        market = {region, *m.NEIGHBOR_STATES[region]}
        stems = Counter()
        for p in payers:
            assert p.remit_address[2] in market | set(m.TPA_HUB_STATES), (region, p.name, p.remit_address)
            for place, states in m._TAIL_PLACES.items():
                if p.name.startswith(place + " "):
                    assert p.remit_address[2] in set(states.split()) & market, (p.name, p.remit_address)
                    stems[place] += 1
        assert max(stems.values()) <= m.MAX_PAYERS_PER_STEM
        weight = Counter()
        for p in payers:
            weight[p.remit_address[2]] += p.weight
        assert weight[region] / sum(weight.values()) >= .6, (region, weight)
        boxes = Counter(len(p.remit_address[0].split()[-1]) for p in payers if p.remit_address[0].startswith("PO BOX"))
        assert len(boxes) >= 4 and max(boxes.values()) < .5 * sum(boxes.values())


def test_every_diagnosis_fits_the_patients_age_and_add_ons_have_parents(run):
    _, files = run
    for c in _claims_837(files):
        codes = {l[0] for l in c["lines"]}
        if "82570" in codes:
            assert codes & m.CREATININE_PARENTS, c["pcn"]
    slots = lyme = 0
    for f, body in files:
        if f["kind"] != "837" or f["expect"] != "parsed":
            continue
        dob = sex = None
        for e in _segments(body):
            if e[0] == "DMG":
                dob, sex = e[2], e[3]
            elif e[0] == "HI":
                age = 2026.5 - int(dob[:4])
                for x in e[1:]:
                    d = R.DX_BY_CODE[re.split("[:>]", x)[1]]
                    assert d.min_age - 1.5 <= age <= d.max_age + 1.5, (d, dob)
                    slots += 1
                    lyme += d.code == "A6920"
    assert lyme <= .002 * slots


def test_streets_take_their_own_suffixes():
    import random
    r = random.Random(5)
    streets = Counter(R.street_address(r, "NC")[0].split(" ", 1)[-1] for _ in range(20000))
    main = {k: v for k, v in streets.items() if re.fullmatch(r"(?:[NSEW] )?MAIN [A-Z]+", k)}
    assert sum(v for k, v in main.items() if k.endswith(" ST")) >= .6 * sum(main.values())
    assert len(streets) > 5000


def test_crossovers_ordering_providers_facilities_and_plb_periods(run):
    T, files = run
    partners = Counter()
    periods = set()
    for f, body in files:
        if f["kind"] == "835" and f["expect"] == "parsed":
            segs = _segments(body)
            partners.update(e[3] for e in segs if e[0] == "NM1" and e[1] == "TT")
            periods |= {e[2][4:] for e in segs if e[0] == "PLB"}
    assert len(partners) >= 5 and partners.most_common(1)[0][0] == "BCBSNC MEDICARE SUPPLEMENT"
    assert len(periods) > 1                                          # PLB02 differs by payer
    npis = Counter(c["referring_npi"] for c in T["claims"].values() if c["submissions"])
    assert npis.most_common(1)[0][1] <= .03 * sum(npis.values())
    rows = [c for c in _claim_rows_837(files) if c["facility"]]
    assert rows and not any(c.get("facility_street") == m.TENANTS["acme_labs"].address[0] for c in rows)


# --- audit 3: file-set level -------------------------------------------------------------------------
def _patients(tc, step=4):
    sim = m._TenantRun(Scenario(), tc, m.TENANTS[tc].claims_per_day * 10)
    for lo, size, seg, rep in sim.pools:
        for i in range(0, size, step):
            yield sim._patient(lo + i)


def test_coordination_of_benefits_follows_the_order_rules():
    """NAIC order: own employer plan before a spouse's, children by the birthday rule, Medicare second behind a
    working-aged employee's plan, Medicaid and TRICARE last; second plans from the lab's market; few Blues plans
    behind a Blues primary; home-Blues members from other states' plans (BlueCard prefixes)."""
    ref = date(2026, 7, 1)
    for tc, t in m.TENANTS.items():
        segments = m.segments_for(t.region)
        local = {p.payer_id for payers in segments.values() for p in payers}
        home_blues = segments["blues"][0]
        comm = aged = blues_sec = blues_blues = home = bluecard = 0
        for p in _patients(tc):
            prim = p.coverages[0]
            cls = prim.payer.payer_class
            comm += cls in ("commercial", "blues")
            if prim.payer is home_blues:
                home += 1
                bluecard += prim.member_id[:3] not in m.blues_prefixes(home_blues)
            assert len(p.coverages) <= 2
            if len(p.coverages) == 1:
                continue
            sec = p.coverages[1]
            assert cls in ("commercial", "blues"), (tc, prim.payer.name)   # TRICARE, VA, Medicaid: never ahead
            assert sec.payer.payer_id in local, (tc, sec.payer.name)
            if sec.payer.is_medicare:
                aged += 1
                assert p.age(ref) >= 64.99 and prim.relationship == sec.relationship == "18"
            elif sec.payer.payer_class in ("commercial", "blues"):
                if prim.relationship == "18":
                    assert sec.relationship == "01"                       # employer rule: the spouse's plan second
                else:
                    assert prim.relationship == sec.relationship == "19"  # birthday rule
                    a, b = prim.subscriber.dob, sec.subscriber.dob
                    assert (a.month, a.day) <= (b.month, b.day)
                if cls == "blues":
                    blues_sec += 1
                    blues_blues += sec.payer.payer_class == "blues"
            else:
                assert sec.payer.payer_class in ("medicaid", "medicaid_mco") or sec.payer.payer_id == "TREST"
        assert blues_blues <= .12 * blues_sec, (tc, blues_blues, blues_sec)
        assert .03 <= aged / comm <= .09, (tc, aged / comm)
        assert .06 <= bluecard / home <= .18, (tc, bluecard / home)


def test_deductibles_are_mostly_met_by_the_fourth_quarter():
    paces = [p.deductible_pace for p in _patients("acme_labs", 9) if p.deductible > 0]
    october = date(2026, 10, 15).timetuple().tm_yday / 365
    assert .05 <= sum(pace > october for pace in paces) / len(paces) <= .16   # deductible still open in October
    assert sum(pace > .25 for pace in paces) / len(paces) >= .8               # but rarely met by March


def test_diagnoses_and_modifiers_at_volume(run):
    """No diagnosis dominates out of its clinical place (hyperprolactinemia, antibody-response exams, chronic
    hepatitis B); electrolyte disorders are adult codes; modifier 90 marks exactly the lab's send-out list and an
    accredited high-complexity lab bills no QW."""
    _, files = run
    claims = _claims_837(files)
    slots = Counter(d for c in claims for d in c["dx"])
    total = sum(slots.values())
    assert slots["E221"] / total < .003 and slots["Z0184"] / total < .01 and slots["B182"] / total < .005
    assert all(dx.min_age >= 18 for dx in R.DX_DOMAINS["electrolytes"] if dx.code != "E860")
    send_outs = m.send_out_codes("acme_labs")
    lines = [l for c in claims for l in c["lines"]]
    assert not any("QW" in mods for _, mods, _ in lines)
    assert all(("90" in mods) == (code in send_outs) for code, mods, _ in lines)
    assert 0 < sum("90" in mods for _, mods, _ in lines) < .03 * len(lines)


def test_billing_system_constants_service_centers_and_msp(run):
    """One billing system: 2420E and REF*D9 on every claim or on none; patient service centers are suites billed
    under the lab's NPI, the busiest far busier than the quietest; no frequency-8 voids to the MAC; Medicare
    as secondary carries SBR05 = 12 (working aged); 81479 carries the test's own name."""
    _, files = run
    t = m.TENANTS["acme_labs"]
    n_clm = n_dk = n_d9 = msp = 0
    sites = Counter()
    for f, body in files:
        if f["kind"] != "837" or f["expect"] != "parsed":
            continue
        sbr = payer = site = None
        dk_this = False
        for e in _segments(body):
            if e[0] == "SBR":
                sbr, payer = e, None
            elif e[0] == "NM1" and e[1] == "PR" and payer is None:
                payer = e[9]
                if sbr[1] == "S" and payer == "11502":
                    assert sbr[5] == "12"
                    msp += 1
            elif e[0] == "CLM":
                n_clm += 1
                assert not (payer == "11502" and e[5].endswith("8")), e[1]
                dk_this = False
            elif e[0] == "REF" and e[1] == "D9":
                n_d9 += 1
            elif e[0] == "NM1" and e[1] == "DK" and not dk_this:
                n_dk += 1
                dk_this = True
            elif e[0] == "NM1" and e[1] == "77":
                assert e[9] == m.npi(t.npi_seed)
                site = e[3]
            elif e[0] == "N3" and site:
                assert " STE " in e[1]
                sites[(site, e[1])] += 1
                site = None
            elif e[0] == "NTE":
                assert e[2] in m.UNLISTED_MOLECULAR_TESTS
            elif e[0] == "SV1" and re.split("[:>]", e[1])[1] == "81479":
                desc = re.split("[:>]", e[1])[6]
                assert desc in m.UNLISTED_MOLECULAR_TESTS or desc.startswith("Z")
    assert n_dk in (0, n_clm) and n_d9 in (0, n_clm)
    assert msp > 0
    counts = [n for _, n in sites.most_common()]
    assert len(counts) >= 8 and counts[0] > 3 * counts[-1]


def test_duplicate_denials_and_medicare_corrections(run):
    """CARC 18 carries one group code per payer; N522 (crossover duplicate) only on crossover claims; no N130 with
    a duplicate; a MAC's corrections never re-price the CLFS."""
    T, files = run
    groups = defaultdict(set)
    for f, body in files:
        if f["kind"] != "835" or f["expect"] != "parsed":
            continue
        payer, clp = None, None
        for e in _segments(body):
            if e[0] == "N1" and e[1] == "PR":
                payer = e[2]
            elif e[0] == "CLP":
                clp = dict(tt=False, carc18=False)
            elif clp is None:
                continue
            elif e[0] == "NM1" and e[1] == "TT":
                clp["tt"] = True
            elif e[0] == "CAS" and "18" in e[2::3]:
                groups[payer].add(e[1])
                clp["carc18"] = True
            elif e[0] == "MOA":
                assert not (clp["carc18"] and "N130" in e)
                assert "N522" not in e or clp["tt"]
            elif e[0] == "LQ" and e[2] == "N522":
                assert clp["tt"]
    assert groups and all(len(g) == 1 for g in groups.values()), groups
    for c in T["claims"].values():
        a = c["adjudications"].get("11502")
        if not a or c["payers"][0] != "11502":
            continue
        paid = [e for e in a["events"] if e["status"] in ("1", "19") and e["paid"] > 0]
        for i in range(len(c["cpts"])):
            allowed = {e["lines"][i]["allowed"] for e in paid if len(e["lines"]) > i and e["lines"][i]["allowed"] > 0}
            assert len(allowed) <= 1, (c["pcn"], allowed)
