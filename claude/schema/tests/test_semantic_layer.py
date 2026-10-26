"""
End-to-end check: build the lossless schema + semantic views in an in-memory DuckDB,
load synthetic 837P/835 rows (made-up payers, includes denials, reversals,
corrections, $0-paid non-denials, unremitted claims, duplicate PCNs across files),
then verify the agent tools against ground truth computed independently in Python.

Run:  python tests/test_semantic_layer.py
"""
import importlib.util
import os
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("agent_interface", os.path.join(ROOT, "04_agent_interface.py"))
ai = importlib.util.module_from_spec(spec)
sys.modules["agent_interface"] = ai
spec.loader.exec_module(ai)

random.seed(7)
con = duckdb.connect()
con.execute(open(os.path.join(ROOT, "01_lossless_edi_schema.sql")).read())
con.execute(open(os.path.join(ROOT, "02_semantic_layer_views.sql")).read())

# --- test-only config -------------------------------------------------------
DENIAL_CARCS = ["50", "197", "27", "16", "109", "96"]
con.executemany("INSERT INTO ref.denial_carc VALUES (?, 'test', 'test', DATE '2026-10-01', NULL)", [[c] for c in DENIAL_CARCS])
con.executemany("INSERT INTO ref.carc (code, description) VALUES (?, ?)", [
    ["45", "Charge exceeds fee schedule"], ["50", "Not medically necessary"], ["197", "Precert/authorization absent"],
    ["27", "Expenses after coverage terminated"], ["16", "Claim lacks information"], ["1", "Deductible"], ["2", "Coinsurance"]])
con.execute("INSERT INTO ref.tenant_setting VALUES ('status4_is_recorded_denial','true','test')")

def ins(table, row: dict):
    cols = ", ".join(row)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))

ins("edi.ingest_run", dict(ingest_run_id="run1", started_at=datetime(2026, 10, 1), status="SUCCEEDED",
                           parser_name="test", parser_version="0", schema_version="1"))

seg_counter = defaultdict(int)
def new_file(kind, received):
    fid = f"{kind}-{len(seg_counter):04d}"
    seg_counter[fid] = 0
    ins("edi.source_file", dict(file_id=fid, tenant_code="TEST", file_kind=kind, s3_uri=f"s3://tenant-test/edi/{fid}.x12",
                                s3_bucket="tenant-test", s3_key=f"edi/{fid}.x12", size_bytes=1000, sha256=fid,
                                received_at=received, ingest_run_id="run1", ingested_at=received, parse_status="PARSED"))
    ic, gp, tx = f"{fid}:0", f"{fid}:1", f"{fid}:2"
    ins("edi.interchange", dict(interchange_uid=ic, file_id=fid, seg_ord=0, element_separator="*", component_separator=":", segment_terminator="~"))
    ins("edi.functional_group", dict(group_uid=gp, interchange_uid=ic, file_id=fid, seg_ord=1))
    ins("edi.transaction_set", dict(txn_uid=tx, group_uid=gp, file_id=fid, seg_ord=2, seg_ord_end=999999,
                                    txn_type="837" if kind == "837P" else "835", bht04_date=received.date() if kind == "837P" else None))
    seg_counter[fid] = 10
    return fid, tx

def seg(fid):
    seg_counter[fid] += 1
    return seg_counter[fid]

PAYERS = [("Northwind Health", "NW001"), ("Contoso Mutual", "CM002"), ("Fabrikam Care", "FC003")]
CPTS = {"87798": .35, "81479": .55, "80053": .05, "85025": .04, "87086": .10, "0240U": .45, "83036": .08, "84443": .06,
        "87491": .20, "99000": .30}

