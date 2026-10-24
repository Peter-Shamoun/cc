"""Mock 837P / 835 X12 for ACME tenants, plus an independent ground-truth ledger.

Every person, payer, NPI, tax id and address is invented. The output looks like a lab's clearinghouse feed:
one 837P batch per business day (plus resubmission and secondary-payer batches), and one 835 per payer per
payment day (sometimes several ST transactions per file). The output models:

  pricing        fixed chargemaster price per CPT and tenant; payer fee schedules; units; modifiers
  patient share  deductible / coinsurance / copay accumulated per patient, payer and plan year
  coverage       subscriber vs dependent (2000C); secondary payers billed after the primary pays (COB
                 2320/2330B/2430 in the 837, CLP02=2 with OA-23 in the 835)
  denials        recorded (CLP02=4) vs signal-v3 (status 1, $0, CO + reviewed CARC); line-level partial
                 denials; claim-level-only denials with no SVC detail; medical necessity driven by diagnosis;
                 prior auth on molecular tests; timely filing (CARC 29) for late submissions; ordering
                 providers with poor documentation
  rework         CO-16 -> corrected claim (CLM05-3=7, REF*F8); voids (freq 8) -> reversal
  835 money      reversals (CLP02=22, everything negated) and corrections under a new ICN in the same 835;
                 PLB interest (L6), recoupments (WO), forward balances (FB) when an 835 would go negative;
                 zero-pay notification 835s (BPR01=H, BPR04=NON)
  delivery       byte-identical re-deliveries, multi-ST files, three delimiter styles, payer names that
                 differ between 837 and 835, corrupt deliveries (truncated / not X12) that must be quarantined

The ledger (truth.json) comes from the generator's own decisions, not from parsing the files, so it can check
both the parser and the SQL views. All knobs are on Scenario; `Scenario.stress()` turns the awkward
cases up. `ccsim/x12_validate.py` independently checks every emitted file (envelopes, counts, balancing).
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

GENERATOR_VERSION = "2.0"
DENIAL_CARCS = ["4", "11", "16", "18", "27", "29", "50", "96", "109", "167", "197", "204"]  # placeholder for the reviewed 23
CARC_DESCRIPTIONS = {
    "1": "Deductible", "2": "Coinsurance", "3": "Copay", "4": "Procedure code inconsistent with modifier",
    "11": "Diagnosis inconsistent with procedure", "16": "Claim lacks information needed for adjudication",
    "18": "Exact duplicate claim/service", "23": "Impact of prior payer(s) adjudication", "27": "Expenses incurred after coverage terminated",
    "29": "Time limit for filing has expired", "45": "Charge exceeds fee schedule/maximum allowable",
    "50": "Not deemed a medical necessity", "96": "Non-covered charge(s)", "97": "Payment included in another service",
    "109": "Claim not covered by this payer", "167": "Diagnosis is not covered", "197": "Precertification/authorization absent",
    "204": "Service not covered under the patient's current benefit plan",
}


# --- reference world -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Payer:
    name: str
    payer_id: str
    sbr09: str                      # 837 claim filing indicator
    payer_class: str
    clp06: str                      # 835 claim filing indicator
    denial_mult: float
    lag: tuple                      # days from receipt to payment
    fee_ratio: float                # allowed / charge, on average
    plan: str                       # ppo | hdhp | hmo | ma | medicaid
    name_835: str | None = None     # how the payer names itself in 835 N1*PR, if different
    claim_level_denials: float = 0  # share of full denials sent at claim level, no SVC detail
    ref6r_rate: float = .97
    svc06_rate: float = .01
    timely_filing_days: int = 90
    multi_st_rate: float = 0
    interest_rate: float = 0        # prompt-pay interest (PLB L6) on claims paid after 30 days
    delimiters: str = "A"


PAYERS = [
    Payer("ACME HEALTH PLAN", "AHP01", "CI", "commercial", "12", 1.0, (7, 21), .55, "ppo"),
    Payer("NORTHWIND HEALTH", "NWH02", "CI", "commercial", "12", 1.4, (10, 30), .50, "hdhp",
          name_835="NORTHWIND HEALTH PLAN INC", claim_level_denials=.5, ref6r_rate=.7, delimiters="B"),
    Payer("CONTOSO MUTUAL", "CTM03", "CI", "commercial", "HM", .8, (5, 15), .62, "hmo", multi_st_rate=.35),
    Payer("FABRIKAM MEDICARE ADVANTAGE", "FMA04", "16", "medicare_advantage", "16", 1.2, (14, 35), .38, "ma",
          timely_filing_days=365, svc06_rate=.04, delimiters="C"),
    Payer("TAILSPIN MEDICAID", "TSM05", "MC", "medicaid", "MC", 1.7, (20, 45), .30, "medicaid",
          timely_filing_days=180, interest_rate=.002),
    Payer("WOODGROVE BENEFITS", "WGB06", "CI", "commercial", "12", .6, (5, 12), .58, "ppo"),
]
PAYER_BY_ID = {p.payer_id: p for p in PAYERS}
PAYER_MIX = [30, 20, 15, 15, 10, 10]
PLANS = {  # deductible, coinsurance, copay per claim (labs), patients billed?
    "ppo": (500, .2, 0), "hdhp": (2500, .1, 0), "hmo": (0, 0, 10), "ma": (0, 0, 0), "medicaid": (0, 0, 0)}
SECONDARY_FOR = {"ma": ["TSM05", "AHP01"], "ppo": ["WGB06"], "hdhp": ["AHP01"], "hmo": [], "medicaid": []}


@dataclass(frozen=True)
class CPT:
    code: str
    price: float                    # chargemaster price per unit
    base_denial: float
    carcs: tuple                    # (carc, weight) for full denials
    description: str
    category: str                   # routine | infectious | molecular | drug | collection
    units: tuple = (1,)
    modifiers: tuple = ()           # (modifier, probability)
    medical_necessity: bool = False # denial risk rises with screening-only diagnoses


CPTS = {c.code: c for c in [
    CPT("80053", 64.00, .03, (("16", 3), ("96", 1)), "Comprehensive metabolic panel", "routine"),
    CPT("85025", 32.00, .02, (("16", 1),), "CBC with automated differential", "routine"),
    CPT("80061", 58.00, .04, (("50", 2), ("11", 1)), "Lipid panel", "routine", medical_necessity=True),
    CPT("83036", 46.00, .05, (("50", 2), ("11", 2)), "Hemoglobin A1c", "routine", medical_necessity=True),
    CPT("84443", 71.00, .04, (("50", 2), ("11", 1)), "Thyroid stimulating hormone", "routine", medical_necessity=True),
    CPT("82306", 128.00, .12, (("50", 3), ("167", 2)), "Vitamin D, 25 hydroxy", "routine", medical_necessity=True),
    CPT("87086", 41.00, .06, (("16", 1), ("96", 1)), "Urine culture, colony count", "infectious"),
    CPT("87491", 139.00, .14, (("50", 2), ("197", 1)), "Chlamydia trachomatis, amplified probe", "infectious",
        modifiers=(("59", .2),)),
    CPT("87798", 187.00, .28, (("50", 3), ("197", 2), ("167", 1)), "Infectious agent detection by NAA, NOS", "infectious",
        units=(1, 1, 2, 3), modifiers=(("59", .3),)),
    CPT("0240U", 312.00, .36, (("197", 3), ("50", 2), ("204", 1)), "Respiratory pathogen panel (PLA)", "infectious"),
    CPT("81479", 2150.00, .52, (("197", 4), ("50", 2), ("204", 2)), "Unlisted molecular pathology", "molecular"),
    CPT("81528", 592.00, .30, (("197", 2), ("167", 2)), "Colorectal cancer screening, stool DNA", "molecular"),
    CPT("G0480", 249.00, .26, (("109", 2), ("50", 2), ("4", 1)), "Drug test, definitive, 1-7 drug classes", "drug",
        modifiers=(("QW", .1),)),
    CPT("80307", 98.00, .18, (("50", 2), ("4", 1)), "Drug test, presumptive, instrument chemistry", "drug"),
    CPT("36415", 8.00, .10, (("97", 1),), "Collection of venous blood by venipuncture", "collection"),
]}
ORDERS = {  # order type: (weight, panel choices, dx choices, screening share)
    "wellness": (40, [["80053", "85025", "80061", "36415"], ["80053", "83036", "36415"], ["85025", "84443", "36415"],
                      ["80061", "83036", "82306", "36415"]], ["Z0000", "Z0001", "E785", "E119", "R7303", "E039"], .55),
    "infectious": (30, [["87491"], ["87798"], ["0240U"], ["87086"], ["87798", "87491"]],
                   ["J069", "R509", "N390", "Z113", "A749", "R0981"], .25),
    "molecular": (10, [["81479"], ["81528"]], ["Z1211", "C189", "Z8000", "D126"], .5),
    "drug": (20, [["G0480"], ["80307"], ["80307", "G0480"]], ["Z79899", "F1120", "F1110"], .3),
}
SCREENING_DX = {"Z0000", "Z0001", "Z113", "Z1211", "Z8000", "Z79899"}
FIRST = ["AVERY", "JORDAN", "TAYLOR", "MORGAN", "CASEY", "RILEY", "QUINN", "JAMIE", "SKYLER", "PARKER", "ROWAN", "DREW",
         "SAGE", "EMERY", "HARPER", "LOGAN", "REESE", "BLAKE", "DAKOTA", "FINLEY", "MARLOWE", "ELLIS", "ARDEN", "KAI"]
LAST = ["ACMEFIELD", "TESTWOOD", "SAMPLEMAN", "MOCKLER", "DUMMIT", "FAKERSON", "PLACEHOLT", "SYNTHIA", "NULLMAN",
        "FIXTURE", "STUBBS", "EXAMPLAR", "PROTOTOV", "DEMOSKI", "TRIALSON", "FIGMENTA", "PRETENDA", "LOREMSON"]
PROVIDER_LAST = ["OSLERTON", "HIPPOCRAS", "GALENSKI", "CURIEWOOD", "PASTEURO", "LISTERMAN", "SNOWDEN", "BLACKWELLO",
                 "AVICENNO", "HARVEYSON", "JENNERTON", "NIGHTINGAL"]
CITIES = [("BURLINGTON", "NC", "27215"), ("RALEIGH", "NC", "27601"), ("AUSTIN", "TX", "78701"),
          ("DENVER", "CO", "80202"), ("PHOENIX", "AZ", "85004"), ("TAMPA", "FL", "33602")]
DELIMS = {  # element, component, repetition, segment terminator, line ending after terminator
    "A": ("*", ":", "^", "~", "\n"), "B": ("|", ">", "^", "~", ""), "C": ("*", ":", "^", "~", "\r\n")}


def npi(seed: int) -> str:
    """10-digit NPI with a valid Luhn check digit (computed over the 80840 prefix, as CMS specifies)."""
    base = f"1{seed % 10**8:08d}"
    total = 0
    for i, ch in enumerate(reversed("80840" + base)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return base + str((10 - total % 10) % 10)


def npi_is_valid(n: str) -> bool:
    if len(n) != 10 or not n.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + n)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class Tenant:
    code: str
    name: str
    tax_id: str
    npi_seed: int
    claims_per_day: int
    price_mult: float
    sender_id: str
    delimiters: str


TENANTS = {
    "acme_labs": Tenant("acme_labs", "ACME LABS INC", "561234567", 4242, 120, 1.0, "ACMELABS", "A"),
    "acme_diagnostics": Tenant("acme_diagnostics", "ACME DIAGNOSTICS LLC", "561234999", 7777, 40, 1.15, "ACMEDX", "B"),
}


@dataclass
class Scenario:
    start: date = date(2026, 7, 1)
    days: int = 30
    seed: int = 7
    volume_scale: float = 1.0
    p_never_remitted: float = .04         # payer never responds within the window
    p_zero_pay_fee_schedule: float = .02  # allowed $0 under the fee schedule: CO-45, NOT a denial
    p_partial_line_denial: float = .06    # one line denied, others paid
    p_reversal_no_correction: float = .015
    p_reversal_correction: float = .025
    p_void: float = .004                  # lab voids a paid claim (freq 8) -> payer reverses it
    p_resubmit_after_co16: float = .7
    p_signal_v3_vs_recorded: float = .4   # share of full denials sent as status 1 / $0 / CO + CARC
    p_late_submission: float = .01        # submitted past timely filing -> CARC 29
    p_dependent: float = .25
    p_secondary_coverage: float = .12
    p_risky_provider: float = .12         # share of ordering providers with poor documentation
    p_duplicate_835_file: float = .04
    p_corrupt_file: float = .03           # per delivery day: an extra damaged file that must be quarantined
    p_plb_recoupment: float = .05
    p_duplicate_submission: float = 0.0   # accidental resend -> CO-18 on a new ICN (stress only)
    skip_weekends: bool = True

    @staticmethod
    def stress(**kw) -> "Scenario":
        base = dict(p_never_remitted=.08, p_zero_pay_fee_schedule=.05, p_partial_line_denial=.12,
                    p_reversal_no_correction=.04, p_reversal_correction=.06, p_void=.02, p_late_submission=.05,
                    p_duplicate_835_file=.15, p_corrupt_file=.25, p_plb_recoupment=.2, p_duplicate_submission=.01)
        base.update(kw)
        return Scenario(**base)


# --- people ----------------------------------------------------------------------------------------
@dataclass
class Person:
    first: str
    last: str
    dob: date
    gender: str
    addr: str
    city: tuple


@dataclass
class Coverage:
    payer: Payer
    member_id: str
    group: str
    subscriber: Person              # the policy holder (may be the patient)
    relationship: str               # 18 self | 01 spouse | 19 child


@dataclass
class Patient:
    pid: int
    person: Person
    coverages: list                 # [primary, secondary?]


@dataclass
class Provider:
    last: str
    first: str
    npi: str
    risky: bool


@dataclass
class Line:
    cpt: str
    units: int
    charge: float
    modifiers: tuple = ()


@dataclass
class Claim:
    pcn: str
    tenant: str
    patient: Patient
    referring: Provider
    dos: date
    lines: list
    dx: list
    submitted: date
    order_type: str


# --- X12 writer ------------------------------------------------------------------------------------
def money(x: float) -> str:
    """X12 R type: no trailing zeros, no leading '+', '-' for negatives."""
    s = f"{round(x, 2):.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class X12:
    def __init__(self, style: str):
        self.el, self.comp, self.rep, self.term, self.nl = DELIMS[style]
        self.segs: list[list[str]] = []

    def s(self, *elements):
        els = ["" if e is None else str(e) for e in elements]
        while len(els) > 1 and els[-1] == "":
            els.pop()
        self.segs.append(els)

    def c(self, *parts) -> str:
        parts = ["" if p is None else str(p) for p in parts]
        while parts and parts[-1] == "":
            parts.pop()
        return self.comp.join(parts)


def interchange(style: str, sender: str, receiver: str, d: date, ctl: int, gs_code: str, impl: str,
                transactions: list[X12], st_id: str) -> str:
    el, comp, rep, term, nl = DELIMS[style]
    isa = el.join(["ISA", "00", " " * 10, "00", " " * 10, "ZZ", f"{sender:<15}", "ZZ", f"{receiver:<15}",
                   f"{d:%y%m%d}", "1200", rep, "00501", f"{ctl:09d}", "0", "P", comp])
    out = [isa, el.join(["GS", gs_code, sender, receiver, f"{d:%Y%m%d}", "1200", str(ctl), "X", impl])]
    for i, t in enumerate(transactions, 1):
        stn = f"{i:04d}"
        out.append(el.join(["ST", st_id, stn, impl]))
        out += [el.join(s) for s in t.segs]
        out.append(el.join(["SE", str(len(t.segs) + 2), stn]))
    out += [el.join(["GE", str(len(transactions)), str(ctl)]), el.join(["IEA", "1", f"{ctl:09d}"])]
    return "".join(s + term + nl for s in out)


# --- generator -------------------------------------------------------------------------------------
def _next_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _stable_unit(*parts) -> float:
    """Deterministic number in [0, 1) from labels (fee schedules must not depend on draw order)."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16) / 16**12


