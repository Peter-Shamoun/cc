"""Template gate benchmark: does a structured "can a premade KPI query answer this?" gate in front of B1 help?

    python -m bench.gate_bench --run gate_val --repeats 2 --workers 12

The 16 query families (bench/question_families.py) are the templates: fixed SQL with blanks (payer, payer class,
month, CPT, CARC). For every VAL question Haiku (effort low, no tools, structured output) sees the catalog, the
allowed values and the data coverage, and returns {fits, template_id, values, reason}. When it says fits and the
values pass validation, the template's SQL runs on the snapshot and is scored against the gold answer like any
agent answer. Otherwise the question falls through to free SQL, scored with B1's recorded result for that
question and repeat (run nm_val, same snapshot), so no B1 session is re-run.

Also asked: the other phrasing of each generated (f_) question, to see whether the gate picks the same template
and values for both wordings.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / ".ccsim" / "bench" / "50054399f815"
B1_RESULTS = ROOT / "research" / "runs" / "nm_val" / "results.json"
WORKER_DIRECTORY = Path("/tmp/cc-gate-bench")

TEMPLATES = {   # family id -> (definition shown to the gate, blanks)
    "payer_denial_rate": ("Denial rate of one payer: share of that payer's current claim adjudications that are denials (each claim counted once per payer).", ["payer"]),
    "payer_class_denial_rate": ("Denial rate of one payer class (all payers in the class), over current claim adjudications.", ["payer_class"]),
    "payer_paid_in_month": ("Total amount one payer paid in one calendar month, current adjudications, by payment date.", ["payer", "month"]),
    "cpt_denied_lines": ("Number of denied service lines billed with one CPT code, current adjudications.", ["cpt"]),
    "claims_first_submitted_in_month": ("Number of claims whose first submission fell in one calendar month.", ["month"]),
    "denials_with_carc": ("Number of denied current claim+payer adjudications carrying one CARC under group CO.", ["carc"]),
    "payer_avg_paid_per_paid_claim": ("Average payment per claim for one payer, over current adjudications that paid more than $0.", ["payer"]),
    "reversals_in_month": ("Number of claim reversals (claim status 22) with a payment date in one calendar month.", ["month"]),
    "payer_patient_responsibility": ("Total patient responsibility one payer assigned on its current adjudications.", ["payer"]),
    "never_remitted_submitted_in_month": ("Number of claims first submitted in one calendar month that never received any 835.", ["month"]),
    "payer_top_denial_carcs": ("For one payer: denial CARCs (group CO, reviewed denial reasons) ranked by number of denied claims, current adjudications.", ["payer"]),
    "payer_monthly_denial_rate": ("One payer's denial rate per payment month, current adjudications.", ["payer"]),
    "payer_days_to_first_remit": ("For one payer as primary: average days from a claim's first submission to that payer's first 835 (any status), one decimal.", ["payer"]),
    "payer_partial_line_denials": ("Number of service lines one payer denied on claims whose current adjudication is not a denial (partial denials).", ["payer"]),
    "secondary_paid_for_primary": ("Total paid by secondary payers (current adjudications) on claims whose primary payer is one given payer.", ["payer"]),
    "cpt_share_of_line_payments": ("Share of all service-line paid dollars (current adjudications) on lines billed with one CPT code.", ["cpt"]),
}

GATE_SCHEMA = {"type": "object", "properties": {
    "fits": {"type": "boolean"},
    "template_id": {"type": "string", "description": "the template's id when fits is true, else empty"},
    "values": {"type": "object", "properties": {k: {"type": "string"} for k in ("payer", "payer_class", "month", "cpt", "carc")}},
    "reason": {"type": "string", "description": "one line"}},
    "required": ["fits", "template_id", "values", "reason"]}


def allowed_values(con) -> dict:
    one = lambda sql: [r[0] for r in con.execute(sql).fetchall()]
    return dict(payer=one("SELECT DISTINCT payer_name FROM sem.remit_claims ORDER BY 1"),
                payer_class=one("SELECT DISTINCT payer_class FROM sem.remit_claims WHERE payer_class IS NOT NULL ORDER BY 1"),
                carc=one("SELECT DISTINCT carc_code FROM sem.adjustments ORDER BY 1"),
                cpt=one("SELECT DISTINCT procedure_code FROM sem.remit_lines ORDER BY 1"),
                month=one("SELECT DISTINCT strftime(payment_month, '%Y-%m-%d') FROM sem.remit_claims ORDER BY 1"))


def gate_prompt(values: dict) -> str:
    catalog = "\n".join(f"- {tid} (blanks: {', '.join(blanks)}): {definition}" for tid, (definition, blanks) in TEMPLATES.items())
    return f"""You are the first step of a claims analytics assistant. Decide whether ONE premade KPI query answers the
