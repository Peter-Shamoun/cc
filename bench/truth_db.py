"""The generator's ledger (truth/<tenant>/claims.jsonl) as DuckDB tables, for answer keys at full scale.

Expected answers are computed with SQL over these tables. They share nothing with the parser or the sem.* views:
the rows come straight from the generator's own decisions. Tables (all in schema `ledger`):

    claims(pcn, dos, submitted, order_type, charge, principal_dx, referring_npi, risky_provider, dependent,
           resubmitted, voided, has_any_remit, n_lines, original_submission_date, first_billed_before_window)
    claim_lines(pcn, line_number, cpt, units, charge, modifiers)
    claim_diagnoses(pcn, sequence, dx)
    submissions(pcn, submission_date, payer_id, payer_sequence, frequency)
    adjudications(pcn, payer_id, payer, payer_class, payer_sequence, remitted_current, denied, denied_recorded,
                  denied_signal_v3, paid_current, status_current, ever_reversed, claim_level_only, has_duplicate_denial)
    events(pcn, payer_id, event_number, event_date, status, icn, paid, patient_responsibility, charge,
           claim_level_only, trn, duplicate_denial, is_current)
    event_adjustments(pcn, payer_id, event_number, group_code, carc, amount)
    event_lines(pcn, payer_id, event_number, line_number, cpt, adjudicated_cpt, paid, allowed, denied, co_carcs,
                units_paid, charge, modifiers)                 charge / modifiers as the 835 line echoes them
    files(day, kind, name, expect, payer_id)        plb(day, payer_id, trn, code, reference, amount, target_pcn,
                                                        payment_date)   day = DTM*405, payment_date = BPR16
    denial_carcs(carc)                              the reviewed CARCs that make a $0 CO adjustment a denial
    payers(payer_id, payer, payer_class)            every payer billed or remitting in the window

The data starts in steady state: the generator simulates a burn-in before the window, so 835s inside the window
also pay, deny, reverse and recoup claims billed before it. `claims` (and claim_lines, claim_diagnoses) hold the
claims with an 837 inside the window, like sem.claims; `submitted` is the first submission inside the window
(original_submission_date is the claim's real first submission, earlier for a claim first billed before the
window). adjudications / events hold every 835 event delivered in the window, like sem.remit_claims, including
those for claims billed before it (which then have no `claims` row unless they were resubmitted in the window).

`is_current` marks the event that defines the claim+payer's current state under the business rule
(latest event that is not a duplicate-claim denial, unless it is a reversal; a duplicate denial is current only
when the claim+payer has no other event).
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa

from ccsim.edi_mock import PAYER_BY_ID, iter_claims

BATCH_ROWS = 50_000


class _Sink:
    def __init__(self, con, table):
        self.con, self.table, self.rows = con, table, []

    def add(self, **row):
        self.rows.append(row)
        if len(self.rows) >= BATCH_ROWS:
            self.flush()

    def flush(self):
        if not self.rows:
            return
        keys = list(dict.fromkeys(k for r in self.rows for k in r))
        arrow = pa.table({k: [r.get(k) for r in self.rows] for k in keys})
        self.con.register("_batch", arrow)
        exists = self.con.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'ledger' AND table_name = ?",
                                  [self.table]).fetchone()[0]
        if exists:
            self.con.execute(f"INSERT INTO ledger.{self.table} BY NAME SELECT * FROM _batch")
        else:
            self.con.execute(f"CREATE TABLE ledger.{self.table} AS SELECT * FROM _batch")
        self.con.unregister("_batch")
        self.rows = []


def build_truth_db(generator_out_dir: Path, tenant: str, db_path: Path) -> Path:
    """db_path must not be named ledger.* (the file name becomes a catalog that would shadow schema `ledger`)."""
    if Path(db_path).stem == "ledger":
        raise ValueError("name the answer-key database something other than ledger.duckdb")
    generator_out_dir, db_path = Path(generator_out_dir), Path(db_path)
    db_path.unlink(missing_ok=True)
    index = json.loads((generator_out_dir / "truth.json").read_text())
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA ledger")
    sinks = {name: _Sink(con, name) for name in ("claims", "claim_lines", "claim_diagnoses", "submissions",
                                                 "adjudications", "events", "event_adjustments", "event_lines")}
    billed_payer_ids = set()
    for c in iter_claims(generator_out_dir, tenant, index):
        pcn = c["pcn"]
        if c["submissions"]:                                  # billed inside the window (sem.claims)
            first_in_window = min(s["date"] for s in c["submissions"])
            sinks["claims"].add(pcn=pcn, dos=c["dos"], submitted=first_in_window, order_type=c.get("order_type"),
                                charge=c["charge"], principal_dx=c.get("principal_dx"),
                                referring_npi=c.get("referring_npi"), risky_provider=c.get("risky_provider"),
                                dependent=c.get("dependent"), resubmitted=c.get("resubmitted"), voided=c.get("voided"),
                                has_any_remit=bool(c["adjudications"]), n_lines=len(c["cpts"]),
                                original_submission_date=c["submitted"],
                                first_billed_before_window=c["submitted"] < first_in_window)
            for i, cpt in enumerate(c["cpts"]):
                sinks["claim_lines"].add(pcn=pcn, line_number=i + 1, cpt=cpt, units=(c.get("units") or [None] * 99)[i],
                                         charge=(c.get("line_charges") or [None] * 99)[i],
                                         modifiers=(c.get("modifiers") or [[]] * 99)[i])
            for i, dx in enumerate(c.get("dx", [])):
                sinks["claim_diagnoses"].add(pcn=pcn, sequence=i + 1, dx=dx)
        for s in c["submissions"]:
            billed_payer_ids.add(s["payer_id"])
            sinks["submissions"].add(pcn=pcn, submission_date=s["date"], payer_id=s["payer_id"], payer_sequence=s["seq"],
                                     frequency=s["frequency"])
        for payer_id, a in c["adjudications"].items():
            billed_payer_ids.add(payer_id)
            sinks["adjudications"].add(pcn=pcn, payer_id=payer_id, payer=a["payer"], payer_class=a.get("payer_class"),
                                       payer_sequence=a["seq"], remitted_current=a["remitted_current"], denied=a["denied"],
                                       denied_recorded=a["denied_recorded"], denied_signal_v3=a["denied_signal_v3"],
                                       paid_current=a["paid_current"], status_current=a["status_current"],
                                       ever_reversed=a["ever_reversed"], claim_level_only=a["claim_level_only"],
                                       has_duplicate_denial=a["has_duplicate_denial"])
            real_events = [i for i, e in enumerate(a["events"]) if not e.get("duplicate_denial")] or \
                list(range(len(a["events"])))
            current_index = real_events[-1] if real_events and a["remitted_current"] else None
            for n, e in enumerate(a["events"]):
                sinks["events"].add(pcn=pcn, payer_id=payer_id, event_number=n + 1, event_date=e["date"], status=e["status"],
                                    icn=e.get("icn"), paid=e["paid"], patient_responsibility=e.get("pr"),
                                    charge=e.get("charge"), claim_level_only=e.get("claim_level_only"), trn=e.get("trn"),
                                    duplicate_denial=bool(e.get("duplicate_denial")), is_current=n == current_index)
                for code, amount in (e.get("cas") or {}).items():
                    group, _, carc = code.partition("-")
                    sinks["event_adjustments"].add(pcn=pcn, payer_id=payer_id, event_number=n + 1, group_code=group,
                                                   carc=carc, amount=amount)
                for li, ln in enumerate(e.get("lines", [])):
                    sinks["event_lines"].add(pcn=pcn, payer_id=payer_id, event_number=n + 1, line_number=li + 1,
                                             cpt=ln["cpt"], adjudicated_cpt=ln.get("adjudicated_cpt"), paid=ln["paid"],
                                             allowed=ln.get("allowed"), denied=ln.get("denied"),
                                             units_paid=ln.get("units_paid"), charge=ln.get("charge"),
                                             modifiers=ln.get("modifiers"),
                                             co_carcs=ln.get("co_carcs"))
    for s in sinks.values():
        s.flush()
    T = index["tenants"][tenant]
    files = [dict(day=f["day"], kind=f["kind"], name=f["name"], expect=f["expect"], payer_id=f.get("payer_id"))
             for f in T.get("files", [])]
    plb = [dict(day=p["day"], payer_id=p["payer_id"], trn=p["trn"], code=p["code"], reference=p["reference"],
                amount=p["amount"], target_pcn=p.get("target_pcn"), payment_date=p.get("payment_date"))
           for p in T.get("plb", [])]
    denial_carcs = [dict(carc=c) for c in index.get("denial_carcs", [])]   # the reviewed denial-CARC list
    payers = [dict(payer_id=pid, payer=PAYER_BY_ID[pid].name, payer_class=PAYER_BY_ID[pid].payer_class)
              for pid in sorted(billed_payer_ids)]
    for name, rows in (("files", files), ("plb", plb), ("denial_carcs", denial_carcs), ("payers", payers)):
        sink = _Sink(con, name)
        for r in rows:
            sink.add(**r)
        sink.flush()
    con.close()
    return db_path
