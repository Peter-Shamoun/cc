"""Mock 837P / 835 X12 for ACME tenants (clinical reference labs), plus an independent ground-truth ledger.

Calibrated to `research/edi_realism_calibration.yaml` (sourced targets; see `research/edi_realism_notes.md`).
Patients, providers, tax ids, EINs, bank accounts and addresses are invented. Payer names and payer ids follow
real conventions (the lab's Medicare MAC jurisdiction number, the national commercial ids the spec lists, a
Zipf-shaped long tail of invented regional plans and TPAs) so payer resolution behaves as it does on real feeds.
Every billed code is on the CMS CY2026 Clinical Laboratory Fee Schedule (`ccsim/data/clfs_2026q4.csv`), except
81479 (MAC-priced) and 88305 (pathology, Physician Fee Schedule), which real lab traffic also carries.

  payers       traditional Medicare (MAC: 14-day payment floor, interest after day 30, CO-253 sequestration on
               every paid line, $0 patient share on CLFS tests, line-level CAS only, MOA remarks, COBA crossover
               as CLP02=19 + NM1*TT + MA18), Medicare Advantage, Medicaid FFS (weekly checkwrite) and MCOs, Blues,
               national commercial, a long tail of small payers (sparse 835s, some paper-only), out-of-network
  pricing      chargemaster = Medicare average submitted charge (4-11x CLFS on routine tests) with a per-tenant
               spread; allowed = CLFS x payer multiplier x stable contract noise (Medicaid capped at CLFS);
               non-contracted payers allow a share of billed charges
  orders       requisition profiles (routine blood, urine with culture reflex, STI, respiratory, PCR panels,
               drug monitoring, molecular, pathology) plus a long tail of CLFS codes: ~4.9 lines per claim;
               venipuncture on ~75% of draws; 87798 billed in multiples of targets; respiratory season
  patients     repeat visits per payer class, deductibles (KFF 2025 averages) met through the year by other
               care, coinsurance/copays, ACA preventive screening at $0, dependents, COB secondaries
  denials      ~11% of claims get a denial, ~60% of them line-level on otherwise paid claims; the reason mix
               follows XiFin 2025 (bundling 97/234/236/B15, duplicates 18, non-covered, prior auth, modifier,
               COB, experimental, wrong payer, information 16 with RARCs, medical necessity, eligibility, PECOS
               183, CLIA B7, MUE 151); recorded (CLP02=4) vs signal-v3 (status 1, $0, CO + reviewed CARC)
  rework       corrected claims (freq 7, or a new freq-1 claim for Medicare), voids (freq 8), duplicate resends
  835 money    reversals (CLP02=22) and corrections (same 835, or split across 835s with offsetting PLB WO),
               MAC mass-adjustment waves, PLB L6 interest (= sum of AMT*I), WO recoupments, 72 refunds, J1, CS,
               IR, forward balances (FB); zero-pay advices (BPR01=H)
  calendar     837s on business days only (Monday catch-up, day after a holiday 1.5x), 835s on each payer's own
               cycle and never on weekends or federal holidays; EFT (BPR16) up to 3 business days after the 835
  delivery     clearinghouse as 835 ISA sender, TRN03 = 1 + payer EIN, REF*2U on some payers, 835 payer names
               that differ from the 837's, byte-identical re-deliveries, multi-ST files, three delimiter styles,
               corrupt deliveries (truncated / not X12) that must be quarantined

The ledger comes from the generator's own decisions, not from parsing the files, so it can check both the
parser and the SQL views. It follows the business rules of claude/schema/02_semantic_layer_views.sql: the
current adjudication is the latest event per claim+payer unless it is a reversal; denied = CLP02=4 OR ($0 paid,
not a reversal, CO adjustment with a CARC in DENIAL_CARCS). All knobs are on Scenario; `Scenario.stress()`
turns the awkward cases up. `ccsim/x12_validate.py` independently checks every emitted file.

Volume and streaming. `Scenario.target_mb_per_day` sets each tenant's claims per day from measured bytes per
claim (`bytes_per_day_model`); `volume_scale` still works when it is unset. A run steps one calendar day at a
time and writes each day's files as soon as the day is final, so memory stays flat over multi-year runs. On disk:

  <out>/<tenant>/raw/received_date=<day>/{837,835}/*.x12
  <out>/truth.json                    index: scenario, files, file days, PLB log per tenant
  <out>/truth/<tenant>/claims.jsonl   one finalized ledger row per claim (.jsonl.gz with truth_gzip)

`load_truth(out)` returns the classic one-dict ledger (what `generate` returns); `iter_claims` streams rows.
`generate_days` (and `python -m ccsim.edi_mock OUT --mb-per-day 5 --days 365 --each CMD`) hands over one day
directory at a time, so a pipeline can ingest and delete day N before day N+1 is generated. Each tenant has its
own random stream; workers > 1 runs tenants in parallel processes with byte-identical output.
"""
from __future__ import annotations

import bisect
import gc
import gzip
import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from functools import lru_cache
from itertools import accumulate
from pathlib import Path
from statistics import NormalDist
from typing import Iterator, NamedTuple

from ccsim import edi_reference as R

GENERATOR_VERSION = "3.1"
RECOUPMENT_CANDIDATE_SHARE = .15   # paid claims kept as possible targets of a later PLB recoupment (bounded memory)
RECOUPMENT_MAX_DAYS = 400          # recoupments reach back at most about a year
CLEARINGHOUSE_ID = "CC"
CLEARINGHOUSE_NAME = "CC CLEARINGHOUSE"

# The reviewed denial CARC list (ref.denial_carc): the denial reasons the calibration spec names. Contractual
# (45), sequestration (253), patient share (1/2/3) and prior-payer (23) adjustments are NOT denials.
DENIAL_CARCS = ["4", "11", "15", "16", "18", "22", "24", "27", "29", "50", "55", "96", "97", "109", "119", "151", "167",
                "183", "197", "198", "204", "234", "236", "242", "252", "288", "B1", "B7", "B15"]
CARC_DESCRIPTIONS = {
    "1": "Deductible amount", "2": "Coinsurance amount", "3": "Co-payment amount",
    "4": "Procedure code inconsistent with the modifier used, or a required modifier is missing",
    "11": "Diagnosis inconsistent with the procedure", "15": "Authorization number missing, invalid, or does not apply",
    "16": "Claim/service lacks information or has submission/billing error(s)", "18": "Exact duplicate claim/service",
    "22": "This care may be covered by another payer per coordination of benefits",
    "23": "Impact of prior payer(s) adjudication including payments and/or adjustments",
    "24": "Charges are covered under a capitation agreement/managed care plan",
    "27": "Expenses incurred after coverage terminated", "29": "The time limit for filing has expired",
    "45": "Charge exceeds fee schedule/maximum allowable or contracted/legislated fee arrangement",
    "50": "Non-covered: not deemed a medical necessity by the payer",
    "55": "Procedure/treatment/drug is deemed experimental/investigational by the payer",
    "96": "Non-covered charge(s)", "97": "Payment is included in the allowance for another service/procedure",
    "109": "Claim/service not covered by this payer/contractor", "151": "Payer deems the information submitted does not support this many/frequency of services",
    "167": "This (these) diagnosis(es) is (are) not covered",
    "183": "The referring provider is not eligible to refer the service billed",
    "197": "Precertification/authorization/notification/pre-treatment absent",
    "198": "Precertification/notification/authorization/pre-treatment exceeded",
    "204": "Service/equipment/drug is not covered under the patient's current benefit plan",
    "234": "This procedure is not paid separately", "236": "Procedure or modifier not compatible with another procedure on the same day (NCCI)",
    "253": "Sequestration - reduction in federal payment", "B1": "Non-covered visits",
    "B7": "Provider was not certified/eligible to be paid for this procedure/service on this date of service",
    "B15": "Service not paid separately: qualifying service/procedure not received or adjudicated",
    "119": "Benefit maximum for this time period or occurrence has been reached",
    "242": "Services not provided by network/primary care providers",
    "252": "An attachment/other documentation is required to adjudicate this claim/service",
    "288": "Referral absent",
}
RARC_DESCRIPTIONS = {
    "M15": "Separately billed services/tests have been bundled", "N19": "Procedure code incidental to primary procedure",
    "M80": "Not covered when performed during the same session as another approved service",
    "N20": "Service not payable with other service rendered on the same date", "N111": "No appeal right except duplicate claim/service issue",
    "N522": "Duplicate of a claim processed, or to be processed, as a crossover claim", "N130": "Consult plan benefit documents",
    "N425": "Statutorily excluded service(s)", "M76": "Missing/incomplete/invalid diagnosis or condition",
    "N54": "Claim information is inconsistent with pre-certified/authorized services", "M62": "Missing/incomplete/invalid treatment authorization code",
    "N519": "Invalid combination of HCPCS modifiers", "M20": "Missing/incomplete/invalid HCPCS",
    "N657": "This should be billed with the appropriate code for these services", "MA04": "Secondary payment cannot be considered without the identity of or payment information from the primary payer",
    "N479": "Missing Explanation of Benefits (Coordination of Benefits or Medicare Secondary Payer)",
    "N386": "This decision was based on a National/Local Coverage Determination", "N115": "This decision was based on a Local Coverage Determination (LCD)",
    "N418": "Misrouted claim", "M127": "Missing patient medical record for this service", "N264": "Missing/incomplete/invalid ordering provider name",
    "N265": "Missing/incomplete/invalid ordering provider primary identifier", "N290": "Missing/incomplete/invalid rendering provider primary identifier",
    "MA130": "Your claim contains incomplete and/or invalid information, and no appeal rights are afforded",
    "N30": "Patient ineligible for this service", "N574": "Our records indicate the ordering/referring provider is not eligible",
    "N575": "Mismatch between the submitted ordering/referring provider name and the name in our records",
    "MA13": "Alert: you may be subject to penalties if you bill the patient for amounts not reported with PR",
    "N95": "This provider type/specialty may not bill this service", "MA120": "Missing/incomplete/invalid CLIA certification number",
    "N362": "The number of days or units of service exceeds our acceptable maximum", "N211": "Alert: you may not appeal this decision",
    "MA01": "Alert: if you do not agree with what we approved for these services, you may appeal",
    "MA18": "Alert: the claim information is also being forwarded to the patient's supplemental insurer",
    "N89": "Alert: payment information for this claim has been forwarded to more than one other payer",
    "M38": "The patient is liable for the charges for this service as you informed the patient in writing before the "
           "service was furnished that we would not pay for it, and the patient agreed to pay",
    "MA39": "Missing/incomplete/invalid gender", "N706": "Missing documentation",
    "N640": "Exceeds number/frequency approved/allowed within time period",
}
PLB_REASON_DESCRIPTIONS = {
    "WO": "Overpayment recovery", "72": "Authorized return (refund)", "FB": "Forward balance", "L6": "Interest owed",
    "J1": "Nonreimbursable", "CS": "Adjustment", "IR": "Internal Revenue Service withholding",
}


# --- 2026 CLFS ------------------------------------------------------------------------------------
def _load_clfs() -> tuple[dict, frozenset]:
    rates, waived = {}, set()
    with open(Path(__file__).with_name("data") / "clfs_2026q4.csv") as fh:
        for line in fh:
            if line.startswith(("#", "hcpcs")):
                continue
            code, rate, qw = line.strip().split(",")
            rates[code] = float(rate)
            if qw == "1":
                waived.add(code)
    return rates, frozenset(waived)


CLFS_2026, CLIA_WAIVED = _load_clfs()
NOT_ON_CLFS = {"81479": "unlisted molecular pathology: MAC-priced (no national CLFS rate)",
               "88305": "surgical pathology: Physician Fee Schedule, not the CLFS"}


@dataclass(frozen=True)
class CPT:
    code: str
    description: str
    category: str                   # collection chemistry hematology urinalysis microbiology sti respiratory pcr drug molecular pathology
    charge: float                   # chargemaster price per unit: Medicare CY2024 national average submitted charge
    rate: float                     # 2026 CLFS national limit (MAC price for 81479, PFS average allowed for 88305)
    medical_necessity: bool = False # LCD/NCD-limited: screening diagnoses draw CO-50/11/167
    units: tuple | None = None      # (min, typical, max) units per line; None = always 1
    mue: int | None = None          # medically unlikely edit: more units than this on one line
    preventive: bool = False        # ACA preventive when ordered with a screening diagnosis: no patient share


def _cpt(code, description, category, charge, rate=None, **kw) -> CPT:
    if rate is None:
        if code not in CLFS_2026:
            raise ValueError(f"{code} is not on the CY2026 CLFS; give its rate and a NOT_ON_CLFS reason")
        rate = CLFS_2026[code]
    return CPT(code, description, category, charge, rate, **kw)


# Codes the order profiles use: charges from the spec (Medicare 2024 average submitted charge); rates from the CLFS.
_SPEC_CODES = [
    _cpt("36415", "Collection of venous blood by venipuncture", "collection", 21.81),
    _cpt("80053", "Comprehensive metabolic panel", "chemistry", 61.44),
    _cpt("85025", "Complete blood count with automated differential", "hematology", 37.12),
    _cpt("80061", "Lipid panel", "chemistry", 96.05, medical_necessity=True, preventive=True),
    _cpt("83036", "Hemoglobin A1c", "chemistry", 58.04, medical_necessity=True, preventive=True),
    _cpt("84443", "Thyroid stimulating hormone", "chemistry", 95.89, medical_necessity=True),
    _cpt("82306", "Vitamin D, 25 hydroxy", "chemistry", 184.26, medical_necessity=True),
    _cpt("81001", "Urinalysis, automated, with microscopy", "urinalysis", 31.94),
    _cpt("81003", "Urinalysis, automated, without microscopy", "urinalysis", 15.18),
    _cpt("82570", "Creatinine, urine", "urinalysis", 45.67),
    _cpt("84439", "Free thyroxine", "chemistry", 87.19, medical_necessity=True),
    _cpt("80048", "Basic metabolic panel", "chemistry", 47.96),
    _cpt("82607", "Vitamin B12", "chemistry", 88.01, medical_necessity=True),
    _cpt("82043", "Urine microalbumin, quantitative", "urinalysis", 57.23),
    _cpt("87086", "Urine culture, colony count", "microbiology", 51.07),
    _cpt("83540", "Iron", "chemistry", 32.90, medical_necessity=True),
    _cpt("82728", "Ferritin", "chemistry", 78.74, medical_necessity=True),
    _cpt("85027", "Complete blood count, automated", "hematology", 31.98),
    _cpt("84153", "Prostate specific antigen, total", "chemistry", 102.68, medical_necessity=True),
    _cpt("85610", "Prothrombin time", "hematology", 27.42, medical_necessity=True),
    _cpt("87088", "Urine culture, isolate identification", "microbiology", 31.20, units=(1, 1, 2)),
    _cpt("87186", "Susceptibility, microdilution (MIC)", "microbiology", 55.03, units=(1, 1, 3)),
    _cpt("87077", "Culture, aerobic isolate identification", "microbiology", 32.76, units=(1, 1, 2)),
    _cpt("80307", "Drug test, presumptive, instrument chemistry", "drug", 211.28),
    _cpt("G0480", "Drug test, definitive, 1-7 drug classes", "drug", 287.96),
    _cpt("G0481", "Drug test, definitive, 8-14 drug classes", "drug", 416.89),
    _cpt("G0482", "Drug test, definitive, 15-21 drug classes", "drug", 509.81),
    _cpt("G0483", "Drug test, definitive, 22+ drug classes", "drug", 636.79),
    _cpt("87491", "Chlamydia trachomatis, amplified probe", "sti", 87.08, preventive=True),
    _cpt("87591", "Neisseria gonorrhoeae, amplified probe", "sti", 84.82, preventive=True),
    _cpt("87661", "Trichomonas vaginalis, amplified probe", "sti", 104.15),
    _cpt("87801", "Infectious agent detection, multiple organisms, amplified probe", "sti", 155.55, units=(1, 2, 4)),
    _cpt("87624", "HPV, high-risk types, pooled result", "sti", 140.57, preventive=True),
    _cpt("86803", "Hepatitis C antibody", "sti", 115.58, preventive=True),
    _cpt("87389", "HIV-1 antigen with HIV-1/HIV-2 antibodies", "sti", 124.40, preventive=True),
    _cpt("87798", "Infectious agent detection by NAA, not otherwise specified, per target", "pcr", 63.45,
         units=(3, 10, 25), mue=20),
    _cpt("87481", "Candida species, amplified probe", "pcr", 60.27, units=(1, 3, 6), mue=5),
    _cpt("87150", "Culture typing, amplified probe, per culture or isolate", "pcr", 63.45, units=(2, 8, 16), mue=12),
    _cpt("87631", "Respiratory virus panel, 3-5 targets", "respiratory", 241.64),
    _cpt("87637", "SARS-CoV-2, influenza A/B and RSV, multiplex amplified probe", "respiratory", 291.40),
    _cpt("81479", "Unlisted molecular pathology procedure", "molecular", 5989.99, rate=2457.65),
    _cpt("81225", "CYP2C19 gene analysis, common variants", "molecular", 1.8 * CLFS_2026["81225"]),
    _cpt("81226", "CYP2D6 gene analysis, common variants", "molecular", 1.8 * CLFS_2026["81226"]),
    _cpt("81162", "BRCA1/BRCA2 full sequence and duplication/deletion analysis", "molecular", 1.8 * CLFS_2026["81162"]),
    _cpt("81220", "CFTR gene analysis, common variants", "molecular", 2.5 * CLFS_2026["81220"]),
    _cpt("81240", "F2 (prothrombin) gene analysis, 20210G>A variant", "molecular", 2.5 * CLFS_2026["81240"]),
    _cpt("81241", "F5 (factor V) gene analysis, Leiden variant", "molecular", 2.5 * CLFS_2026["81241"]),
    _cpt("81291", "MTHFR gene analysis, common variants", "molecular", 2.5 * CLFS_2026["81291"]),
    _cpt("81270", "JAK2 gene analysis, p.Val617Phe variant", "molecular", 2.5 * CLFS_2026["81270"]),
    _cpt("G0103", "Prostate cancer screening; prostate specific antigen test (PSA)", "chemistry", 6 * CLFS_2026["G0103"],
         preventive=True),
    _cpt("G0328", "Colorectal cancer screening; fecal occult blood test, immunoassay", "chemistry",
         6 * CLFS_2026["G0328"], preventive=True),
    _cpt("82274", "Blood, occult, by fecal hemoglobin determination by immunoassay", "chemistry",
         6 * CLFS_2026["82274"], preventive=True),
    _cpt("80055", "Obstetric panel", "chemistry", 6 * CLFS_2026["80055"], preventive=True),
    _cpt("80081", "Obstetric panel, including HIV testing", "chemistry", 6 * CLFS_2026["80081"], preventive=True),
    _cpt("86762", "Rubella antibody", "sti", 6 * CLFS_2026["86762"]),
    _cpt("84702", "Gonadotropin, chorionic (hCG), quantitative", "chemistry", 6 * CLFS_2026["84702"]),
    _cpt("84703", "Gonadotropin, chorionic (hCG), qualitative", "chemistry", 6 * CLFS_2026["84703"]),
    _cpt("84144", "Progesterone", "chemistry", 6 * CLFS_2026["84144"]),
    _cpt("83655", "Lead", "chemistry", 6 * CLFS_2026["83655"]),
    _cpt("87510", "Gardnerella vaginalis, direct probe", "pcr", 6 * CLFS_2026["87510"]),
    _cpt("88305", "Surgical pathology, level IV", "pathology", 186.06, rate=50.74, units=(1, 1, 4)),
]
# Long tail of routine CLFS codes (the spec's ~30% of lines outside its top list): (code, category, relative weight).
# Rates come from the CLFS file; charge = rate x the spec's routine charge/allowed ratio (6x). Weights are low
# confidence (no public line counts in the spec for these codes); order follows typical outpatient lab volume.
_TAIL_CODES = [
    ("82947", "chemistry", 60), ("84295", "chemistry", 8), ("84132", "chemistry", 20), ("82565", "chemistry", 30),
    ("84520", "chemistry", 12), ("82310", "chemistry", 14), ("80076", "chemistry", 30), ("86140", "chemistry", 32),
    ("85652", "hematology", 25), ("84460", "chemistry", 14), ("84450", "chemistry", 10), ("82977", "chemistry", 8),
    ("84075", "chemistry", 6), ("82040", "chemistry", 6), ("82247", "chemistry", 6), ("82248", "chemistry", 4),
    ("84100", "chemistry", 8), ("83735", "chemistry", 14), ("84550", "chemistry", 16), ("80069", "chemistry", 8),
    ("83721", "chemistry", 10), ("83718", "chemistry", 4), ("84478", "chemistry", 5), ("82465", "chemistry", 4),
    ("86141", "chemistry", 5), ("86038", "chemistry", 12), ("86431", "chemistry", 7), ("84436", "chemistry", 5),
    ("84480", "chemistry", 6), ("84481", "chemistry", 4), ("86376", "chemistry", 5), ("82746", "chemistry", 9),
    ("83550", "chemistry", 12), ("84466", "chemistry", 4), ("83001", "chemistry", 6), ("83002", "chemistry", 5),
    ("82670", "chemistry", 5), ("84403", "chemistry", 10), ("84402", "chemistry", 4),
    ("82627", "chemistry", 3), ("83970", "chemistry", 8),
    ("82330", "chemistry", 3), ("83880", "chemistry", 6), ("84484", "chemistry", 2), ("85379", "hematology", 3),
    ("85730", "hematology", 5), ("85384", "hematology", 2), ("85044", "hematology", 2), ("85014", "hematology", 2),
    ("85018", "hematology", 3), ("85049", "hematology", 2), ("86592", "sti", 6), ("86780", "sti", 5),
    ("87340", "sti", 5), ("86704", "sti", 3), ("86706", "sti", 4), ("86708", "sti", 2), ("87522", "sti", 2),
    ("87536", "sti", 1), ("86677", "chemistry", 2), ("86787", "sti", 1), ("86900", "hematology", 2),
    ("86901", "hematology", 2), ("86850", "hematology", 2), ("82785", "chemistry", 3), ("83690", "chemistry", 4),
    ("82150", "chemistry", 2), ("86235", "chemistry", 2), ("86200", "chemistry", 3), ("82533", "chemistry", 3),
    ("83525", "chemistry", 2), ("80074", "chemistry", 2), ("86618", "sti", 1), ("84270", "chemistry", 1),
    ("80178", "drug", 2), ("80164", "drug", 1), ("80156", "drug", 1), ("80162", "drug", 1), ("80197", "drug", 1),
    ("80202", "drug", 1), ("80051", "chemistry", 1), ("82085", "chemistry", 1), ("82088", "chemistry", 1),
]
_URINE_TAIL = [("81015", "urinalysis", 6), ("84156", "urinalysis", 3), ("82575", "urinalysis", 1),
               ("81025", "urinalysis", 3), ("82042", "urinalysis", 1), ("87070", "microbiology", 2),
               ("87081", "microbiology", 1), ("87045", "microbiology", 1), ("87177", "microbiology", 1),
               ("87493", "microbiology", 1), ("87338", "microbiology", 1)]
CHARGE_TO_CLFS_ROUTINE = 6.0            # spec: routine chemistry/hematology average charge / Medicare allowed

CPTS: dict[str, CPT] = {c.code: c for c in _SPEC_CODES}
for _code, _cat, _w in _TAIL_CODES + _URINE_TAIL:
    CPTS[_code] = _cpt(_code, f"CLFS {_code}", _cat, round(CLFS_2026[_code] * CHARGE_TO_CLFS_ROUTINE, 2))
