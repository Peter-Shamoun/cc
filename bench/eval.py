"""Accuracy + latency benchmark for the agent layer over the mock world.

Scoring, after the OpenAI in-house data agent write-up: run the candidate SQL and the gold SQL, compare
result sets (order-insensitive unless the question asks for a ranking, numeric tolerance), and when they
differ ask an LLM judge whether the candidate still answers the question (e.g. extra columns). Gold SQL is
itself checked against the generator's ledger (`truth`), which catches parser/view bugs, not agent bugs.

  python bench/eval.py --agent gold                 # harness self-check (should be 100%)
  python bench/eval.py --agent claude --judge       # text-to-SQL agent, needs ANTHROPIC_API_KEY
  python bench/eval.py --agent mymodule:MyAgent     # any class with .answer(question, con) -> {"sql", ...}
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ccsim.edi_mock import Scenario  # noqa: E402
from ccsim.query import open_session  # noqa: E402
from ccsim.world import build_world  # noqa: E402

MODEL = os.environ.get("CC_AGENT_MODEL", "claude-sonnet-5-5")
JUDGE_MODEL = os.environ.get("CC_JUDGE_MODEL", "claude-opus-5-5")


# --- ledger checks (independent of parser + SQL) -------------------------------------------------
def _remitted(cl):
    return [v for v in cl.values() if v["remitted_current"]]


TRUTH = {
    "remitted_count": lambda cl: [(len(_remitted(cl)),)],
    "denial_rate": lambda cl: [(round(sum(v["denied"] for v in _remitted(cl)) / len(_remitted(cl)), 4),)],
    "total_paid": lambda cl: [(round(sum(v["paid_current"] for v in cl.values()), 2),)],
    "zero_paid_not_denied": lambda cl: [(sum(1 for v in _remitted(cl) if v["paid_current"] == 0 and not v["denied"]),)],
    "never_remitted": lambda cl: [(sum(1 for v in cl.values() if not v["events"]),)],
    "reversed_no_correction": lambda cl: [(sum(1 for v in cl.values() if v["events"] and v["events"][-1]["status"] == "22"),)],
    "resubmitted": lambda cl: [(sum(1 for v in cl.values() if v.get("resubmitted")),)],
}


def _by(cl, key, pred=lambda v: True, min_n=1):
    agg = defaultdict(lambda: [0, 0])
    for v in _remitted(cl):
        if pred(v):
            for k in key(v):
                agg[k][0] += 1
                agg[k][1] += v["denied"]
    return agg


TRUTH["denial_rate_by_payer"] = lambda cl: [(p.title(), round(d / n, 4), n) for p, (n, d) in _by(cl, lambda v: [v["payer"]]).items()]
TRUTH["cpt_denial_min20"] = lambda cl: [(c, round(d / n, 4), n) for c, (n, d) in _by(cl, lambda v: set(v["cpts"])).items() if n >= 20]
TRUTH["medicaid_denial_rate"] = lambda cl: [(round(d / n, 4),) for (n, d) in _by(cl, lambda v: ["m"], lambda v: v["payer_class"] == "medicaid").values()]


# --- comparison ----------------------------------------------------------------------------------
def _norm(rows):
    out = []
    for r in rows:
        out.append(tuple(round(float(x), 2) if isinstance(x, (int, float)) or type(x).__name__ == "Decimal" else
                         (str(x) if x is not None else None) for x in r))
    return sorted(out, key=lambda t: tuple("" if x is None else str(x) for x in t))


def same_result(a, b) -> bool:
    return _norm(a) == _norm(b)


# --- agents --------------------------------------------------------------------------------------
class GoldAgent:
    def __init__(self, cases):
        self.cases = {c["question"]: c for c in cases}

    def answer(self, question, con):
        c = self.cases[question]
        return {"refused": True} if c.get("expect_refusal") else {"sql": c["gold_sql"]}


SCHEMA_HINT = None


def describe(con) -> str:
    lines = []
    for (v,) in con.execute("SELECT view_name FROM duckdb_views() WHERE schema_name = 'sem' ORDER BY 1").fetchall():
        cols = con.execute(f"DESCRIBE sem.{v}").fetchall()
        lines.append(f"sem.{v}(" + ", ".join(f"{c[0]} {c[1]}" for c in cols) + ")")
    return "\n".join(lines)


class ClaudeSQLAgent:
    """Baseline single-shot text-to-SQL over sem.* (no semantic compiler, no retries). The thing to beat."""

    SYSTEM = ("You answer questions about a lab's 837P claims and 835 remittances by writing ONE DuckDB SQL query over "
              "the sem.* views below. Current state = is_current_adjudication. Never select patient identifiers; if the "
              "question asks for PHI, reply exactly REFUSE. Reply with only the SQL, no prose, no code fences.\n\n")

    def __init__(self, model=MODEL):
        import anthropic
        self.client, self.model = anthropic.Anthropic(), model

    def answer(self, question, con):
        msg = self.client.messages.create(model=self.model, max_tokens=800,
                                          system=self.SYSTEM + describe(con),
                                          messages=[{"role": "user", "content": question}])
        text = "".join(b.text for b in msg.content if b.type == "text").strip().strip("`")
        if text.upper().startswith("REFUSE"):
            return {"refused": True, "usage": msg.usage.model_dump()}
        return {"sql": text.removeprefix("sql").strip(), "usage": msg.usage.model_dump()}


def judge(question, gold_sql, gold_rows, cand_sql, cand_rows) -> dict:
    import anthropic
    prompt = (f"Question: {question}\n\nReference SQL:\n{gold_sql}\nReference result (first 20 rows): {gold_rows[:20]}\n\n"
              f"Candidate SQL:\n{cand_sql}\nCandidate result (first 20 rows): {cand_rows[:20]}\n\n"
              "Does the candidate correctly answer the question? Extra columns, ordering and naming differences are fine; "
              "different numbers, a different population, or a different definition are not. "
              'Reply as JSON: {"correct": true|false, "reason": "..."}')
    msg = anthropic.Anthropic().messages.create(model=JUDGE_MODEL, max_tokens=300,
                                                messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        return {"correct": False, "reason": "unparseable judge reply: " + text[:200]}


def load_agent(name, cases):
    if name == "gold":
        return GoldAgent(cases)
    if name == "claude":
        return ClaudeSQLAgent()
    mod, _, cls = name.partition(":")
    return getattr(importlib.import_module(mod), cls)()


def run(agent_name="gold", world_dir=None, days=30, scale=0.5, use_judge=False, customer_user="ana@xifin.example"):
    cases = yaml.safe_load((Path(__file__).parent / "questions.yaml").read_text())
    world_dir = Path(world_dir or ROOT / ".ccsim" / "eval-world")
    aws, idp, truth = build_world(world_dir, Scenario(days=days, volume_scale=scale))
    customer, con = open_session(aws, idp, idp.login(customer_user))
    ledger = truth["tenants"][customer]["claims"]
    agent = load_agent(agent_name, cases)
    results = []
    for case in cases:
        gold_rows = con.execute(case["gold_sql"]).fetchall()
        gold_ok = None
        if case.get("truth"):
            gold_ok = same_result(gold_rows, TRUTH[case["truth"]](ledger))
        t0 = time.perf_counter()
        r = dict(id=case["id"], tags=case.get("tags", []), gold_matches_ledger=gold_ok)
        try:
            out = agent.answer(case["question"], con)
            r["agent_s"] = round(time.perf_counter() - t0, 3)
            if case.get("expect_refusal"):
                r["correct"] = bool(out.get("refused"))
            elif out.get("refused"):
                r["correct"] = False
            else:
                q0 = time.perf_counter()
                cand_rows = con.execute(out["sql"]).fetchall()
                r["query_s"] = round(time.perf_counter() - q0, 4)
                r["sql"] = out["sql"]
                r["exact"] = same_result(cand_rows, gold_rows)
                r["correct"] = r["exact"]
                if not r["exact"] and use_judge:
                    r["judge"] = judge(case["question"], case["gold_sql"], gold_rows, out["sql"], cand_rows)
                    r["correct"] = bool(r["judge"].get("correct"))
            r["usage"] = out.get("usage")
        except Exception as e:  # SQL error or guard rejection is a wrong answer, not a crash
            r.update(correct=False, error=f"{type(e).__name__}: {e}"[:300])
        results.append(r)
    summary = dict(agent=agent_name, n=len(results), accuracy=round(sum(r["correct"] for r in results) / len(results), 3),
                   gold_ledger_mismatches=[r["id"] for r in results if r["gold_matches_ledger"] is False],
                   mean_agent_s=round(sum(r.get("agent_s", 0) for r in results) / len(results), 3))
    return summary, results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="gold")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--out", default=str(ROOT / ".ccsim" / "eval-results.json"))
    a = ap.parse_args()
    summary, results = run(a.agent, days=a.days, scale=a.scale, use_judge=a.judge)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(dict(summary=summary, results=results), indent=1, default=str))
    print(json.dumps(summary, indent=1))
    for r in results:
        print(f"{'PASS' if r['correct'] else 'FAIL'}  {r['id']:<28} ledger={r['gold_matches_ledger']}  {r.get('error', '')}")