class Generator:
    def __init__(self, sc: Scenario, tenants=None):
        self.sc = sc
        self.rng = random.Random(sc.seed)
        self.tenant_codes = list(tenants or TENANTS)
        self.ctl = 100

    # people and providers
    def _person(self) -> Person:
        r = self.rng
        return Person(r.choice(FIRST), r.choice(LAST), date(1940, 1, 1) + timedelta(days=r.randint(0, 29500)),
                      r.choice("MF"), f"{r.randint(1, 9999)} {r.choice(['TEST', 'SAMPLE', 'MOCK'])} ST", r.choice(CITIES))

    def _patients(self, n: int) -> list[Patient]:
        r, out = self.rng, []
        for i in range(n):
            pat = self._person()
            primary = r.choices(PAYERS, weights=PAYER_MIX)[0]
            if r.random() < self.sc.p_dependent and pat.dob.year > 1985:
                sub, rel = self._person(), r.choice(["01", "19", "19"])
                sub.last = pat.last
            else:
                sub, rel = pat, "18"
            covs = [Coverage(primary, f"{primary.payer_id[:2]}{r.randint(10**8, 10**9 - 1)}", f"G{r.randint(1000, 9999)}",
                             sub, rel)]
            if SECONDARY_FOR[primary.plan] and r.random() < self.sc.p_secondary_coverage:
                sp = PAYER_BY_ID[r.choice(SECONDARY_FOR[primary.plan])]
                covs.append(Coverage(sp, f"{sp.payer_id[:2]}{r.randint(10**8, 10**9 - 1)}", f"G{r.randint(1000, 9999)}",
                                     pat, "18"))
            out.append(Patient(i, pat, covs))
        return out

    def _providers(self, seed: int) -> list[Provider]:
        r = self.rng
        return [Provider(r.choice(PROVIDER_LAST), r.choice(FIRST), npi(seed * 1000 + i), r.random() < self.sc.p_risky_provider)
                for i in range(30)]

    def _next_ctl(self) -> int:
        self.ctl += 1
        return self.ctl

    # pricing
    def _allowed(self, payer: Payer, line: Line) -> float:
        adj = .8 + .4 * _stable_unit(payer.payer_id, line.cpt)
        return round(min(line.charge, line.charge * payer.fee_ratio * adj), 2)

    # -- 837P ----------------------------------------------------------------------------------------
    def _claim_837(self, x: X12, t: Tenant, c: Claim, seq: int, freq: str, hl: list, prior: dict | None,
                   orig_icn: str | None):
        """Append one claim. seq=0 bills the primary; seq=1 bills the secondary with the primary's
        adjudication (prior) in 2320/2330B/2430."""
        cov = c.patient.coverages[seq]
        pat = c.patient.person
        hl[0] += 1
        sub_hl = hl[0]
        dependent = cov.relationship != "18"
        x.s("HL", sub_hl, "1", "22", "1" if dependent else "0")
        x.s("SBR", "S" if seq else "P", "" if dependent else "18", cov.group, "", "", "", "", "", cov.payer.sbr09)
        s = cov.subscriber
        x.s("NM1", "IL", "1", s.last, s.first, "", "", "", "MI", cov.member_id)
        x.s("N3", s.addr)
        x.s("N4", *s.city)
        x.s("DMG", "D8", f"{s.dob:%Y%m%d}", s.gender)
        x.s("NM1", "PR", "2", cov.payer.name, "", "", "", "", "PI", cov.payer.payer_id)
        if dependent:
            hl[0] += 1
            x.s("HL", hl[0], sub_hl, "23", "0")
            x.s("PAT", cov.relationship)
            x.s("NM1", "QC", "1", pat.last, pat.first)
            x.s("N3", pat.addr)
            x.s("N4", *pat.city)
            x.s("DMG", "D8", f"{pat.dob:%Y%m%d}", pat.gender)
        total = round(sum(l.charge for l in c.lines), 2)
        x.s("CLM", c.pcn, money(total), "", "", x.c("81", "B", freq), "Y", "A", "Y", "Y")
        if freq in ("7", "8"):
            x.s("REF", "F8", orig_icn)
        x.s("HI", *[x.c("ABK" if i == 0 else "ABF", dx) for i, dx in enumerate(c.dx)])
        r = c.referring
        x.s("NM1", "DN", "1", r.last, r.first, "", "", "", "XX", r.npi)
        if prior:                                                 # 2320 / 2330A / 2330B: the primary's adjudication
            pcov = c.patient.coverages[0]
            x.s("SBR", "P", "18" if pcov.relationship == "18" else "", pcov.group, "", "", "", "", "", pcov.payer.sbr09)
            for grp, carc, amt in prior["claim_cas"]:
                x.s("CAS", grp, carc, money(amt))
            x.s("AMT", "D", money(prior["paid"]))
            x.s("OI", "", "", "Y", "", "", "Y")
            x.s("NM1", "IL", "1", pcov.subscriber.last, pcov.subscriber.first, "", "", "", "MI", pcov.member_id)
            x.s("NM1", "PR", "2", pcov.payer.name, "", "", "", "", "PI", pcov.payer.payer_id)
        for i, l in enumerate(c.lines, 1):
            x.s("LX", i)
            x.s("SV1", x.c("HC", l.cpt, *l.modifiers), money(l.charge), "UN", l.units, "", "", "1")
            x.s("DTP", "472", "D8", f"{c.dos:%Y%m%d}")
            x.s("REF", "6R", f"{c.pcn}-{i}")
            if prior:
                pl = prior["lines"][i - 1]
                x.s("SVD", c.patient.coverages[0].payer.payer_id, money(pl["paid"]), x.c("HC", l.cpt, *l.modifiers), "",
                    l.units)
                for grp, carc, amt in pl["cas"]:
                    x.s("CAS", grp, carc, money(amt))
                x.s("DTP", "573", "D8", f"{prior['date']:%Y%m%d}")

    def _file_837(self, t: Tenant, d: date, items: list) -> str:
        x = X12(t.delimiters)
        x.s("BHT", "0019", "00", f"{t.sender_id}{d:%Y%m%d}{self.ctl + 1}", f"{d:%Y%m%d}", "1200", "CH")
        x.s("NM1", "41", "2", t.name, "", "", "", "", "46", t.tax_id)
        x.s("PER", "IC", "BILLING OFFICE", "TE", "5555550100")
        x.s("NM1", "40", "2", "CLAIMCLARITY CLEARINGHOUSE", "", "", "", "", "46", "CCCH01")
        x.s("HL", "1", "", "20", "1")
        x.s("PRV", "BI", "PXC", "291U00000X")
        x.s("NM1", "85", "2", t.name, "", "", "", "", "XX", npi(t.npi_seed))
        x.s("N3", "1 ACME WAY")
        x.s("N4", "BURLINGTON", "NC", "272150001")
        x.s("REF", "EI", t.tax_id)
        hl = [1]
        for c, seq, freq, prior, orig_icn in items:
            self._claim_837(x, t, c, seq, freq, hl, prior, orig_icn)
        return interchange(t.delimiters, t.sender_id, "CLAIMCLARITY", d, self._next_ctl(), "HC", "005010X222A1", [x], "837")

    # -- adjudication ----------------------------------------------------------------------------------
    def _adjudicate(self, c: Claim, seq: int, outcome: str, carc: str | None, accum: dict, d: date,
                    prior: dict | None = None) -> dict:
        r = self.rng
        cov = c.patient.coverages[seq]
        payer = cov.payer
        deductible, coins, copay = PLANS[payer.plan]
        acc_key = (c.patient.pid, payer.payer_id, d.year)
        ded_left = accum.setdefault(acc_key, deductible)
        lines, copay_left = [], copay
        deny_line = r.randrange(len(c.lines)) if outcome == "partial" else None
        for i, l in enumerate(c.lines):
            cas, rarc = [], None
            if seq == 1:                                          # secondary: pays toward what the primary left the patient
                pl = prior["lines"][i]
                left = round(sum(a for g, _, a in pl["cas"] if g == "PR"), 2)
                cas.append(("OA", "23", round(l.charge - left, 2)))
                paid = round(left * (1.0 if payer.plan == "medicaid" else r.choice([1.0, .8])), 2)
                rest = round(left - paid, 2)
                if rest:
                    cas.append(("CO" if payer.plan == "medicaid" else "PR", "45" if payer.plan == "medicaid" else "2", rest))
                allowed = left
            elif outcome in ("denied", "claim_level_denied") or i == deny_line:
                code = carc if i != deny_line else ("97" if l.cpt == "36415" else self._denial_carc(c, l))
                paid, allowed = 0.0, 0.0
                cas.append(("CO", code, l.charge))
                rarc = r.choice(["N130", "M127", "N290", "N522"]) if outcome != "claim_level_denied" else None
            elif outcome == "zero_fee":
                paid, allowed = 0.0, 0.0
                cas.append(("CO", "45", l.charge))
            else:
                allowed = self._allowed(payer, l)
                pr_parts = []
                ded = round(min(ded_left, allowed), 2)
                if ded > 0:
                    pr_parts.append(("1", ded))
                    ded_left = round(ded_left - ded, 2)
                co_ins = round((allowed - ded) * coins, 2)
                if co_ins > 0:
                    pr_parts.append(("2", co_ins))
                cp = round(min(copay_left, allowed - ded - co_ins), 2)
                if cp > 0:
                    pr_parts.append(("3", cp))
                    copay_left = round(copay_left - cp, 2)
                paid = round(allowed - sum(a for _, a in pr_parts), 2)
                if l.charge - allowed > 0:
                    cas.append(("CO", "45", round(l.charge - allowed, 2)))
                cas += [("PR", code, a) for code, a in pr_parts]
            lines.append(dict(cpt=l.cpt, adjudicated_cpt=l.cpt, units=l.units, modifiers=l.modifiers, charge=l.charge,
                              paid=paid, allowed=allowed, cas=cas, rarc=rarc))
        accum[acc_key] = ded_left
        e = dict(pcn=c.pcn, payer_id=payer.payer_id, seq=seq, lines=lines, claim_cas=[],
                 icn=f"{payer.payer_id[:3]}{_stable_unit(c.pcn, payer.payer_id, 'icn') * 1e10:010.0f}",
                 claim_level_only=outcome == "claim_level_denied", status=None)
        if outcome == "claim_level_denied":                      # move CAS to the claim, drop line detail
            e["claim_cas"] = [("CO", carc, round(sum(l.charge for l in c.lines), 2))]
            for ln in lines:
                ln["cas"] = []
        if outcome == "paid" and seq == 0 and r.random() < payer.svc06_rate and lines[0]["cpt"] == "80053":
            lines[0]["adjudicated_cpt"] = "80048"                 # payer re-coded: SVC01 = paid code, SVC06 = billed
        e["no_6r"] = r.random() > payer.ref6r_rate
        e["paid"] = round(sum(ln["paid"] for ln in lines), 2)
        e["pr"] = round(sum(a for ln in lines for g, _, a in ln["cas"] if g == "PR"), 2)
        return e

    def _denial_carc(self, c: Claim, l: Line) -> str:
        cpt = CPTS[l.cpt]
        codes, weights = zip(*cpt.carcs)
        if cpt.medical_necessity and set(c.dx) & SCREENING_DX:
            weights = [w * (3 if code in ("50", "11", "167") else 1) for code, w in zip(codes, weights)]
        if c.referring.risky:
            weights = [w * (3 if code == "16" else 1) for code, w in zip(codes, weights)]
        return self.rng.choices(codes, weights=weights)[0]

    def _p_denial(self, c: Claim, payer: Payer) -> float:
        p = max(CPTS[l.cpt].base_denial for l in c.lines) * payer.denial_mult
        if any(CPTS[l.cpt].medical_necessity for l in c.lines) and set(c.dx) & SCREENING_DX:
            p *= 1.8
        if c.referring.risky:
            p *= 1.8
        return min(p, .95)

    @staticmethod
    def _reverse(e: dict) -> dict:
        neg = lambda cas: [(g, k, -a) for g, k, a in cas]
        return dict(e, status="22", paid=-e["paid"], pr=-e["pr"], claim_cas=neg(e["claim_cas"]),
                    lines=[dict(ln, paid=-ln["paid"], allowed=-ln["allowed"], charge=-ln["charge"], cas=neg(ln["cas"]))
                           for ln in e["lines"]], reversal=True)

    # -- 835 ------------------------------------------------------------------------------------------
    def _st_835(self, x: X12, t: Tenant, payer: Payer, d: date, events: list, plbs: list, trn: str):
        total = round(sum(e["paid"] for e in events) - sum(a for _, _, a in plbs), 2)
        if total == 0:
            x.s("BPR", "H", "0", "C", "NON", "", "", "", "", "", "", "", "", "", "", "", f"{d:%Y%m%d}")
        else:
            x.s("BPR", "I", money(total), "C", "ACH", "CCP", "01", "999999999", "DA", "1234567", f"1{payer.payer_id}", "",
                "01", "888888888", "DA", "7654321", f"{d:%Y%m%d}")
        x.s("TRN", "1", trn, f"1{payer.payer_id}")
        x.s("DTM", "405", f"{d:%Y%m%d}")
        x.s("N1", "PR", payer.name_835 or payer.name)
        x.s("N3", "PO BOX 1000")
        x.s("N4", "HARTFORD", "CT", "06101")
        x.s("REF", "2U", payer.payer_id)
        x.s("PER", "BL", "PROVIDER SERVICES", "TE", "8005550199")
        x.s("N1", "PE", t.name, "XX", npi(t.npi_seed))
        x.s("REF", "TJ", t.tax_id)
        if events:
            x.s("LX", "1")
        for e in events:
            c: Claim = e["claim"]
            charge = round(sum(ln["charge"] for ln in e["lines"]), 2)
            x.s("CLP", c.pcn, e["status"], money(charge), money(e["paid"]), money(e["pr"]) if e["pr"] else "",
                payer.clp06, e["icn"], "81", "1")
            for grp, carc, amt in e["claim_cas"]:
                x.s("CAS", grp, carc, money(amt))
            cov = c.patient.coverages[e["seq"]]
            p = c.patient.person
            x.s("NM1", "QC", "1", p.last, p.first, "", "", "", "MI" if cov.relationship == "18" else "", cov.member_id
                if cov.relationship == "18" else "")
            if cov.relationship != "18":
                x.s("NM1", "IL", "1", cov.subscriber.last, cov.subscriber.first, "", "", "", "MI", cov.member_id)
            x.s("DTM", "232", f"{c.dos:%Y%m%d}")
            x.s("DTM", "050", f"{e['received']:%Y%m%d}")
            if e["claim_level_only"]:
                continue
            for i, ln in enumerate(e["lines"], 1):
                billed = x.c("HC", ln["cpt"], *ln["modifiers"])
                adjud = x.c("HC", ln["adjudicated_cpt"], *ln["modifiers"])
                x.s("SVC", adjud, money(ln["charge"]), money(ln["paid"]), "", ln["units"],
                    billed if ln["adjudicated_cpt"] != ln["cpt"] else "")
                x.s("DTM", "472", f"{c.dos:%Y%m%d}")
                for grp, carc, amt in ln["cas"]:
                    x.s("CAS", grp, carc, money(amt))
                if not e["no_6r"]:
                    x.s("REF", "6R", f"{c.pcn}-{i}")
                if ln["allowed"] or ln["paid"]:
                    x.s("AMT", "B6", money(ln["allowed"]))
                if ln["rarc"]:
                    x.s("LQ", "HE", ln["rarc"])
        for code, ref, amt in plbs:
            x.s("PLB", npi(t.npi_seed), f"{d.year}1231", x.c(code, ref) if ref else code, money(amt))
        return total

    def run(self, out_dir: str | Path) -> dict:
        sc, r = self.sc, self.rng
        out = Path(out_dir)
        truth = {"generator_version": GENERATOR_VERSION, "scenario": {k: str(v) for k, v in asdict(sc).items()},
                 "denial_carcs": DENIAL_CARCS, "tenants": {}}
        end = sc.start + timedelta(days=sc.days)
        for tc in self.tenant_codes:
            t = TENANTS[tc]
            n_pat = max(50, int(t.claims_per_day * sc.volume_scale * sc.days * .35))
            patients, providers = self._patients(n_pat), self._providers(t.npi_seed)
            accum: dict = {}
            outbox: dict[date, list] = {}                         # day -> 837 items
            pending: dict[tuple, list] = {}                       # (pay_day, payer_id) -> events
            claims: dict[str, dict] = {}
            n = 0

            def schedule(e, day):
                day = _next_business_day(day)
                # DTM*050: the payer received the claim a business day after it was sent
                e["received"] = e.get("received") or _next_business_day(e["claim"].submitted + timedelta(days=1))
                pending.setdefault((day, e["payer_id"]), []).append(e)

            d = sc.start
            while d < end:
                if sc.skip_weekends and d.weekday() >= 5:
                    d += timedelta(days=1)
                    continue
                factor = {0: 1.3, 4: .9}.get(d.weekday(), 1.0) * r.uniform(.8, 1.2)
                for _ in range(max(1, round(t.claims_per_day * sc.volume_scale * factor))):
                    n += 1
                    c = self._new_claim(t, n, d, patients, providers)
                    claims[c.pcn] = self._ledger_claim(c)
                    outbox.setdefault(d, []).append((c, 0, "1", None, None))
                    self._plan(c, d, schedule, outbox, claims, accum)
                d += timedelta(days=1)

            files: list[dict] = []
            fwd: dict[str, float] = {}                            # payer -> forward balance owed back
            days = sorted({k[0] for k in pending} | set(outbox))
            for day in days:
                if day >= end:
                    continue
                if day in outbox:
                    items = outbox[day]
                    for c, seq, freq, prior, orig in items:
                        led = claims[c.pcn]
                        led["submissions"].append(dict(date=str(day), payer_id=c.patient.coverages[seq].payer.payer_id,
                                                       seq=seq, frequency=freq))
                    files.append(dict(day=day, kind="837", name=f"{tc}_837P_{day:%Y%m%d}.x12",
                                      body=self._file_837(t, day, items), expect="parsed"))
                for payer in PAYERS:
                    evs = pending.get((day, payer.payer_id))
                    if not evs:
                        continue
                    files += self._files_835(t, payer, day, evs, fwd, claims)
                if r.random() < sc.p_corrupt_file and files:
                    files.append(self._corrupt(r.choice([f for f in files if f["day"] == day] or files), day))
            for f in files:
                p = out / tc / "raw" / f"received_date={f['day']}" / f["kind"] / f["name"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(f["body"] if isinstance(f["body"], bytes) else f["body"].encode("ascii"))
            truth["tenants"][tc] = {"claims": _finalize(claims), "files": [
                {k: (str(v) if k == "day" else v) for k, v in f.items() if k != "body"} for f in files],
                "file_days": sorted({str(f["day"]) for f in files}), "plb": self.plb_log.get(tc, [])}
            for led in claims.values():
                led.pop("_claim", None)
        return truth

    plb_log: dict = {}

    def _new_claim(self, t: Tenant, n: int, d: date, patients, providers) -> Claim:
        r = self.rng
        otype = r.choices(list(ORDERS), weights=[o[0] for o in ORDERS.values()])[0]
        _, panels, dxs, screen_share = ORDERS[otype]
        codes = r.choice(panels)
        lines = []
        for code in codes:
            cpt = CPTS[code]
            units = r.choice(cpt.units)
            mods = tuple(m for m, p in cpt.modifiers if r.random() < p)
            lines.append(Line(code, units, round(cpt.price * t.price_mult * units, 2), mods))
        screening = [x for x in dxs if x in SCREENING_DX]
        diagnostic = [x for x in dxs if x not in SCREENING_DX]
        pool = screening if (r.random() < screen_share and screening) else (diagnostic or dxs)
        dx = r.sample(pool, 1) + r.sample(dxs, r.choice([0, 0, 1, 2]))
        dx = list(dict.fromkeys(dx))
        dos = d - timedelta(days=r.randint(1, 5))
        if r.random() < self.sc.p_late_submission:
            dos = d - timedelta(days=r.randint(120, 400))
        return Claim(f"{t.code[:2].upper()}{d:%y%m%d}{n:06d}", t.code, r.choice(patients), r.choice(providers), dos,
                     lines, dx, d, otype)

    def _ledger_claim(self, c: Claim) -> dict:
        return dict(pcn=c.pcn, dos=str(c.dos), submitted=str(c.submitted), order_type=c.order_type,
                    cpts=[l.cpt for l in c.lines], units=[l.units for l in c.lines],
                    line_charges=[l.charge for l in c.lines], charge=round(sum(l.charge for l in c.lines), 2),
                    dx=c.dx, principal_dx=c.dx[0], referring_npi=c.referring.npi, risky_provider=c.referring.risky,
                    dependent=c.patient.coverages[0].relationship != "18",
                    payers=[cv.payer.payer_id for cv in c.patient.coverages], submissions=[], adjudications={},
                    _claim=c)

    def _plan(self, c: Claim, d: date, schedule, outbox, claims, accum):
        """Decide this claim's whole life now; events are scheduled for later days."""
        sc, r = self.sc, self.rng
        payer = c.patient.coverages[0].payer
        if r.random() < sc.p_never_remitted:
            return
        pay_day = d + timedelta(days=r.randint(*payer.lag))
        late = (c.submitted - c.dos).days > payer.timely_filing_days
        u = r.random()
        cum = 0.0

        def add(e, day, status):
            e = dict(e, status=status, claim=c)
            schedule(e, day)
            return e

        if late:
            carc = "29"
            add(self._adjudicate(c, 0, "denied", carc, accum, pay_day), pay_day, "4")
            return
        for name, p in (("rev", sc.p_reversal_no_correction), ("revcor", sc.p_reversal_correction),
                        ("void", sc.p_void), ("zero", sc.p_zero_pay_fee_schedule), ("partial", sc.p_partial_line_denial)):
            cum += p
            if u < cum:
                break
        else:
            name = None
        if name == "rev":
            e = add(self._adjudicate(c, 0, "paid", None, accum, pay_day), pay_day, "1")
            later = pay_day + timedelta(days=r.randint(14, 40))
            add(self._reverse(e), later, "22")
            return
        if name == "revcor":
            carc = self._denial_carc(c, c.lines[0])
            e = add(self._adjudicate(c, 0, "denied", carc, accum, pay_day), pay_day, "4")
            later = pay_day + timedelta(days=r.randint(10, 30))
            add(self._reverse(e), later, "22")
            cor = self._adjudicate(c, 0, "paid", None, accum, later)
            cor["icn"] = e["icn"] + "C"
            add(cor, later, "1")
            return
        if name == "void":
            e = add(self._adjudicate(c, 0, "paid", None, accum, pay_day), pay_day, "1")
            void_day = _next_business_day(pay_day + timedelta(days=r.randint(3, 10)))
            outbox.setdefault(void_day, []).append((c, 0, "8", None, e["icn"]))
            add(self._reverse(e), void_day + timedelta(days=r.randint(*payer.lag)), "22")
            return
        if name == "zero":
            add(self._adjudicate(c, 0, "zero_fee", None, accum, pay_day), pay_day, "1")
            return
        if name == "partial" and len(c.lines) > 1:
            e = add(self._adjudicate(c, 0, "partial", None, accum, pay_day), pay_day, "1")
            self._maybe_secondary(c, e, pay_day, schedule, outbox, accum)
            return
        if r.random() < self._p_denial(c, payer):
            carc = self._denial_carc(c, max(c.lines, key=lambda l: CPTS[l.cpt].base_denial))
            if carc == "97":
                carc = "16"
            level = "claim_level_denied" if r.random() < payer.claim_level_denials else "denied"
            status = "1" if r.random() < sc.p_signal_v3_vs_recorded else "4"
            e = add(self._adjudicate(c, 0, level, carc, accum, pay_day), pay_day, status)
            if carc == "16" and r.random() < sc.p_resubmit_after_co16:
                rs = _next_business_day(pay_day + timedelta(days=r.randint(3, 10)))
                outbox.setdefault(rs, []).append((c, 0, "7", None, e["icn"]))
                fixed = self._adjudicate(c, 0, "paid", None, accum, rs)
                fixed["icn"] = e["icn"] + "R"
                fixed["received"] = rs
                add(fixed, rs + timedelta(days=r.randint(*payer.lag)), "1")
            return
        e = add(self._adjudicate(c, 0, "paid", None, accum, pay_day), pay_day, "1")
        if r.random() < sc.p_duplicate_submission:
            dup_day = _next_business_day(d + timedelta(days=r.randint(2, 6)))
            outbox.setdefault(dup_day, []).append((c, 0, "1", None, None))
            dup = self._adjudicate(c, 0, "denied", "18", accum, dup_day)
            dup["icn"], dup["received"], dup["duplicate_denial"] = e["icn"] + "D", dup_day, True
            add(dup, dup_day + timedelta(days=r.randint(*payer.lag)), "4")
        self._maybe_secondary(c, e, pay_day, schedule, outbox, accum)

    def _maybe_secondary(self, c, primary_event, pay_day, schedule, outbox, accum):
        if len(c.patient.coverages) < 2 or primary_event["pr"] <= 0:
            return
        r = self.rng
        sec_payer = c.patient.coverages[1].payer
        bill_day = _next_business_day(pay_day + timedelta(days=r.randint(2, 6)))
        prior = dict(paid=primary_event["paid"], date=pay_day, claim_cas=primary_event["claim_cas"],
                     lines=[dict(paid=ln["paid"], cas=ln["cas"]) for ln in primary_event["lines"]])
        outbox.setdefault(bill_day, []).append((c, 1, "1", prior, None))
        e = self._adjudicate(c, 1, "paid", None, accum, bill_day, prior)
        e["received"] = bill_day
        e = dict(e, status="2", claim=c)
        schedule(e, bill_day + timedelta(days=r.randint(*sec_payer.lag)))

    def _files_835(self, t: Tenant, payer: Payer, day: date, evs: list, fwd: dict, claims: dict) -> list[dict]:
        r, sc = self.rng, self.sc
        groups = [evs]
        if len(evs) > 3 and r.random() < payer.multi_st_rate:
            k = len(evs) // 2
            groups = [evs[:k], evs[k:]]                           # one file, two checks / transactions
            # keep a reversal and its correction in the same transaction
            while k < len(evs) and groups[1] and groups[0][-1]["pcn"] == groups[1][0]["pcn"]:
                groups = [evs[:k + 1], evs[k + 1:]]
                k += 1
            groups = [g for g in groups if g]
        txs = []
        for gi, g in enumerate(groups):
            x = X12(payer.delimiters)
            plbs = []
            if fwd.get(payer.payer_id):
                plbs.append(("FB", f"FWD{payer.payer_id}", fwd.pop(payer.payer_id)))   # recover last forward balance
            if r.random() < sc.p_plb_recoupment:
                paid = [e for e in g if e["status"] == "1" and e["paid"] > 0]
                if paid:
                    e = r.choice(paid)
                    plbs.append(("WO", e["icn"], round(min(e["paid"], r.uniform(5, 40)), 2)))
            if payer.interest_rate:
                late = [e for e in g if e["paid"] > 0 and (day - e["received"]).days > 30]
                if late:
                    plbs.append(("L6", None, -round(sum(e["paid"] for e in late) * payer.interest_rate *
                                                    max((day - e["received"]).days for e in late) / 30, 2) or -0.01))
            total = round(sum(e["paid"] for e in g) - sum(a for _, _, a in plbs), 2)
            if total < 0:                                         # an 835 never pays a negative amount
                plbs.append(("FB", f"FWD{payer.payer_id}", total))
                fwd[payer.payer_id] = round(fwd.get(payer.payer_id, 0) - total, 2)
            trn = f"EFT{day:%Y%m%d}{payer.payer_id}{gi + 1}"
            bpr = self._st_835(x, t, payer, day, g, plbs, trn)
            self.plb_log.setdefault(t.code, []).extend(
                dict(day=str(day), payer_id=payer.payer_id, trn=trn, code=code, reference=ref, amount=amt)
                for code, ref, amt in plbs)
            txs.append((x, bpr, trn))
            for e in g:
                led = claims[e["pcn"]]["adjudications"].setdefault(e["payer_id"], dict(
                    payer_id=e["payer_id"], payer=PAYER_BY_ID[e["payer_id"]].name, seq=e["seq"],
                    payer_class=PAYER_BY_ID[e["payer_id"]].payer_class, events=[]))
                led["events"].append(dict(date=str(day), status=e["status"], icn=e["icn"], paid=e["paid"], pr=e["pr"],
                                          claim_level_only=e["claim_level_only"], trn=trn,
                                          duplicate_denial=bool(e.get("duplicate_denial")),
                                          carcs=sorted({k for _, k, _ in e["claim_cas"]} |
                                                       {k for ln in e["lines"] for _, k, _ in ln["cas"]}),
                                          co_carcs=sorted({k for g_, k, _ in e["claim_cas"] if g_ == "CO"} |
                                                          {k for ln in e["lines"] for g_, k, _ in ln["cas"] if g_ == "CO"}),
                                          lines=[dict(cpt=ln["cpt"], adjudicated_cpt=ln["adjudicated_cpt"],
                                                      paid=ln["paid"], allowed=ln["allowed"],
                                                      co_carcs=sorted({k for g_, k, _ in ln["cas"] if g_ == "CO"}))
                                                 for ln in ([] if e["claim_level_only"] else e["lines"])]))
        body = interchange(payer.delimiters, payer.payer_id, t.sender_id, day, self._next_ctl(), "HP", "005010X221A1",
                           [tx[0] for tx in txs], "835")
        out = [dict(day=day, kind="835", name=f"{t.code}_835_{payer.payer_id}_{day:%Y%m%d}.x12", body=body,
                    expect="parsed", payer_id=payer.payer_id, trns=[tx[2] for tx in txs], totals=[tx[1] for tx in txs])]
        if r.random() < sc.p_duplicate_835_file:
            out.append(dict(out[0], name=out[0]["name"].replace(".x12", "_redelivered.x12"), expect="duplicate"))
        return out

    def _corrupt(self, f: dict, day: date) -> dict:
        r = self.rng
        body = f["body"] if isinstance(f["body"], str) else f["body"].decode()
        kind = r.choice(["truncated", "not_x12", "bad_envelope"])
        if kind == "truncated":
            bad = body[: r.randint(len(body) // 3, max(len(body) // 3 + 1, len(body) - 40))]
        elif kind == "not_x12":
            bad = "%PDF-1.4\n% scanned remittance image, not an EDI file\n"
        else:
            bad = body.replace("IEA" + body[3], "IEX" + body[3], 1)   # trailer damaged: IEA missing
        return dict(day=day, kind=f["kind"], name=f["name"].replace(".x12", f"_{kind}.x12"), body=bad,
                    expect="rejected", corruption=kind)


def _finalize(claims: dict) -> dict:
    """Current state per claim and payer, from the events actually delivered.

    Mirrors the business rule, not the SQL: the latest event per (claim, payer) is current unless it is a
    reversal. denied = recorded (status 4) or signal v3 ($0 paid, not a reversal, CO with a reviewed CARC).
    Exact-duplicate denials (CO-18 on an accidental resend) do not replace the real adjudication.
    """
    out = {}
    for pcn, led in claims.items():
        led = {k: v for k, v in led.items() if k != "_claim"}
        for pid, a in led["adjudications"].items():
            evs = [e for e in a["events"] if not e["duplicate_denial"]]
            cur = evs[-1] if evs else None
            remitted = cur is not None and cur["status"] != "22"
            recorded = remitted and cur["status"] == "4"
            signal = remitted and cur["paid"] == 0 and any(k in DENIAL_CARCS for k in cur["co_carcs"])
            if cur:                                               # line rule: $0, not a reversal, and a reviewed
                for ln in cur["lines"]:                           # CO CARC on the line or a denied claim
                    ln["denied"] = (remitted and ln["paid"] == 0 and
                                    (any(k in DENIAL_CARCS for k in ln["co_carcs"]) or recorded or signal))
            a.update(remitted_current=remitted, denied=recorded or signal, denied_recorded=recorded,
                     denied_signal_v3=signal and not recorded, paid_current=cur["paid"] if remitted else 0.0,
                     status_current=cur["status"] if cur else None, ever_reversed=any(e["status"] == "22" for e in evs),
                     claim_level_only=bool(cur and cur["claim_level_only"]),
                     has_duplicate_denial=any(e["duplicate_denial"] for e in a["events"]))
        prim = led["adjudications"].get(led["payers"][0])
        led.update(remitted=bool(led["adjudications"]), primary=prim and {k: prim[k] for k in (
            "remitted_current", "denied", "paid_current", "status_current")},
            resubmitted=any(s["frequency"] == "7" for s in led["submissions"]),
            voided=any(s["frequency"] == "8" for s in led["submissions"]))
        out[pcn] = led
    return out


def generate(out_dir: str | Path, scenario: Scenario | None = None, tenants=None) -> dict:
    g = Generator(scenario or Scenario(), tenants)
    g.plb_log = {}
    truth = g.run(out_dir)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "truth.json").write_text(json.dumps(truth, indent=1, default=str))
    return truth


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--stress", action="store_true")
    a = ap.parse_args()
    sc = Scenario.stress(days=a.days, volume_scale=a.scale, seed=a.seed) if a.stress else \
        Scenario(days=a.days, volume_scale=a.scale, seed=a.seed)
    t = generate(a.out, sc)
    for tc, v in t["tenants"].items():
        print(f"{tc}: {len(v['claims'])} claims, {len(v['files'])} files over {len(v['file_days'])} days, "
              f"{sum(f['expect'] == 'rejected' for f in v['files'])} corrupt")