TAIL = [c for c, _, _ in _TAIL_CODES]
_TAIL_CUM = list(accumulate(w for _, _, w in _TAIL_CODES))
URINE_TAIL = [c for c, _, _ in _URINE_TAIL]
_URINE_TAIL_CUM = list(accumulate(w for _, _, w in _URINE_TAIL))


# Never in the random long tail: molecular / MAAA codes (81200-81599, often single-lab), cytopathology and
# reproductive medicine (88xxx, 89xxx), and pregnancy- or sex-specific chemistry (ordered through the profiles).
DEEP_TAIL_EXCLUDED = {"82105", "82143", "82677", "82731", "82757", "83661", "83662", "83663", "83664", "84112",
                      "84152", "84154", "84163", "84704", "84830", "86336", "87623", "87625"}


def _deep_tail() -> tuple[list, list]:
    """Rare routine CLFS codes (rate <= $60, not PLA/ADLT/G/M codes), Zipf-weighted in a fixed shuffled order:
    the distinct-codes-per-month long tail (spec: 400-1,500 distinct codes a month)."""
    pool = sorted(c for c, rate in CLFS_2026.items() if rate <= 60 and c[:1].isdigit() and c[-1].isdigit()
                  and c not in CPTS and c[:2] in ("80", "81", "82", "83", "84", "85", "86", "87")
                  and not "81200" <= c <= "81599" and c not in DEEP_TAIL_EXCLUDED)
    random.Random(2026).shuffle(pool)
    return pool, list(accumulate(1 / (i + 1) ** 1.1 for i in range(len(pool))))


DEEP_TAIL, _DEEP_TAIL_CUM = _deep_tail()
for _code in DEEP_TAIL:
    CPTS[_code] = _cpt(_code, f"CLFS {_code}", "chemistry", round(CLFS_2026[_code] * CHARGE_TO_CLFS_ROUTINE, 2))
for _c in CPTS.values():                  # fail fast: every billed code is on the CLFS or explicitly priced
    assert _c.code in CLFS_2026 or _c.code in NOT_ON_CLFS, _c.code

# Order profiles (spec cpt_mix.order_profiles): one claim = one requisition. menu = independent order probability
# per code (menu_F / menu_M by the patient's sex); pick_one = exactly one code by weight. ages / sex = who gets the
# profile at all. Every code is then checked against the patient (edi_reference.cpt_fits: PSA only for men, HPV and
# hCG only for women, ...), so a test never contradicts the patient's sex or age.
PROFILES = {
    "routine_blood": dict(weight=46, menu={"80053": .55, "85025": .55, "80061": .35, "83036": .30, "84443": .30,
                                           "82306": .12, "80048": .10, "84439": .12, "82607": .10, "82728": .08,
                                           "83540": .07, "84153": .11, "85610": .04}, draw=True, tail=True),
    "routine_urine": dict(weight=10, menu={"81001": .5, "81003": .35, "87086": .45, "82570": .15, "82043": .15},
                          reflex={"87088": .35, "87077": .25, "87186": .25}, urine_tail=True),
    "sti_panel": dict(weight=10, ages=(13, 70), menu_F={"87491": .9, "87591": .9, "87661": .45, "87624": .2,
                                                       "87389": .2, "86803": .1, "86592": .15},
                      menu_M={"87491": .9, "87591": .9, "87389": .35, "86592": .3, "86803": .15},
                      draw_if=("87389", "86592", "86803")),
    "prenatal": dict(weight=3, sex="F", ages=(15, 45), pick_one={"80055": .6, "80081": .4},
                     menu={"87389": .3, "86803": .5, "81003": .3, "87086": .45, "87491": .5, "87591": .5, "84702": .1},
                     draw=True),
    "respiratory": dict(weight=6, pick_one={"87637": .70, "87631": .15, "87798": .15}, seasonal=True),
    "pcr_panels": dict(weight=4, ages=(13, 100), menu_F={"87798": 1.0, "87481": .5, "87510": .3, "87661": .3},
                       menu_M={"87798": 1.0, "87150": .2}),
    "drug_monitoring": dict(weight=7, ages=(14, 85), menu={"80307": .85, "82570": .3},     # 82570: specimen validity
                            pick_one_optional=({"G0480": .22, "G0481": .18, "G0482": .14, "G0483": .18})),
    "molecular": dict(weight=2, ages=(18, 90), pick_one={"81479": .22, "81225": .16, "81226": .16, "81162": .12,
                                                        "81220": .08, "81240": .06, "81241": .09, "81291": .06,
                                                        "81270": .05}),
    "colorectal_screening": dict(weight=1.5, ages=(45, 85), pick_one={"82274": 1.0}),
    "pathology": dict(weight=2, ages=(18, 100), menu={"88305": 1.0}),
    "mixed_blood_plus_urine": dict(weight=8, combine=("routine_blood", "routine_urine")),
}
# Profile mix by payer class (relative to the weights above): older Medicare patients get few STI/drug panels.
PROFILE_CLASS_MULT = {
    "medicare": {"sti_panel": .05, "drug_monitoring": .4, "routine_blood": 1.25, "mixed_blood_plus_urine": 1.3,
                 "molecular": 1.5, "pathology": 1.5, "colorectal_screening": 1.6},
    "medicare_advantage": {"sti_panel": .05, "drug_monitoring": .5, "routine_blood": 1.2, "mixed_blood_plus_urine": 1.3,
                           "molecular": 1.3, "pathology": 1.3, "colorectal_screening": 1.6},
    "medicaid": {"sti_panel": 1.8, "drug_monitoring": 1.8, "routine_blood": .85, "prenatal": 3.0},
    "medicaid_mco": {"sti_panel": 1.8, "drug_monitoring": 1.8, "routine_blood": .85, "prenatal": 3.0},
}
PROFILES_FOR_CHILDREN = {"routine_blood": .7, "routine_urine": 1.0, "respiratory": 2.5, "mixed_blood_plus_urine": .5}
# Mean long-tail add-on codes on a blood draw, by payer class (calibrated to lines/claim: Medicare 6.0, MA 5.5,
# commercial 4.3, Medicaid 4.0; spec volume.lines_per_claim.by_payer_class). Drawn before the claim scrubber strips
# NCCI panel components, so slightly above the billed means.
TAIL_LINES_BY_CLASS = {"medicare": 6.5, "medicare_advantage": 5.8, "commercial": 4.0, "blues": 4.0, "other": 4.0,
                       "medicaid": 4.3, "medicaid_mco": 4.3}
DEEP_TAIL_SHARE = .08                   # share of long-tail add-ons drawn from the rare-code tail
P_VENIPUNCTURE_BILLED = .75             # notes s.9 #4: the lab drew the specimen (patient service center)

SCREENING_DX = R.SCREENING_CODES
# Share of requisitions ordered as screening (a Z00/Z01/Z11/Z12/Z13 code first-listed), by profile; Medicare
# patients far less often (Medicare has no routine-physical benefit).
SCREENING_SHARE = {"routine_blood": .35, "sti_panel": .55, "colorectal_screening": 1.0}
MEDICARE_SCREENING_FACTOR = .25
P_LISTED_DX_REUSED = .85                # a test shares a diagnosis already on the requisition when one fits
P_PRIOR_DX_REUSED = .75                 # else it is ordered for one of the patient's known conditions when one fits
DX_EXTRA_CHRONIC = .06                  # each further chronic condition listed as an extra diagnosis
MAX_DX = 12                             # HI segment: 12 diagnoses

# Payer-policy bundling a scrubber cannot remove (not NCCI): line -> the service it is folded into when both are
# billed (None: folded into the lab work itself). Medicare pays these; commercial and Medicaid plans often do not.
PAYER_BUNDLED_INTO = {"36415": None, "87088": ("87086",), "87077": ("87086",), "87186": ("87086",), "83721": ("80061",),
                      "82570": ("82043", "80307"), "87481": ("87798",), "87150": ("87798",), "81015": ("81003",),
                      "87510": ("87798",), "80307": ("G0480", "G0481", "G0482", "G0483")}
P_NCCI_RESIDUE_GIVEN_CONFLICT = .02    # a panel component slips past the scrubber (~0.5% of claims; CO-97/236)
P_GENDER_MARKER_DIFFERS = .004          # patients whose registered sex differs from the sex of their tests (KX)


# --- payers ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Payer:
    name: str                       # 837 2010BB name (the clearinghouse payer list)
    payer_id: str                   # 837 NM109; unique per payer here (MA products get their own ids)
    payer_class: str                # medicare | medicare_advantage | medicaid | medicaid_mco | blues | commercial | other
    sbr09: str                      # 837 claim filing indicator
    clp06: str                      # 835 claim filing indicator
    ein: str                        # TRN03 = "1" + EIN
    name_835: str | None = None     # N1*PR name on the 835 when it differs from the 837 name
    sends_ref_2u: bool = True       # 835 REF*2U carries the payer id; otherwise the payer is known by TRN03
    allowed_mult: float = 1.0       # contracted allowed / CLFS
    out_of_network: float = 0.0     # > 0: non-contracted lab; allowed = this share of billed charges
    denial_mult: float = 1.0
    lag: tuple = (7, 14, 40)        # days from payer receipt to adjudication: (min, typical, max)
    remit_weekdays: tuple = (0, 1, 2, 3, 4)
    remit_every_weeks: int = 1      # 2 = biweekly, 4 = every four weeks
    skip_half_year_end_cycle: bool = False   # NC Medicaid skips the last checkwrite of June and December
    eft_lag_days: int = 1           # business days from 835 production (DTM*405) to the EFT (BPR16); CORE 370: <= 3
    payment_method: str = "ACH"     # ACH | CHK
    era: bool = True                # False: paper remittance only; no 835 ever arrives
    claim_level_denials: float = .15  # share of full denials sent as claim-level CAS with no SVC detail
    ref6r_rate: float = .92         # REF*6R echoed when the 837 sent it
    svc06_rate: float = .01
    timely_filing_days: int = 180
    multi_st_rate: float = 0
    interest_rate: float = 0        # annual prompt-pay interest (AMT*I and PLB L6) after interest_after_days
    interest_after_days: int = 30
    delimiters: str = "A"
    member_id_style: str = "digits9"
    icn_style: str = "digits12"
    weight: float = 1.0             # share within its market segment
    remit_address: tuple = ()       # 835 N3/N4: (street or PO box, city, state, ZIP+4)
    website: str = ""               # 835 PER*IC UR (payer web site), when the payer sends one
    sequestration: bool = False     # MA plan paying at Medicare rates passes the 2% sequestration cut on (CO-253)
    plb_reference_style: str = "icn"       # how PLB03-2 names the claim: icn | dos_pcn | pcn | icn_pcn
    reduction_group: str = "CO"     # group code of the fee reduction (PI on non-contracted payers that use it)

    @property
    def is_medicare(self) -> bool:
        return self.payer_class == "medicare"

    @property
    def alias_id_835(self) -> str:
        """The payer id the semantic layer resolves an 835 by: REF*2U when sent, else TRN03."""
        return self.payer_id if self.sends_ref_2u else "1" + self.ein

    @property
    def bank(self) -> tuple[str, str]:
        """The payer's originating bank (BPR07 ABA routing number, BPR09 account): fixed per payer."""
        return payer_bank(self)


FED_DISTRICT = {"CT": 1, "MA": 1, "NY": 2, "NJ": 2, "PA": 3, "OH": 4, "KY": 4, "NC": 5, "SC": 5, "VA": 5, "MD": 5,
                "GA": 6, "FL": 6, "TN": 6, "AL": 6, "IL": 7, "WI": 7, "MI": 7, "IN": 7, "MO": 8, "AR": 8, "MN": 9,
                "KS": 10, "CO": 10, "NE": 10, "OK": 10, "TX": 11, "CA": 12, "UT": 12, "WA": 12, "AZ": 12, "OR": 12}


def aba_check_digit(first8: str) -> str:
    """ABA routing number check digit: 3-7-1 weights over the nine digits sum to a multiple of 10."""
    total = sum(int(d) * w for d, w in zip(first8, (3, 7, 1, 3, 7, 1, 3, 7)))
    return str((10 - total % 10) % 10)


def aba_is_valid(n: str) -> bool:
    return len(n) == 9 and n.isdigit() and n[:2] in {f"{i:02d}" for i in list(range(1, 13)) + list(range(21, 33))} \
        and aba_check_digit(n[:8]) == n[8]


def _aba(state: str, *label) -> str:
    h = _stable_int("aba", *label)
    district = FED_DISTRICT.get(state, 5) + (20 if h % 7 == 0 else 0)      # some thrifts (21-32)
    first8 = f"{district:02d}{h // 7 % 4 + 1}{h // 28 % 10}{h // 280 % 10000:04d}"
    return first8 + aba_check_digit(first8)


TOLL_FREE = ("800", "866", "877", "888", "855", "844")


def payer_contacts(payer: "Payer") -> dict:
    """Fixed per payer: provider-services and EDI support lines (toll-free), and an EDI mailbox for some."""
    r = random.Random(_stable_int("contacts", payer.payer_id))
    domain = payer.website.split("/")[0] if payer.website else ""
    return dict(business=R.phone(r, r.choice(TOLL_FREE)) if r.random() < .85 or payer.is_medicare else "",
                technical=R.phone(r, r.choice(TOLL_FREE)),
                technical_name=r.choice(["EDI SUPPORT", "EDI HELP DESK", "ELECTRONIC DATA INTERCHANGE",
                                         "TECHNICAL SUPPORT", "EDI CUSTOMER SERVICE"]),
                email=f"EDI@{domain}" if domain and r.random() < .5 else "")


def payer_bank(payer: "Payer") -> tuple[str, str]:
    """Fixed per paying company (the 835 N1*PR name): one insurer's products pay from the same account."""
    company = payer.name_835 or payer.name
    state = payer.remit_address[2] if payer.remit_address else "NC"
    h = _stable_int("account", company)
    return _aba(state, company), f"{h % 10 ** (9 + h % 4):0{9 + h % 4}d}"


def _ein(name: str) -> str:
    """Invented 9-digit employer id (TRN03 / BPR10 carry '1' + EIN)."""
    return f"{10**8 + _stable_int('ein', name) % (9 * 10**8)}"


def _stable_int(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:15], 16)


PROMPT_PAY_RATE = .08              # state prompt-pay interest on clean claims paid after 45 days (spec: low confidence)
P_PENDED_CLEAN = .02               # share of pended claims that were clean (interest still accrues)


def _payer(name, payer_id, payer_class, **kw) -> Payer:
    defaults = {
        "medicare": dict(sbr09="MB", clp06="MB", lag=(14, 14, 30), eft_lag_days=0, claim_level_denials=0,
                         timely_filing_days=365, denial_mult=.6, member_id_style="mbi", icn_style="medicare",
                         ref6r_rate=.97),
        "medicare_advantage": dict(sbr09="16", clp06="16", lag=(7, 16, 45), timely_filing_days=365, denial_mult=1.3,
                                   remit_weekdays=(1, 3), interest_rate=PROMPT_PAY_RATE, interest_after_days=45),
        "medicaid": dict(sbr09="MC", clp06="MC", lag=(7, 14, 45), timely_filing_days=180, denial_mult=1.3,
                         allowed_mult=.8, remit_weekdays=(2,), member_id_style="digits10", icn_style="digits17"),
        "medicaid_mco": dict(sbr09="HM", clp06="HM", lag=(10, 22, 60), timely_filing_days=180, denial_mult=1.5,
                             allowed_mult=.85, remit_weekdays=(1,), member_id_style="digits11", interest_rate=PROMPT_PAY_RATE,
                             interest_after_days=45),
        "blues": dict(sbr09="BL", clp06="BL", lag=(7, 14, 40), timely_filing_days=180, allowed_mult=1.1,
                      remit_weekdays=(0, 3), member_id_style="prefix3", icn_style="digits15", interest_rate=PROMPT_PAY_RATE,
                      interest_after_days=45),
        "commercial": dict(sbr09="CI", clp06="12", lag=(5, 12, 35), timely_filing_days=180, allowed_mult=1.15,
                           remit_weekdays=(1, 4), interest_rate=PROMPT_PAY_RATE, interest_after_days=45),
        "other": dict(sbr09="CH", clp06="CH", lag=(10, 20, 60), timely_filing_days=365, denial_mult=1.2,
                      remit_weekdays=(2,)),
    }[payer_class]
    return Payer(name, payer_id, payer_class, ein=_ein(name), **{**defaults, **kw})


# Market segments: share of insurance-billed claims (spec payer_mix.share_of_insurance_billed_claims, typical).
SEGMENT_SHARE = {"medicare_ffs": .20, "medicare_advantage": .16, "medicaid_mco": .11, "medicaid_ffs": .03,
                 "blues": .20, "national_commercial": .21, "regional_tpa": .07, "tricare_other": .02}
SEGMENT_VISITS_PER_YEAR = {"medicare_ffs": 2.7, "medicare_advantage": 2.6, "medicaid_mco": 1.6, "medicaid_ffs": 1.6,
                           "blues": 1.8, "national_commercial": 1.8, "regional_tpa": 1.6, "tricare_other": 1.6}

_NATIONAL = [
    _payer("UNITEDHEALTHCARE", "87726", "commercial", name_835="UNITEDHEALTHCARE INSURANCE COMPANY", weight=.38,
           remit_weekdays=(0, 1, 2, 3, 4), sends_ref_2u=False, multi_st_rate=.15, allowed_mult=1.08,
           plb_reference_style="dos_pcn", remit_address=("PO BOX 30555", "SALT LAKE CITY", "UT", "841300555"), website="UHCPROVIDER.COM"),
    _payer("AETNA", "60054", "commercial", name_835="AETNA LIFE INSURANCE COMPANY", weight=.24,
           remit_weekdays=(0, 2, 4), eft_lag_days=2, delimiters="C", allowed_mult=1.12,
           remit_address=("PO BOX 981106", "EL PASO", "TX", "799981106"), website="AETNA.COM"),
    _payer("CIGNA", "62308", "commercial", name_835="CIGNA HEALTH AND LIFE INSURANCE CO", weight=.18,
           remit_weekdays=(1, 4), claim_level_denials=.5, ref6r_rate=.7, delimiters="B", allowed_mult=1.2,
           plb_reference_style="icn_pcn", remit_address=("PO BOX 188061", "CHATTANOOGA", "TN", "374228061"),
           website="CIGNAFORHCP.COM"),
    _payer("HUMANA", "61101", "commercial", weight=.05, remit_weekdays=(2,), eft_lag_days=2, plb_reference_style="icn",
           remit_address=("PO BOX 14601", "LEXINGTON", "KY", "405124601"), website="HUMANA.COM"),
    _payer("UMR", "39026", "commercial", name_835="UMR (UNITEDHEALTHCARE)", weight=.08, remit_weekdays=(3,),
           sends_ref_2u=False, remit_address=("PO BOX 30541", "SALT LAKE CITY", "UT", "841300541"), website="UMR.COM"),
    _payer("MERITAIN HEALTH", "41124", "commercial", weight=.04, remit_weekdays=(3,), payment_method="CHK",
           plb_reference_style="pcn", remit_address=("PO BOX 853921", "RICHARDSON", "TX", "750853921")),
    _payer("OSCAR HEALTH", "OSCAR", "commercial", weight=.03, remit_weekdays=(0, 3), plb_reference_style="icn",
           remit_address=("PO BOX 52146", "PHOENIX", "AZ", "850722146"), website="HIOSCAR.COM"),
]
_MEDICARE_ADVANTAGE = [
    _payer("UNITEDHEALTHCARE MEDICARE ADVANTAGE", "87727", "medicare_advantage",
           name_835="UNITEDHEALTHCARE INSURANCE COMPANY", weight=.29, remit_weekdays=(0, 1, 2, 3, 4),
           sends_ref_2u=False, svc06_rate=.03, delimiters="C", sequestration=True,
           remit_address=("PO BOX 31362", "SALT LAKE CITY", "UT", "841311362"), website="UHCPROVIDER.COM"),
    _payer("HUMANA MEDICARE ADVANTAGE", "61102", "medicare_advantage", name_835="HUMANA INSURANCE COMPANY", weight=.17,
           remit_weekdays=(2,), eft_lag_days=2, sequestration=True, plb_reference_style="icn",
           remit_address=("PO BOX 14601", "LEXINGTON", "KY", "405124601"), website="HUMANA.COM"),
    _payer("AETNA MEDICARE", "60055", "medicare_advantage", weight=.10, remit_weekdays=(0, 2, 4), sequestration=True,
           plb_reference_style="icn", remit_address=("PO BOX 981106", "EL PASO", "TX", "799981106"), website="AETNA.COM"),
    _payer("WELLCARE MEDICARE", "14163", "medicare_advantage", name_835="WELLCARE HEALTH PLANS INC", weight=.08,
           sequestration=True, remit_address=("PO BOX 31372", "TAMPA", "FL", "336311372")),
    _payer("CIGNA MEDICARE ADVANTAGE", "62309", "medicare_advantage", weight=.05, plb_reference_style="icn_pcn",
           remit_address=("PO BOX 20002", "NASHVILLE", "TN", "372020002")),
    _payer("DEVOTED HEALTH", "DEV01", "medicare_advantage", weight=.04, remit_weekdays=(3,),
           remit_address=("PO BOX 211299", "EAGAN", "MN", "551211299")),
    _payer("ALIGNMENT HEALTH PLAN", "CCHPC", "medicare_advantage", weight=.03, remit_weekdays=(3,),
           remit_address=("PO BOX 14010", "ORANGE", "CA", "928631410")),
    _payer("CLOVER HEALTH", "CLOVR", "medicare_advantage", weight=.02, remit_weekdays=(4,),
           remit_address=("PO BOX 3236", "FRANKLIN", "TN", "370653236")),
]
_TRICARE_OTHER = [
    _payer("TRICARE EAST REGION", "TREST", "other", name_835="HUMANA MILITARY", weight=.55, lag=(10, 18, 45),
           remit_address=("PO BOX 7981", "MADISON", "WI", "537077981"), website="TRICARE-EAST.COM"),
    _payer("VA COMMUNITY CARE NETWORK", "VACCN", "other", name_835="OPTUM VA CCN", weight=.25, lag=(14, 30, 75),
           remit_address=("PO BOX 202117", "FLORENCE", "SC", "295022117")),
    _payer("WORKERS COMP STATE FUND", "WCSF1", "other", weight=.20, lag=(20, 35, 90), era=False, payment_method="CHK"),
]


def _regional(state: str, mac: tuple, blues: tuple, medicaid: tuple, mcos: list, nc_skip: bool) -> dict:
    mac_name, mac_id, mac_835, mac_addr, mac_web = mac
    return {
        "medicare_ffs": [_payer(mac_name, mac_id, "medicare", name_835=mac_835, remit_address=mac_addr,
                                website=mac_web, plb_reference_style="icn")],
        "blues": [_payer(blues[0], blues[1], "blues", name_835=blues[2], weight=.9, multi_st_rate=.2,
                         remit_address=blues[3], website=blues[4]),
                  _payer(f"BCBS FEDERAL EMPLOYEE PROGRAM {state}", f"FEP{state}", "blues",
                         name_835=f"FEP {blues[2]}", weight=.1, sends_ref_2u=False, remit_address=blues[3],
                         website="FEPBLUE.ORG")],
        "medicaid_ffs": [_payer(medicaid[0], medicaid[1], "medicaid", name_835=medicaid[2],
                                skip_half_year_end_cycle=nc_skip, interest_rate=.06, sends_ref_2u=False,
                                remit_address=medicaid[3], website=medicaid[4], plb_reference_style="icn")],
        "medicaid_mco": [_payer(n, i, "medicaid_mco", name_835=n835, weight=w, remit_weekdays=wd, remit_address=addr)
                         for n, i, n835, w, wd, addr in mcos],
    }


