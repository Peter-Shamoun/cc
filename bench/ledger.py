"""Expected answers computed from the generator's ledger (truth.json), never from the parser or SQL views.

Each function takes one tenant's ledger and returns rows in the same column order as the question's gold
SQL. The self-test requires gold SQL == ledger answer for every question that has one; a mismatch means
the semantic layer (or the parser) disagrees with the business rules, which is a data-layer finding, not
an agent error.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date


def _claims(T):
    return T["claims"].values()


def _adj(T, seq=None):
    """(claim, adjudication) for every claim+payer the payer ever answered."""
    for c in _claims(T):
        for a in c["adjudications"].values():
            if seq is None or a["seq"] == seq:
                yield c, a


def _current(T, seq=None):
    return [(c, a) for c, a in _adj(T, seq) if a["remitted_current"]]


def _cur_event(a):
    return [e for e in a["events"] if not e["duplicate_denial"]][-1]


def _rate(pairs, key, min_n=1, r=4):
    agg = defaultdict(lambda: [0, 0])
    for c, a in pairs:
        for k in key(c, a):
            agg[k][0] += 1
            agg[k][1] += a["denied"]
    return [(k, round(d / n, r), n) for k, (n, d) in agg.items() if n >= min_n]


def _title(name):
    return name.title()


def claims_submitted(T):
    return [(len(T["claims"]),)]


def total_billed(T):
    return [(round(sum(c["charge"] for c in _claims(T)), 2),)]


def total_paid(T):
    return [(round(sum(a["paid_current"] for _, a in _adj(T)), 2),)]


def paid_by_payment_month(T):
    m = defaultdict(float)
    for _, a in _current(T):
        d = date.fromisoformat(_cur_event(a)["date"])
        m[date(d.year, d.month, 1)] += a["paid_current"]
    return sorted((k, round(v, 2)) for k, v in m.items())


def payments_count(T):
    return [(sum(len(f["trns"]) for f in T["files"] if f["kind"] == "835" and f["expect"] == "parsed"),)]


def interest_paid(T):
    return [(round(-sum(p["amount"] for p in T["plb"] if p["code"] == "L6"), 2),)]


def denial_rate(T):
    cur = _current(T)
    return [(round(sum(a["denied"] for _, a in cur) / len(cur), 4),)]


def denial_rate_by_payer(T):
    return [(p, r) for p, r, _ in _rate(_current(T), lambda c, a: [_title(a["payer"])])]


def denials_by_type(T):
    cur = _current(T)
    return [(sum(a["denied_recorded"] for _, a in cur), sum(a["denied_signal_v3"] for _, a in cur))]


def top_denial_carcs(T, denial_carcs):
    n = defaultdict(int)
    for _, a in _current(T):
        if a["denied"]:
            for k in set(_cur_event(a)["co_carcs"]) & set(denial_carcs):
                n[k] += 1
    return sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))


def cpt_denial_rate_min20(T):
    return [(cpt, r, n) for cpt, r, n in _rate(_current(T), lambda c, a: sorted(set(c["cpts"])), min_n=20)]


def referring_provider_denials(T):
    rows = _rate(_current(T, seq=0), lambda c, a: [c["referring_npi"]], min_n=15)
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def principal_dx_denials(T):
    return [(dx, r, n) for dx, r, n in _rate(_current(T, seq=0), lambda c, a: [c["principal_dx"]], min_n=30)]


def zero_paid_not_denied(T):
    return [(sum(1 for _, a in _current(T) if a["paid_current"] == 0 and not a["denied"]),)]


def partial_line_denials(T):
    return [(sum(sum(1 for ln in _cur_event(a)["lines"] if ln["denied"]) for _, a in _current(T) if not a["denied"]),)]


def denied_charges_by_payer(T):
    m = defaultdict(float)
    for _, a in _current(T):
        if a["denied"]:
            m[_title(a["payer"])] += _cur_event(a)["charge"]
    return [(k, round(v, 2)) for k, v in m.items()]


def resubmitted(T):
    return [(sum(c["resubmitted"] for c in _claims(T)),)]


def voided(T):
    return [(sum(c["voided"] for c in _claims(T)),)]


def reversed_no_correction(T):
    return [(sum(1 for _, a in _adj(T) if a["ever_reversed"] and not a["remitted_current"]),)]


def never_remitted(T):
    return [(sum(1 for c in _claims(T) if not c["adjudications"]),)]


def timely_filing_denials(T):
    return [(sum(1 for _, a in _current(T) if a["denied"] and "29" in _cur_event(a)["co_carcs"]),)]


def days_to_first_primary_remit(T):
    m = defaultdict(list)
    for c, a in _adj(T, seq=0):
        first_sub = min(date.fromisoformat(s["date"]) for s in c["submissions"] if s["seq"] == 0)
        first_remit = min(date.fromisoformat(e["date"]) for e in a["events"])
        m[_title(a["payer"])].append((first_remit - first_sub).days)
    return [(k, round(sum(v) / len(v), 1)) for k, v in m.items()]


def secondary_paid_claims(T):
    cur = _current(T, seq=1)
    return [(sum(1 for _, a in cur if a["paid_current"] > 0), round(sum(a["paid_current"] for _, a in cur), 2))]


def primary_patient_responsibility(T):
    return [(round(sum(_cur_event(a)["pr"] for _, a in _current(T, seq=0)), 2),)]


def primary_deductible(T):
    return [(round(sum(_cur_event(a)["cas"].get("PR-1", 0) for _, a in _current(T, seq=0)), 2),)]


def payers_remitting(T):
    return [(len({a["payer_id"] for _, a in _adj(T)}),)]


def unbalanced_payments(T):
    return [(0,)]


def rejected_files(T):
    return [(sum(f["expect"] == "rejected" for f in T["files"]),)]


def modifier_59_claims(T):
    return [(sum(1 for c in _claims(T) if any("59" in m for m in c["modifiers"])),)]


def answer(name: str, T: dict, truth: dict):
    fn = globals()[name]
    return fn(T, truth["denial_carcs"]) if name == "top_denial_carcs" else fn(T)