user's question exactly, with its blanks filled. If yes, the server runs that query and its result is the answer.
If no, a separate analyst writes custom SQL.

Say fits=true only when the template computes exactly what was asked: same measure, same population, same filters,
same grouping. Any extra filter, different grouping, comparison, ranking beyond the template, explanation ("why"),
several numbers, or a definition that differs from the template's means fits=false. When unsure, fits=false: a
wrong template gives a confidently wrong answer, while fits=false only costs a little time.

Templates:
{catalog}

Blank values must be copied exactly from these lists:
- payer: {"; ".join(values["payer"])}
- payer_class: {", ".join(values["payer_class"])}
- carc: {", ".join(values["carc"])}
- cpt: a CPT/HCPCS code as written, e.g. 87798
- month: first day of the month as YYYY-MM-DD; data covers payments {values["month"][0]} to {values["month"][-1]}

Fill only the blanks the chosen template has. With fits=false, template_id is "" and values is {{}}."""


def call_gate(system_prompt: str, question: str) -> dict:
    WORKER_DIRECTORY.mkdir(parents=True, exist_ok=True)   # outside the repo: no project instructions
    completed = subprocess.run(["claude", "-p", question, "--model", "haiku", "--effort", "low", "--system-prompt", system_prompt,
                                "--tools", "", "--json-schema", json.dumps(GATE_SCHEMA), "--output-format", "json"],
                               cwd=WORKER_DIRECTORY, capture_output=True, text=True, timeout=300)
    output = json.loads(completed.stdout)
    decision = output.get("structured_output") or {"fits": False, "template_id": "", "values": {}, "reason": "no structured output"}
    return dict(decision=decision, cost=output.get("total_cost_usd", 0.0), seconds=output.get("duration_ms", 0) / 1000)


def template_sql(decision: dict, values: dict) -> tuple[str | None, str]:
    """The filled template SQL, or (None, why the values were rejected)."""
    from bench.question_families import FAMILIES
    family = next((f for f in FAMILIES if f.family_id == decision["template_id"]), None)
    if family is None:
        return None, f"unknown template {decision['template_id']!r}"
    blanks = TEMPLATES[family.family_id][1]
    given = decision.get("values") or {}
    params = {}
    for blank in blanks:
        value = (given.get(blank) or "").strip()
        if value not in values[blank]:
            return None, f"{blank}={value!r} not an allowed value"
        params[blank] = value
    if "payer" in params:
        params["payer_title_sql"] = params["payer"].replace("'", "''")
    return family.gold_sql.format(**params), "ok"


def score_template(con, case: dict, sql: str) -> tuple[bool, str]:
    from bench.compare import match
    if case.get("expect_refusal") or case.get("expect_clarification") or case.get("expect_unanswerable") or not case.get("gold_sql"):
        return False, "answered a question that needed a refusal or a question back"
    gold = con.execute(case["gold_sql"]).fetchall()
    rows = con.execute(sql).fetchall()
    spec = case.get("compare", {})
    if spec.get("order") == "topn":
        rows = rows[:max(spec["n"], len(rows))]
    return match(gold, rows, spec)


def alternate_phrasings(case_ids: set[str]) -> dict[str, str]:
    """The other wording of each generated question (build_questions alternates the two by index)."""
    import duckdb
    from bench.question_families import FAMILIES, _fill, _parameters
    out = {}
    with duckdb.connect(str(SNAPSHOT / "answer_key.duckdb"), read_only=True) as ledger:
        for family in FAMILIES:
            cursor = ledger.execute(family.parameter_sql)
            names = [d[0] for d in cursor.description]
            for i, row in enumerate([dict(zip(names, r)) for r in cursor.fetchall()][:12]):
                key = "_".join(str(row[n]).lower().replace(" ", "-") for n in names)
                case_id = f"f_{family.family_id}__{key}"
                if case_id in case_ids:
                    out[case_id] = _fill(family.phrasings[(i + 1) % len(family.phrasings)], _parameters(row))
    return out


def bootstrap(differences: list[float], resamples: int = 10000) -> list[float]:
    rng = random.Random(0)
    means = sorted(sum(rng.choice(differences) for _ in differences) / len(differences) for _ in range(resamples))
    return [round(means[int(.025 * resamples)] * 100, 1), round(means[int(.975 * resamples)] * 100, 1)]


def main():
    from bench.dataset import open_snapshot
    from bench.eval import load_cases
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="gate_val")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    out_dir = ROOT / "research" / "runs" / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    b1 = defaultdict(dict)   # question -> repeat -> correct
    for r in json.loads(B1_RESULTS.read_text())["results"]:
        if r["config"] == "B1":
            b1[r["question_id"]][int(r["repeat"])] = str(r["correct"]) == "True"
    cases = {c["id"]: c for c in load_cases(snapshot_path=SNAPSHOT) if c["id"] in b1}
    con = open_snapshot(SNAPSHOT)
    values = allowed_values(con)
    system_prompt = gate_prompt(values)
    (out_dir / "gate_prompt.txt").write_text(system_prompt)
    alternates = alternate_phrasings(set(cases))

    jobs = [(qid, repeat, cases[qid]["question"], "original") for qid in cases for repeat in range(args.repeats)]
    jobs += [(qid, repeat, text, "alternate") for qid, text in alternates.items() for repeat in range(args.repeats)]
    with ThreadPoolExecutor(args.workers) as pool:
        calls = list(pool.map(lambda job: call_gate(system_prompt, job[2]), jobs))

    rows = []
    for (qid, repeat, text, wording), call in zip(jobs, calls):
        decision = call["decision"]
        row = dict(question_id=qid, repeat=repeat, wording=wording, question=text, decision=decision, cost=call["cost"],
                   seconds=call["seconds"], b1_correct=b1[qid][repeat], generated=qid.startswith("f_"))
        if decision.get("fits"):
            sql, why = template_sql(decision, values)
            row.update(path="template" if sql else "fallback_invalid", sql=sql, invalid_reason=None if sql else why)
            if sql:
                row["template_correct"], row["template_why"] = score_template(con, cases[qid], sql)
        else:
            row["path"] = "free_sql"
        row["blended_correct"] = row["template_correct"] if row["path"] == "template" else row["b1_correct"]
        rows.append(row)
    (out_dir / "results.json").write_text(json.dumps(rows, indent=1, default=str))

    original = [r for r in rows if r["wording"] == "original"]
    templated = [r for r in original if r["path"] == "template"]
    lines = [f"# Template gate on VAL ({len(cases)} questions x {args.repeats} repeats, snapshot {SNAPSHOT.name})", ""]
    for label, subset in (("all", original), ("generated (f_)", [r for r in original if r["generated"]]),
                          ("hand-written / authored", [r for r in original if not r["generated"]])):
        n = len(subset)
        t = [r for r in subset if r["path"] == "template"]
        lines += [f"## {label}: {n} runs", "",
                  f"- B1 alone: {sum(r['b1_correct'] for r in subset) / n:.1%}",
                  f"- gate + templates + B1 fallback: {sum(r['blended_correct'] for r in subset) / n:.1%}",
                  f"- answered by a template: {len(t)} ({len(t) / n:.1%}); template answers correct: "
                  f"{sum(r['template_correct'] for r in t)}/{len(t)}; B1 on those same runs: {sum(r['b1_correct'] for r in t)}/{len(t)}",
                  f"- gate said fits but values invalid (fell back): {sum(r['path'] == 'fallback_invalid' for r in subset)}", ""]
    per_question = defaultdict(list)
    for r in original:
        per_question[r["question_id"]].append(r["blended_correct"] - r["b1_correct"])
    lines += [f"Paired difference, blended - B1 (per question, bootstrap 95% CI): "
              f"{sum(sum(v) / len(v) for v in per_question.values()) / len(per_question) * 100:+.1f} pp "
              f"{bootstrap([sum(v) / len(v) for v in per_question.values()])}", ""]
    wrong = [r for r in templated if not r["template_correct"]]
    lines += ["## Wrong template answers", ""] + [f"- {r['question_id']} (r{r['repeat']}): {r['decision']['template_id']} "
                                                 f"{r['decision']['values']}: {r['template_why']}" for r in wrong] + [""]
    signature = lambda r: (r["decision"].get("fits"), r["decision"].get("template_id"), json.dumps(r["decision"].get("values"), sort_keys=True))
    same_repeat = Counter(len({signature(r) for r in original if r["question_id"] == q}) == 1 for q in cases)
    lines += [f"Same gate decision (template and values) on both repeats: {same_repeat[True]}/{len(cases)} questions"]
    pairs = [(o, a) for o in original if o["generated"] for a in rows
             if a["wording"] == "alternate" and a["question_id"] == o["question_id"] and a["repeat"] == o["repeat"]]
    lines += [f"Same gate decision for the two wordings of a generated question: {sum(signature(o) == signature(a) for o, a in pairs)}/{len(pairs)}",
              f"Gate cost: ${sum(r['cost'] for r in rows):.3f} for {len(rows)} calls; mean {sum(r['seconds'] for r in rows) / len(rows):.1f} s per call"]
    (out_dir / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