# --- build claims ------------------------------------------------------------
claims = []
for i in range(900):
    payer = random.choice(PAYERS)
    n_lines = random.choice([1, 1, 2, 3])
    lines = [(random.choice(list(CPTS)), round(random.uniform(20, 400), 2)) for _ in range(n_lines)]
    dos = date(2026, 1, 1) + timedelta(days=random.randint(0, 200))
    claims.append(dict(pcn=f"PCN{i:05d}", payer=payer, lines=lines, dos=dos))

# 837 files: 3 batches; ~5% of claims resubmitted (frequency 7) in a later batch
f837 = [new_file("837P", datetime(2026, 3, 1) + timedelta(days=60 * k)) for k in range(3)]
for c in claims:
    submissions = [random.randint(0, 1)]
    if random.random() < .05:
        submissions.append(2)
    for si, b in enumerate(submissions):
        fid, tx = f837[b]
        s_bill = seg(fid); bill_uid = f"{fid}:{s_bill}"
        ins("c837.hl", dict(hl_uid=bill_uid, txn_uid=tx, file_id=fid, seg_ord=s_bill, loop_id="2000A", hl01_id="1", hl03_level_code="20"))
        ins("edi.entity", dict(entity_uid=f"{fid}:{seg(fid)}", txn_uid=tx, owner_uid=bill_uid, owner_table="c837.hl", file_id=fid,
                               seg_ord=s_bill + 1, loop_id="2010AA", segment_id="NM1", entity_id_code="85",
                               name_last_or_org="TEST LAB", id_qualifier="XX", id_value="1234567893", state="NC"))
        s_sub = seg(fid); sub_uid = f"{fid}:{s_sub}"
        ins("c837.hl", dict(hl_uid=sub_uid, txn_uid=tx, file_id=fid, seg_ord=s_sub, loop_id="2000B", hl01_id="2",
                            hl03_level_code="22", sbr01_payer_resp_seq="P", sbr09_filing_indicator="CI"))
        ins("edi.entity", dict(entity_uid=f"{fid}:{seg(fid)}", txn_uid=tx, owner_uid=sub_uid, owner_table="c837.hl", file_id=fid,
                               seg_ord=s_sub + 1, loop_id="2010BB", segment_id="NM1", entity_id_code="PR",
                               name_last_or_org=c["payer"][0], id_qualifier="PI", id_value=c["payer"][1]))
        s_clm = seg(fid); clm_uid = f"{fid}:{s_clm}"
        ins("c837.claim", dict(claim_uid=clm_uid, txn_uid=tx, file_id=fid, seg_ord=s_clm, seg_ord_end=s_clm + 50, claim_ord_in_txn=1,
                               billing_hl_uid=bill_uid, subscriber_hl_uid=sub_uid, clm01_patient_control_number=c["pcn"],
                               clm02_total_charge=sum(x[1] for x in c["lines"]), clm05_1_place_of_service="81",
                               clm05_3_frequency_code="1" if si == 0 else "7", line_count=len(c["lines"])))
        s_hi = seg(fid)
        ins("c837.claim_code", dict(claim_code_uid=f"{fid}:{s_hi}:1", claim_uid=clm_uid, txn_uid=tx, file_id=fid, seg_ord=s_hi,
                                    hi_segment_ordinal=1, element_position=1, code_sequence=1, code_list_qualifier="ABK",
                                    code=random.choice(["Z1159", "R6889", "E119", "N390"]), raw_composite="ABK:Z1159"))
        for ln, (cpt, chg) in enumerate(c["lines"], start=1):
            s_lx = seg(fid); line_uid = f"{fid}:{s_lx}"
            ins("c837.service_line", dict(line_uid=line_uid, claim_uid=clm_uid, txn_uid=tx, file_id=fid, seg_ord=s_lx, seg_ord_end=s_lx + 3,
                                          lx01_line_number=ln, sv101_raw=f"HC:{cpt}", sv101_1_qualifier="HC", sv101_2_procedure_code=cpt,
                                          sv102_charge=chg, sv104_units=1, line_item_control_number=f"{c['pcn']}-{ln}"))
            s_dt = seg(fid)
            ins("edi.date_time", dict(dt_uid=f"{fid}:{s_dt}", txn_uid=tx, owner_uid=line_uid, owner_table="c837.service_line", file_id=fid,
                                      seg_ord=s_dt, loop_id="2400", segment_id="DTP", qualifier="472", format_qualifier="D8",
                                      raw_value=c["dos"].strftime("%Y%m%d"), date_start=c["dos"], date_end=c["dos"]))

