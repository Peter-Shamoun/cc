"""Paired B1 vs B1_ask analysis over runs clar_val + clar_probe (research/clarify_results.md): python research/clarify_paired_analysis.py"""
import json, random, sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench.agent_session import SESSIONS_ROOT, score_session
from bench.eval_subagents import _load_cases_file
from bench.eval import load_cases
from bench.dataset import open_snapshot

rows = []
for run in ["clar_val", "clar_probe"]:
    m = json.loads((SESSIONS_ROOT / run / "manifest.json").read_text())
    cases = {c["id"]: c for c in (_load_cases_file(m["cases_file"]) if m.get("cases_file") else load_cases(snapshot_path=Path(m["snapshot"])))}
    gold = {}
    for e in m["entries"]:
        s = Path(e["session"])
        cp = s / "claude_print.json"
        if not cp.exists():
            continue
        case = cases[e["question_id"]]
        if case.get("gold_sql") and case["id"] not in gold:
            gold[case["id"]] = open_snapshot(Path(m["snapshot"]), escalate=case.get("escalate", False)).execute(case["gold_sql"]).fetchall()
        r = score_session(s, case, gold.get(case["id"]))
        final = json.loads((s / "final.json").read_text()) if (s / "final.json").exists() else None
        kind = final["kind"] if final else "none"
        group = ("ambiguous" if case.get("expect_clarification") else "unanswerable" if case.get("expect_unanswerable")
                 else "refusal" if case.get("expect_refusal") else "clear")
        cost = json.loads(cp.read_text()).get("total_cost_usd") or 0.0
        rows.append(dict(q=case["id"], arm=e["config"], group=group, correct=bool(r["correct"]), kind=kind,
                         wrong_answer=(kind == "answer" and not r["correct"]), cost=cost,
                         nq=len((final or {}).get("questions") or [])))

def per_q(group_filter, metric):
    d = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if group_filter(r["group"]):
            d[r["q"]][r["arm"]].append(metric(r))
    return {q: {a: sum(v) / len(v) for a, v in arms.items()} for q, arms in d.items() if len(arms) == 2}

def paired(group_filter, metric, n=10000):
    pq = per_q(group_filter, metric)
    qs = list(pq)
    diffs = [pq[q]["B1_ask"] - pq[q]["B1"] for q in qs]
    b1 = sum(pq[q]["B1"] for q in qs) / len(qs); ask = sum(pq[q]["B1_ask"] for q in qs) / len(qs)
    rng = random.Random(0); boots = []
    for _ in range(n):
        smp = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        boots.append(sum(smp) / len(smp))
    boots.sort()
    return len(qs), b1, ask, ask - b1, boots[int(.025 * n)], boots[int(.975 * n)]

G = {"clear (VAL)": lambda g: g == "clear", "PHI refusal (VAL)": lambda g: g == "refusal",
     "ambiguous (probe)": lambda g: g == "ambiguous", "unanswerable (probe)": lambda g: g == "unanswerable",
     "all VAL": lambda g: g in ("clear", "refusal")}
print("runs:", len(rows))
for name, f in G.items():
    for mname, metric in [("correct", lambda r: r["correct"]), ("answered&wrong", lambda r: r["wrong_answer"]),
                          ("asked", lambda r: r["kind"] == "clarification"), ("refused", lambda r: r["kind"] == "refusal")]:
        n, b1, ask, d, lo, hi = paired(f, metric)
        print(f"{name:22s} {mname:15s} n={n:3d} B1={b1:6.1%} ask={ask:6.1%} diff={d*100:+5.1f}pp [{lo*100:+5.1f},{hi*100:+5.1f}]")
for arm in ["B1", "B1_ask"]:
    for name, f in [("VAL", lambda g: g in ("clear", "refusal")), ("probe", lambda g: g in ("ambiguous", "unanswerable"))]:
        rs = [r for r in rows if r["arm"] == arm and f(r["group"])]
        c = sum(r["correct"] for r in rs); cost = sum(r["cost"] for r in rs)
        print(arm, name, "runs", len(rs), "cost/q %.4f" % (cost / len(rs)), "cost/correct %.4f" % (cost / max(c, 1)))
asks = [r for r in rows if r["arm"] == "B1_ask" and r["kind"] == "clarification"]
print("form questions per ask:", sum(r["nq"] for r in asks) / max(len(asks), 1), "plain asks:", sum(r["nq"] == 0 for r in asks), "of", len(asks))
print("clear-VAL asks by question:", sorted({r["q"] for r in rows if r["arm"] == "B1_ask" and r["group"] == "clear" and r["kind"] == "clarification"}))
pq = per_q(lambda g: g == "clear", lambda r: r["correct"])
print("clear better", sum(v["B1_ask"] > v["B1"] for v in pq.values()), "worse", sum(v["B1_ask"] < v["B1"] for v in pq.values()))
print("clear ask/refuse runs", [(r["q"], r["kind"]) for r in rows if r["arm"] == "B1_ask" and r["group"] == "clear" and r["kind"] != "answer"])
print("ambiguous non-asks", [(r["q"], r["kind"]) for r in rows if r["arm"] == "B1_ask" and r["group"] == "ambiguous" and r["kind"] != "clarification"])
