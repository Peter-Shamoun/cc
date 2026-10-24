"""Independent X12 checks for 837P (005010X222A1) and 835 (005010X221A1) files.

It shares no code with the generator or the parser. It checks structure and money, the rules a
clearinghouse or a payer's front end would apply:

  envelope   ISA is 106 chars with 16 elements; ISA13 = IEA02; GS06 = GE02; GE01 = # of ST;
             ST02 = SE02; SE01 = segment count ST..SE; IEA01 = # of GS
  837P       HL ids sequential, parents exist; CLM02 = sum of SV102; LX numbered from 1; HI present;
             billing NPI (NM1*85*XX) passes Luhn; every date is a real CCYYMMDD
  835        BPR02 = sum(CLP04) - sum(PLB amounts); BPR01 = H iff BPR02 = 0; per CLP:
             CLP03 = CLP04 + sum(CAS, claim and lines); per SVC: SVC02 = SVC03 + sum(line CAS);
             AMT*B6 (allowed) = SVC03 + line PR adjustments; CLP05 = sum(PR CAS) unless reversal; payee NPI Luhn

    python -m ccsim.x12_validate FILE...     # exit 1 if any file has errors
"""
from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal

CENT = Decimal("0.01")


def _dec(v: str) -> Decimal:
    return Decimal(v or "0")


def _luhn_npi(n: str) -> bool:
    if len(n) != 10 or not n.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + n)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _date_ok(v: str) -> bool:
    try:
        datetime.strptime(v, "%Y%m%d")
        return True
    except ValueError:
        return False


def tokenize(text: str):
    if len(text) < 106 or not text.startswith("ISA"):
        raise ValueError("not an X12 interchange")
    el, rep, comp, term = text[3], text[82], text[104], text[105]
    segs = [s.strip("\r\n") for s in text.split(term)]
    segs = [s for s in segs if s]
    return [s.split(el) for s in segs], el, comp, rep


def validate(text: str) -> list[str]:
    errs: list[str] = []
    try:
        segs, el, comp, rep = tokenize(text)
    except ValueError as e:
        return [str(e)]
    isa = segs[0]
    raw_isa = text[:106]
    if len(isa) != 17:
        errs.append(f"ISA has {len(isa) - 1} elements, expected 16")
    if raw_isa[105] in (el, comp) or len(set([el, comp, rep, raw_isa[105]])) != 4:
        errs.append("delimiters are not distinct")
    if segs[-1][0] != "IEA":
        errs.append("missing IEA trailer")
        return errs
    if segs[-1][2] != isa[13]:
        errs.append("IEA02 != ISA13")
    groups = [s for s in segs if s[0] == "GS"]
    if int(segs[-1][1]) != len(groups):
        errs.append("IEA01 != number of GS")
    i, st_start, st_count_in_gs, gs = 1, None, 0, None
    tx_segments: list[list[list[str]]] = []
    while i < len(segs) - 1:
        s = segs[i]
        if s[0] == "GS":
            gs, st_count_in_gs = s, 0
        elif s[0] == "GE":
            if gs is None:
                errs.append("GE without GS")
            else:
                if s[2] != gs[6]:
                    errs.append("GE02 != GS06")
                if int(s[1]) != st_count_in_gs:
                    errs.append(f"GE01={s[1]} but {st_count_in_gs} transaction sets")
            gs = None
        elif s[0] == "ST":
            st_start = i
            st_count_in_gs += 1
        elif s[0] == "SE":
            if st_start is None:
                errs.append("SE without ST")
            else:
                st = segs[st_start]
                if s[2] != st[2]:
                    errs.append("SE02 != ST02")
                if int(s[1]) != i - st_start + 1:
                    errs.append(f"SE01={s[1]} but {i - st_start + 1} segments in ST {st[2]}")
                tx_segments.append(segs[st_start:i + 1])
            st_start = None
        i += 1
    if st_start is not None or gs is not None:
        errs.append("unterminated transaction set or functional group")
    for tx in tx_segments:
        kind = tx[0][1]
        errs += (_check_837(tx, comp) if kind == "837" else _check_835(tx, comp) if kind == "835"
                 else [f"unexpected transaction set {kind}"])
    return errs


