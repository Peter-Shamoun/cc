"""Independent X12 checks for 837P (005010X222A1) and 835 (005010X221A1) files.

It shares no code with the generator or the parser. It checks structure and money, the rules a
clearinghouse or a payer's front end would apply:

  envelope   ISA is 106 chars with 16 elements; ISA13 = IEA02; GS06 = GE02; GE01 = # of ST;
             ST02 = SE02; SE01 = segment count ST..SE; IEA01 = # of GS
  837P       HL ids sequential, parents exist; CLM02 = sum of SV102; LX numbered from 1; HI present;
             billing NPI (NM1*85*XX) passes Luhn; every date is a real CCYYMMDD
             REF*X4 (CLIA certificate number, NNDNNNNNNN) on every claim
  835        BPR02 = sum(CLP04) - sum(PLB amounts); BPR01 = H iff BPR02 = 0; per CLP:
             CLP03 = CLP04 + sum(CAS, claim and lines); per SVC: SVC02 = SVC03 + sum(line CAS);
             AMT*B6 (allowed) = SVC03 + line PR + CO-253 sequestration, and CO-253 = 2% of (allowed - PR);
             SVC05 (units paid) <= SVC07 (units billed); CLP05 = sum(PR CAS) unless reversal; payee NPI Luhn;
             interest: sum(claim AMT*I) = -sum(PLB L6); Medicare (CLP06 = MB): no claim-level CAS, no PI group

    python -m ccsim.x12_validate FILE...     # exit 1 if any file has errors
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

CENT = Decimal("0.01")
_CLIA = re.compile(r"^\d{2}D\d{7}$")


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
    text = text.lstrip("\ufeff \t\r\n")
    if "\n" in text[:106] or "\r" in text[:106]:              # fixed-width wrapped file
        text = text.replace("\r", "").replace("\n", "")
    if len(text) < 106 or not text.startswith("ISA"):
        raise ValueError("not an X12 interchange")
    el, rep, comp, term = text[3], text[82], text[104], text[105]
    if term not in "\r\n":
        text = text.replace("\r", "").replace("\n", "")
    segs = [s.strip("\r\n \x00") for s in text.split(term)]
    segs = [s for s in segs if s]
    return [s.split(el) for s in segs], el, comp, rep


def validate(text: str) -> list[str]:
    errs: list[str] = []
    try:
        segs, el, comp, rep = tokenize(text)
    except ValueError as e:
        return [str(e)]
    if len({el, comp, rep}) != 3:
        errs.append("delimiters are not distinct")
    blocks, cur = [], None
    for s in segs:
        if s[0] == "ISA":
            if cur is not None:
                errs.append("ISA before the previous interchange's IEA")
            cur = [s]
        elif cur is None:
            errs.append(f"segment {s[0]} outside an interchange")
        else:
            cur.append(s)
            if s[0] == "IEA":
                blocks.append(cur)
                cur = None
    if cur is not None:
        errs.append("missing IEA trailer")
    for blk in blocks:
        errs += _check_interchange(blk, comp)
    return errs


def _check_interchange(segs, comp) -> list[str]:
    errs = []
    isa, iea = segs[0], segs[-1]
    if len(isa) != 17:
        errs.append(f"ISA has {len(isa) - 1} elements, expected 16")
    if iea[2] != isa[13]:
        errs.append("IEA02 != ISA13")
    if int(iea[1]) != sum(1 for s in segs if s[0] == "GS"):
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
    hl_rows = []
    clm, line_sum, lx_expected, has_hi, has_clia = None, Decimal(0), 1, False, False
    in_claim = False
    sv1 = None
    svd_paid, svd_cas = defaultdict(Decimal), defaultdict(Decimal)   # per other-payer id, current line
    amt_d, svd_total = {}, defaultdict(Decimal)                      # per other payer, current claim
    other_payer_ids = []
    cur_svd = None

    def close_line():
        nonlocal sv1, cur_svd
        if sv1 is not None:
            for pid in set(svd_paid) | set(svd_cas):
                if _dec(sv1[2]) != svd_paid[pid] + svd_cas[pid]:
                    errs.append(f"CLM {clm[1]}: SV102 {sv1[2]} != SVD02 {svd_paid[pid]} + 2430 CAS {svd_cas[pid]} ({pid})")
        sv1, cur_svd = None, None
        svd_paid.clear()
        svd_cas.clear()

    def close():
        close_line()
        if clm is not None:
            if _dec(clm[2]) != line_sum:
                errs.append(f"CLM {clm[1]}: CLM02 {clm[2]} != sum(SV102) {line_sum}")
            if not has_hi:
                errs.append(f"CLM {clm[1]}: no HI segment")
            if not has_clia:
                errs.append(f"CLM {clm[1]}: no CLIA number (REF*X4); a lab claim is rejected without it")
            if lx_expected == 1:
                errs.append(f"CLM {clm[1]}: no service lines")
            for pid, amt in amt_d.items():
                if svd_total.get(pid, Decimal(0)) != amt:
                    errs.append(f"CLM {clm[1]}: 2320 AMT*D {amt} != sum(SVD02) {svd_total.get(pid, 0)} for {pid}")
    last_op, pending_amt = None, None
    for s in tx:
        sid = s[0]
        if sid == "HL":
            close()
            clm, in_claim = None, False
            amt_d, svd_total = {}, defaultdict(Decimal)
            if s[1] in ids or (s[2] and s[2] not in ids):
                errs.append(f"HL {s[1]}: duplicate id or unknown parent {s[2]}")
            if int(s[1]) != len(ids) + 1:
                errs.append(f"HL ids not sequential at {s[1]}")
            ids.add(s[1])
            hl_rows.append(s)
        elif sid == "CLM":
            close()
            clm, line_sum, lx_expected, has_hi, has_clia, in_claim = s, Decimal(0), 1, False, False, True
            amt_d, svd_total, last_op = {}, defaultdict(Decimal), None
            if len(s[5].split(comp)) != 3:
                errs.append(f"CLM {s[1]}: CLM05 must be POS:B:frequency")
        elif sid == "SBR" and in_claim:
            if len(s) < 3 or not s[2]:
                errs.append(f"CLM {clm[1]}: 2320 SBR02 (relationship) is required")
            last_op, pending_amt = "pending", None
        elif sid == "AMT" and in_claim and s[1] == "D" and last_op == "pending":
            pending_amt = _dec(s[2])                       # 2320 AMT precedes the loop's 2330B NM1*PR
        elif sid == "NM1" and in_claim and s[1] == "PR" and last_op == "pending":
            last_op = s[9] if len(s) > 9 else "?"
            if pending_amt is not None:
                amt_d[last_op] = pending_amt
        elif sid == "HI":
            has_hi = True
        elif sid == "REF" and s[1] == "X4" and in_claim:
            has_clia = True
            if not _CLIA.match(s[2] if len(s) > 2 else ""):
                errs.append(f"CLM {clm[1]}: CLIA number {s[2] if len(s) > 2 else ''} is not NNDNNNNNNN")
        elif sid == "LX":
            close_line()
            if int(s[1]) != lx_expected:
                errs.append(f"CLM {clm and clm[1]}: LX {s[1]} out of order")
            lx_expected += 1
        elif sid == "SV1":
            if not s[1].startswith("HC" + comp):
                errs.append("SV101 must start with HC")
            line_sum += _dec(s[2])
            sv1 = s
        elif sid == "SVD":
            cur_svd = s[1]
            svd_paid[cur_svd] += _dec(s[2])
            svd_total[cur_svd] += _dec(s[2])
        elif sid == "CAS" and cur_svd is not None:
            svd_cas[cur_svd] += sum((_dec(s[k]) for k in range(3, len(s), 3)), Decimal(0))
        elif sid == "NM1" and s[1] == "85" and (len(s) < 10 or s[8] != "XX" or not _luhn_npi(s[9])):
            errs.append("billing provider NPI missing or fails Luhn")
        elif sid == "DTP" and s[2] == "D8" and not _date_ok(s[3]):
            errs.append(f"bad date {s[3]}")
        elif sid == "DMG" and not _date_ok(s[2]):
            errs.append(f"bad birth date {s[2]}")
    close()
    parents = {h[2] for h in hl_rows if h[2]}
    for h in hl_rows:
        has_child = h[1] in parents
        if len(h) > 4 and h[4] in ("0", "1") and (h[4] == "1") != has_child:
            errs.append(f"HL {h[1]}: HL04={h[4]} but it {'has' if has_child else 'has no'} child HL")
    return errs


def _check_835(tx, comp) -> list[str]:
    errs = []
    bpr = next((s for s in tx if s[0] == "BPR"), None)
    if bpr is None:
        return ["835 without BPR"]
    clp_paid, plb_total, interest_total, plb_l6 = Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    clp = None
    svc = None
    claim_cas = line_cas_total = pr_total = Decimal(0)
    line_cas = line_pr = line_seq = Decimal(0)
    amt_b6 = None

    def close_svc():
        nonlocal svc, line_cas, line_pr, line_seq, amt_b6
        if svc is not None:
            if _dec(svc[2]) != _dec(svc[3]) + line_cas:
                errs.append(f"CLP {clp[1]} SVC {svc[1]}: SVC02 {svc[2]} != SVC03 {svc[3]} + CAS {line_cas}")
            if amt_b6 is not None and amt_b6 != _dec(svc[3]) + line_pr + line_seq:
                errs.append(f"CLP {clp[1]} SVC {svc[1]}: AMT*B6 {amt_b6} != paid + PR + sequestration "
                            f"{_dec(svc[3]) + line_pr + line_seq}")
            if line_seq and amt_b6 is not None and abs(line_seq - (Decimal("0.02") * (amt_b6 - line_pr)).quantize(CENT)) > CENT:
                errs.append(f"CLP {clp[1]} SVC {svc[1]}: CO-253 {line_seq} is not 2% of allowed - PR")
            if len(svc) > 7 and svc[7] and svc[5] and _dec(svc[5]) > _dec(svc[7]):
                errs.append(f"CLP {clp[1]} SVC {svc[1]}: SVC05 units paid {svc[5]} > SVC07 units billed {svc[7]}")
        svc, line_cas, line_pr, line_seq, amt_b6 = None, Decimal(0), Decimal(0), Decimal(0), None

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
            if s[2] == "4" and _dec(s[4]) != 0:
                errs.append(f"CLP {s[1]}: status 4 (denied) with a payment {s[4]}")
            if s[2] == "22" and (_dec(s[3]) > 0 or _dec(s[4]) > 0):
                errs.append(f"CLP {s[1]}: reversal (22) must carry negative amounts")
        elif sid == "SVC":
            close_svc()
            svc = s
        elif sid == "CAS":
            amt = sum((_dec(s[k]) for k in range(3, len(s), 3)), Decimal(0))
            pr = amt if s[1] == "PR" else Decimal(0)
            medicare = clp is not None and len(clp) > 6 and clp[6] == "MB"
            if medicare and s[1] == "PI":
                errs.append(f"CLP {clp[1]}: group PI is never used by Medicare")
            if svc is not None:
                line_cas += amt
                line_cas_total += amt
                line_pr += pr
                if s[1] == "CO":
                    line_seq += sum((_dec(s[k]) for k in range(3, len(s), 3) if s[k - 1] == "253"), Decimal(0))
            else:
                claim_cas += amt
                if medicare:
                    errs.append(f"CLP {clp[1]}: claim-level CAS on a Medicare Part B claim (carriers report lines)")
            pr_total += pr
        elif sid == "AMT" and s[1] == "B6" and svc is not None:
            amt_b6 = _dec(s[2])
        elif sid == "AMT" and s[1] == "I" and svc is None and clp is not None:
            interest_total += _dec(s[2])
        elif sid == "PLB":
            close_clp()
            plb_total += sum((_dec(s[k]) for k in range(4, len(s), 2)), Decimal(0))
            plb_l6 += sum((_dec(s[k]) for k in range(4, len(s), 2) if s[k - 1].split(comp)[0] == "L6"), Decimal(0))
        elif sid == "N1" and s[1] == "PE" and (len(s) < 5 or s[3] != "XX" or not _luhn_npi(s[4])):
            errs.append("payee NPI missing or fails Luhn")
        elif sid == "DTM" and not _date_ok(s[2]):
            errs.append(f"bad date {s[2]}")
        elif sid in ("SE",):
            close_clp()
    close_clp()
    if interest_total != -plb_l6:
        errs.append(f"claim interest AMT*I {interest_total} != -PLB L6 {plb_l6}")
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
        e = validate(open(path, encoding="utf-8", errors="replace").read())
        if e:
            bad += 1
            print(path, *e, sep="\n  ")
    sys.exit(1 if bad else 0)