CENTENE_CLAIMS = ("PO BOX 8040", "FARMINGTON", "MO", "636408040")
ELEVANCE_CLAIMS = ("PO BOX 61010", "VIRGINIA BEACH", "VA", "234661010")
REGIONS = {
    "NC": _regional("NC", ("MEDICARE PART B NORTH CAROLINA", "11502", "PALMETTO GBA",
                           ("PO BOX 100190", "COLUMBIA", "SC", "292023190"), "PALMETTOGBA.COM/JM"),
                    ("BLUE CROSS BLUE SHIELD OF NORTH CAROLINA", "61473", "BCBSNC",
                     ("PO BOX 35", "DURHAM", "NC", "277020035"), "BLUECROSSNC.COM"),
                    ("NC MEDICAID", "NCMCD", "NC DEPARTMENT OF HEALTH AND HUMAN SERVICES",
                     ("PO BOX 300001", "RALEIGH", "NC", "276220001"), "NCTRACKS.NCDHHS.GOV"),
                    [("WELLCARE OF NORTH CAROLINA", "68069", "WELLCARE OF NC INC", .30, (1,), CENTENE_CLAIMS),
                     ("HEALTHY BLUE NORTH CAROLINA", "HBNC1", "BLUE CROSS NC HEALTHY BLUE", .25, (0, 3),
                      ("PO BOX 61010", "VIRGINIA BEACH", "VA", "234661010")),
                     ("UNITEDHEALTHCARE COMMUNITY PLAN NC", "UHCNC", "UNITEDHEALTHCARE COMMUNITY PLAN", .20, (2,),
                      ("PO BOX 31341", "SALT LAKE CITY", "UT", "841311341")),
                     ("AMERIHEALTH CARITAS NORTH CAROLINA", "81671", "AMERIHEALTH CARITAS NC", .15, (3,),
                      ("PO BOX 7110", "LONDON", "KY", "407427110")),
                     ("CAROLINA COMPLETE HEALTH", "68068", "CAROLINA COMPLETE HEALTH INC", .10, (4,), CENTENE_CLAIMS)],
                    nc_skip=True),
    "TX": _regional("TX", ("MEDICARE PART B TEXAS", "04412", "NOVITAS SOLUTIONS",
                           ("PO BOX 3095", "MECHANICSBURG", "PA", "170551849"), "NOVITAS-SOLUTIONS.COM"),
                    ("BLUE CROSS BLUE SHIELD OF TEXAS", "84980", "BCBSTX",
                     ("PO BOX 660044", "DALLAS", "TX", "752660044"), "BCBSTX.COM"),
                    ("TEXAS MEDICAID", "TXMCD", "TEXAS MEDICAID AND HEALTHCARE PARTNERSHIP",
                     ("PO BOX 200555", "AUSTIN", "TX", "787200555"), "TMHP.COM"),
                    [("SUPERIOR HEALTHPLAN", "SHPTX", "SUPERIOR HEALTHPLAN INC", .30, (1,), CENTENE_CLAIMS),
                     ("AMERIGROUP TEXAS", "26375", "AMERIGROUP TEXAS INC", .25, (0, 3), ELEVANCE_CLAIMS),
                     ("MOLINA HEALTHCARE OF TEXAS", "20554", "MOLINA HEALTHCARE OF TEXAS INC", .20, (2,),
                      ("PO BOX 22719", "LONG BEACH", "CA", "908012719")),
                     ("TEXAS CHILDRENS HEALTH PLAN", "76048", "TEXAS CHILDRENS HEALTH PLAN", .15, (3,),
                      ("PO BOX 300286", "HOUSTON", "TX", "772300286")),
                     ("COMMUNITY FIRST HEALTH PLANS", "COMM1", "COMMUNITY FIRST HEALTH PLANS INC", .10, (4,),
                      ("12238 SILICON DR STE 100", "SAN ANTONIO", "TX", "782492021"))],
                    nc_skip=False),
}

# Long tail: regional plans, TPAs, union and public-employee funds, cooperatives (name patterns from real payer lists,
# words and places invented). Each gets a claims office in a plausible city.
_TAIL_PLACES = ["PIEDMONT", "TRIAD", "CAPE FEAR", "BLUE RIDGE", "SANDHILLS", "CATAWBA VALLEY", "TAR HEEL", "YADKIN",
                "CHEROKEE", "ALAMANCE", "PAMLICO", "GULF COAST", "HILL COUNTRY", "BRAZOS VALLEY", "PERMIAN BASIN",
                "PANHANDLE", "RIO GRANDE", "PINEY WOODS", "BIG COUNTRY", "CROSS TIMBERS", "SOUTHEASTERN", "MID-SOUTH",
                "TRI-STATE", "GREAT LAKES", "HEARTLAND", "MID-ATLANTIC", "SOUTHWEST", "TIDEWATER", "CUMBERLAND",
                "SHENANDOAH", "OZARK", "BLUEGRASS", "LOWCOUNTRY", "APPALACHIAN", "CHESAPEAKE", "SUNBELT"]
_TAIL_SURNAMES = ["ALLIED", "BENEFIT", "CORESOURCE", "HARRINGTON", "WEBTPA", "LUCENT", "ASR", "HEALTHSCOPE", "BOON",
                  "AMERIBEN", "PLANSTIN", "MARPAI", "CENTIVO", "GILSBAR", "ZENITH", "LANDMARK", "PRIMUS", "SELMAN",
                  "WESTPORT", "KINGSBRIDGE", "CORNERSTONE", "BRIGHTPATH", "CRESTVIEW", "TRUEWAY", "NORTHSTAR"]
_TAIL_UNIONS = ["IBEW LOCAL {n} HEALTH AND WELFARE FUND", "UFCW LOCAL {n} HEALTH FUND", "PLUMBERS AND PIPEFITTERS LOCAL {n} WELFARE FUND",
                "TEAMSTERS LOCAL {n} HEALTH PLAN", "IRONWORKERS LOCAL {n} BENEFIT FUND", "OPERATING ENGINEERS LOCAL {n} HEALTH FUND",
                "CARPENTERS LOCAL {n} HEALTH TRUST", "SHEET METAL WORKERS LOCAL {n} BENEFIT PLAN"]
_TAIL_PATTERNS = ["{place} HEALTH PLAN", "{place} COMMUNITY HEALTH PLAN", "{place} EMPLOYEE BENEFIT TRUST",
                  "{place} HEALTH ALLIANCE", "{place} MUTUAL INSURANCE COMPANY", "{place} TEACHERS HEALTH FUND",
                  "{place} COUNTY EMPLOYEES HEALTH PLAN", "{place} SCHOOLS BENEFIT TRUST", "{place} CARE NETWORK",
                  "{place} HEALTH COOPERATIVE", "{place} PHYSICIANS HEALTH PLAN", "{place} MEDICAL BENEFITS FUND",
                  "{place} SELECT HEALTH", "{place} REGIONAL HEALTH PLAN", "{place} FARM BUREAU HEALTH PLAN",
                  "{name} BENEFIT ADMINISTRATORS", "{name} HEALTH SERVICES", "{name} PLAN ADMINISTRATORS",
                  "{name} AND ASSOCIATES BENEFITS", "{name} CLAIMS ADMINISTRATION", "{name} TPA",
                  "{name} HEALTH SOLUTIONS", "{name} INSURANCE SERVICES", "{name} RISK MANAGEMENT"]
_TAIL_OFFICES = [("RALEIGH", "NC", "27622"), ("CHARLOTTE", "NC", "28241"), ("GREENSBORO", "NC", "27420"),
                 ("WINSTON SALEM", "NC", "27113"), ("DURHAM", "NC", "27717"), ("ASHEVILLE", "NC", "28813"),
                 ("COLUMBIA", "SC", "29221"), ("GREENVILLE", "SC", "29602"), ("RICHMOND", "VA", "23285"),
                 ("ROANOKE", "VA", "24022"), ("ATLANTA", "GA", "30348"), ("NASHVILLE", "TN", "37224"),
                 ("KNOXVILLE", "TN", "37950"), ("BIRMINGHAM", "AL", "35238"), ("DALLAS", "TX", "75265"),
                 ("HOUSTON", "TX", "77210"), ("SAN ANTONIO", "TX", "78265"), ("AUSTIN", "TX", "78711"),
                 ("FORT WORTH", "TX", "76113"), ("OKLAHOMA CITY", "OK", "73124"), ("LITTLE ROCK", "AR", "72203"),
                 ("LOUISVILLE", "KY", "40232"), ("COLUMBUS", "OH", "43216"), ("INDIANAPOLIS", "IN", "46206"),
                 ("CHICAGO", "IL", "60680"), ("ST LOUIS", "MO", "63166"), ("MILWAUKEE", "WI", "53201"),
                 ("MINNEAPOLIS", "MN", "55440"), ("DENVER", "CO", "80217"), ("PHOENIX", "AZ", "85062"),
                 ("SALT LAKE CITY", "UT", "84130"), ("KANSAS CITY", "MO", "64121"), ("OMAHA", "NE", "68103")]
N_TAIL_PAYERS = 320
P_TAIL_PAPER, P_TAIL_OUT_OF_NETWORK = .30, .45   # small payers: no ERA (spec ~30%); non-contracted with the lab


def _tail_name(r: random.Random) -> str:
    u = r.random()
    if u < .12:
        return r.choice(_TAIL_UNIONS).format(n=r.choice([3, 11, 26, 46, 58, 98, 104, 112, 130, 152, 223, 237, 270, 324,
                                                        357, 405, 421, 440, 474, 494, 520, 617, 688, 727, 876]))
    pattern = r.choice(_TAIL_PATTERNS)
    return pattern.format(place=r.choice(_TAIL_PLACES), name=r.choice(_TAIL_SURNAMES))


def _tail_payers() -> list[Payer]:
    """Regional plans and TPAs: Zipf-weighted (s ~1.1), weekly to monthly 835s, many by paper check."""
    r = random.Random(1101)
    names, out = set(), []
    taken = {p.payer_id for p in _NATIONAL + _MEDICARE_ADVANTAGE + _TRICARE_OTHER} | {
        p.payer_id for region in REGIONS.values() for payers in region.values() for p in payers}
    while len(out) < N_TAIL_PAYERS:
        name = _tail_name(r)
        pid = f"{r.choice('ABCDEGHJKLMNPRSTW')}{r.randint(1000, 9999)}" if r.random() < .5 else f"{r.randint(10000, 99999)}"
        if name in names or pid in taken:
            continue
        names.add(name)
        taken.add(pid)
        rank = len(out)
        oon = r.random() < P_TAIL_OUT_OF_NETWORK
        city, state, zip5 = r.choice(_TAIL_OFFICES)
        name_835 = r.choice([None, None, f"{name} CLAIMS", name.replace(" AND ", " & "), f"{name} INC"])
        out.append(_payer(
            name, pid, "commercial", weight=1 / (rank + 1) ** 1.1, name_835=name_835 if name_835 != name else None,
            lag=(10, r.choice([18, 21, 28]), 75), remit_weekdays=(r.randrange(5),), remit_every_weeks=r.choice([1, 1, 1, 2, 2, 4]),
            era=r.random() >= (P_TAIL_PAPER * (.4 if rank < 40 else 1.15)),
            payment_method="CHK" if r.random() < .4 else "ACH", sends_ref_2u=r.random() < .5,
            out_of_network=round(r.uniform(.25, .80), 2) if oon else 0.0, denial_mult=1.55 if oon else 1.0,
            allowed_mult=round(math.exp(r.gauss(math.log(1.15), .25)), 3), claim_level_denials=r.choice([.05, .15, .3, .5]),
            ref6r_rate=r.choice([.5, .9, .97]), member_id_style=r.choice(["digits9", "alnum10", "prefix3"]),
            icn_style=r.choice(["digits11", "digits12", "alpha2digits10", "digits15"]),
            delimiters=r.choice("AABC"), timely_filing_days=r.choice([90, 180, 180, 365]),
            plb_reference_style=r.choice(["icn", "icn", "icn", "dos_pcn", "pcn", "icn_pcn"]),
            reduction_group="PI" if oon and r.random() < .5 else "CO",
            remit_address=(f"PO BOX {r.randint(1000, 999999)}", city, state, f"{zip5}{r.randint(1, 9999):04d}")))
    return out


TAIL_PAYERS = _tail_payers()


def segments_for(region: str) -> dict[str, list[Payer]]:
    return {**REGIONS[region], "national_commercial": _NATIONAL, "medicare_advantage": _MEDICARE_ADVANTAGE,
            "regional_tpa": TAIL_PAYERS, "tricare_other": _TRICARE_OTHER}


def _all_payers() -> list[Payer]:
    seen: dict[str, Payer] = {}
    for region in REGIONS:
        for payers in segments_for(region).values():
            for p in payers:
                if seen.setdefault(p.payer_id, p) is not p:
                    raise ValueError(f"payer id {p.payer_id} used twice")
    return list(seen.values())


PAYERS = _all_payers()
PAYER_BY_ID = {p.payer_id: p for p in PAYERS}
# COB: who is billed as secondary (by 837) behind a commercial primary. Medicare primaries cross over (COBA).
SECONDARY_SEGMENTS = {"blues": .35, "national_commercial": .45, "medicaid_ffs": .20}
MEDIGAP_CARRIERS = ["AARP MEDICARE SUPPLEMENT", "MUTUAL OF OMAHA MEDIGAP", "STATE MEDICAID CROSSOVER"]

# Commercial plan designs (KFF EHBS 2025: 88% have a deductible, average single deductible $1,886, 34% >= $2,000).
COMMERCIAL_PLANS = {  # name: (share, deductible range, lab coinsurance, share of plans with lab coinsurance, copay,
    #                         share of plans that apply the deductible to outpatient lab work); the last three are
    #                         low confidence, tuned so ~30% of all claims carry patient responsibility (XiFin 2025)
    "ppo": (.45, (500, 2000), .2, .3, 0, .55),
    "hdhp": (.30, (2000, 5000), .2, .45, 0, 1.0),
    "hmo": (.13, (0, 1000), 0, 0, 10, .2),
    "zero_deductible": (.12, (0, 0), .1, .3, 0, 0),
}
_PLAN_NAMES = list(COMMERCIAL_PLANS)
_PLAN_CUM = list(accumulate(v[0] for v in COMMERCIAL_PLANS.values()))
MEDICARE_PART_B_DEDUCTIBLE_2026 = 283.0   # applies only to pathology (PFS) lines; CLFS tests have no cost sharing
MEDICARE_INTEREST = {1: .04125, 2: .0475}   # Prompt Payment Act rate by half of 2026 (spec payment_timing.interest)
SEQUESTRATION = .02


# --- denials --------------------------------------------------------------------------------------
# share of denials by category (XiFin 2025, spec denials.denial_category_mix_*), level, CARC weights, RARC weights.
# Duplicates (15-35% of denials) come from duplicate resends (Scenario.p_duplicate_submission), not from this draw.
DENIAL_CATEGORIES = {
    "bundling":          dict(clinical=.85, molecular=.142, pathology=.230, medicare=.42, level="line",
                              carcs={"97": .55, "234": .15, "236": .20, "B15": .10},
                              medicare_carcs={"97": .4, "236": .6}, rarcs={"M15": .4, "N19": .3, "M80": .1, "N20": .2}),
    "non_covered":       dict(clinical=.066, molecular=.046, pathology=.052, medicare=.05, level="line_or_claim",
                              carcs={"96": .45, "204": .3, "B1": .1, "119": .1, "242": .05},
                              medicare_carcs={"96": .6, "204": .4},
                              rarcs={"N130": .5, "N425": .15, "M76": .1, "N640": .05, "": .2}),
    "prior_auth":        dict(clinical=.035, molecular=.276, pathology=.049, medicare=0, level="claim",
                              carcs={"197": .62, "15": .13, "198": .13, "288": .12}, rarcs={"N54": .4, "M62": .3, "": .3}),
    "modifier":          dict(clinical=.032, molecular=.0, pathology=.029, medicare=.03, level="line",
                              carcs={"4": 1}, rarcs={"N519": .3, "M20": .3, "N657": .1, "": .3}),
    "cob":               dict(clinical=.021, molecular=.034, pathology=.150, medicare=.03, level="claim",
                              carcs={"22": .8, "24": .2}, medicare_carcs={"22": 1}, rarcs={"MA04": .3, "N479": .2, "": .5}),
    "experimental":      dict(clinical=.018, molecular=.145, pathology=.064, medicare=.01, level="line",
                              carcs={"55": 1}, rarcs={"N386": .4, "": .6}),
    "wrong_payer":       dict(clinical=.004, molecular=.009, pathology=.016, medicare=.01, level="claim",
                              carcs={"109": 1}, rarcs={"N418": .5, "": .5}),
    "information":       dict(clinical=.0256, molecular=.06, pathology=.02, medicare=.08, level="claim",
                              carcs={"16": .82, "252": .18}, medicare_carcs={"16": 1},
                              rarcs={"M127": .25, "N264": .3, "N290": .15, "MA130": .15, "N706": .15}),
    "medical_necessity": dict(clinical=.0314, molecular=.07, pathology=.012, medicare=.20, level="line",
                              carcs={"50": .58, "11": .23, "167": .19}, medicare_carcs={"50": 1},
                              rarcs={"N386": .6, "N115": .4}),
    "eligibility":       dict(clinical=.0051, molecular=.024, pathology=.009, medicare=.02, level="claim",
                              carcs={"27": 1}, rarcs={"N30": 1}),
    "pecos":             dict(clinical=.0022, molecular=.0, pathology=.0, medicare=.09, level="claim",
                              carcs={"183": 1}, rarcs={"N574": .4, "N264": .2, "N265": .2, "N575": .1, "MA13": .1}),
    "clia":              dict(clinical=.0015, molecular=.0, pathology=.0, medicare=.03, level="claim",
                              carcs={"B7": 1}, rarcs={"N95": .5, "MA120": .5}),
}
CATEGORY_OF_CARC = {k: name for name, c in DENIAL_CATEGORIES.items() for k in list(c["carcs"]) + list(c.get("medicare_carcs", {}))}
CATEGORY_OF_CARC.update({"18": "duplicate", "29": "timely_filing", "151": "units_mue"})
INITIAL_DENIAL_RATE = {"routine": .08, "drug": .18, "molecular": .32, "pathology": .12}   # spec recommended_targets
REWORK_SUCCESS = {"information": .75, "pecos": .75, "modifier": .75, "cob": .6, "eligibility": .5, "wrong_payer": .5,
                  "prior_auth": .4, "medical_necessity": .3, "non_covered": .2, "bundling": .2, "experimental": .15,
                  "clia": .7}
MOLECULAR_PROFILES = {"molecular"}
PATHOLOGY_PROFILES = {"pathology"}


# --- reference world: tenants ---------------------------------------------------------------------
NPI_ISSUED_RANGE = (100300000, 199399999)      # first nine digits of the NPIs NPPES has issued so far


def npi(seed) -> str:
    """10-digit NPI with a valid Luhn check digit (computed over the 80840 prefix, as CMS specifies). The first nine
    digits are spread over the issued range by a hash of the seed, so related providers share no prefix."""
    lo, hi = NPI_ISSUED_RANGE
    base = f"{lo + _stable_int('npi', seed) % (hi - lo + 1)}"
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
    pcn_prefix: str                 # distinct per tenant: PCNs never collide across tenants
    region: str                     # state: decides the MAC, the home Blues plan and the state Medicaid
    address: tuple                  # street, city, state, 9-digit ZIP
    clia: str                       # CLIA certificate number (REF*X4): 2-digit state code + 'D' + 7 digits; invented
    #                                 (checked absent from the CMS CLIA provider-of-services file, Q3 2026)
    billing_phone: str              # 1000A PER: the billing office, a local number
    bank: tuple                     # the lab's receiving account (BPR13 ABA, BPR15 account)


TENANTS = {
    "acme_labs": Tenant("acme_labs", "ACME LABS INC", "561234567", 4242, 120, 1.0, "ACMELABS", "A", "LB", "NC",
                        ("2617 ANTHONY RD", "BURLINGTON", "NC", "272155230"), "34D2187436", "3365702140",
                        ("053112929", "4410273395")),
    "acme_diagnostics": Tenant("acme_diagnostics", "ACME DIAGNOSTICS LLC", "561234999", 7777, 40, 1.15, "ACMEDX", "B",
                               "DX", "TX", ("8701 N MOPAC EXPY STE 300", "AUSTIN", "TX", "787594114"), "45D2034401",
                               "5124673300", ("114924742", "3801159926")),
}
assert len({t.pcn_prefix for t in TENANTS.values()}) == len(TENANTS)


@dataclass
class Scenario:
    start: date = date(2026, 7, 1)
    days: int = 30
    seed: int = 7
    volume_scale: float = 1.0
    target_mb_per_day: float | None = None  # raw EDI MB per file day per tenant; overrides claims_per_day * volume_scale
    # rates below are the spec's typical values (research/edi_realism_calibration.yaml); low-confidence ones are tunable
    denial_rate_scale: float = 1.0        # multiplies every initial denial probability (spec blended ~11% of claims)
    p_never_remitted: float = .01         # pended or lost beyond the window (paper-only payers add ~2% more)
    p_pended_tail: float = .05            # adjudication slips past 60 days (spec pended_tail; 2% past 90)
    p_zero_pay_fee_schedule: float = .01  # allowed $0 under the fee schedule: CO-45, NOT a denial
    p_reversal_no_correction: float = .004
    p_reversal_correction: float = .010
    p_split_reversal_correction: float = .3   # correction in a later 835 than its reversal, with offsetting PLB WO
    p_void: float = .002                  # lab voids a paid claim (freq 8) -> payer reverses it
    p_rework_routine: float = .35         # share of denials the lab works (corrected claim) on routine tests
    p_rework_molecular: float = .85
    p_signal_v3_vs_recorded: float = .45  # share of full denials sent as status 1 / $0 / CO + CARC
    p_late_submission: float = .005       # submitted 60-400 days after service -> CARC 29 if past the payer's limit
    p_dependent: float = .22              # commercial / Medicaid patients covered as a dependent
    p_secondary_coverage: float = .14     # commercial-primary patients with a second plan billed by 837
    p_medicare_crossover: float = .20     # Medicare patients with a Medigap/Medicaid supplement (COBA, CLP02=19)
    p_risky_provider: float = .08         # share of ordering providers with poor documentation
    p_duplicate_835_file: float = .02
    p_corrupt_file: float = .01           # per delivery day: an extra damaged file that must be quarantined
    p_plb_recoupment: float = .06         # PLB WO per 835 transaction
    p_duplicate_submission: float = .015  # accidental resend -> CO/OA-18 (~15% of denials, XiFin 2025)
    p_repeat_patient_claims: float = .10  # claims from drug-monitoring / anticoagulation repeaters (weekly-monthly)
    mac_mass_adjustment_waves_per_year: int = 2   # erroneous MAC edit (CO-B7) later reversed and paid in a wave
    skip_weekends: bool = True
    skip_federal_holidays: bool = True
    burn_in_days: int = 90                # history simulated before `start` and not emitted: day 1 is steady state

    @staticmethod
    def stress(**kw) -> "Scenario":
        base = dict(p_never_remitted=.04, p_zero_pay_fee_schedule=.05, p_reversal_no_correction=.04,
                    p_reversal_correction=.06, p_void=.02, p_late_submission=.05, p_duplicate_835_file=.15,
                    p_corrupt_file=.25, p_plb_recoupment=.2, p_duplicate_submission=.04, denial_rate_scale=1.5,
                    p_secondary_coverage=.3)
        base.update(kw)
        return Scenario(**base)