# --- 835 events --------------------------------------------------------------
f835 = [new_file("835", datetime(2026, 4, 1) + timedelta(days=45 * k)) for k in range(4)]
payments = {}
def payment_for(k, payer):
    key = (k, payer)
    if key in payments:
        return payments[key]
    fid, tx = f835[k]
    s = seg(fid)
    ins("r835.payment", dict(payment_uid=tx + f"/{payer[1]}", txn_uid=tx, file_id=fid, seg_ord=s, bpr01_transaction_handling="I",
                             bpr02_total_paid=0, bpr04_payment_method="ACH", bpr16_effective_date=date(2026, 4, 1) + timedelta(days=45 * k),
                             trn02_check_eft_number=f"EFT{k}{payer[1]}", trn03_payer_id="1" + payer[1]))
    pu = tx + f"/{payer[1]}"
    ins("edi.entity", dict(entity_uid=f"{fid}:{seg(fid)}", txn_uid=tx, owner_uid=pu, owner_table="r835.payment", file_id=fid,
                           seg_ord=s + 1, loop_id="1000A", segment_id="N1", entity_id_code="PR",
                           name_last_or_org=payer[0], id_qualifier="XV", id_value=payer[1]))
    payments[key] = (fid, tx, pu)
    return payments[key]

def add_clp(k, c, status, paid_factor, cas, sign=1):
    fid, tx, pu = payment_for(k, c["payer"])
    s = seg(fid); cpu = f"{fid}:{s}"
    charge = sum(x[1] for x in c["lines"])
    line_paid = [round(chg * paid_factor, 2) for _, chg in c["lines"]]
    ins("r835.claim_payment", dict(claim_payment_uid=cpu, payment_uid=pu, txn_uid=tx, file_id=fid, seg_ord=s, seg_ord_end=s + 20,
                                   clp_ord_in_txn=s, clp01_patient_control_number=c["pcn"], clp02_claim_status=status,
                                   clp03_total_charge=sign * charge, clp04_total_paid=sign * sum(line_paid),
                                   clp05_patient_responsibility=0, clp07_payer_claim_control_number="ICN" + c["pcn"]))
    for ln, ((cpt, chg), lp) in enumerate(zip(c["lines"], line_paid), start=1):
        s2 = seg(fid); spu = f"{fid}:{s2}"
        ins("r835.service_payment", dict(service_payment_uid=spu, claim_payment_uid=cpu, txn_uid=tx, file_id=fid, seg_ord=s2,
                                         seg_ord_end=s2 + 4, svc_ord_in_claim=ln, svc01_raw=f"HC:{cpt}", svc01_1_qualifier="HC",
                                         svc01_2_procedure_code=cpt, svc02_charge=sign * chg, svc03_paid=sign * lp,
                                         line_item_control_number=f"{c['pcn']}-{ln}"))
        for ti, (grp, carc) in enumerate(cas, start=1):
            amt = round(chg - lp, 2) if ti == 1 else 0
            ins("edi.adjustment", dict(adj_uid=f"{fid}:{s2}:{ti}", txn_uid=tx, txn_type="835", owner_uid=spu,
                                       owner_table="r835.service_payment", adj_level="LINE", file_id=fid, seg_ord=s2 + 1,
                                       cas_ordinal=1, triplet_index=ti, group_code=grp, reason_code=carc, amount=sign * amt))
        ins("edi.amount", dict(amt_uid=f"{fid}:{seg(fid)}", txn_uid=tx, owner_uid=spu, owner_table="r835.service_payment",
                               file_id=fid, seg_ord=s2 + 2, qualifier="B6", amount=sign * lp))
        if status == "4":
            ins("r835.remark", dict(remark_uid=f"{fid}:{seg(fid)}", owner_uid=spu, owner_table="r835.service_payment", remark_level="LINE",
                                    txn_uid=tx, file_id=fid, seg_ord=s2 + 3, source_segment="LQ", source_element="LQ02",
                                    code_list_qualifier="HE", remark_code="N130"))

