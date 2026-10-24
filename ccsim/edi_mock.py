"""Mock 837P / 835 X12 generator for ACME tenants, with a ground-truth ledger.

Every person, payer, NPI and address is invented. Files look like what a lab's clearinghouse sends:
one 837P batch per business day, one 835 per payer per payment day, and the awkward cases the semantic
layer has to get right:

  paid with CO-45 + PR-1/2/3     $0 paid with CO-45 (NOT a denial)    recorded denial (CLP02=4)
  signal-v3 denial (status 1, $0, CO + reviewed CARC)                   resubmission (CLM05-3=7) after CO-16
  reversal (CLP02=22) + correction in the same 835, reversal first      reversal with no correction
  payer-changed procedure (SVC06)  REF*6R line control numbers (mostly) PLB recoupment (WO) / interest (L6)
  byte-identical re-delivery of an 835 under a new filename             claims never remitted

The ledger (truth.json) is computed here, independently of the parser and the SQL views, so the
eval can check both. Knobs live in Scenario; a mock-data agent can author new Scenario variants.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path

DENIAL_CARCS = ["4", "11", "16", "27", "50", "96", "109", "167", "197", "204"]   # placeholder for the 23 reviewed

PAYERS = [  # name, payer id, SBR09 filing indicator, payer_class, denial multiplier, adjudication lag (days)
    ("ACME HEALTH PLAN", "AHP01", "CI", "commercial", 1.0, (7, 21)),
    ("NORTHWIND HEALTH", "NWH02", "CI", "commercial", 1.4, (10, 30)),
    ("CONTOSO MUTUAL", "CTM03", "CI", "commercial", 0.8, (5, 15)),
    ("FABRIKAM MEDICARE ADVANTAGE", "FMA04", "16", "medicare_advantage", 1.2, (14, 35)),
    ("TAILSPIN MEDICAID", "TSM05", "MC", "medicaid", 1.7, (20, 45)),
    ("WOODGROVE BENEFITS", "WGB06", "CI", "commercial", 0.6, (5, 12)),
]

CPTS = {  # code: (charge low, high, base denial prob, denial CARCs, description)
    "80053": (40, 90, .04, ["16", "96"], "Comprehensive metabolic panel"),
    "85025": (20, 45, .03, ["16"], "CBC with differential"),
    "83036": (30, 70, .07, ["50", "11"], "Hemoglobin A1c"),
    "84443": (45, 95, .06, ["50", "11"], "TSH"),
    "80061": (35, 80, .05, ["50"], "Lipid panel"),
    "87086": (25, 60, .09, ["16", "96"], "Urine culture"),
    "87491": (90, 180, .18, ["50", "197"], "Chlamydia NAA"),
    "87798": (110, 260, .32, ["50", "197", "167"], "Infectious agent NAA, NOS"),
    "0240U": (150, 400, .40, ["197", "50", "204"], "Respiratory panel PLA"),
    "81479": (800, 3200, .55, ["197", "50", "204"], "Unlisted molecular pathology"),
    "81528": (450, 650, .35, ["197", "167"], "Colorectal cancer screening, stool DNA"),
    "G0480": (180, 320, .28, ["109", "50"], "Drug test, definitive, 1-7 classes"),
    "36415": (3, 10, .10, ["97"], "Venipuncture"),
}
DX = ["Z1159", "R6889", "E119", "N390", "Z0000", "E785", "R509", "Z113", "J069", "Z79899", "Z1211"]
FIRST = ["AVERY", "JORDAN", "TAYLOR", "MORGAN", "CASEY", "RILEY", "QUINN", "JAMIE", "SKYLER", "PARKER", "ROWAN",
         "DREW", "SAGE", "EMERY", "HARPER", "LOGAN", "REESE", "BLAKE", "DAKOTA", "FINLEY"]
LAST = ["ACMEFIELD", "TESTWOOD", "SAMPLEMAN", "MOCKLER", "DUMMIT", "FAKERSON", "PLACEHOLT", "SYNTHIA", "NULLMAN",
        "FIXTURE", "STUBBS", "EXAMPLAR", "PROTOTOV", "DEMOSKI", "TRIALSON"]
CITIES = [("BURLINGTON", "NC", "27215"), ("RALEIGH", "NC", "27601"), ("AUSTIN", "TX", "78701"), ("DENVER", "CO", "80202")]


def npi(seed: int) -> str:
    """10-digit Luhn-valid NPI from a seed (prefix 80840 per CMS check-digit rule)."""
    base = f"1{seed % 10**8:08d}"
    digits = [int(d) for d in "80840" + base]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return base + str((10 - total % 10) % 10)


@dataclass
class Tenant:
    code: str
    name: str
    tax_id: str
    npi_seed: int
    claims_per_day: int


TENANTS = {
    "acme_labs": Tenant("acme_labs", "ACME LABS INC", "561234567", 4242, 120),
    "acme_diagnostics": Tenant("acme_diagnostics", "ACME DIAGNOSTICS LLC", "561234999", 7777, 40),
}


@dataclass
class Scenario:
    start: date = date(2026, 7, 1)
    days: int = 30
    seed: int = 7
    volume_scale: float = 1.0
    p_never_remitted: float = .06
    p_zero_pay_fee_schedule: float = .03
    p_reversal_no_correction: float = .02
    p_reversal_correction: float = .03
    p_resubmit_after_co16: float = .7
    p_signal_v3_vs_recorded: float = .4   # among denials: share sent as status 1 / $0 / CO+CARC
    p_svc06_change: float = .02
    p_ref6r_missing: float = .1
    p_duplicate_835_file: float = .05
    p_plb: float = .15
    skip_weekends: bool = True


@dataclass
class Line:
    cpt: str
    charge: float
    units: int = 1


@dataclass
class Claim:
    pcn: str
    tenant: str
    payer: tuple
    patient: dict
    referring: dict
    dos: date
    lines: list
    dx: list
    submitted: date
    freq: str = "1"


class _X12:
    def __init__(self):
        self.segs: list[str] = []

    def s(self, *el):
        self.segs.append("*".join("" if e is None else str(e) for e in el).rstrip("*"))

    def wrap(self, sender: str, receiver: str, d: date, ctl: int, gs_code: str, st_id: str, impl: str) -> str:
        body = self.segs
        hdr = [f"ISA*00*{' '*10}*00*{' '*10}*ZZ*{sender:<15}*ZZ*{receiver:<15}*{d:%y%m%d}*1200*^*00501*{ctl:09d}*0*P*:",
               f"GS*{gs_code}*{sender}*{receiver}*{d:%Y%m%d}*1200*{ctl}*X*{impl}",
               f"ST*{st_id}*0001*{impl}"]
        tail = [f"SE*{len(body) + 2}*0001", f"GE*1*{ctl}", f"IEA*1*{ctl:09d}"]
        return "~\n".join(hdr + body + tail) + "~\n"


def _money(x: float) -> str:
    s = f"{x:.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class Generator:
    def __init__(self, sc: Scenario, tenants=None):
        self.sc = sc
        self.rng = random.Random(sc.seed)
        self.tenants = tenants or list(TENANTS)
        self.ctl = 1
        self.patients = [self._patient(i) for i in range(400)]
        self.referring = [dict(last=self.rng.choice(LAST), first=self.rng.choice(FIRST), npi=npi(9000 + i)) for i in range(25)]

    def _patient(self, i):
        r = self.rng
        city = r.choice(CITIES)
        return dict(first=r.choice(FIRST), last=r.choice(LAST), member_id=f"MBR{r.randint(10**8, 10**9 - 1)}",
                    dob=date(1940, 1, 1) + timedelta(days=r.randint(0, 29000)), gender=r.choice("MF"),
                    addr=f"{r.randint(1, 9999)} TEST ST", city=city)

    # -- 837P -----------------------------------------------------------------------------------------
    def _837(self, t: Tenant, claims: list[Claim], d: date) -> str:
        x = _X12()
        x.s("BHT", "0019", "00", f"B{d:%Y%m%d}{t.code[:4].upper()}", f"{d:%Y%m%d}", "1200", "CH")
        x.s("NM1", "41", "2", t.name, "", "", "", "", "46", t.tax_id)
        x.s("PER", "IC", "BILLING OFFICE", "TE", "5555550100")
        x.s("NM1", "40", "2", "CLAIMCLARITY CLEARINGHOUSE", "", "", "", "", "46", "CCCH01")
        hl = 1
        x.s("HL", hl, "", "20", "1")
        x.s("PRV", "BI", "PXC", "291U00000X")
        x.s("NM1", "85", "2", t.name, "", "", "", "", "XX", npi(t.npi_seed))
        x.s("N3", "1 ACME WAY"); x.s("N4", "BURLINGTON", "NC", "272150001"); x.s("REF", "EI", t.tax_id)
        for c in claims:
            hl += 1
            p = c.patient
            x.s("HL", hl, "1", "22", "0")
            x.s("SBR", "P", "18", f"GRP{c.payer[1]}", "", "", "", "", "", c.payer[2])
            x.s("NM1", "IL", "1", p["last"], p["first"], "", "", "", "MI", p["member_id"])
            x.s("N3", p["addr"]); x.s("N4", *p["city"]); x.s("DMG", "D8", f"{p['dob']:%Y%m%d}", p["gender"])
            x.s("NM1", "PR", "2", c.payer[0], "", "", "", "", "PI", c.payer[1])
            x.s("CLM", c.pcn, _money(sum(l.charge for l in c.lines)), "", "", f"81:B:{c.freq}", "Y", "A", "Y", "Y")
            if c.freq == "7":
                x.s("REF", "F8", "ICN" + c.pcn)
            x.s("HI", *[("ABK:" if i == 0 else "ABF:") + dx for i, dx in enumerate(c.dx)])
            r = c.referring
            x.s("NM1", "DN", "1", r["last"], r["first"], "", "", "", "XX", r["npi"])
            for i, l in enumerate(c.lines, 1):
                x.s("LX", i)
                x.s("SV1", f"HC:{l.cpt}", _money(l.charge), "UN", l.units, "", "", "1")
                x.s("DTP", "472", "D8", f"{c.dos:%Y%m%d}")
                x.s("REF", "6R", f"{c.pcn}-{i}")
        return x.wrap(t.code[:15].upper(), "CLAIMCLARITY", d, self._next_ctl(), "HC", "837", "005010X222A1")

    # -- 835 ------------------------------------------------------------------------------------------
    def _835(self, t: Tenant, payer: tuple, d: date, events: list[dict]) -> str:
        x = _X12()
        plbs = []
        if self.rng.random() < self.sc.p_plb:
            if self.rng.random() < .5:
                plbs.append(("WO", "ICN" + self.rng.choice(events)["pcn"], round(self.rng.uniform(5, 60), 2)))
            else:
                plbs.append(("L6", None, -round(self.rng.uniform(.5, 8), 2)))
        total = round(sum(e["paid"] for e in events) - sum(a for _, _, a in plbs), 2)
        x.s("BPR", "I", _money(total), "C", "ACH", "CCP", "01", "999999999", "DA", "1234567", f"1{payer[1]}", "",
            "01", "888888888", "DA", "7654321", f"{d:%Y%m%d}")
        x.s("TRN", "1", f"EFT{d:%Y%m%d}{payer[1]}", f"1{payer[1]}")
        x.s("DTM", "405", f"{d:%Y%m%d}")
        x.s("N1", "PR", payer[0]); x.s("N3", "PO BOX 1000"); x.s("N4", "HARTFORD", "CT", "06101")
        x.s("REF", "2U", payer[1])
        x.s("PER", "BL", "PROVIDER SERVICES", "TE", "8005550199")
        x.s("N1", "PE", t.name, "XX", npi(t.npi_seed))
        x.s("LX", "1")
        for e in events:
            c: Claim = e["claim"]
            sign = -1 if e["status"] == "22" else 1
            charge = sum(l.charge for l in c.lines)
            x.s("CLP", c.pcn, e["status"], _money(sign * charge), _money(e["paid"]), _money(e["pr"]) if e["pr"] else "",
                "12" if payer[2] == "CI" else ("MB" if payer[2] == "16" else "MC"), "ICN" + c.pcn, "81", "1")
            p = c.patient
            x.s("NM1", "QC", "1", p["last"], p["first"], "", "", "", "MI", p["member_id"])
            x.s("DTM", "050", f"{c.submitted + timedelta(days=2):%Y%m%d}")
            for i, (l, lp) in enumerate(zip(c.lines, e["line_paid"]), 1):
                svc06 = e.get("svc06", {}).get(i)
                proc = svc06 or l.cpt
                x.s("SVC", f"HC:{proc}", _money(sign * l.charge), _money(lp), "", l.units, f"HC:{l.cpt}" if svc06 else "")
                x.s("DTM", "472", f"{c.dos:%Y%m%d}")
                for grp, carc, amt in e["line_cas"][i - 1]:
                    x.s("CAS", grp, carc, _money(sign * amt))
                if i not in e.get("no_6r", ()):
                    x.s("REF", "6R", f"{c.pcn}-{i}")
                x.s("AMT", "B6", _money(lp))
                if e["status"] == "4":
                    x.s("LQ", "HE", self.rng.choice(["N130", "M127", "N290"]))
        for code, ref, amt in plbs:
            x.s("PLB", npi(t.npi_seed), f"{d.year}1231", f"{code}:{ref}" if ref else code, _money(amt))
        return x.wrap(payer[1], t.code[:15].upper(), d, self._next_ctl(), "HP", "835", "005010X221A1")

    def _next_ctl(self):
        self.ctl += 1
        return self.ctl

    # -- adjudication ---------------------------------------------------------------------------------
    def _adjudicate(self, c: Claim, outcome: str, carc: str | None = None) -> dict:
        r = self.rng
        line_paid, line_cas, pr_total = [], [], 0.0
        for l in c.lines:
            if outcome == "paid":
                allowed = round(l.charge * r.uniform(.35, .9), 2)
                pr = round(allowed * r.choice([0, 0, 0, .1, .2]), 2)
                paid = round(allowed - pr, 2)
                cas = [("CO", "45", round(l.charge - allowed, 2))]
                if pr:
                    cas.append(("PR", r.choice(["1", "2", "3"]), pr))
                pr_total += pr
            elif outcome == "zero_fee":
                paid, cas = 0.0, [("CO", "45", l.charge)]
            else:  # denied
                paid, cas = 0.0, [("CO", carc, l.charge)]
            line_paid.append(paid)
            line_cas.append(cas)
        e = dict(claim=c, pcn=c.pcn, line_paid=line_paid, line_cas=line_cas, pr=round(pr_total, 2),
                 paid=round(sum(line_paid), 2))
        if r.random() < self.sc.p_svc06_change and outcome == "paid":
            e["svc06"] = {1: "80048" if c.lines[0].cpt == "80053" else c.lines[0].cpt}
            if e["svc06"][1] == c.lines[0].cpt:
                e.pop("svc06")
        if r.random() < self.sc.p_ref6r_missing:
            e["no_6r"] = {i for i in range(1, len(c.lines) + 1)}
        return e

    @staticmethod
    def _reverse(e: dict) -> dict:
        return dict(e, status="22", paid=-e["paid"], pr=-e["pr"] if e["pr"] else 0,
                    line_paid=[-p for p in e["line_paid"]], svc06={}, no_6r=e.get("no_6r", set()))

    def run(self, out_dir: str | Path) -> dict:
        """Write raw/received_date=YYYY-MM-DD/{837,835}/*.x12 per tenant under out_dir/<tenant>/;
        return the truth ledger."""
        sc, r = self.sc, self.rng
        out = Path(out_dir)
        truth = {"scenario": {k: str(v) for k, v in asdict(sc).items()}, "denial_carcs": DENIAL_CARCS,
                 "tenants": {}}
        end = sc.start + timedelta(days=sc.days)
        for tc in self.tenants:
            t = TENANTS[tc]
            files: dict[date, list[tuple[str, str, str]]] = {}
            pending: dict[date, dict[tuple, list]] = {}
            ledger: dict[str, dict] = {}
            n = 0
            d = sc.start
            while d < end:
                if sc.skip_weekends and d.weekday() >= 5:
                    d += timedelta(days=1)
                    continue
                todays = []
                for _ in range(max(1, int(t.claims_per_day * sc.volume_scale * r.uniform(.7, 1.3)))):
                    n += 1
                    payer = r.choices(PAYERS, weights=[30, 20, 15, 15, 10, 10])[0]
                    lines = [Line(cpt, round(r.uniform(*CPTS[cpt][:2]), 2))
                             for cpt in r.sample(list(CPTS), r.choice([1, 1, 2, 2, 3, 4]))]
                    c = Claim(f"{tc[:2].upper()}{d:%y%m%d}{n:06d}", tc, payer, r.choice(self.patients),
                              r.choice(self.referring), d - timedelta(days=r.randint(1, 5)), lines,
                              r.sample(DX, r.choice([1, 2, 3])), d)
                    todays.append(c)
                    self._plan(c, d, pending, ledger)
                files.setdefault(d, []).append(("837", f"{tc}_837P_{d:%Y%m%d}.x12", self._837(t, todays, d)))
                resub = [c for c in ledger_resubs(ledger, d)]
                if resub:
                    files[d].append(("837", f"{tc}_837P_{d:%Y%m%d}_resub.x12", self._837(t, resub, d)))
                d += timedelta(days=1)
            # 835s
            for pd in sorted(pending):
                if pd >= end:
                    continue
                for payer_id, evs in pending[pd].items():
                    payer = next(p for p in PAYERS if p[1] == payer_id)
                    body = self._835(t, payer, pd, evs)
                    files.setdefault(pd, []).append(("835", f"{tc}_835_{payer_id}_{pd:%Y%m%d}.x12", body))
                    for e in evs:
                        led = ledger[e["pcn"]]
                        led["events"].append(dict(date=str(pd), status=e["status"], paid=e["paid"],
                                                  carcs=sorted({x[1] for cs in e["line_cas"] for x in cs})))
                    if r.random() < sc.p_duplicate_835_file:
                        files[pd].append(("835", f"{tc}_835_{payer_id}_{pd:%Y%m%d}_redeliver.x12", body))
            for day, fl in files.items():
                base = out / tc / "raw" / f"received_date={day}"
                for kind, name, body in fl:
                    (base / kind).mkdir(parents=True, exist_ok=True)
                    (base / kind / name).write_text(body)
            truth["tenants"][tc] = {"claims": _finalize(ledger, end), "file_days": sorted(str(d) for d in files)}
        return truth

    def _plan(self, c: Claim, d: date, pending, ledger):
        sc, r = self.sc, self.rng
        payer = c.payer
        ledger[c.pcn] = dict(pcn=c.pcn, payer_id=payer[1], payer=payer[0], payer_class=payer[3], dos=str(c.dos),
                             submitted=str(d), cpts=[l.cpt for l in c.lines], charge=round(sum(l.charge for l in c.lines), 2),
                             events=[], resubmit_on=None, claim=c)
        lag = lambda: d + timedelta(days=r.randint(*payer[5]))
        add = lambda day, e: pending.setdefault(day, {}).setdefault(payer[1], []).append(e)
        u = r.random()
        if u < sc.p_never_remitted:
            return
        u = r.random()
        p_den = min(.95, max(CPTS[l.cpt][2] for l in c.lines) * payer[4])
        if u < sc.p_reversal_no_correction:
            e = dict(self._adjudicate(c, "paid"), status="1"); day = lag()
            add(day, e); add(day + timedelta(days=r.randint(14, 30)), self._reverse(e)); return
        if u < sc.p_reversal_no_correction + sc.p_reversal_correction:
            carc = r.choice(CPTS[c.lines[0].cpt][3]) if CPTS[c.lines[0].cpt][3][0] != "97" else "50"
            e = dict(self._adjudicate(c, "denied", carc), status="4"); day = lag(); later = day + timedelta(days=r.randint(10, 25))
            add(day, e); add(later, self._reverse(e)); add(later, dict(self._adjudicate(c, "paid"), status="1")); return
        if u < sc.p_reversal_no_correction + sc.p_reversal_correction + sc.p_zero_pay_fee_schedule:
            add(lag(), dict(self._adjudicate(c, "zero_fee"), status="1")); return
        if r.random() < p_den:
            worst = max(c.lines, key=lambda l: CPTS[l.cpt][2])
            carc = r.choice(CPTS[worst.cpt][3])
            if carc == "97":
                carc = "16"
            status = "1" if r.random() < sc.p_signal_v3_vs_recorded else "4"
            day = lag()
            add(day, dict(self._adjudicate(c, "denied", carc), status=status))
            if carc == "16" and r.random() < sc.p_resubmit_after_co16:
                rs = day + timedelta(days=r.randint(3, 10))
                ledger[c.pcn]["resubmit_on"] = rs
                add(rs + timedelta(days=r.randint(*payer[5])), dict(self._adjudicate(c, "paid"), status="1"))
            return
        add(lag(), dict(self._adjudicate(c, "paid"), status="1" if payer[3] != "medicaid" or r.random() < .9 else "2"))


def ledger_resubs(ledger, d):
    out = []
    for led in ledger.values():
        if led["resubmit_on"] and led["resubmit_on"] <= d and "resubmitted" not in led:
            c = led["claim"]
            out.append(Claim(c.pcn, c.tenant, c.payer, c.patient, c.referring, c.dos, c.lines, c.dx, d, freq="7"))
            led["resubmitted"] = str(d)
    return out


def _finalize(ledger, end) -> dict:
    """Current state per claim, from the events the 835s actually carried (latest event wins;
    a reversal with nothing after it leaves the claim not currently remitted)."""
    out = {}
    for pcn, led in ledger.items():
        led = {k: v for k, v in led.items() if k != "claim"}
        led["resubmit_on"] = str(led["resubmit_on"]) if led["resubmit_on"] else None
        ev = led["events"]
        cur = ev[-1] if ev else None
        remitted = cur is not None and cur["status"] != "22"
        recorded = remitted and cur["status"] == "4"
        signal = remitted and cur["status"] != "4" and cur["paid"] == 0 and any(c in DENIAL_CARCS for c in cur["carcs"])
        led.update(remitted_current=remitted, denied=recorded or signal, denied_recorded=recorded,
                   denied_signal_v3=signal, paid_current=cur["paid"] if remitted else 0.0)
        out[pcn] = led
    return out


def generate(out_dir: str | Path, scenario: Scenario | None = None, tenants=None) -> dict:
    truth = Generator(scenario or Scenario(), tenants).run(out_dir)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "truth.json").write_text(json.dumps(truth, indent=1, default=str))
    return truth


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out"); ap.add_argument("--days", type=int, default=30); ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    t = generate(a.out, Scenario(days=a.days, volume_scale=a.scale))
    for tc, v in t["tenants"].items():
        print(tc, len(v["claims"]), "claims,", len(v["file_days"]), "file days")