def _check_837(tx, comp) -> list[str]:
    errs, ids = [], set()
    clm, line_sum, lx_expected, has_hi = None, Decimal(0), 1, False

    def close():
        if clm is not None:
            if _dec(clm[2]) != line_sum:
                errs.append(f"CLM {clm[1]}: CLM02 {clm[2]} != sum(SV102) {line_sum}")
            if not has_hi:
                errs.append(f"CLM {clm[1]}: no HI segment")
            if lx_expected == 1:
                errs.append(f"CLM {clm[1]}: no service lines")
    for s in tx:
        sid = s[0]
        if sid == "HL":
            close()
            clm = None
            if s[1] in ids or (s[2] and s[2] not in ids):
                errs.append(f"HL {s[1]}: duplicate id or unknown parent {s[2]}")
            if int(s[1]) != len(ids) + 1:
                errs.append(f"HL ids not sequential at {s[1]}")
            ids.add(s[1])
        elif sid == "CLM":
            close()
            clm, line_sum, lx_expected, has_hi = s, Decimal(0), 1, False
            if len(s[5].split(comp)) != 3:
                errs.append(f"CLM {s[1]}: CLM05 must be POS:B:frequency")
        elif sid == "HI":
            has_hi = True
        elif sid == "LX":
            if int(s[1]) != lx_expected:
                errs.append(f"CLM {clm and clm[1]}: LX {s[1]} out of order")
            lx_expected += 1
        elif sid == "SV1":
            if not s[1].startswith("HC" + comp):
                errs.append("SV101 must start with HC")
            line_sum += _dec(s[2])
        elif sid == "NM1" and s[1] == "85" and (len(s) < 10 or s[8] != "XX" or not _luhn_npi(s[9])):
            errs.append("billing provider NPI missing or fails Luhn")
        elif sid == "DTP" and s[2] == "D8" and not _date_ok(s[3]):
            errs.append(f"bad date {s[3]}")
        elif sid == "DMG" and not _date_ok(s[2]):
            errs.append(f"bad birth date {s[2]}")
    close()
    return errs


def _check_835(tx, comp) -> list[str]:
    errs = []
    bpr = next((s for s in tx if s[0] == "BPR"), None)
    if bpr is None:
        return ["835 without BPR"]
    clp_paid, plb_total = Decimal(0), Decimal(0)
    clp = None
    svc = None
    claim_cas = line_cas_total = pr_total = Decimal(0)
    line_cas = line_pr = Decimal(0)
    amt_b6 = None

    def close_svc():
        nonlocal svc, line_cas, line_pr, amt_b6
        if svc is not None:
            if _dec(svc[2]) != _dec(svc[3]) + line_cas:
                errs.append(f"CLP {clp[1]} SVC {svc[1]}: SVC02 {svc[2]} != SVC03 {svc[3]} + CAS {line_cas}")
            if amt_b6 is not None and amt_b6 != _dec(svc[3]) + line_pr:
                errs.append(f"CLP {clp[1]} SVC {svc[1]}: AMT*B6 {amt_b6} != paid + PR {_dec(svc[3]) + line_pr}")
        svc, line_cas, line_pr, amt_b6 = None, Decimal(0), Decimal(0), None

    def close_clp():
        nonlocal clp, claim_cas, line_cas_total, pr_total
        close_svc()
        if clp is not None:
            if _dec(clp[3]) != _dec(clp[4]) + claim_cas + line_cas_total:
                errs.append(f"CLP {clp[1]}: CLP03 {clp[3]} != CLP04 {clp[4]} + CAS {claim_cas + line_cas_total}")
            if clp[2] != "22" and len(clp) > 5 and clp[5] and _dec(clp[5]) != pr_total:
                errs.append(f"CLP {clp[1]}: CLP05 {clp[5]} != PR adjustments {pr_total}")
        clp, claim_cas, line_cas_total, pr_total = None, Decimal(0), Decimal(0), Decimal(0)

    for s in tx:
        sid = s[0]
        if sid == "CLP":
            close_clp()
            clp = s
            clp_paid += _dec(s[4])
        elif sid == "SVC":
            close_svc()
            svc = s
        elif sid == "CAS":
            amt = sum((_dec(s[k]) for k in range(3, len(s), 3)), Decimal(0))
            pr = amt if s[1] == "PR" else Decimal(0)
            if svc is not None:
                line_cas += amt
                line_cas_total += amt
                line_pr += pr
            else:
                claim_cas += amt
            pr_total += pr
        elif sid == "AMT" and s[1] == "B6" and svc is not None:
            amt_b6 = _dec(s[2])
        elif sid == "PLB":
            close_clp()
            plb_total += sum((_dec(s[k]) for k in range(4, len(s), 2)), Decimal(0))
        elif sid == "N1" and s[1] == "PE" and (len(s) < 5 or s[3] != "XX" or not _luhn_npi(s[4])):
            errs.append("payee NPI missing or fails Luhn")
        elif sid in ("SE",):
            close_clp()
    close_clp()
    total = _dec(bpr[2])
    if total != clp_paid - plb_total:
        errs.append(f"BPR02 {total} != sum(CLP04) {clp_paid} - sum(PLB) {plb_total}")
    if total < 0:
        errs.append("BPR02 is negative")
    if (bpr[1] == "H") != (total == 0):
        errs.append(f"BPR01={bpr[1]} with BPR02={total}: H is required exactly when nothing is paid")
    if bpr[1] == "H" and bpr[4] != "NON":
        errs.append("BPR01=H requires BPR04=NON")
    return errs


if __name__ == "__main__":
    bad = 0
    for path in sys.argv[1:]:
        e = validate(open(path, encoding="ascii", errors="replace").read())
        if e:
            bad += 1
            print(path, *e, sep="\n  ")
    sys.exit(1 if bad else 0)