truth = {}  # (pcn, payer_key) -> (remitted_current: bool, denied: bool)
for c in claims:
    r = random.random()
    k = random.randint(0, 1)
    p_den = max(CPTS[cpt] for cpt, _ in c["lines"])
    if r < .08:                          # never remitted
        continue
    if r < .13:                          # paid, then reversed, no correction  -> not currently remitted
        add_clp(k, c, "1", .6, [("CO", "45")]); add_clp(k + 2, c, "22", .6, [("CO", "45")], sign=-1)
        truth[c["pcn"]] = (False, False); continue
    if r < .18:                          # denied, then reversed + corrected to paid (same later file, reversal first)
        add_clp(k, c, "4", 0, [("CO", "50")]); add_clp(k + 2, c, "22", 0, [("CO", "50")], sign=-1)
        add_clp(k + 2, c, "1", .7, [("CO", "45")]); truth[c["pcn"]] = (True, False); continue
    if r < .22:                          # $0 paid with a non-denial CARC (fee schedule) -> NOT denied
        add_clp(k, c, "1", 0, [("CO", "45")]); truth[c["pcn"]] = (True, False); continue
    if random.random() < p_den:
        if random.random() < .6:
            add_clp(k, c, "4", 0, [("CO", random.choice(["50", "16"]))])        # recorded denial
        else:
            add_clp(k, c, "1", 0, [("CO", random.choice(["197", "27"]))])       # signal v3 denial
        truth[c["pcn"]] = (True, True)
    else:
        add_clp(k, c, "1", random.uniform(.3, .9), [("CO", "45"), ("PR", "2")])
        truth[c["pcn"]] = (True, False)
    if random.random() < .03:            # exact duplicate delivery of the same adjudication in another file -> must not double count
        dup = dict(c); add_clp(3, dup, "1" if not truth[c["pcn"]][1] else "4",
                               0 if truth[c["pcn"]][1] else .5, [("CO", "50" if truth[c["pcn"]][1] else "45")])

# NB: the duplicate-delivery case above is a *new* event in a later file, so it becomes the current state.
# Recompute truth for those claims from the last event we wrote: simplest is to re-derive from what the
# view should do -> latest event wins. The generator wrote status consistent with prior truth, so truth holds.

# --- ground truth: CPT denial rate among codes with >= 20 remitted claims ------
by_cpt = defaultdict(lambda: [0, 0])
for c in claims:
    t = truth.get(c["pcn"])
    if not t or not t[0]:
        continue
    for cpt in {x[0] for x in c["lines"]}:
        by_cpt[cpt][0] += 1
        by_cpt[cpt][1] += int(t[1])
expected = {cpt: (d / n, d, n) for cpt, (n, d) in by_cpt.items() if n >= 20}

# --- run the agent interface ----------------------------------------------------
api = ai.SemanticAPI(con, os.path.join(ROOT, "03_semantic_model.yaml"))
res = api.dispatch("query_metrics", {
    "metrics": ["denial_rate", "denied_claims", "remitted_claims"], "group_by": ["procedure_code"],
    "min_group_size": 20, "order_by": [{"field": "denial_rate", "direction": "desc"}], "limit": 50})
