"""Data-level counts for the DEV_HARD_v1 trap conditions. Reads only data/sem/*.parquet of a snapshot
(no answer key, no question families, no reference SQL)."""
import json, sys, duckdb
snap = sys.argv[1]
con = duckdb.connect()
for t in ("claims", "claim_lines", "payments", "remit_claims", "remit_lines", "adjustments"):
    con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{snap}/data/sem/sem.{t}.parquet')")
q = lambda s: con.sql(s).fetchall()
one = lambda s: q(s)[0][0]
st = {}
st["rows"] = {t: one(f"select count(*) from {t}") for t in ("claims", "claim_lines", "payments", "remit_claims", "remit_lines", "adjustments")}
st["distinct_claim_keys"] = one("select count(distinct claim_key) from claims")
st["submission_date_range"] = [str(x) for x in q("select min(submission_date), max(submission_date) from claims")[0]]
st["payment_date_range"] = [str(x) for x in q("select min(payment_date), max(payment_date) from payments")[0]]
st["payments_by_month"] = {str(m): n for m, n in q("select payment_month, count(*) from payments group by 1 order by 1")}
st["remit_claims_by_payment_month"] = {str(m): n for m, n in q("select payment_month, count(*) from remit_claims group by 1 order by 1")}
st["august_payments"] = one("select count(*) from payments where payment_date >= DATE '2026-08-01' and payment_date < DATE '2026-09-01'")
st["august_remit_claims"] = one("select count(*) from remit_claims where payment_date >= DATE '2026-08-01' and payment_date < DATE '2026-09-01'")
st["claims_by_frequency_code"] = {str(f): n for f, n in q("select frequency_code, count(*) from claims group by 1 order by 1")}
st["freq8_void_claim_rows"] = one("select count(*) from claims where frequency_code = '8'")
st["freq8_void_claim_keys"] = one("select count(distinct claim_key) from claims where frequency_code = '8'")
st["remit_claims_freq8"] = one("select count(*) from remit_claims where frequency_code = '8'")
for col in ("referring_npi", "rendering_npi", "billing_npi"):
    r = q(f"""with c as (select {col} npi, count(distinct claim_key) n from claims where {col} is not null group by 1)
              select count(*), coalesce(max(n),0), count(*) filter (where n >= 15) from c""")[0]
    st[f"providers_{col}"] = {"distinct": r[0], "max_claims": r[1], "with_ge_15_claims": r[2]}
for col in ("referring_npi",):
    r = q(f"""with c as (select {col} npi, count(distinct claim_key) n from claims where {col} is not null and is_denied and is_latest_submission group by 1)
              select count(*), coalesce(max(n),0), count(*) filter (where n >= 15) from c""")[0]
    st[f"providers_{col}_denied_latest"] = {"distinct": r[0], "max_denied_claims": r[1], "with_ge_15_denied": r[2]}
st["denied_latest_claims"] = one("select count(distinct claim_key) from claims where is_denied and is_latest_submission")
# corrected / replacement claims (freq 7) and whether their original submission is inside the data window
st["freq7_corrected_claim_keys"] = one("select count(distinct claim_key) from claims where frequency_code = '7'")
st["freq7_with_original_in_range"] = one("""select count(distinct claim_key) from claims c7 where frequency_code = '7'
    and exists (select 1 from claims o where o.claim_key = c7.claim_key and o.frequency_code = '1' and o.submission_date <= c7.submission_date)""")
st["claim_keys_with_multiple_submissions"] = one("select count(*) from (select claim_key from claims group by 1 having count(*) > 1)")
st["reversed_claims"] = one("select count(distinct claim_key) from claims where ever_reversed")
print(json.dumps(st, indent=1))