# --- calendar -------------------------------------------------------------------------------------
@lru_cache(maxsize=None)
def federal_holidays(year: int) -> frozenset:
    """US federal holidays as observed (Saturday -> Friday, Sunday -> Monday): no 835s, EFTs or 837 batches."""
    def nth(month, weekday, n):
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    def last(month, weekday):
        d = date(year, month + 1, 1) - timedelta(days=1)
        return d - timedelta(days=(d.weekday() - weekday) % 7)

    def observed(d):
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d
    days = {observed(date(year, m, d)) for m, d in ((1, 1), (6, 19), (7, 4), (11, 11), (12, 25))}
    days |= {nth(1, 0, 3), nth(2, 0, 3), last(5, 0), nth(9, 0, 1), nth(10, 0, 2), nth(11, 3, 4)}
    if date(year + 1, 1, 1).weekday() == 5:
        days.add(date(year, 12, 31))
    return frozenset(days)


ROUTINE_MONTH_INDEX = {1: 1.05, 2: 1.00, 3: 1.05, 4: 1.00, 5: 1.00, 6: .95, 7: .93, 8: 1.00, 9: 1.00, 10: 1.03,
                       11: .97, 12: .90}
RESPIRATORY_MONTH_INDEX = {1: 3.2, 2: 2.5, 3: 1.6, 4: .9, 5: .6, 6: .5, 7: .6, 8: .7, 9: .9, 10: 1.3, 11: 2.0, 12: 3.0}
WEEKDAY_INDEX_837 = {0: 1.30, 1: 1.10, 2: 1.00, 3: .95, 4: .85}
DOS_WEEKDAY_SHARE = {0: .195, 1: .19, 2: .19, 3: .185, 4: .165, 5: .055, 6: .02}   # collection dates
DOS_HOLIDAY_FACTOR = .12                # patient service centers close on federal holidays (hospitals still draw)
DAY_AFTER_HOLIDAY_INDEX, HOLIDAY_WEEK_INDEX = 1.5, .75
PCN_DIGITS = 6


# --- people ----------------------------------------------------------------------------------------
@dataclass
class Person:
    first: str
    last: str
    dob: date
    gender: str                     # DMG03 as registered (the gender marker on the patient's insurance)
    addr: str
    city: tuple                     # (city, state, ZIP5)
    middle: str = ""                # NM105 middle initial
    addr2: str | None = None        # N302 apartment / unit


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
    segment: str
    repeater: bool
    sex: str = "F"                  # clinical sex: decides sex-specific tests and diagnoses
    conditions: tuple = ()          # known chronic conditions: ICD-10-CM codes carried from visit to visit
    deductible: float = 0.0         # annual (commercial); Medicare Part B for pathology lines
    lab_deductible: bool = True     # the plan applies the deductible to outpatient lab work
    coinsurance: float = 0.0
    copay: float = 0.0
    deductible_pace: float = 1.0    # years of other care it takes this patient to meet the deductible
    crossover: str | None = None    # Medicare supplemental carrier (COBA crossover)
    repeater_kind: str = ""         # anticoag | drug: what a repeat patient comes back for

    def age(self, on: date) -> float:
        return (on - self.person.dob).days / 365.25


@dataclass
class Provider:
    last: str
    first: str
    npi: str
    risky: bool
    middle: str = ""


@dataclass
class Line:
    cpt: str
    units: int
    charge: float
    modifiers: tuple = ()
    rate_factor: float = 1.0        # professional/technical component share (pathology 26/TC)
    pointers: tuple = ("1",)      # SV107 diagnosis pointers


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
    pos: str = "81"
    prior_auth: str | None = None   # REF*G1
    trace: str | None = None        # REF*D9 clearinghouse trace
    line_level_ordering: bool = False   # 2420E NM1*DK on each line
    service_facility: bool = False  # 2310C NM1*77
    sends_line_ids: bool = True     # REF*6R on each line (the payer can only echo what was sent)
    interest_exempt: bool = False   # pended for information: not a clean claim, so no prompt-pay interest
    line_edits: dict = field(default_factory=dict)   # line -> (group, CARC, RARC, category): the primary payer's
    #                                 deterministic edits (NCCI residue, NCD coverage, ABN liability, sex edits)


DELIMS = {  # element, component, repetition, segment terminator, line ending after terminator
    "A": ("*", ":", "^", "~", "\n"), "B": ("|", ">", "^", "~", ""), "C": ("*", ":", "^", "~", "\r\n")}
_MBI_ALPHA = "ACDEFGHJKMNPQRTUVWXY"
_MBI_ALNUM = _MBI_ALPHA + "0123456789"


def member_id(style: str, payer: Payer, r: random.Random) -> str:
    """Member id in the payer's format: Medicare MBI (11 chars, CMS pattern), numeric Medicaid ids, Blues 3-letter
    prefixes, alphanumeric commercial ids."""
    if style == "mbi":
        pick = lambda s: s[r.randrange(len(s))]
        return (str(r.randint(1, 9)) + pick(_MBI_ALPHA) + pick(_MBI_ALNUM) + str(r.randint(0, 9)) + pick(_MBI_ALPHA)
                + pick(_MBI_ALNUM) + str(r.randint(0, 9)) + pick(_MBI_ALPHA) + pick(_MBI_ALPHA)
                + f"{r.randint(0, 99):02d}")
    if style == "prefix3":
        pre = "".join(chr(65 + _stable_int(payer.payer_id, i) % 26) for i in range(3))
        return f"{pre}{r.randint(10**8, 10**9 - 1)}"
    if style == "alnum10":
        return f"{chr(65 + r.randrange(26))}{r.randint(10**8, 10**9 - 1)}"
    n = {"digits9": 9, "digits10": 10, "digits11": 11}[style]
    return str(r.randint(10 ** (n - 1), 10**n - 1))


# --- X12 writer ------------------------------------------------------------------------------------
def money(x: float) -> str:
    """X12 R type: no trailing zeros, no leading '+', '-' for negatives."""
    s = f"{round(x, 2):.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class X12:
    """Segments are joined as they are added (one string per segment, without terminator)."""
    __slots__ = ("el", "comp", "rep", "term", "nl", "segs")

    def __init__(self, style: str):
        self.el, self.comp, self.rep, self.term, self.nl = DELIMS[style]
        self.segs: list[str] = []

    def s(self, *elements):
        """Trailing empty elements are dropped (no element ever ends with a delimiter)."""
        el = self.el
        try:
            self.segs.append(el.join(elements).rstrip(el))
        except TypeError:                                     # an int or None: convert (hot paths pass str)
            self.segs.append(el.join([_x12str(e) for e in elements]).rstrip(el))

    def c(self, *parts) -> str:
        comp = self.comp
        try:
            return comp.join(parts).rstrip(comp)
        except TypeError:
            return comp.join([_x12str(p) for p in parts]).rstrip(comp)


def _x12str(e) -> str:
    return e if e.__class__ is str else ("" if e is None else str(e))


def interchange(style: str, sender: str, receiver: str, d: date, ctl: int, gs_code: str, impl: str,
                transactions: list[X12], st_id: str, gs_sender: str | None = None, sent_at: str = "120000") -> str:
    """sent_at = HHMMSS the sender built the interchange (ISA10 = HHMM; GS05 carries the seconds)."""
    el, comp, rep, term, nl = DELIMS[style]
    isa = el.join(["ISA", "00", " " * 10, "00", " " * 10, "ZZ", f"{sender:<15}", "ZZ", f"{receiver:<15}",
                   f"{d:%y%m%d}", sent_at[:4], rep, "00501", f"{ctl:09d}", "0", "P", comp])
    out = [isa, el.join(["GS", gs_code, gs_sender or sender, receiver, f"{d:%Y%m%d}", sent_at, str(ctl), "X", impl])]
    for i, t in enumerate(transactions, 1):
        stn = f"{i:04d}"
        out.append(el.join(["ST", st_id, stn, impl]))
        out += t.segs
        out.append(el.join(["SE", str(len(t.segs) + 2), stn]))
    out += [el.join(["GE", str(len(transactions)), str(ctl)]), el.join(["IEA", "1", f"{ctl:09d}"])]
    return (term + nl).join(out) + term + nl


def cas_segments(cas: list) -> list[list]:
    """CAS as payers send it: one segment per group code, up to six reason/amount triplets each."""
    by_group: dict[str, list] = {}
    for grp, carc, amt in cas:
        by_group.setdefault(grp, []).append((carc, amt))
    out = []
    for grp, pairs in by_group.items():
        for i in range(0, len(pairs), 6):
            seg = ["CAS", grp]
            for carc, amt in pairs[i:i + 6]:
                seg += [carc, money(amt), ""]
            out.append(seg)
    return out


# payer re-coding of billed procedures (panel unbundled / downcoded): SVC01 = paid code, SVC06 = billed code
RECODE = {"80053": "80048", "80061": "82465", "G0481": "G0480", "87631": "87798", "85025": "85027"}