assert "error" not in res, res
print("view chosen:", res["view"])
print(res["sql"], "\n")
got = {r[0]: (r[1], r[2], r[3]) for r in res["rows"]}
print(f"{'CPT':8}{'rate':>8}{'denied':>8}{'remitted':>10}   expected")
for cpt, (rate, d, n) in got.items():
    e = expected[cpt]
    print(f"{cpt:8}{rate:8.3f}{d:8}{n:10}   {e[0]:.3f} {e[1]}/{e[2]}")
    assert d == e[1] and n == e[2], (cpt, (d, n), e)
assert set(got) == set(expected), (set(got) ^ set(expected))
print("\nCPT denial-rate question: MATCHES ground truth\n")

# Overall checks
mixed = api.dispatch("query_metrics", {"metrics": ["remitted_claims", "reversal_events"]})
assert "error" in mixed, mixed
tot = api.query_metrics(metrics=["remitted_claims", "denied_claims", "denial_rate"])
n_rem = sum(1 for t in truth.values() if t[0]); n_den = sum(1 for t in truth.values() if t[1])
print("overall:", dict(zip(tot["columns"], tot["rows"][0])), "| expected remitted", n_rem, "denied", n_den)
assert tot["rows"][0][0] == n_rem and tot["rows"][0][1] == n_den

for name, call in [
    ("by payer", dict(metrics=["denial_rate", "remitted_claims"], group_by=["payer_name"])),
    ("monthly trend", dict(metrics=["denial_rate", "remitted_claims"], group_by=[{"field": "payment_date", "grain": "month"}],
                           order_by=[{"field": "payment_date_month", "direction": "asc"}])),
    ("top CARCs", dict(metrics=["claims_with_adjustment", "adjustment_amount"], group_by=["carc_code", "carc_description"],
                       filters=[{"field": "group_code", "op": "=", "value": "CO"}],
                       order_by=[{"field": "claims_with_adjustment", "direction": "desc"}])),
    ("remit coverage", dict(metrics=["submitted_claims", "claims_with_remit", "remit_coverage_rate", "resubmitted_claims"])),
    ("billed mix", dict(metrics=["billed_lines", "line_remit_coverage_rate"], group_by=["procedure_code"],
                        order_by=[{"field": "billed_lines", "direction": "desc"}], limit=5)),
    ("payments", dict(metrics=["payment_count", "total_payment_amount"], group_by=["payer_name"])),
]:
    r = api.dispatch("query_metrics", call)
    assert "error" not in r, (name, r)
    print(f"\n[{name}] view={r['view']}")
    print("  ", r["columns"])
    for row in r["rows"][:6]:
        print("  ", row)

print("\nlookup_values('payer_name','northwind'):", api.lookup_values("payer_name", "northwind", 3)["matches"])
print("timeline keys:", list(api.get_claim_timeline(claims[0]["pcn"]).keys()))

# Guard rails
bad = api.dispatch("run_semantic_sql", {"sql": "SELECT * FROM r835.claim_payment"})
assert "error" in bad, bad
bad2 = api.dispatch("run_semantic_sql", {"sql": "SELECT * FROM read_parquet('s3://x/*.parquet')"})
assert "error" in bad2, bad2
ok = api.dispatch("run_semantic_sql", {"sql": "WITH x AS (SELECT procedure_code, line_paid_amount FROM sem.remit_lines WHERE is_current_adjudication) "
                                              "SELECT procedure_code, quantile_cont(line_paid_amount, .5) AS median_paid FROM x GROUP BY 1 ORDER BY 2 DESC"})
assert "error" not in ok, ok
err = api.dispatch("query_metrics", {"metrics": ["denail_rate"]})
print("\nunknown-metric error (model-facing):", err["error"])
err2 = api.dispatch("query_metrics", {"metrics": ["denial_rate"], "group_by": ["carc_code"]})
print("incompatible combo error:", err2["error"][:160], "...")
print("\nALL CHECKS PASSED")