# --- helpers ---------------------------------------------------------------------------------------
def _stable_unit(*parts) -> float:
    """Deterministic number in [0, 1) from labels (fee schedules must not depend on draw order)."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16) / 16**12


def _stable_lognormal(sigma: float, *parts) -> float:
    """exp(N(0, sigma)) from labels, via the inverse normal CDF approximation of the stable unit."""
    u = min(max(_stable_unit(*parts), 1e-9), 1 - 1e-9)
    return math.exp(sigma * NormalDist().inv_cdf(u))


def _pick(r: random.Random, weights: dict):
    keys = list(weights)
    return r.choices(keys, weights=[weights[k] for k in keys])[0]


def _tri(r: random.Random, lo, mode, hi) -> float:
    return r.triangular(lo, hi, mode)


def _poisson(r: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, r.random()
    while p > limit:
        k += 1
        p *= r.random()
    return k


@lru_cache(maxsize=None)
def _contract_noise(payer_id: str, code: str, sigma: float) -> float:
    return _stable_lognormal(sigma, payer_id, code, "contract")


@lru_cache(maxsize=None)
def _chargemaster(tenant: str, code: str, price_mult: float) -> float:
    """Per-unit list price: fixed per tenant and code (spec per_tenant_chargemaster_variation, sigma .25)."""
    return round(CPTS[code].charge * price_mult * _stable_lognormal(.25, tenant, code, "chargemaster"), 2)


# --- generator -------------------------------------------------------------------------------------
class Generator:
    def __init__(self, sc: Scenario, tenants=None):
        self.sc = sc
        self.rng = random.Random(sc.seed)
        self._prng = random.Random()                              # reseeded per patient
        self._pbase: dict[str, int] = {}
        self.tenant_codes = list(tenants or TENANTS)
        self.isa_ctl: dict = {}                                   # (sender, receiver) -> last ISA13 sent
        self.plb_log: dict = {}
        self.paid_history: dict = {}                              # (tenant, payer) -> ICNs paid in earlier 835s
        self.trace_seq: dict = {}                                 # payer -> running EFT trace / check number

    # calendar
    def _is_business_day(self, d: date) -> bool:
        if self.sc.skip_weekends and d.weekday() >= 5:
            return False
        return not (self.sc.skip_federal_holidays and d in federal_holidays(d.year))

    def _next_business_day(self, d: date) -> date:
        while not self._is_business_day(d):
            d += timedelta(days=1)
        return d

    def _add_business_days(self, d: date, n: int) -> date:
        d = self._next_business_day(d)
        for _ in range(n):
            d = self._next_business_day(d + timedelta(days=1))
        return d

    def _remit_day(self, payer: Payer, d: date) -> date:
        """The payer's next 835 cycle on or after d (fixed weekdays, every N weeks), on a business day."""
        every = payer.remit_every_weeks
        phase = _stable_int(payer.payer_id, "cycle") % every
        for _ in range(7 * every * 3 + 14):
            if d.weekday() in payer.remit_weekdays and ((d.toordinal() - 1) // 7 + phase) % every == 0:
                skip = (payer.skip_half_year_end_cycle and d.month in (6, 12)
                        and (d + timedelta(weeks=every)).month != d.month)
                if not skip:
                    return self._next_business_day(d)
            d += timedelta(days=1)
        raise ValueError(f"no remittance cycle for {payer.payer_id}")

    def _pay_date(self, payer: Payer, remit_day: date) -> date:
        return self._add_business_days(remit_day, payer.eft_lag_days)

    # people and providers
    def _person(self, r: random.Random, region: str, age_range: tuple, ref: date, sex: str | None = None,
                surname: str | None = None, home: "Person | None" = None) -> Person:
        """A fictional person: a common surname for the state, a first name of the right sex and birth cohort, a
        street address in the lab's referral area. A household member shares the surname and address."""
        lo, hi = age_range
        age = lo + (hi - lo) * r.random() ** (1.6 if lo >= 65 else 1.0)   # Medicare: more 65-79 than 85+
        dob = ref - timedelta(days=round(age * 365.25))
        sex = sex or r.choice("FM")
        last = surname or R.SURNAMES[bisect.bisect_left(self.surname_cum, r.random() * self.surname_cum[-1])]
        first = R.first_name(r, sex, dob.year, last in R.HISPANIC_SURNAMES)
        middle = r.choice(R.MIDDLE_INITIALS[:12] if r.random() < .7 else R.MIDDLE_INITIALS) \
            if r.random() < R.P_MIDDLE_INITIAL else ""
        if home is not None:
            return Person(first, last, dob, sex, home.addr, home.city, middle, home.addr2)
        where = R.place(r, region)
        line1, line2 = R.street_address(r, region)
        return Person(first, last, dob, sex, line1, (where.city, where.state, where.zip5), middle, line2)

    def _providers(self, seed: int, n: int) -> list[Provider]:
        r = self.rng
        out = []
        for i in range(n):
            sex = "F" if r.random() < .45 else "M"
            last = R.SURNAMES[bisect.bisect_left(self.surname_cum, r.random() * self.surname_cum[-1])]
            year = r.randint(1952, 1992)
            out.append(Provider(last, R.first_name(r, sex, year, last in R.HISPANIC_SURNAMES), npi((seed, "ordering", i)),
                                r.random() < self.sc.p_risky_provider, r.choice(R.MIDDLE_INITIALS)))
        return out

    def _next_ctl(self, sender: str, receiver: str) -> int:
        """ISA13: each sender numbers its interchanges to a receiver on its own running counter."""
        key = (sender, receiver)
        n = self.isa_ctl.get(key) or 1000 + _stable_int("isa13", sender, receiver) % 400000
        self.isa_ctl[key] = n + 1
        return n + 1

    def _reused_ctl(self, sender: str, receiver: str) -> int:
        """A control number this sender already used (a resend from a restored batch, spec quirks.envelope)."""
        last = self._next_ctl(sender, receiver)
        return max(1001, last - self.rng.randint(50, 500))

    def _trace(self, payer: Payer) -> str:
        n = self.trace_seq[payer.payer_id] = self.trace_seq.get(payer.payer_id, 0) + 1
        if payer.payment_method == "CHK":
            return f"{_stable_int('chk', payer.payer_id) % 9000 + 1000}{n:06d}"
        return f"{_stable_int('eft', payer.payer_id) % 10**8:08d}{n:07d}"

    def _icn(self, payer: Payer, c: Claim, salt: str, day: date) -> str:
        """Payer claim control number (CLP07) in the payer's own format."""
        h = _stable_int(c.pcn, payer.payer_id, salt)
        style = payer.icn_style
        if style == "medicare":                                   # RR YY JJJ BBB SSS: region, Julian receipt date, batch
            return f"{'02' if salt == 'orig' else '50'}{day:%y}{day.timetuple().tm_yday:03d}{h % 10**6:06d}"
        if style == "alpha2digits10":
            return f"{chr(65 + h % 26)}{chr(65 + h // 26 % 26)}{h // 676 % 10**10:010d}"
        n = int(style.removeprefix("digits"))
        return f"{h % 10**n:0{n}d}"

    # pricing
    def _allowed_per_unit(self, payer: Payer, cpt: CPT, rate_factor: float) -> float:
        if payer.is_medicare:
            mult = 1.0
        elif payer.payer_class in ("medicaid", "medicaid_mco"):
            mult = min(1.0, payer.allowed_mult * _contract_noise(payer.payer_id, cpt.code, .08))   # SSA 1903(i)(7) cap
        else:
            mult = payer.allowed_mult * _contract_noise(payer.payer_id, cpt.code, .35 if cpt.code == "81479" else .08)
        return cpt.rate * mult * rate_factor

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
        x.s("HL", str(sub_hl), "1", "22", "1" if dependent else "0")
        x.s("SBR", "S" if seq else "P", "" if dependent else "18", cov.group, "", "", "", "", "", cov.payer.sbr09)
        s = cov.subscriber
        x.s("NM1", "IL", "1", s.last, s.first, s.middle, "", "", "MI", cov.member_id)
        x.s("N3", s.addr, s.addr2)
        x.s("N4", s.city[0], s.city[1], _zip(s))
        x.s("DMG", "D8", f"{s.dob:%Y%m%d}", s.gender)
        x.s("NM1", "PR", "2", cov.payer.name, "", "", "", "", "PI", cov.payer.payer_id)
        if dependent:
            hl[0] += 1
            x.s("HL", str(hl[0]), str(sub_hl), "23", "0")
            x.s("PAT", cov.relationship)
            x.s("NM1", "QC", "1", pat.last, pat.first, pat.middle)
            x.s("N3", pat.addr, pat.addr2)
            x.s("N4", pat.city[0], pat.city[1], _zip(pat))
            x.s("DMG", "D8", f"{pat.dob:%Y%m%d}", pat.gender)
        total = round(sum(l.charge for l in c.lines), 2)
        x.s("CLM", c.pcn, money(total), "", "", x.c(c.pos, "B", freq), "Y", "A", "Y", "Y")
        if freq in ("7", "8"):
            x.s("REF", "F8", orig_icn)
        if c.trace:
            x.s("REF", "D9", c.trace)
        x.s("REF", "X4", t.clia)
        if c.prior_auth:
            x.s("REF", "G1", c.prior_auth)
        if any(l.cpt == "81479" for l in c.lines):
            x.s("NTE", "ADD", "UNLISTED MOLECULAR PATHOLOGY - GENE PANEL")
        x.s("HI", *[x.c("ABK" if i == 0 else "ABF", dx) for i, dx in enumerate(c.dx)])
        r = c.referring
        x.s("NM1", "DN", "1", r.last, r.first, r.middle, "", "", "XX", r.npi)
        if c.service_facility:
            x.s("NM1", "77", "2", f"{t.name} PATIENT SERVICE CENTER", "", "", "", "", "XX", npi(t.npi_seed + 1))
            x.s("N3", t.address[0])
            x.s("N4", t.address[1], t.address[2], t.address[3])
        if prior:                                                 # 2320 / 2330A / 2330B: the primary's adjudication
            pcov = c.patient.coverages[0]
            x.s("SBR", "P", pcov.relationship, pcov.group, "", "", "", "", "", pcov.payer.sbr09)
            for seg in cas_segments(prior["claim_cas"]):
                x.s(*seg)
            x.s("AMT", "D", money(prior["paid"]))
            x.s("OI", "", "", "Y", "", "", "Y")
            x.s("NM1", "IL", "1", pcov.subscriber.last, pcov.subscriber.first, "", "", "", "MI", pcov.member_id)
            x.s("NM1", "PR", "2", pcov.payer.name, "", "", "", "", "PI", pcov.payer.payer_id)
        send_6r = c.sends_line_ids
        for i, l in enumerate(c.lines, 1):
            x.s("LX", str(i))
            if l.cpt == "81479":                                  # unlisted code: description in SV101-7
                proc = x.c("HC", l.cpt, *(list(l.modifiers) + [""] * (4 - len(l.modifiers))), "GENE PANEL")
            else:
                proc = x.c("HC", l.cpt, *l.modifiers)
            x.s("SV1", proc, money(l.charge), "UN", str(l.units), "", "", x.c(*l.pointers))
            x.s("DTP", "472", "D8", f"{c.dos:%Y%m%d}")
            if send_6r:
                x.s("REF", "6R", f"{c.pcn}{i:02d}")
            if c.line_level_ordering:
                x.s("NM1", "DK", "1", r.last, r.first, r.middle, "", "", "XX", r.npi)
            if prior:
                pl = prior["lines"][i - 1]
                x.s("SVD", c.patient.coverages[0].payer.payer_id, money(pl["paid"]), x.c("HC", l.cpt, *l.modifiers), "",
                    str(l.units))
                for seg in cas_segments(pl["cas"]):
                    x.s(*seg)
                x.s("DTP", "573", "D8", f"{prior['date']:%Y%m%d}")

    def _file_837(self, t: Tenant, d: date, items: list, batch: int, sent_at: str) -> str:
        x = X12(t.delimiters)
        x.s("BHT", "0019", "00", f"{t.sender_id}{d:%Y%m%d}{batch:02d}", f"{d:%Y%m%d}", sent_at[:4], "CH")
        x.s("NM1", "41", "2", t.name, "", "", "", "", "46", t.tax_id)
        x.s("PER", "IC", "BILLING OFFICE", "TE", t.billing_phone)
        x.s("NM1", "40", "2", CLEARINGHOUSE_NAME, "", "", "", "", "46", "CCCH01")
        x.s("HL", "1", "", "20", "1")
        x.s("PRV", "BI", "PXC", "291U00000X")
        x.s("NM1", "85", "2", t.name, "", "", "", "", "XX", npi(t.npi_seed))
        x.s("N3", t.address[0])
        x.s("N4", t.address[1], t.address[2], t.address[3])
        x.s("REF", "EI", t.tax_id)
        hl = [1]
        for c, seq, freq, prior, orig_icn in items:
            self._claim_837(x, t, c, seq, freq, hl, prior, orig_icn)
        return interchange(t.delimiters, t.sender_id, CLEARINGHOUSE_ID, d, self._next_ctl(t.sender_id, CLEARINGHOUSE_ID),
                           "HC", "005010X222A1", [x], "837", sent_at=sent_at)

    # -- adjudication ----------------------------------------------------------------------------------
    def _deductible_left(self, c: Claim, payer: Payer, accum: dict, base: float) -> tuple[float, tuple]:
        """Deductible still owed on the service date: other care met part of it through the year (each patient at
        their own pace), and this lab's own earlier claims (accum) met some more."""
        p = c.patient
        doy = c.dos.timetuple().tm_yday / 365.0
        left = base * max(0.0, 1 - doy / p.deductible_pace)
        key = (p.pid, payer.payer_id)
        spent = accum.setdefault(c.dos.year, {}).get(key, 0.0)
        return max(0.0, round(left - spent, 2)), key

    def _adjudicate(self, c: Claim, seq: int, outcome: str, accum: dict, d: date, prior: dict | None = None,
                    denial: dict | None = None, allowed_scale: float = 1.0, salt: str = "orig") -> dict:
        """One CLP. outcome: paid | denied (every line) | partial (denial['lines'] only) | zero_fee | secondary."""
        r = self.rng
        cov = c.patient.coverages[seq]
        payer = cov.payer
        pat = c.patient
        medicare = payer.is_medicare
        commercial = payer.payer_class in ("commercial", "blues", "other")
        lines, copay_left = [], pat.copay
        ded_base = (pat.deductible if pat.lab_deductible else 0.0) if commercial else (MEDICARE_PART_B_DEDUCTIBLE_2026 if medicare else 0.0)
        if ded_base:
            ded_left, acc_key = self._deductible_left(c, payer, accum, ded_base)
        else:
            ded_left, acc_key = 0.0, None
        ded_used = 0.0
        denied_lines = denial["lines"] if denial else set()
        recodes = denial["recode"] if denial else {}
        edits = c.line_edits if seq == 0 and outcome in ("paid", "partial") else {}
        edit_category = None
        sequestered = medicare or payer.sequestration
        for i, l in enumerate(c.lines):
            cpt = CPTS[l.cpt]
            cas, rarc, units_paid, units_billed, adjudicated = [], None, l.units, None, l.cpt
            screening = c.dx[int(l.pointers[0]) - 1] in SCREENING_DX
            if outcome == "secondary":                            # pays toward what the primary left the patient
                pl = prior["lines"][i]
                left = round(sum(a for g, _, a in pl["cas"] if g == "PR"), 2)
                cas.append(("OA", "23", round(l.charge - left, 2)))
                paid = round(left * (1.0 if payer.payer_class == "medicaid" else r.choice([1.0, .8])), 2)
                rest = round(left - paid, 2)
                if rest:
                    cas.append(("PR", "2", rest))
                allowed = left
            elif outcome == "denied" or i in denied_lines:
                code = denial["line_carc"].get(i, denial["carc"])
                group = denial["line_group"].get(i, "CO")
                rarc = denial["line_rarc"].get(i, denial["rarc"])
                paid, allowed = 0.0, 0.0
                cas.append((group, code, l.charge))
            elif i in edits:                                      # NCCI residue, NCD coverage, ABN, sex edit
                group, code, rarc, category = edits[i]
                paid, allowed = 0.0, 0.0
                cas.append((group, code, l.charge))
                edit_category = edit_category or category
            elif outcome == "zero_fee":
                paid, allowed = 0.0, 0.0
                cas.append(("CO", "45", l.charge))
            elif cpt.mue and l.units > cpt.mue and payer.payer_class in ("medicare", "medicare_advantage"):
                paid, allowed, rarc = 0.0, 0.0, "N362"            # MUE: the whole line is denied
                cas.append(("CO", "151", l.charge))
            else:
                if i in recodes:                                   # payer folds billed lines into a ceiling code
                    adjudicated = recodes[i]
                    per_unit = self._allowed_per_unit(payer, CPTS[adjudicated], 1.0)
                    allowed_units = 1
                else:
                    per_unit = self._allowed_per_unit(payer, cpt, l.rate_factor)
                    allowed_units = l.units
                excess = 0.0
                if cpt.mue and l.units > cpt.mue and not medicare:     # commercial: pays up to the frequency cap
                    allowed_units, units_paid, units_billed = cpt.mue, cpt.mue, l.units
                    excess = round(l.charge * (l.units - cpt.mue) / l.units, 2)
                if payer.out_of_network:
                    allowed = round((l.charge - excess) * payer.out_of_network, 2)
                else:
                    allowed = round(min(l.charge - excess, per_unit * allowed_units * allowed_scale), 2)
                pr_parts = []
                if commercial and not (cpt.preventive and screening):
                    ded = round(min(ded_left, allowed), 2)
                    if ded > 0:
                        pr_parts.append(("1", ded))
                        ded_left = round(ded_left - ded, 2)
                        ded_used += ded
                    co_ins = round((allowed - ded) * pat.coinsurance, 2)
                    if co_ins > 0:
                        pr_parts.append(("2", co_ins))
                    cp = round(min(copay_left, allowed - ded - co_ins), 2)
                    if cp > 0:
                        pr_parts.append(("3", cp))
                        copay_left = round(copay_left - cp, 2)
                elif payer.payer_class == "medicare_advantage":
                    cp = round(min(copay_left, allowed), 2)
                    if cp > 0:
                        pr_parts.append(("3", cp))
                        copay_left = round(copay_left - cp, 2)
                elif medicare and cpt.category == "pathology":     # PFS: Part B deductible + 20%
                    ded = round(min(ded_left, allowed), 2)
                    if ded > 0:
                        pr_parts.append(("1", ded))
                        ded_left = round(ded_left - ded, 2)
                        ded_used += ded
                    co_ins = round((allowed - ded) * .2, 2)
                    if co_ins > 0:
                        pr_parts.append(("2", co_ins))
                pr = round(sum(a for _, a in pr_parts), 2)
                seq_cut = round(SEQUESTRATION * (allowed - pr), 2) if sequestered else 0.0
                paid = round(allowed - pr - seq_cut, 2)
                contractual = round(l.charge - excess - allowed, 2)
                if contractual > 0:
                    cas.append((payer.reduction_group, "45", contractual))
                if excess:
                    cas.append(("CO", "151", excess))
                if seq_cut:
                    cas.append(("CO", "253", seq_cut))
                cas += [("PR", code, a) for code, a in pr_parts]
            lines.append(dict(cpt=l.cpt, adjudicated_cpt=adjudicated, units=l.units, units_paid=units_paid,
                              units_billed=units_billed, modifiers=l.modifiers, charge=l.charge, paid=paid,
                              allowed=allowed, cas=cas, rarc=rarc))
        if ded_used and acc_key is not None:
            acc = accum[c.dos.year]
            acc[acc_key] = round(acc.get(acc_key, 0.0) + ded_used, 2)
        claim_level = bool(denial and denial["claim_level_only"] and outcome == "denied")
        e = dict(pcn=c.pcn, payer_id=payer.payer_id, seq=seq, lines=lines, claim_cas=[],
                 icn=self._icn(payer, c, salt, d), claim_level_only=claim_level, status=None,
                 category=denial["category"] if denial else edit_category, moa=[], plb=[], interest=0, crossover=None)
        if claim_level:                                           # move CAS to the claim, drop line detail
            e["claim_cas"] = [(denial["line_group"].get(0, "CO"), denial["carc"], round(sum(l.charge for l in c.lines), 2))]
            e["moa"] = [denial["rarc"] or "N130"]
            for ln in lines:
                ln["cas"], ln["rarc"] = [], None
        if outcome in ("paid", "partial") and seq == 0 and not medicare:
            for ln in lines:                                      # payer re-coded: SVC01 = paid code, SVC06 = billed
                if ln["cpt"] in RECODE and ln["adjudicated_cpt"] == ln["cpt"] and ln["paid"] > 0 \
                        and r.random() < payer.svc06_rate * 4:
                    ln["adjudicated_cpt"] = RECODE[ln["cpt"]]
        if medicare and any(ln["paid"] == 0 and ln["cas"] for ln in lines):
            e["moa"] = ["MA01"]                                   # appeal rights on a claim with denied lines
        e["no_6r"] = not c.sends_line_ids or r.random() > payer.ref6r_rate
        e["paid"] = round(sum(ln["paid"] for ln in lines), 2)
        e["pr"] = round(sum(a for ln in lines for g, _, a in ln["cas"] if g == "PR") +
                        sum(a for g, _, a in e["claim_cas"] if g == "PR"), 2)
        return e

    @staticmethod
    def _reverse(e: dict) -> dict:
        neg = lambda cas: [(g, k, -a) for g, k, a in cas]
        return dict(e, status="22", paid=-e["paid"], pr=-e["pr"], claim_cas=neg(e["claim_cas"]), plb=[], interest=0,
                    lines=[dict(ln, paid=-ln["paid"], allowed=-ln["allowed"], charge=-ln["charge"], cas=neg(ln["cas"]))
                           for ln in e["lines"]], reversal=True, crossover=None, moa=[])

    # -- denials -----------------------------------------------------------------------------------------
    @staticmethod
    def _claim_class(c: Claim) -> str:
        if c.order_type in MOLECULAR_PROFILES:
            return "molecular"
        if c.order_type in PATHOLOGY_PROFILES:
            return "pathology"
        return "drug" if c.order_type == "drug_monitoring" else "routine"

    def _p_denial(self, c: Claim, payer: Payer) -> float:
        klass = self._claim_class(c)
        p = INITIAL_DENIAL_RATE[klass] * payer.denial_mult * self.sc.denial_rate_scale * DENIAL_CALIBRATION
        if c.dx[0] in SCREENING_DX and any(CPTS[l.cpt].medical_necessity for l in c.lines):
            p *= 2.2
        if not self._bundling_lines(c, payer):
            p *= .45                                          # nothing to bundle (the top denial reason)
        if c.referring.risky:
            p *= 1.8
        if klass == "molecular" and not c.prior_auth and not payer.is_medicare:
            p *= 1.5
        return min(p, .95)

    def _denial_category(self, c: Claim, payer: Payer) -> str:
        klass = self._claim_class(c)
        col = "medicare" if payer.is_medicare else {"routine": "clinical", "drug": "clinical"}.get(klass, klass)
        has_mn = any(CPTS[l.cpt].medical_necessity for l in c.lines)
        weights = {}
        for name, cat in DENIAL_CATEGORIES.items():
            w = cat[col]
            if name == "medical_necessity":                  # Medicare: decided by the NCD edits (_line_edits)
                w *= (1.5 if c.dx[0] in SCREENING_DX else 1) if has_mn and not payer.is_medicare else 0
            elif name == "prior_auth":
                w *= 0 if c.prior_auth else (3 if klass == "molecular" else 1)
            elif name in ("information", "pecos") and c.referring.risky:
                w *= 4
            elif name == "bundling" and not self._bundling_lines(c, payer):
                w = 0                                         # nothing on the claim to fold into another service
            if w > 0:
                weights[name] = w
        return _pick(self.rng, weights)

    def _denial(self, c: Claim, payer: Payer, category: str) -> dict:
        """Which lines a denial hits, with CARC/group/RARC. A line-level denial that covers every line is a full one."""
        r = self.rng
        cat = DENIAL_CATEGORIES[category]
        carcs = cat.get("medicare_carcs", cat["carcs"]) if payer.is_medicare else cat["carcs"]
        n = len(c.lines)
        spec = dict(category=category, carc=_pick(r, carcs), rarc=_pick(r, cat["rarcs"]) or None, lines=set(),
                    recode={}, line_carc={}, line_group={}, line_rarc={}, claim_level_only=False)
        level = cat["level"]
        if level == "line_or_claim":
            level = "line" if n > 1 and r.random() < .7 else "claim"
        if level == "line" and n > 1:
            if category == "bundling":                        # payer policy, not NCCI (the scrubber removed those)
                folded = [i for i in self._bundling_lines(c, payer) if c.lines[i].cpt not in ("87491", "87591")]
                draw = [i for i in folded if c.lines[i].cpt == "36415"]
                sti = [i for i, l in enumerate(c.lines) if l.cpt in ("87491", "87591")]
                if len(sti) == 2 and not payer.is_medicare and (not folded or r.random() < .5):
                    a, b = sorted(sti, key=lambda i: c.lines[i].cpt)   # 87491 paid as 87801 (SVC06), 87591 -> CO-97
                    spec["recode"][a] = "87801"
                    spec["lines"] = {b}
                    spec["line_carc"][b] = "97"
                elif draw and (len(folded) == 1 or r.random() < .6):
                    spec["lines"] = set(draw)
                else:
                    rest = [i for i in folded if i not in draw] or folded
                    spec["lines"] = set(r.sample(rest, min(len(rest), r.choice([1, 1, 2]))))
            elif category == "medical_necessity":
                mn = [i for i, l in enumerate(c.lines) if CPTS[l.cpt].medical_necessity]
                spec["lines"] = set(r.sample(mn, max(1, min(len(mn), _poisson(r, .6) + 1))))
            elif category == "modifier":
                mods = [i for i, l in enumerate(c.lines) if l.modifiers]
                spec["lines"] = {r.choice(mods or list(range(n)))}
            elif category == "experimental":
                mol = [i for i, l in enumerate(c.lines) if CPTS[l.cpt].category in ("molecular", "pcr")]
                spec["lines"] = {r.choice(mol or list(range(n)))}
            else:
                spec["lines"] = {r.randrange(n)}
        else:
            spec["lines"] = set(range(n))
            if not payer.is_medicare and r.random() < payer.claim_level_denials:
                spec["claim_level_only"] = True
        spec["full"] = len(spec["lines"]) == n and not spec["recode"]
        return spec

    @staticmethod
    def _bundling_lines(c: Claim, payer: Payer) -> list[int]:
        """Lines a payer's own bundling policy folds into another service on the claim (Medicare pays them)."""
        if payer.is_medicare or len(c.lines) < 2:
            return []
        codes = {l.cpt for l in c.lines}
        out = [i for i, l in enumerate(c.lines) if l.cpt in PAYER_BUNDLED_INTO
               and (PAYER_BUNDLED_INTO[l.cpt] is None or codes & set(PAYER_BUNDLED_INTO[l.cpt]))
               and not {"59", "XU"} & set(l.modifiers)]
        if {"87491", "87591"} <= codes:
            out += [i for i, l in enumerate(c.lines) if l.cpt in ("87491", "87591")]
        return out

    def _fixed_denial(self, c: Claim, payer: Payer, category: str, carc: str, rarc: str | None,
                      group: str = "CO") -> dict:
        """A whole-claim denial with a given reason (timely filing, duplicate, MAC edit wave)."""
        n = len(c.lines)
        claim_level = not payer.is_medicare and self.rng.random() < payer.claim_level_denials
        return dict(category=category, carc=carc, rarc=rarc, lines=set(range(n)), recode={}, line_carc={},
                    line_group={i: group for i in range(n)}, line_rarc={}, claim_level_only=claim_level, full=True)

    # -- 835 ------------------------------------------------------------------------------------------
    def _st_835(self, x: X12, t: Tenant, payer: Payer, d: date, pay_date: date, events: list, plbs: list, trn: str):
        total = round(sum(e["paid"] for e in events) - sum(p[2] for p in plbs), 2)
        if total == 0:
            x.s("BPR", "H", "0", "C", "NON", "", "", "", "", "", "", "", "", "", "", "", f"{pay_date:%Y%m%d}")
        elif payer.payment_method == "CHK":
            x.s("BPR", "I", money(total), "C", "CHK", "", "", "", "", "", "", "", "", "", "", "", f"{pay_date:%Y%m%d}")
        else:
            aba, account = payer.bank
            x.s("BPR", "I", money(total), "C", "ACH", "CCP", "01", aba, "DA", account, "1" + payer.ein, "",
                "01", t.bank[0], "DA", t.bank[1], f"{pay_date:%Y%m%d}")
        x.s("TRN", "1", trn, "1" + payer.ein)
        x.s("DTM", "405", f"{d:%Y%m%d}")
        x.s("N1", "PR", payer.name_835 or payer.name)
        if payer.remit_address:
            x.s("N3", payer.remit_address[0])
            x.s("N4", *payer.remit_address[1:])
        if payer.sends_ref_2u:
            x.s("REF", "2U", payer.payer_id)
        contact = payer_contacts(payer)
        if contact["business"]:
            x.s("PER", "CX", "PROVIDER SERVICES", "TE", contact["business"])
        x.s("PER", "BL", contact["technical_name"], "TE", contact["technical"], *(("EM", contact["email"])
                                                                               if contact["email"] else ()))
        if payer.website:
            x.s("PER", "IC", "", "UR", payer.website)
        x.s("N1", "PE", t.name, "XX", npi(t.npi_seed))
        x.s("REF", "TJ", t.tax_id)
        medicare = payer.is_medicare
        commercial_extras = not medicare and _stable_unit(payer.payer_id, "extras") < .7
        if events:
            x.s("LX", "1")
        for e in events:
            c: Claim = e["claim"]
            charge = round(sum(ln["charge"] for ln in e["lines"]), 2)
            x.s("CLP", c.pcn, e["status"], money(charge), money(e["paid"]), money(e["pr"]) if e["pr"] else "",
                payer.clp06, e["icn"], c.pos, e.get("freq", "1"))
            for seg in cas_segments(e["claim_cas"]):
                x.s(*seg)
            cov = c.patient.coverages[e["seq"]]
            p = c.patient.person
            if medicare or cov.relationship == "18":
                x.s("NM1", "QC", "1", p.last, p.first, "", "", "", "MI", cov.member_id)
            else:
                x.s("NM1", "QC", "1", p.last, p.first)
                x.s("NM1", "IL", "1", cov.subscriber.last, cov.subscriber.first, "", "", "", "MI", cov.member_id)
            if e["crossover"]:
                x.s("NM1", "TT", "2", e["crossover"], "", "", "", "", "PI", f"{_stable_int(e['crossover']) % 90000 + 10000}")
            if e["moa"]:
                x.s("MOA", "", "", *e["moa"])
            if commercial_extras and e["seq"] == 0 and not e.get("reversal"):
                x.s("REF", "1L", cov.group)
            if e["claim_level_only"] or (commercial_extras and not medicare):
                x.s("DTM", "232", f"{c.dos:%Y%m%d}")
            x.s("DTM", "050", f"{e['received']:%Y%m%d}")
            if commercial_extras and e["paid"] > 0 and not e.get("reversal"):
                x.s("AMT", "AU", money(sum(ln["allowed"] for ln in e["lines"])))    # coverage amount
            if e["interest"]:
                x.s("AMT", "I", money(e["interest"]))
            if e["claim_level_only"]:
                continue
            for i, ln in enumerate(e["lines"], 1):
                billed = x.c("HC", ln["cpt"], *ln["modifiers"])
                adjud = x.c("HC", ln["adjudicated_cpt"], *ln["modifiers"])
                units = str(ln["units_paid"])
                x.s("SVC", adjud, money(ln["charge"]), money(ln["paid"]), "", units,
                    billed if ln["adjudicated_cpt"] != ln["cpt"] else "",
                    str(ln["units_billed"]) if ln["units_billed"] else "")
                x.s("DTM", "472", f"{c.dos:%Y%m%d}")
                for seg in cas_segments(ln["cas"]):
                    x.s(*seg)
                if not e["no_6r"]:
                    x.s("REF", "6R", f"{c.pcn}{i:02d}")
                if medicare and ln["rarc"] in ("N386", "N115"):
                    x.s("REF", "0K", COVERAGE_POLICY.get(ln["cpt"], "L35062"))   # the NCD/LCD the denial rests on
                if ln["allowed"] or ln["paid"]:
                    x.s("AMT", "B6", money(ln["allowed"]))
                if ln["rarc"]:
                    x.s("LQ", "HE", ln["rarc"])
        for code, ref, amt, _ in plbs:
            x.s("PLB", npi(t.npi_seed), f"{d.year}1231", x.c(code, ref) if ref else code, money(amt))
        return total

    def _interest(self, payer: Payer, e: dict, pay_date: date) -> float:
        """Prompt-pay interest on a clean claim paid late (Medicare: after day 30, at the Treasury rate)."""
        if e["paid"] <= 0 or e.get("reversal") or e["seq"] != 0 or e["claim"].interest_exempt:
            return 0.0
        days = (pay_date - e["received"]).days
        if payer.is_medicare:
            rate, after = MEDICARE_INTEREST[1 if pay_date.month <= 6 else 2], 30
        elif payer.interest_rate:
            rate, after = payer.interest_rate, payer.interest_after_days
        else:
            return 0.0
        return round(e["paid"] * rate * (days - after) / 365, 2) if days > after else 0.0

    @staticmethod
    def _plb_reference(payer: Payer, e: dict) -> str:
        """PLB03-2 as this payer writes it: the payer's claim number (Medicare: the FCN; real MACs append the MBI,
        which is PHI in a provider-level field), UHC's date of service + patient account, or the account alone."""
        c = e["claim"]
        style = "icn" if payer.is_medicare else payer.plb_reference_style
        if style == "icn":
            return e["icn"]
        if style == "pcn":
            return c.pcn
        if style == "icn_pcn":
            return f"{e['icn']} {c.pcn}"
        return f"{c.dos:%Y%m%d}{c.pcn}"

    def _recoupment_target(self, history: list, day: date):
        """A payment made 30-365 days ago (median ~3 months): overpayments surface in audits and retro-eligibility
        reviews long after the check, so recoupments trail payment by months."""
        if not history:
            return None
        lag = min(365, max(30, round(math.exp(self.rng.gauss(math.log(85), .7)))))
        target = (day - timedelta(days=lag)).toordinal()
        i = bisect.bisect_right(history, (target, "\uffff")) - 1
        if i < 0:
            i = 0
        entry = history[i]
        return entry if day.toordinal() - entry[0] >= 30 else None

    def _files_835(self, t: Tenant, payer: Payer, day: date, evs: list, fwd: dict, claims: dict) -> list[dict]:
        """One payer's 835 for the day. During the burn-in (self.emit False) the money still moves (recoupment
        history, forward balances, trace numbers) but nothing is written or recorded."""
        r, sc, emit = self.rng, self.sc, self.emit
        pay_date = self._pay_date(payer, day)
        groups = [evs]
        if len(evs) > 3 and r.random() < payer.multi_st_rate:
            k = len(evs) // 2
            while k < len(evs) and evs[k - 1]["pcn"] == evs[k]["pcn"]:   # a claim's events stay in one transaction
                k += 1
            groups = [g for g in (evs[:k], evs[k:]) if g]
        txs = []
        history = self.paid_history.setdefault((t.code, payer.payer_id), [])
        for g in groups:
            trn = self._trace(payer)
            plbs = []                                             # (reason, PLB03-2 reference, amount, target pcn)
            if fwd.get(payer.payer_id):
                amount, origin = fwd.pop(payer.payer_id)
                plbs.append(("FB", origin, amount, None))         # recover the balance carried forward
            if r.random() < sc.p_plb_recoupment:                 # overpayments found on earlier remits: usually
                for _ in range(1 if r.random() < .8 else r.randint(2, 6)):   # one claim, sometimes an audit batch
                    hit = self._recoupment_target(history, day)
                    if hit and not any(p[3] == hit[4] for p in plbs):
                        _, icn, amt, ref, pcn = hit
                        part = amt if r.random() < .4 else round(amt * r.uniform(.1, .9), 2)
                        plbs.append(("WO", ref, max(.01, part), pcn))
            if r.random() < .005:                                 # refund check received: 72 offsets a WO
                hit = self._recoupment_target(history, day)
                if hit:
                    _, icn, amt, ref, pcn = hit
                    plbs += [("WO", ref, amt, pcn), ("72", ref, -amt, pcn)]
            if r.random() < .005:
                plbs.append(("J1", None, round(r.uniform(.01, 4.99), 2), None))
            if payer.payer_class == "blues" and r.random() < .01:
                hit = self._recoupment_target(history, day)
                if hit:
                    plbs.append(("CS", hit[3], round(r.uniform(5, min(150, max(5.01, hit[2]))), 2), hit[4]))
            if r.random() < .0005:
                plbs.append(("IR", None, round(r.uniform(10, 300), 2), None))
            for e in g:
                plbs += e["plb"]
                e["interest"] = self._interest(payer, e, pay_date)
            interest = round(sum(e["interest"] for e in g), 2)
            if interest:
                plbs.append(("L6", None, -interest, None))        # L6 = sum of the claims' AMT*I
            total = round(sum(e["paid"] for e in g) - sum(p[2] for p in plbs), 2)
            if total < 0:                                         # an 835 never pays a negative amount: carry it
                plbs.append(("FB", trn, total, None))             # forward, referencing this remittance's trace
                prior = fwd.get(payer.payer_id, (0.0, trn))[0]
                fwd[payer.payer_id] = (round(prior - total, 2), trn)
            for e in g:
                if e["status"] in ("1", "2", "19") and e["paid"] > 0 and r.random() < RECOUPMENT_CANDIDATE_SHARE:
                    history.append((pay_date.toordinal(), e["icn"], e["paid"], self._plb_reference(payer, e), e["pcn"]))
            if not emit:
                continue
            x = X12(payer.delimiters)
            bpr = self._st_835(x, t, payer, day, pay_date, g, plbs, trn)
            self.plb_log.setdefault(t.code, []).extend(
                dict(day=str(day), payment_date=str(pay_date), payer_id=payer.payer_id, trn=trn, code=code,
                     reference=ref, amount=amt, target_pcn=pcn) for code, ref, amt, pcn in plbs)
            txs.append((x, bpr, trn))
            for e in g:
                self._record_event(claims, e, payer, day, pay_date, trn)
        cutoff = (day - timedelta(days=RECOUPMENT_MAX_DAYS)).toordinal()
        if history and history[0][0] < cutoff:                    # bounded memory: only the last year can be recouped
            del history[:bisect.bisect_left(history, (cutoff,))]
        if not emit:
            self._next_ctl(CLEARINGHOUSE_ID, t.sender_id)
            return []
        sender, receiver = CLEARINGHOUSE_ID, t.sender_id
        reused = r.random() < DELIVERY_NOISE["isa13_reused"]
        ctl = self._reused_ctl(sender, receiver) if reused else self._next_ctl(sender, receiver)
        sent_at = self._sent_at_835(payer)
        body = interchange(payer.delimiters, sender, receiver, day, ctl, "HP", "005010X221A1",
                           [tx[0] for tx in txs], "835", gs_sender=payer.payer_id, sent_at=sent_at)
        out = [dict(day=day, kind="835", name=self._file_name(t, "835", day, sent_at, payer.payer_id), body=self._noise(body),
                    expect="parsed", payer_id=payer.payer_id, trns=[tx[2] for tx in txs], totals=[tx[1] for tx in txs])]
        if r.random() < sc.p_duplicate_835_file:                  # the clearinghouse sends the same file again later
            later = f"{min(23, int(sent_at[:2]) + r.randint(1, 9)):02d}{r.randint(0, 59):02d}{r.randint(0, 59):02d}"
            out.append(dict(out[0], name=self._file_name(t, "835", day, later, payer.payer_id), expect="duplicate",
                            redelivery_of=out[0]["name"]))
        return out

    @staticmethod
    def _record_event(claims: dict, e: dict, payer: Payer, day: date, pay_date: date, trn: str):
        led = claims[e["pcn"]]["adjudications"].setdefault(e["payer_id"], dict(
            payer_id=e["payer_id"], payer=PAYER_BY_ID[e["payer_id"]].name, seq=e["seq"],
            payer_class=PAYER_BY_ID[e["payer_id"]].payer_class, events=[]))
        cas_totals: dict = {}
        for g_, k, amt in e["claim_cas"] + [x for ln in e["lines"] for x in ln["cas"]]:
            cas_totals[f"{g_}-{k}"] = round(cas_totals.get(f"{g_}-{k}", 0) + amt, 2)
        led["events"].append(dict(date=str(pay_date), production_date=str(day), status=e["status"], icn=e["icn"],
                                  paid=e["paid"], pr=e["pr"], interest=e["interest"],
                                  charge=round(sum(ln["charge"] for ln in e["lines"]), 2), cas=cas_totals,
                                  claim_level_only=e["claim_level_only"], trn=trn, category=e["category"],
                                  received=str(e["received"]), crossover=bool(e["crossover"]),
                                  duplicate_denial=bool(e.get("duplicate_denial")),
                                  carcs=sorted({k for _, k, _ in e["claim_cas"]} |
                                               {k for ln in e["lines"] for _, k, _ in ln["cas"]}),
                                  co_carcs=sorted({k for g_, k, _ in e["claim_cas"] if g_ == "CO"} |
                                                  {k for ln in e["lines"] for g_, k, _ in ln["cas"] if g_ == "CO"}),
                                  lines=[dict(cpt=ln["cpt"], adjudicated_cpt=ln["adjudicated_cpt"],
                                              units=ln["units"], units_paid=ln["units_paid"], paid=ln["paid"],
                                              allowed=ln["allowed"], charge=ln["charge"],
                                              modifiers=list(ln["modifiers"]),
                                              co_carcs=sorted({k for g_, k, _ in ln["cas"] if g_ == "CO"}))
                                         for ln in ([] if e["claim_level_only"] else e["lines"])]))

    def _sent_at_835(self, payer: Payer) -> str:
        """The clearinghouse releases each payer's ERAs in its overnight run (some payers' files come mid-day)."""
        base = (1, 2, 2, 3, 3, 4, 5, 6, 13, 16)[_stable_int(payer.payer_id, "era-hour") % 10]
        minute = _stable_int(payer.payer_id, "era-minute") % 60 + self.rng.randint(-20, 20)
        hour = base + minute // 60
        return f"{hour % 24:02d}{minute % 60:02d}{self.rng.randint(0, 59):02d}"

    def _file_name(self, t: Tenant, kind: str, day: date, sent_at: str, payer_id: str | None = None) -> str:
        """As the clearinghouse names them: sender, transaction, payer (835), and the send timestamp. Names are
        unique within a day; a re-delivery is a new name (new timestamp) with the same bytes."""
        stem = (f"{t.sender_id}_837P_{day:%Y%m%d}{sent_at}" if kind == "837" else
                f"ERA_{payer_id}_{t.sender_id}_{day:%Y%m%d}{sent_at}" if payer_id else
                f"ERA_{t.sender_id}_{day:%Y%m%d}{sent_at}")
        name, n = f"{stem}.x12", 1
        while name in self.names_today:
            n += 1
            name = f"{stem}_{n}.x12"
        self.names_today.add(name)
        return name

    def _noise(self, body: str) -> str:
        """Delivery noise real feeds carry and a parser must survive (spec quirks.envelope): 80-column wrapping, a
        UTF-8 byte-order mark, NUL padding after IEA (fixed-block transfers)."""
        u = self.rng.random()
        if u < DELIVERY_NOISE["wrapped_80"]:
            flat = body.replace("\r", "").replace("\n", "")
            return "\n".join(flat[i:i + 80] for i in range(0, len(flat), 80)) + "\n"
        u -= DELIVERY_NOISE["wrapped_80"]
        if u < DELIVERY_NOISE["utf8_bom"]:
            return "\ufeff" + body
        u -= DELIVERY_NOISE["utf8_bom"]
        if u < DELIVERY_NOISE["nul_padding"]:
            return body + "\x00" * self.rng.randint(1, 255)
        return body

    def _concatenate(self, remits: list, day: date) -> list:
        """The clearinghouse sometimes concatenates several payers' 835 interchanges into one file (several ISAs).
        Files that also arrive as a byte-identical re-delivery are left alone."""
        redelivered = {f["redelivery_of"] for f in remits if f["expect"] == "duplicate"}
        out, batches = [], {}
        for f in remits:
            plain = f["body"][:3] == "ISA" and "\x00" not in f["body"][-1:] and "\n" not in f["body"][:106]
            if (f["expect"] == "parsed" and plain and f["name"] not in redelivered
                    and self.rng.random() < DELIVERY_NOISE["multi_isa"]):
                b = f["body"]
                batches.setdefault((b[3], b[104], b[105], b[106:107]), []).append(f)   # one delimiter set per file
            else:
                out.append(f)
        for k, (style, batch) in enumerate(sorted(batches.items())):
            if len(batch) < 2:
                out += batch
                continue
            out.append(dict(batch[0], name=self._file_name(self.t, "835", day, f"0{k + 4}{self.rng.randint(0, 5959):04d}"),
                            payer_id="+".join(f["payer_id"] for f in batch), body="".join(f["body"] for f in batch),
                            trns=[x for f in batch for x in f["trns"]], totals=[x for f in batch for x in f["totals"]]))
        return out

    def _corrupt(self, f: dict, day: date) -> dict:
        r = self.rng
        body = f["body"] if isinstance(f["body"], str) else f["body"].decode()
        kind = r.choice(["truncated", "not_x12", "bad_envelope"])
        if kind == "bad_envelope" and "IEA" + body.lstrip("\ufeff")[3] not in body:
            kind = "truncated"                                    # an 80-column wrap split the IEA tag
        if kind == "truncated":
            bad = body[: r.randint(len(body) // 3, max(len(body) // 3 + 1, len(body) - 40))]
        elif kind == "not_x12":
            bad = "%PDF-1.4\n% scanned remittance image, not an EDI file\n"
        else:
            el = body.lstrip("\ufeff")[3]
            bad = body.replace("IEA" + el, "IEX" + el, 1)          # trailer damaged: IEA missing
        sent_at = f"{r.randint(0, 23):02d}{r.randint(0, 59):02d}{r.randint(0, 59):02d}"
        name = self._file_name(self.t, f["kind"], day, sent_at, f.get("payer_id") if f["kind"] == "835" else None)
        return dict(day=day, kind=f["kind"], name=name, body=bad, expect="rejected", corruption=kind)

    def run(self, out_dir: str | Path, workers: int = 1, return_claims: bool = True, truth_gzip: bool = False) -> dict:
        """Generate every tenant into out_dir. Each day's files are written as soon as the day is final, and the
        ledger streams to truth/<tenant>/claims.jsonl with a small truth.json index (see `load_truth`).

        Returns the ledger in the classic in-memory shape. Pass return_claims=False for big runs: a year of claims
        is several GB as Python dicts; read them back with `iter_claims` instead."""
        run = _Run(out_dir, self.sc, self.tenant_codes, workers=workers, truth_gzip=truth_gzip,
                   keep_claims=return_claims and workers <= 1, backpressure=False)
        for _ in run:
            pass
        if return_claims and workers > 1:
            return load_truth(out_dir)
        return run.truth


def _zip(p: Person) -> str:
    """Patient ZIPs: 9 digits on most claims, 5 on ~20% (spec quirks.837_content.zip5_for_patient)."""
    z = p.city[2]
    return z if _stable_unit(p.first, p.last, p.dob, "zip") < .2 else f"{z}{_stable_int(p.addr) % 9000 + 1000}"


def _finalize_claim(led: dict) -> dict:
    """Current state of one claim per payer, from the events actually delivered.

    Mirrors the business rule of the semantic layer, not its SQL: the latest event per (claim, payer) is current
    unless it is a reversal; denied = recorded (status 4) or signal v3 ($0 paid, not a reversal, CO with a reviewed
    CARC). A duplicate-claim denial (CARC 18 on an accidental resend) never replaces another adjudication: payers
    deny the resend and the original adjudication stands. It is current only when nothing else was received.
    `has_duplicate_denial` flags those claims.
    """
    led = {k: v for k, v in led.items() if k != "_claim"}
    for pid, a in led["adjudications"].items():
        evs = a["events"]
        real = [e for e in evs if not e["duplicate_denial"]] or evs
        cur = real[-1] if real else None
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
                 has_duplicate_denial=any(e["duplicate_denial"] for e in evs))
    prim = led["adjudications"].get(led["payers"][0])
    led.update(remitted=bool(led["adjudications"]), primary=prim and {k: prim[k] for k in (
        "remitted_current", "denied", "paid_current", "status_current")},
        resubmitted=any(s["frequency"] == "7" for s in led["submissions"]),
        voided=any(s["frequency"] == "8" for s in led["submissions"]))
    return led


def _derive_seed(*parts) -> int:
    """64-bit seed from labels; independent of PYTHONHASHSEED, process and worker layout."""
    return int.from_bytes(hashlib.blake2b("/".join(map(str, parts)).encode(), digest_size=8).digest(), "big")


def _creation_order(pcn: str) -> int:
    return int(pcn[8:])                                       # PCN = 2-letter tenant prefix + yymmdd + running number


# --- streaming run ---------------------------------------------------------------------------------
class _TenantRun(Generator):
    """One tenant's simulation, advanced one calendar day at a time.

    Every claim's life is planned the day it is submitted, and no event is ever scheduled for an earlier day, so
    once `step(d)` has planned day d's claims, day d is final. Only what later days need is kept: claims that
    still have a submission or remit to come, the events queued for later days, deductible accumulators for the
    plan years still being billed, and a bounded window of paid ICNs for recoupments. The tenant has its own
    random stream, so its output does not depend on which other tenants run, or where.

    The simulation starts `burn_in_days` before the window: claims billed then are adjudicated, paid, recouped and
    reworked as usual, but nothing is written until the window opens. Day 1 therefore already has a full 835 feed
    (remits and PLB recoupments for claims billed before the window), as a lab's data does when a feed is switched
    on. The ledger records only what is delivered inside the window: a claim billed in the burn-in appears with the
    835 events (and 837 resubmissions) the window received, and an empty or partial `submissions` list.
    """

    def __init__(self, sc: Scenario, tc: str, claims_per_day: float, keep_claims: bool = False):
        super().__init__(sc, [tc])
        self.rng = random.Random(_derive_seed(sc.seed, tc))
        self.tc, self.t, self.cpd = tc, TENANTS[tc], claims_per_day
        self.end = sc.start + timedelta(days=sc.days)
        self.first_day = sc.start - timedelta(days=sc.burn_in_days)
        self.emit = sc.burn_in_days <= 0                      # False while simulating the burn-in
        self.names_today: set = set()
        self.surname_cum = R.surname_table(self.t.region)
        self.segments = segments_for(self.t.region)
        self.seg_names = list(SEGMENT_SHARE)
        self.seg_cum = list(accumulate(SEGMENT_SHARE.values()))
        self.seg_payer_cum = {s: list(accumulate(p.weight for p in self.segments[s])) for s in self.seg_names}
        # patient pools sized from a year of claims, so repeat-visit rates do not depend on the run length
        annual = max(claims_per_day, 1) * 250
        self.pools: list[tuple] = []                          # (first pid, size, segment, repeater)
        nxt = 0
        for s in self.seg_names:
            for rep, visits in ((False, SEGMENT_VISITS_PER_YEAR[s]), (True, 24.0)):
                share = sc.p_repeat_patient_claims if rep else 1 - sc.p_repeat_patient_claims
                size = max(5 if rep else 20, int(annual * SEGMENT_SHARE[s] * share / visits))
                self.pools.append((nxt, size, s, rep))
                nxt += size
        self.pool_index = {(s, rep): (lo, n) for lo, n, s, rep in self.pools}
        self.pool_starts = [p[0] for p in self.pools]
        n_providers = max(30, round(claims_per_day / 4))
        self.providers = self._providers(self.t.npi_seed, n_providers)
        self.provider_cum = list(accumulate(1 / (i + 1) ** .8 for i in range(n_providers)))
        self.accum: dict = {}                                 # plan year -> {(pid, payer): deductible met here}
        self.outbox: dict[date, list] = {}                    # day -> 837 items
        self.pending: dict[date, dict] = {}                   # 835 day -> {payer_id: events}
        self.claims: dict[str, dict] = {}                     # ledger of claims still in flight
        self.open: dict[str, int] = {}                        # pcn -> deliveries still to come
        self.fwd: dict[str, float] = {}                       # payer -> forward balance owed back
        self.done: dict | None = {} if keep_claims else None
        self.n = 0
        self.noise = True                                     # day-to-day volume noise (off for calibration)
        self.seasonal = True                                  # monthly / respiratory / holiday-week indexes
        self._profile_cache: dict = {}
        self.waves = self._mass_adjustment_waves()

    # people
    def _patient(self, pid: int) -> Patient:
        """Patient `pid`, rebuilt on demand from its own seed: the pool costs no memory, and a patient is the same
        person (same sex, coverage, plan and known conditions) on every claim. The pid's pool fixes its segment."""
        sc, t = self.sc, self.t
        lo, _, seg, rep = self.pools[bisect.bisect_right(self.pool_starts, pid) - 1]
        r = self._prng
        base = self._pbase.get(self.tc) or self._pbase.setdefault(self.tc, _derive_seed(sc.seed, self.tc, "patient") << 40)
        r.seed(base + pid)
        payer = r.choices(self.segments[seg], cum_weights=self.seg_payer_cum[seg])[0]
        cls = payer.payer_class
        ref = date(2026, 7, 1)
        dependent = False
        if cls in ("medicare", "medicare_advantage"):
            ages = (65, 95) if r.random() < .9 else (30, 64)      # ~10% are under-65 disability beneficiaries
        elif cls in ("medicaid", "medicaid_mco"):
            ages = (1, 26) if r.random() < .5 else (19, 64)      # >= 1 year: born before any service date
        else:
            dependent = r.random() < sc.p_dependent               # a child (under 26) or a spouse on the policy
            ages = ((1, 25) if r.random() < .6 else (26, 64)) if dependent else (22, 64)
        sex = "F" if r.random() < (.58 if ages[0] >= 18 else .5) else "M"   # women are most adult lab patients
        pat = self._person(r, t.region, ages, ref, sex)
        age = (ref - pat.dob).days / 365.25
        if age >= 18 and r.random() < P_GENDER_MARKER_DIFFERS:
            pat.gender = "M" if sex == "F" else "F"               # insurance gender marker differs from the tests' sex
        if dependent:
            rel = "19" if age < 26 else "01"
            sub_sex = (("M" if sex == "F" else "F") if r.random() < .92 else sex) if rel == "01" else r.choice("FM")
            sub_ages = (age + 22, age + 45) if rel == "19" else (max(age - 5, 26), age + 5)
            sub = self._person(r, t.region, sub_ages, ref, sub_sex, surname=pat.last, home=pat)
        else:
            sub, rel = pat, "18"
        group = "" if cls == "medicare" else f"{r.randint(10**5, 10**6 - 1)}"
        covs = [Coverage(payer, member_id(payer.member_id_style, payer, r), group, sub, rel)]
        kind = ("anticoag" if age >= 30 and r.random() < .5 else "drug") if rep else ""
        p = Patient(pid, pat, covs, seg, rep, sex=sex, conditions=self._conditions(r, sex, age, kind),
                    deductible_pace=r.uniform(.15, 1.7))
        p.repeater_kind = kind
        if cls in ("commercial", "blues", "other"):
            plan = _PLAN_NAMES[bisect.bisect_left(_PLAN_CUM, r.random() * _PLAN_CUM[-1])]
            _, (dlo, dhi), coins, p_coins, copay, p_lab_deductible = COMMERCIAL_PLANS[plan]
            p.deductible = round(r.uniform(dlo, dhi) / 50) * 50
            p.lab_deductible = r.random() < p_lab_deductible
            p.coinsurance = coins if r.random() < p_coins else 0.0
            p.copay = copay if r.random() < .3 else 0.0
            if r.random() < sc.p_secondary_coverage:
                sseg = _pick(r, SECONDARY_SEGMENTS)
                options = [q for q in self.segments[sseg] if q.payer_id != payer.payer_id]
                sp = r.choices(options, weights=[q.weight for q in options])[0]
                covs.append(Coverage(sp, member_id(sp.member_id_style, sp, r), f"{r.randint(10**5, 10**6 - 1)}", pat, "18"))
        elif cls == "medicare_advantage":
            p.copay = 0.0 if r.random() < .85 else round(_tri(r, 5, 10, 40) / 5) * 5
        elif cls == "medicare" and r.random() < sc.p_medicare_crossover:
            p.crossover = r.choice(MEDIGAP_CARRIERS)
        return p

    @staticmethod
    def _conditions(r: random.Random, sex: str, age: float, repeater_kind: str) -> tuple:
        """The patient's known chronic conditions (the codes their physician lists visit after visit)."""
        band = 0 if age < 18 else 1 if age < 45 else 2 if age < 65 else 3
        out = []
        for domain, prevalence in R.CHRONIC.items():
            p = prevalence[band] * (R.CHRONIC_FEMALE_MULT.get(domain, 1.0) if sex == "F" else 1.0)
            if r.random() < p:
                code = R.SAMPLERS[domain].pick(r, sex, age)
                if code and code not in out:
                    out.append(code)
        if repeater_kind == "anticoag":
            out += [c for c in ("Z7901", R.SAMPLERS["anticoag"].pick(r, sex, age)) if c and c not in out]
        elif repeater_kind == "drug":
            out += [c for c in (R.SAMPLERS["drug"].pick(r, sex, age), "Z79891") if c and c not in out]
        return tuple(out)

    # claims
    def _profile(self, cls: str, month: int, pat: Patient, age: float, r: random.Random) -> str:
        if pat.repeater:
            return "drug_monitoring" if pat.repeater_kind == "drug" else "routine_blood"
        key = (cls, month if self.seasonal else 0, pat.sex, min(int(age), 100))
        if key not in self._profile_cache:
            mult = PROFILE_CLASS_MULT.get(cls, {})
            names = list(PROFILES)
            w = []
            for n in names:
                prof = PROFILES[n]
                lo, hi = prof.get("ages", (0, 200))
                ok = prof.get("sex", pat.sex) == pat.sex and lo <= age <= hi
                w.append(prof["weight"] * mult.get(n, 1.0) * ok *
                         (PROFILES_FOR_CHILDREN.get(n, 0.0) if age < 13 else 1.0) *
                         (RESPIRATORY_MONTH_INDEX[month] if self.seasonal and prof.get("seasonal") else 1.0))
            self._profile_cache[key] = (names, list(accumulate(w)))
        names, cum = self._profile_cache[key]
        return r.choices(names, cum_weights=cum)[0]

    def _codes(self, profile: str, cls: str, pat: Patient, age: float, r: random.Random) -> list[str]:
        prof = PROFILES[profile]
        if "combine" in prof:
            out = []
            for sub in prof["combine"]:
                out += [c for c in self._codes(sub, cls, pat, age, r) if c not in out]
            return out
        sex = pat.sex
        menu = prof.get(f"menu_{sex}") or prof.get("menu", {})
        codes = [code for code, p in menu.items() if r.random() < p]
        if "pick_one" in prof:
            codes.append(_pick(r, prof["pick_one"]))
        if "pick_one_optional" in prof:
            opt = prof["pick_one_optional"]
            u, cum = r.random(), 0.0
            for code, p in opt.items():
                cum += p
                if u < cum:
                    codes.append(code)
                    break
        if pat.repeater and profile == "routine_blood" and "85610" not in codes:
            codes.append("85610")                                 # anticoagulation monitoring
        for code, p in prof.get("reflex", {}).items():
            if "87086" in codes and r.random() < p:
                codes.append(code)
        if prof.get("tail"):
            for _ in range(_poisson(r, TAIL_LINES_BY_CLASS[cls])):
                if r.random() < DEEP_TAIL_SHARE:
                    code = r.choices(DEEP_TAIL, cum_weights=_DEEP_TAIL_CUM)[0]
                else:
                    code = r.choices(TAIL, cum_weights=_TAIL_CUM)[0]
                if code not in codes:
                    codes.append(code)
            if age < 6 and r.random() < .35:
                codes.append("83655")                             # blood lead screening (Medicaid EPSDT)
        if prof.get("urine_tail"):
            for _ in range(_poisson(r, .5)):
                code = r.choices(URINE_TAIL, cum_weights=_URINE_TAIL_CUM)[0]
                if code not in codes:
                    codes.append(code)
        codes = [c for c in dict.fromkeys(codes) if R.cpt_fits(c, sex, age)]
        if not codes:
            choices = menu or prof.get("pick_one") or {}
            codes = [c for c in sorted(choices, key=choices.get, reverse=True) if R.cpt_fits(c, sex, age)][:1] or ["85025"]
        if (prof.get("draw") or set(codes) & set(prof.get("draw_if", ()))) and r.random() < P_VENIPUNCTURE_BILLED:
            codes.insert(0, "36415")
        return codes

    @staticmethod
    def _scrub(codes: list, r: random.Random) -> tuple[list, str | None]:
        """The lab's claim scrubber: drop NCCI panel components billed with their panel. Now and then one slips
        through (a manual override, an edit-table lag); the payer then denies it (CO-97 / CO-236)."""
        conflicts = R.ncci_conflicts(codes)
        if not conflicts:
            return codes, None
        conflicts.sort()                                          # deterministic order (codes come from a set)
        keep = conflicts[0][0] if r.random() < P_NCCI_RESIDUE_GIVEN_CONFLICT else None
        drop = {comp for comp, _ in conflicts} - {keep}
        return [c for c in codes if c not in drop], keep

    def _diagnose(self, codes: list, pat: Patient, age: float, profile: str, screening: bool,
                  r: random.Random) -> tuple[list, dict]:
        """ICD-10-CM codes for the requisition and each test's pointers. Every test points at a diagnosis that
        justifies it: one of the patient's known conditions when one fits, else a code from the test's clinical
        domains (sex- and age-appropriate). A screening order lists the screening code first."""
        sex, dx = pat.sex, []

        def add(code: str) -> int | None:
            if code in dx:
                return dx.index(code) + 1
            if len(dx) >= MAX_DX:
                return None
            dx.append(code)
            return len(dx)

        tests = [c for c in codes if c != "36415"]
        reason: dict = {}
        if screening:
            if profile == "colorectal_screening":
                principal = "Z1211"
            elif profile == "sti_panel":
                principal = "Z113"
            elif age < 18:
                principal = "Z00129" if r.random() < .85 else "Z00121"
            else:
                principal = "Z0000" if r.random() < .85 else "Z0001"
            first = add(principal)
            for code in tests:
                if code not in R.SCREENING_ROUTINE_TESTS:
                    continue
                specific = [d for d in R.SCREENING_BY_CPT.get(code, ()) if R.DX_BY_CODE[d].fits(sex, age)]
                if specific and (r.random() < .55 or not R.dx_fits_cpt(principal, code)):
                    reason[code] = add(r.choice(specific))
                elif R.dx_fits_cpt(principal, code):
                    reason[code] = first
        for code in tests:
            if code not in reason:
                listed = [d for d in dx if R.dx_fits_cpt(d, code) and d not in SCREENING_DX]
                fitting = [d for d in pat.conditions if R.dx_fits_cpt(d, code)]
                if listed and r.random() < P_LISTED_DX_REUSED:
                    chosen = r.choice(listed)                     # one diagnosis covers several tests
                elif fitting and r.random() < P_PRIOR_DX_REUSED:
                    chosen = r.choice(fitting)
                else:
                    chosen = R.pick_dx_for_cpt(r, code, sex, age)
                idx = add(chosen)
                if idx is None:                                   # HI is full: reuse a listed code that fits
                    idx = next((i + 1 for i, d in enumerate(dx) if R.dx_fits_cpt(d, code)), 1)
                reason[code] = idx
        for d in pat.conditions:                                  # the rest of the problem list, sometimes
            if d not in dx and r.random() < DX_EXTRA_CHRONIC:
                add(d)
        pointers = {}
        for code in tests:
            ptr = [reason[code]]
            for i, d in enumerate(dx, 1):
                if len(ptr) < 4 and i not in ptr and R.dx_fits_cpt(d, code) and r.random() < .3:
                    ptr.append(i)
            pointers[code] = tuple(str(i) for i in ptr)
        if "36415" in codes:
            pointers["36415"] = pointers[tests[0]] if tests else ("1",)
        return dx or ["R6889"], pointers

    def _lines(self, codes: list, pointers: dict, profile: str, r: random.Random) -> list[Line]:
        t = self.t
        lines = []
        for code in codes:
            cpt = CPTS[code]
            units = 1
            if cpt.units:
                lo, mode, hi = (1, 2, 4) if (code == "87798" and profile == "respiratory") else cpt.units
                units = max(1, round(_tri(r, lo, mode, hi)))
            mods, factor = [], 1.0
            if cpt.category == "pathology":
                u = r.random()
                if u < .3:
                    mods, factor = ["26"], .45
                elif u < .4:
                    mods, factor = ["TC"], .55
            if code in CLIA_WAIVED and r.random() < .02:
                mods.append("QW")
            if code in ("87077", "87186", "87088") and units > 1 and r.random() < .3:
                mods.append("59" if r.random() < .7 else "XU")      # a second isolate: distinct specimen
            if r.random() < .01:
                mods.append("90")                                    # sent out to a reference laboratory
            price = _chargemaster(t.code, code, t.price_mult)
            lines.append(Line(code, units, round(price * units * factor, 2), tuple(mods[:4]), factor,
                              pointers.get(code, ("1",))))
        if r.random() < .005:                                       # repeat test the same day (modifier 91)
            chem = [l for l in lines if CPTS[l.cpt].category == "chemistry" and l.cpt not in R.NCCI_COMPONENT_OF]
            if chem:
                l = r.choice(chem)
                lines.append(replace(l, modifiers=("91",)))
        return lines

    def _line_edits(self, c: Claim, payer: Payer, ncci_residue: str | None, r: random.Random) -> dict:
        """Deterministic payer edits on the primary claim, decided when the claim is billed:
        NCCI residue (a component billed with its panel) -> CO-97 or CO-236; Medicare coverage of screening-only
        orders (NCD dx edits; the ABN modifiers GA / GZ / GY set who is liable); sex edits on patients whose
        insurance gender marker differs from the sex the test applies to (Medicare: modifier KX, paid)."""
        edits, cls = {}, payer.payer_class
        for i, l in enumerate(c.lines):
            if l.cpt == ncci_residue and l.modifiers[:1] != ("91",):
                government = cls in ("medicare", "medicare_advantage", "medicaid", "medicaid_mco")
                edits[i] = (("CO", "236", "N20") if government and r.random() < .5 else
                            ("CO", "97", r.choice(["M80", "N20"]))) + ("bundling",)
        if cls in ("medicare", "medicare_advantage"):
            for i, l in enumerate(c.lines):
                if l.cpt == "36415" or i in edits:
                    continue
                pointed = [c.dx[int(p) - 1] for p in l.pointers]
                if any(d not in SCREENING_DX for d in pointed) or set(pointed) & MEDICARE_SCREENING_BENEFIT.get(l.cpt, set()):
                    continue                                      # a diagnosis supports it, or a screening benefit
                routine = bool(set(pointed) & {"Z0000", "Z0001"})
                ncd = l.cpt in NCD_LIMITED
                if cls == "medicare_advantage":
                    if r.random() < .1:                           # MA plans apply the NCDs, less strictly
                        edits[i] = ("CO", "50", "N386" if ncd else None, "medical_necessity")
                    continue
                u = r.random()
                if routine and u < .35:                           # routine physical: statutorily excluded
                    modifier, edit = "GY", ("PR", r.choice(["204", "96"]), "N425", None)
                elif u < .82:                                     # ABN signed at the patient service center
                    modifier, edit = "GA", ("PR", "50", "M38", None)
                elif u < .91:
                    modifier, edit = "GZ", ("CO", "50", "N386" if ncd else None, "medical_necessity")
                else:                                             # the scrubber missed it
                    modifier, edit = None, ("CO", "50", "N386" if ncd else None, "medical_necessity")
                if modifier:
                    c.lines[i] = replace(l, modifiers=(l.modifiers + (modifier,))[:4])
                edits[i] = edit
        gender = c.patient.person.gender
        if gender != c.patient.sex:
            for i, l in enumerate(c.lines):
                sex_specific = R.CPT_SEX.get(l.cpt) or next(
                    (R.DX_BY_CODE[d].sex for d in (c.dx[int(p) - 1] for p in l.pointers)
                     if d in R.DX_BY_CODE and R.DX_BY_CODE[d].sex), "")
                if not sex_specific or i in edits:
                    continue
                if cls in ("medicare", "medicare_advantage") or r.random() < .5:
                    c.lines[i] = replace(l, modifiers=(l.modifiers + ("KX",))[:4])   # gender-specific service
                elif R.CPT_SEX.get(l.cpt):
                    edits[i] = ("CO", "16", "MA39", "sex_edit")
                else:
                    edits[i] = ("CO", "11", "M76", "sex_edit")
        return edits

    def _dos(self, d: date, profile: str, r: random.Random) -> date:
        slow = profile in MOLECULAR_PROFILES or profile in PATHOLOGY_PROFILES
        for _ in range(8):                                          # weight service dates by weekday (few weekends)
            lag = round(_tri(r, 5, 14, 45) if slow else _tri(r, 1, 2, 7))
            if r.random() < .05:
                lag += 10                                           # held for missing information
            dos = d - timedelta(days=max(1, lag))
            share = DOS_WEEKDAY_SHARE[dos.weekday()] / .20
            if dos in federal_holidays(dos.year):
                share *= DOS_HOLIDAY_FACTOR
            if r.random() < share:
                break
        if r.random() < self.sc.p_late_submission:
            dos = d - timedelta(days=r.randint(60, 400))
        return dos

    def _new_claim(self, d: date) -> Claim:
        r, sc, t = self.rng, self.sc, self.t
        self.n += 1
        seg = self.seg_names[bisect.bisect_left(self.seg_cum, r.random() * self.seg_cum[-1])]
        rep = r.random() < sc.p_repeat_patient_claims
        lo, size = self.pool_index[(seg, rep)]
        pat = self._patient(lo + r.randrange(size))
        payer = pat.coverages[0].payer
        medicare_like = payer.payer_class in ("medicare", "medicare_advantage")
        age = pat.age(d)
        profile = self._profile(payer.payer_class, d.month, pat, age, r)
        key = PROFILES[profile]["combine"][0] if "combine" in PROFILES[profile] else profile
        screening = r.random() < SCREENING_SHARE.get(key, 0) * (
            MEDICARE_SCREENING_FACTOR if medicare_like and key != "colorectal_screening" else 1)
        codes = self._codes(profile, payer.payer_class, pat, age, r)
        if medicare_like:                                         # Medicare's own screening codes
            codes = [("G0103" if c == "84153" and screening else "G0328" if c == "82274" else c) for c in codes]
        codes, residue = self._scrub(codes, r)
        dx, pointers = self._diagnose(codes, pat, age, key, screening, r)
        lines = self._lines(codes, pointers, profile, r)
        pos = _pick(r, {"81": .90, "31": .04, "12": .03, "11": .02, "22": .01} if age >= 65 else
                    {"81": .94, "11": .03, "22": .02, "12": .01})          # SNF (31) and home draws (12): elderly
        pa = None
        if r.random() < (.6 if profile in MOLECULAR_PROFILES else .005):
            pa = f"PA{r.randint(10**8, 10**9 - 1)}"
        pcn = f"{t.pcn_prefix}{d:%y%m%d}{self.n:0{PCN_DIGITS}d}"
        prov = self.providers[bisect.bisect_left(self.provider_cum, r.random() * self.provider_cum[-1])]
        c = Claim(pcn, t.code, pat, prov, self._dos(d, profile, r), lines, dx, d, profile, pos, pa,
                  f"{CLEARINGHOUSE_ID[:4]}{d:%y%m%d}{self.n:07d}" if r.random() < .5 else None,
                  r.random() < .25, r.random() < .15, _stable_unit(pcn, "6r") < .85)
        c.line_edits = self._line_edits(c, payer, residue, r)
        return c

    def _ledger_claim(self, c: Claim) -> dict:
        return dict(pcn=c.pcn, dos=str(c.dos), submitted=str(c.submitted), order_type=c.order_type,
                    cpts=[l.cpt for l in c.lines], units=[l.units for l in c.lines],
                    modifiers=[list(l.modifiers) for l in c.lines],
                    line_charges=[l.charge for l in c.lines], charge=round(sum(l.charge for l in c.lines), 2),
                    dx=c.dx, principal_dx=c.dx[0], line_dx_pointers=[list(l.pointers) for l in c.lines],
                    referring_npi=c.referring.npi, risky_provider=c.referring.risky,
                    dependent=c.patient.coverages[0].relationship != "18", segment=c.patient.segment,
                    payer_class=c.patient.coverages[0].payer.payer_class, place_of_service=c.pos,
                    prior_auth=bool(c.prior_auth), patient_id=c.patient.pid,
                    payers=[cv.payer.payer_id for cv in c.patient.coverages], submissions=[], adjudications={})

    # timing
    def _adjudication_day(self, payer: Payer, received: date, c: Claim) -> date:
        r = self.rng
        lo, mode, hi = payer.lag                          # most claims near the typical lag, a long right tail
        lag = _tri(r, lo, mode, mode + (hi - mode) / 4) if r.random() < .8 else _tri(r, mode, mode, hi)
        if r.random() < (self.sc.p_pended_tail * (.4 if payer.is_medicare else 1)):
            lag = r.uniform(31, 60) if payer.is_medicare else r.uniform(60, 120)   # pended / development letters
            c.interest_exempt = r.random() >= P_PENDED_CLEAN          # not a clean claim: no prompt-pay interest
        if payer.is_medicare:
            lag = max(lag, 14)                                  # 13-day payment floor for clean electronic claims
        return received + timedelta(days=round(lag))

    def _mass_adjustment_waves(self) -> list[tuple]:
        """MAC edit errors (e.g. Noridian's CO-B7 CLIA denials in 2024 and 2025): for a week the MAC denies a share of
        claims in error, then reverses and repays them all in one mass adjustment weeks later."""
        out = []
        for year in range(self.first_day.year, self.end.year + 1):
            for i in range(self.sc.mac_mass_adjustment_waves_per_year):
                wr = random.Random(_derive_seed(self.sc.seed, self.tc, "wave", year, i))
                start = date(year, 1, 1) + timedelta(days=wr.randrange(358))
                out.append((start, start + timedelta(days=7), start + timedelta(days=7 + wr.randint(20, 45))))
        return out

    def _schedule(self, e: dict, day: date) -> date | None:
        """Queue an 835 event for the payer's next cycle on or after `day`. Returns the payment (EFT) date, or None
        when the 835 would fall after the window (never delivered)."""
        payer = PAYER_BY_ID[e["payer_id"]]
        remit = self._remit_day(payer, day)
        if remit >= self.end:
            return None
        if e["status"] == "19":                               # COBA crossover: NM1*TT + MOA MA18
            e["crossover"] = e["claim"].patient.crossover
            e["moa"] = e["moa"] + ["MA18"]
        # DTM*050: the payer received the claim a business day after it was sent
        e["received"] = e.get("received") or self._next_business_day(e["claim"].submitted + timedelta(days=1))
        self.pending.setdefault(remit, {}).setdefault(payer.payer_id, []).append(e)
        self.open[e["pcn"]] += 1
        return self._pay_date(payer, remit)

    def _bill(self, day: date, item: tuple):
        if day >= self.end:
            return
        self.outbox.setdefault(day, []).append(item)
        self.open[item[0].pcn] += 1

    # the life of a claim
    def _plan(self, c: Claim, d: date):
        """Decide this claim's whole life now; 835 events and 837 resubmissions are queued for later days."""
        sc, r, accum = self.sc, self.rng, self.accum
        cov = c.patient.coverages[0]
        payer = cov.payer
        if not payer.era or r.random() < sc.p_never_remitted:
            return
        received = self._next_business_day(d + timedelta(days=1))
        adj = self._adjudication_day(payer, received, c)

        def add(e, day, status, **kw):
            e = dict(e, status=status, claim=c, **kw)
            return e, self._schedule(e, day)

        def full_status():
            return "1" if r.random() < sc.p_signal_v3_vs_recorded else "4"

        if (c.submitted - c.dos).days > payer.timely_filing_days:
            den = self._fixed_denial(c, payer, "timely_filing", "29", "N211")
            add(self._adjudicate(c, 0, "denied", accum, adj, denial=den), adj, full_status())
            return
        if payer.is_medicare and r.random() < MASS_ADJUSTMENT_SHARE and any(s <= adj < e for s, e, _ in self.waves):
            fix_day = next(f for s, e, f in self.waves if s <= adj < e)
            den = self._fixed_denial(c, payer, "clia", "B7", "MA120")
            e, _ = add(self._adjudicate(c, 0, "denied", accum, adj, denial=den), adj, "1")
            fix_day = max(fix_day, adj + timedelta(days=1))
            add(self._reverse(e), fix_day, "22")
            add(self._adjudicate(c, 0, "paid", accum, fix_day, salt="mass"), fix_day, self._paid_status(c))
            return
        u, cum, flow = r.random(), 0.0, None
        for name, p in (("rev", sc.p_reversal_no_correction), ("revcor", sc.p_reversal_correction),
                        ("void", sc.p_void), ("zero", sc.p_zero_pay_fee_schedule)):
            cum += p
            if u < cum:
                flow = name
                break
        if flow == "rev":
            e, pay = add(self._adjudicate(c, 0, "paid", accum, adj), adj, self._paid_status(c))
            add(self._reverse(e), adj + timedelta(days=r.randint(5, 60)), "22")
            return
        if flow == "revcor":
            later = adj + timedelta(days=r.randint(10, 45))
            if r.random() < .5:                                  # denial reprocessed in the lab's favour
                den = self._denial(c, payer, self._denial_category(c, payer))
                e, _ = add(self._adjudicate(c, 0, "denied" if den["full"] else "partial", accum, adj, denial=den), adj,
                           full_status() if den["full"] else self._paid_status(c))
                add(self._reverse(e), later, "22")
                add(self._adjudicate(c, 0, "paid", accum, later, salt="cor"), later, self._paid_status(c))
                return
            e, _ = add(self._adjudicate(c, 0, "paid", accum, adj), adj, self._paid_status(c))   # repriced payment
            cor = self._adjudicate(c, 0, "paid", accum, later, allowed_scale=r.choice([.9, .95, 1.05, 1.1]), salt="cor")
            if r.random() < .3:
                cor["icn"] = e["icn"]                             # ICN reused on the adjustment
            rev = self._reverse(e)
            if e["paid"] > 0 and r.random() < sc.p_split_reversal_correction:
                ref = self._plb_reference(payer, dict(e, claim=c))
                rev["plb"] = [("WO", ref, -e["paid"], c.pcn)]     # reversal 835: negative WO keeps the check whole
                cor["plb"] = [("WO", ref, e["paid"], c.pcn)]      # correction 835: positive WO recoups it
                add(rev, later, "22")
                add(cor, later + timedelta(days=7), self._paid_status(c))
            else:
                add(rev, later, "22")
                add(cor, later, self._paid_status(c))
            return
        if flow == "void":
            e, pay = add(self._adjudicate(c, 0, "paid", accum, adj), adj, self._paid_status(c))
            if pay is None:
                return
            void_day = self._next_business_day(pay + timedelta(days=r.randint(3, 10)))
            self._bill(void_day, (c, 0, "8", None, e["icn"]))
            rv = self._reverse(e)
            rv["freq"] = "8"
            add(rv, void_day + timedelta(days=r.randint(3, 12)), "22")
            return
        if flow == "zero":
            add(self._adjudicate(c, 0, "zero_fee", accum, adj), adj, self._paid_status(c))
            return
        dup_first = r.random() < sc.p_duplicate_submission and r.random() < .5
        if dup_first:                                            # resent while pending: denied before the original pays
            self._duplicate(c, payer, d, adj, before=True)
        if r.random() < self._p_denial(c, payer):
            den = self._denial(c, payer, self._denial_category(c, payer))
            status = full_status() if den["full"] else self._paid_status(c)
            e, pay = add(self._adjudicate(c, 0, "denied" if den["full"] else "partial", accum, adj, denial=den),
                         adj, status)
            if pay is not None:
                self._maybe_rework(c, payer, e, den, pay)
                if not den["full"]:
                    self._maybe_secondary(c, e, pay)
            return
        e, pay = add(self._adjudicate(c, 0, "paid", accum, adj), adj, self._paid_status(c))
        if pay is None:
            return
        if not dup_first and r.random() < sc.p_duplicate_submission:
            self._duplicate(c, payer, d, pay, before=False)
        self._maybe_secondary(c, e, pay)

    def _paid_status(self, c: Claim) -> str:
        return "19" if c.patient.crossover else "1"

    def _duplicate(self, c: Claim, payer: Payer, d: date, around: date, before: bool):
        """An accidental resend of the same claim: the payer denies the copy as an exact duplicate (CO or OA 18).
        before=True: resent while the original was pending, denied before it pays. Otherwise resent after the
        payment went unposted; that denial is then the latest event, i.e. the current adjudication."""
        r = self.rng
        if before:
            resend = self._next_business_day(d + timedelta(days=r.randint(2, 6)))
            deny_day = resend + timedelta(days=r.randint(3, 8))
            if deny_day >= around - timedelta(days=7):
                return
        else:
            resend = self._next_business_day(around + timedelta(days=r.randint(5, 30)))
            deny_day = resend + timedelta(days=r.randint(5, 15))
        self._bill(resend, (c, 0, "1", None, None))
        den = self._fixed_denial(c, payer, "duplicate", "18", _pick(r, {"N111": .3, "N522": .2, "": .5}) or None,
                                 group="CO" if r.random() < .6 else "OA")
        dup = self._adjudicate(c, 0, "denied", self.accum, deny_day, denial=den, salt=f"dup{resend}")
        dup.update(received=self._next_business_day(resend + timedelta(days=1)), duplicate_denial=True)
        dup = dict(dup, status="4" if r.random() >= self.sc.p_signal_v3_vs_recorded else "1", claim=c)
        self._schedule(dup, deny_day)

    def _maybe_rework(self, c: Claim, payer: Payer, e: dict, den: dict, pay: date):
        """The lab works the denial: a corrected claim (freq 7 with REF*F8; Medicare wants a new freq-1 claim)."""
        r = self.rng
        p = self.sc.p_rework_molecular if self._claim_class(c) == "molecular" else self.sc.p_rework_routine
        if den["category"] not in REWORK_SUCCESS or r.random() >= p:
            return
        rs = self._next_business_day(pay + timedelta(days=round(_tri(r, 2, 12, 60))))
        freq = "1" if payer.is_medicare else "7"
        self._bill(rs, (c, 0, freq, None, e["icn"] if freq == "7" else None))
        received = self._next_business_day(rs + timedelta(days=1))
        adj = self._adjudication_day(payer, received, c)
        if r.random() < REWORK_SUCCESS[den["category"]]:
            fixed = self._adjudicate(c, 0, "paid", self.accum, adj, salt="rework")
            status = self._paid_status(c)
        else:
            fixed = self._adjudicate(c, 0, "denied" if den["full"] else "partial", self.accum, adj, denial=den,
                                     salt="rework")
            status = e["status"]
        fixed.update(received=received, freq=freq)
        self._schedule(dict(fixed, status=status, claim=c), adj)

    def _maybe_secondary(self, c: Claim, primary_event: dict, pay: date):
        if len(c.patient.coverages) < 2 or primary_event["pr"] <= 0:
            return
        r = self.rng
        sec_payer = c.patient.coverages[1].payer
        if not sec_payer.era:
            return
        bill_day = self._next_business_day(pay + timedelta(days=r.randint(2, 6)))
        prior = dict(paid=primary_event["paid"], date=pay, claim_cas=primary_event["claim_cas"],
                     lines=[dict(paid=ln["paid"], cas=ln["cas"]) for ln in primary_event["lines"]])
        self._bill(bill_day, (c, 1, "1", prior, None))
        received = self._next_business_day(bill_day + timedelta(days=1))
        e = self._adjudicate(c, 1, "secondary", self.accum, received, prior)
        e["received"] = received
        self._schedule(dict(e, status="2", claim=c), self._adjudication_day(sec_payer, received, c))

    # one day
    def _claims_today(self, d: date) -> int:
        if not self._is_business_day(d):
            return 0
        factor = WEEKDAY_INDEX_837[d.weekday()]
        if self.seasonal:
            factor *= ROUTINE_MONTH_INDEX[d.month] * (1 + .06 * (RESPIRATORY_MONTH_INDEX[d.month] - 1))
            prev = d - timedelta(days=1)
            while prev.weekday() >= 5:
                prev -= timedelta(days=1)
            if prev in federal_holidays(prev.year):
                factor *= DAY_AFTER_HOLIDAY_INDEX                 # the backlog from the holiday
            week = {d - timedelta(days=d.weekday()) + timedelta(days=k) for k in range(7)}
            hol = federal_holidays(d.year)
            if any(h in week and h.month in (11, 12) and h.day > 20 for h in hol):
                factor *= HOLIDAY_WEEK_INDEX                      # Thanksgiving and Christmas weeks
        if self.noise:
            factor *= min(1.4, max(.6, self.rng.gauss(1.0, .08)))
        return max(1, round(self.cpd * factor))

    def step(self, d: date) -> tuple[list[dict], list[dict]]:
        """Plan day d's new claims, then deliver day d. Returns (files with bodies, ledger rows of claims that
        have nothing left to deliver)."""
        sc, r, t = self.sc, self.rng, self.t
        self.names_today = set()
        n_new = self._claims_today(d)
        if n_new:
            stale = (d - timedelta(days=60)).year             # service dates trail submission by <= ~55 days
            for y in [y for y in self.accum if y < stale]:
                del self.accum[y]
            for _ in range(n_new):
                c = self._new_claim(d)
                self.claims[c.pcn] = self._ledger_claim(c)
                self.open[c.pcn] = 0
                self._bill(d, (c, 0, "1", None, None))
                self._plan(c, d)

        files: list[dict] = []
        touched: list[str] = []
        items = self.outbox.pop(d, None)
        if items:
            for c, seq, freq, prior, orig in items:
                if self.emit:
                    self.claims[c.pcn]["submissions"].append(dict(
                        date=str(d), payer_id=c.patient.coverages[seq].payer.payer_id, seq=seq, frequency=freq))
                touched.append(c.pcn)
            for b, (chunk, sent_at) in enumerate(self._batches_837(items), 1):
                if self.emit:
                    files.append(dict(day=d, kind="837", name=self._file_name(t, "837", d, sent_at),
                                      body=self._noise(self._file_837(t, d, chunk, b, sent_at)), expect="parsed"))
                else:
                    self._next_ctl(t.sender_id, CLEARINGHOUSE_ID)
        remits = []
        for payer_id, evs in self.pending.pop(d, {}).items():
            remits += self._files_835(t, PAYER_BY_ID[payer_id], d, evs, self.fwd, self.claims)
            touched += [e["pcn"] for e in evs]
        if self.emit:
            files += self._concatenate(remits, d)
            if files and r.random() < sc.p_corrupt_file:
                files.append(self._corrupt(r.choice(files), d))
        done = []
        for pcn in touched:
            k = self.open[pcn] - 1
            if k:
                self.open[pcn] = k
            else:
                del self.open[pcn]
                led = self._close(pcn)
                if led is not None:
                    done.append(led)
        return files, done

    def burn_in(self):
        """Simulate the days before the window without writing anything (see the class docstring)."""
        d = self.first_day
        while d < self.sc.start:
            self.step(d)
            d += timedelta(days=1)
        self.emit = True

    def _batches_837(self, items: list) -> list[tuple[list, str]]:
        """The lab's billing system releases claims in a few scheduled runs a day, of uneven size (at most
        MAX_CLAIMS_PER_837_FILE claims per file, under the TR3's 5,000 per transaction)."""
        r, n = self.rng, len(items)
        k = max(-(-n // MAX_CLAIMS_PER_837_FILE), min(n, r.choice(BATCH_RUNS_PER_DAY)))
        while True:
            cuts = sorted(r.sample(range(1, n), k - 1)) if k > 1 else []
            bounds = [0] + cuts + [n]
            if max(b - a for a, b in zip(bounds, bounds[1:])) <= MAX_CLAIMS_PER_837_FILE:
                break
        runs = sorted(r.sample(BATCH_RUN_TIMES, k)) if k <= len(BATCH_RUN_TIMES) else \
            sorted(r.choice(BATCH_RUN_TIMES) for _ in range(k))
        out = []
        for (a, b), hhmm in zip(zip(bounds, bounds[1:]), runs):
            minute = int(hhmm[:2]) * 60 + int(hhmm[2:]) + r.randint(0, 14)
            out.append((items[a:b], f"{minute // 60 % 24:02d}{minute % 60:02d}{r.randint(0, 59):02d}"))
        return out

    def _close(self, pcn: str) -> dict | None:
        """The finalized ledger row, or None for a burn-in claim the window never saw (nothing was delivered)."""
        led = self.claims.pop(pcn)
        if not led["submissions"] and not led["adjudications"]:
            return None
        led = _finalize_claim(led)
        if self.done is not None:
            self.done[pcn] = led
        return led

    def finish(self) -> list[dict]:
        """Claims still open at the end of the window (their later deliveries fall outside it)."""
        out = [led for led in (self._close(pcn) for pcn in list(self.claims)) if led is not None]
        self.open.clear()
        self.outbox.clear()
        self.pending.clear()
        return out


MASS_ADJUSTMENT_SHARE = .15                                   # Medicare claims hit by an erroneous MAC edit in a wave
DELIVERY_NOISE = {"wrapped_80": .02, "utf8_bom": .01, "nul_padding": .005, "multi_isa": .05, "isa13_reused": .01}
# Medicare: tests limited by a national (or local) coverage determination; a screening-only order fails them.
NCD_LIMITED = frozenset("85025 85027 84443 84439 84436 84480 84481 80061 82465 83718 84478 83721 83036 82947 82950 "
                        "82951 85610 82728 83540 83550 84466 84153 84154 84152 82306 82607 82746".split())
# Medicare screening benefits (covered without symptoms): cardiovascular screening blood tests (Z13.6, every 5
# years), diabetes screening glucose, screening PSA (G0103), FIT (G0328), STI / HIV / hepatitis C screening.
MEDICARE_SCREENING_BENEFIT = {"80061": {"Z136"}, "82465": {"Z136"}, "83718": {"Z136"}, "84478": {"Z136"},
                              "82947": {"Z131"}, "G0103": {"Z125"}, "G0328": {"Z1211"}, "87491": {"Z113"},
                              "87591": {"Z113"}, "86592": {"Z113"}, "87389": {"Z114"}, "86803": {"Z1159"}}
COVERAGE_POLICY = {"85025": "190.15", "85027": "190.15", "82947": "190.20", "84436": "190.22", "84480": "190.22",
                   "84481": "190.22", "82465": "190.23", "83718": "190.23", "84478": "190.23", "83721": "190.23",
                   "84466": "190.18", "83550": "190.18", "84154": "190.31", "84152": "190.31",
                   "80061": "190.23", "83036": "190.21", "84443": "190.22", "84439": "190.22", "84153": "190.31",
                   "85610": "190.17", "82306": "L34658", "82607": "L34914", "82728": "190.18", "83540": "190.18"}
MAX_CLAIMS_PER_837_FILE = 4500                                # TR3 limit is 5,000 CLM per ST
BATCH_RUNS_PER_DAY = (1, 2, 2, 3, 3, 3, 4, 4, 5)                # billing system release runs per business day
BATCH_RUN_TIMES = ("0205", "0540", "0915", "1130", "1410", "1650", "1925", "2240")
DENIAL_CALIBRATION = .86                                      # set from a measured run so ~11% of claims see a denial
class DayDir(NamedTuple):
    tenant: str
    day: date
    path: Path                  # <out>/<tenant>/raw/received_date=<day>, as IT's SFTP copy expects it
    files: list                 # ledger metadata of the files written there (name, kind, expect, ...)
    bytes: int


LEDGER_LAYOUT = "claims-jsonl-1"


class _TenantWriter:
    def __init__(self, out: Path, tc: str, gz: bool):
        self.out, self.tc = Path(out), tc
        (self.out / "truth" / tc).mkdir(parents=True, exist_ok=True)
        self.rel = f"truth/{tc}/claims.jsonl" + (".gz" if gz else "")
        self.fh = gzip.open(self.out / self.rel, "wt", compresslevel=1) if gz else open(self.out / self.rel, "w")
        self.n, self.files, self.bytes = 0, [], 0
        self._dumps = json.JSONEncoder(separators=(",", ":"), default=str).encode

    def claims(self, rows: list[dict]):
        if rows:
            enc = self._dumps
            self.fh.write("\n".join(enc(r) for r in rows) + "\n")
            self.n += len(rows)

    def day(self, d: date, files: list[dict]) -> DayDir:
        base = self.out / self.tc / "raw" / f"received_date={d}"
        meta, nbytes = [], 0
        made = set()
        for f in files:
            body = f["body"] if isinstance(f["body"], bytes) else f["body"].encode("utf-8")   # ASCII, or a BOM
            folder = base / f["kind"]
            if folder not in made:
                folder.mkdir(parents=True, exist_ok=True)
                made.add(folder)
            (folder / f["name"]).write_bytes(body)
            nbytes += len(body)
            meta.append({k: (str(v) if k == "day" else v) for k, v in f.items() if k != "body"})
        self.files += meta
        self.bytes += nbytes
        return DayDir(self.tc, d, base, meta, nbytes)

    def close(self, sim: _TenantRun) -> dict:
        self.fh.close()
        idx = dict(files=self.files, file_days=sorted({f["day"] for f in self.files}), plb=sim.plb_log.get(self.tc, []),
                   claims_file=self.rel, n_claims=self.n, claims_per_day=round(sim.cpd, 3), raw_bytes=self.bytes)
        if sim.done is not None:
            idx["claims"] = dict(sorted(sim.done.items(), key=lambda kv: _creation_order(kv[0])))
        return idx


def _calendar(sc: Scenario):
    return [sc.start + timedelta(days=i) for i in range(sc.days)]


def _tenant_days(sc: Scenario, cpds: dict, out: Path, gz: bool, keep_claims: bool, sem=None):
    """Run these tenants day by day (day-major, tenant order within a day). Yields ("day", DayDir) as each
    tenant-day is written, then ("done", {tenant: ledger index})."""
    sims = {tc: _TenantRun(sc, tc, cpd, keep_claims) for tc, cpd in cpds.items()}
    for sim in sims.values():
        sim.burn_in()
    writers = {tc: _TenantWriter(out, tc, gz) for tc in cpds}
    gc_was = gc.get_threshold()
    gc.set_threshold(max(gc_was[0], 100_000), 50, 1000)       # ~10^6 long-lived objects: default GC costs ~25%
    try:
        for d in _calendar(sc):
            for tc, sim in sims.items():
                files, done = sim.step(d)
                writers[tc].claims(done)
                if files:
                    if sem is not None:
                        sem.acquire()                             # at most `lookahead` undelivered days on disk
                    yield "day", writers[tc].day(d, files)
        idx = {}
        for tc, sim in sims.items():
            writers[tc].claims(sim.finish())
            idx[tc] = writers[tc].close(sim)
        yield "done", idx
    finally:
        gc.set_threshold(*gc_was)
        for w in writers.values():
            w.fh.close()


def _worker(sc, cpds, out, gz, q, sem):
    try:
        for msg in _tenant_days(sc, cpds, Path(out), gz, False, sem):
            q.put(msg)
    except BaseException:
        import traceback
        q.put(("error", traceback.format_exc()))


class _Run:
    """Iterates DayDirs in (day, tenant) order and writes truth.json when exhausted. With workers > 1, tenants
    are split across processes; every tenant has its own random stream, so the bytes do not depend on the
    worker count."""

    def __init__(self, out_dir, sc: Scenario, tenants, workers: int = 1, truth_gzip: bool = False,
                 keep_claims: bool = False, backpressure: bool = True, lookahead: int = 2):
        self.out, self.sc, self.tenants = Path(out_dir), sc, list(tenants or TENANTS)
        self.workers = max(1, min(workers, len(self.tenants)))
        self.gz, self.keep, self.backpressure, self.lookahead = truth_gzip, keep_claims, backpressure, lookahead
        self.cpds = {tc: claims_per_day(sc, tc) for tc in self.tenants}
        self.truth: dict | None = None

    def __iter__(self):
        self.out.mkdir(parents=True, exist_ok=True)
        if self.workers == 1:
            stream = _tenant_days(self.sc, self.cpds, self.out, self.gz, self.keep)
            idx = yield from self._days(stream)
        else:
            idx = yield from self._parallel()
        order = {tc: i for i, tc in enumerate(self.tenants)}
        self.truth = {"generator_version": GENERATOR_VERSION, "layout": LEDGER_LAYOUT,
                      "scenario": {k: str(v) for k, v in asdict(self.sc).items()}, "denial_carcs": DENIAL_CARCS,
                      "tenants": dict(sorted(idx.items(), key=lambda kv: order[kv[0]]))}
        index = dict(self.truth, tenants={tc: {k: v for k, v in T.items() if k != "claims"}
                                          for tc, T in self.truth["tenants"].items()})
        (self.out / "truth.json").write_text(json.dumps(index, indent=1, default=str))

    @staticmethod
    def _days(stream):
        for kind, val in stream:
            if kind == "done":
                return val
            yield val

    def _parallel(self):
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        groups = [self.tenants[i::self.workers] for i in range(self.workers)]
        qs = [ctx.Queue() for _ in groups]
        sems = [ctx.Semaphore(self.lookahead) if self.backpressure else None for _ in groups]
        procs = [ctx.Process(target=_worker, daemon=True, name=f"edi_mock-{'+'.join(g)}",
                             args=(self.sc, {tc: self.cpds[tc] for tc in g}, str(self.out), self.gz, q, s))
                 for g, q, s in zip(groups, qs, sems)]
        for p in procs:
            p.start()
        order = {tc: i for i, tc in enumerate(self.tenants)}
        heads: dict[int, DayDir] = {}
        idx: dict = {}
        finished: set[int] = set()
        try:
            while True:
                for i, q in enumerate(qs):
                    if i in finished or i in heads:
                        continue
                    kind, val = q.get()
                    if kind == "error":
                        raise RuntimeError(f"generator worker {procs[i].name} failed:\n{val}")
                    if kind == "done":
                        idx.update(val)
                        finished.add(i)
                    else:
                        heads[i] = val
                if not heads:
                    break
                i = min(heads, key=lambda k: (heads[k].day, order[heads[k].tenant]))
                yield heads.pop(i)
                if sems[i] is not None:
                    sems[i].release()
            for p in procs:
                p.join()
        finally:
            for p in procs:
                if p.is_alive():
                    p.terminate()
                    p.join()
        return idx


# --- volume calibration ----------------------------------------------------------------------------
CAL_START = date(2026, 1, 5)                                  # a Monday: whole weeks in the window
CAL_WARMUP_DAYS, CAL_WINDOW_DAYS, CAL_POINTS = 56, 28, (40, 160)
_CAL_CACHE: dict = {}


def bytes_per_day_model(sc: Scenario, tc: str) -> tuple[float, float]:
    """Raw bytes per file day for tenant tc as (fixed, per_claim): bytes = fixed + per_claim * claims_per_day.

    Measured, not guessed: two short noise-free runs (fixed calendar and seed, so the answer is deterministic and
    the same for every seed) at two volumes; each is measured over 4 whole weeks after an 8-week warm-up, once
    remits for earlier claims arrive at the steady-state rate. Depends on the scenario's probabilities (stress
    data has more reversals, re-deliveries and corrupt files) and on the tenant (delimiters, price levels)."""
    knobs = {k: v for k, v in asdict(sc).items()
             if k not in ("start", "days", "seed", "volume_scale", "target_mb_per_day", "burn_in_days")}
    key = (tc, GENERATOR_VERSION, tuple(sorted(knobs.items())))
    if key not in _CAL_CACHE:
        cal = replace(sc, start=CAL_START + timedelta(days=CAL_WARMUP_DAYS), days=CAL_WINDOW_DAYS, seed=0,
                      target_mb_per_day=None, burn_in_days=CAL_WARMUP_DAYS)
        pts = []
        for cpd in CAL_POINTS:
            sim = _TenantRun(cal, tc, cpd)
            sim.noise = sim.seasonal = False
            sim.burn_in()
            total = n_days = 0
            for d in _calendar(cal):
                files, _ = sim.step(d)
                if files:
                    total += sum(len(f["body"]) for f in files)
                    n_days += 1
            pts.append((cpd, total / n_days))
        (c1, b1), (c2, b2) = pts
        k = (b2 - b1) / (c2 - c1)
        _CAL_CACHE[key] = (b1 - k * c1, k)
    return _CAL_CACHE[key]


def claims_per_day(sc: Scenario, tc: str) -> float:
    """Base claims per business day for a tenant (before the weekday pattern and daily noise)."""
    if sc.target_mb_per_day is None:
        return TENANTS[tc].claims_per_day * sc.volume_scale
    fixed, per_claim = bytes_per_day_model(sc, tc)
    return max(1.0, (sc.target_mb_per_day * 1e6 - fixed) / per_claim)


# --- public API ------------------------------------------------------------------------------------
def generate(out_dir: str | Path, scenario: Scenario | None = None, tenants=None, workers: int = 1,
             return_claims: bool = True, truth_gzip: bool = False) -> dict:
    """Generate the whole window into out_dir (files + ledger) and return the ledger (see Generator.run)."""
    return Generator(scenario or Scenario(), tenants).run(out_dir, workers=workers, return_claims=return_claims,
                                                          truth_gzip=truth_gzip)


def generate_days(out_dir: str | Path, scenario: Scenario | None = None, tenants=None, workers: int = 1,
                  lookahead: int = 2, truth_gzip: bool = False) -> Iterator[DayDir]:
    """Yield each tenant's day directory as soon as it is written, in (day, tenant) order.

    Generation pauses while the caller handles a day, so the caller can ingest day N and delete its local copy
    (the SFTP drop) before day N+1 is generated; with workers > 1 each worker runs at most `lookahead` days
    ahead. The ledger keeps growing under out_dir/truth/ and truth.json is written once the iterator is
    exhausted. Output is identical to `generate` for the same scenario."""
    yield from _Run(out_dir, scenario or Scenario(), tenants, workers=workers, truth_gzip=truth_gzip,
                    keep_claims=False, backpressure=True, lookahead=lookahead)


def iter_claims(out_dir: str | Path, tenant: str, index: dict | None = None) -> Iterator[dict]:
    """Stream one tenant's finalized ledger rows (in the order claims were closed, not submitted)."""
    out = Path(out_dir)
    index = index or json.loads((out / "truth.json").read_text())
    rel = index["tenants"][tenant]["claims_file"]
    with (gzip.open(out / rel, "rt") if rel.endswith(".gz") else open(out / rel)) as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_truth(out_dir: str | Path, tenants=None) -> dict:
    """The ledger as one dict: {generator_version, scenario, denial_carcs, tenants: {tc: {claims: {pcn: ...},
    files, file_days, plb, ...}}}, claims in submission order. This is what `generate` returns; it reads both the
    JSONL layout and an old single-file truth.json. Mind the size: ~5 KB of Python objects per claim."""
    out = Path(out_dir)
    truth = json.loads((out / "truth.json").read_text())
    if tenants is not None:
        truth["tenants"] = {tc: T for tc, T in truth["tenants"].items() if tc in set(tenants)}
    for tc, T in truth["tenants"].items():
        if "claims" not in T:
            T["claims"] = {c["pcn"]: c for c in sorted(iter_claims(out, tc, truth), key=lambda c: _creation_order(c["pcn"]))}
    return truth


def write_truth(out_dir: str | Path, truth: dict, truth_gzip: bool = False) -> Path:
    """Write an in-memory ledger (e.g. a subset of load_truth's) in the on-disk layout load_truth reads."""
    out = Path(out_dir)
    index = dict(truth, layout=LEDGER_LAYOUT, tenants={})
    for tc, T in truth["tenants"].items():
        rel = f"truth/{tc}/claims.jsonl" + (".gz" if truth_gzip else "")
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        with (gzip.open(out / rel, "wt", compresslevel=1) if truth_gzip else open(out / rel, "w")) as fh:
            for c in T["claims"].values():
                fh.write(json.dumps(c, separators=(",", ":"), default=str) + "\n")
        index["tenants"][tc] = dict({k: v for k, v in T.items() if k != "claims"}, claims_file=rel,
                                    n_claims=len(T["claims"]))
    (out / "truth.json").write_text(json.dumps(index, indent=1, default=str))
    return out / "truth.json"


def main(argv=None):
    import argparse
    import shlex
    import subprocess
    import time
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=30, help="calendar days")
    ap.add_argument("--start", type=date.fromisoformat, default=Scenario.start)
    ap.add_argument("--scale", type=float, default=1.0, help="volume_scale (ignored with --mb-per-day)")
    ap.add_argument("--mb-per-day", type=float, default=None, help="raw EDI MB per business day per tenant")
    ap.add_argument("--tenants", default=",".join(TENANTS), help="comma-separated tenant codes")
    ap.add_argument("--workers", type=int, default=1, help="processes (tenants are split across them)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--gzip-truth", action="store_true", help="write truth/<tenant>/claims.jsonl.gz")
    ap.add_argument("--each", metavar="CMD", help="shell command run after each day directory is written, before "
                    "the next is generated; {dir} {day} {tenant} are substituted (e.g. ingest, then rm -r {dir})")
    a = ap.parse_args(argv)
    kw = dict(start=a.start, days=a.days, volume_scale=a.scale, seed=a.seed, target_mb_per_day=a.mb_per_day)
    sc = Scenario.stress(**kw) if a.stress else Scenario(**kw)
    tenants = [t for t in a.tenants.split(",") if t]
    t0, total, n_days = time.perf_counter(), 0, 0
    for dd in generate_days(a.out, sc, tenants, workers=a.workers, truth_gzip=a.gzip_truth):
        total += dd.bytes
        n_days += 1
        if a.each:
            cmd = a.each.format(dir=shlex.quote(str(dd.path)), day=dd.day, tenant=dd.tenant)
            subprocess.run(cmd, shell=True, check=True)
        if n_days % 20 == 0:
            el = time.perf_counter() - t0
            print(f"{dd.day} {dd.tenant}: {total / 1e6:,.0f} MB in {el:,.0f}s ({total / 1e6 / el:.2f} MB/s, "
                  f"rss {_rss_mb():,.0f} MB)", flush=True)
    el = time.perf_counter() - t0
    idx = json.loads((Path(a.out) / "truth.json").read_text())
    for tc, v in idx["tenants"].items():
        print(f"{tc}: {v['n_claims']:,} claims ({v['claims_per_day']:,.0f}/day base), {len(v['files']):,} files over "
              f"{len(v['file_days'])} days, {sum(f['expect'] == 'rejected' for f in v['files'])} corrupt, "
              f"{v['raw_bytes'] / 1e6:,.1f} MB raw")
    import resource
    peak = max(resource.getrusage(w).ru_maxrss for w in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)) / 1024
    print(f"total {total / 1e6:,.1f} MB raw in {el:,.1f}s = {total / 1e6 / el:.2f} MB/s; peak rss {peak:,.0f} MB per process")


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * __import__("os").sysconf("SC_PAGE_SIZE") / 2**20
    except (OSError, ValueError):
        return float("nan")


if __name__ == "__main__":
    main()
