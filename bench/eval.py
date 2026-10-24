"""ClaimClarity agent benchmark.

    python -m bench.eval selftest                     # build/load the dataset; prove the benchmark itself is sound
    python -m bench.eval run --agent gold             # oracle run (must be 100%)
    python -m bench.eval run --agent claude-sql --repeats 3 --judge
    python -m bench.eval run --agent mypkg.agent:MyAgent --only denials
    python -m bench.eval calibrate                    # judge agreement on alt_sql (accept) / wrong_sql (reject)

Scores: accuracy per category and difficulty; pass^k (every repeat correct) for stability; median agent
latency, SQL calls and tokens per question. Results go to .ccsim/bench/runs/<timestamp>-<agent>.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench import agents as A  # noqa: E402
from bench.compare import match  # noqa: E402
from bench.dataset import CACHE, load_truth, open_snapshot, snapshot  # noqa: E402
from bench.ledger import answer as ledger_answer  # noqa: E402

CASES = Path(__file__).with_name("questions.yaml")


def load_cases(only: str | None = None) -> list[dict]:
    cases = yaml.safe_load(CASES.read_text())
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate question ids"
    if only:
        cases = [c for c in cases if only in (c["id"], c["category"], c["difficulty"])]
    return cases


def _spec(c):
    return c.get("compare", {})


def _expected(con, c):
    """Expected rows: the gold SQL result (ranked questions keep every row so ties can be judged)."""
    return con.execute(c["gold_sql"]).fetchall()


def selftest(path: Path, strict: bool = True) -> dict:
    """The benchmark is trustworthy on a dataset only if:
      - gold SQL == ledger answer for every question that has a ledger function (else: a data-layer finding)
      - every alt_sql is accepted, and the oracle agent scores 100%
      - (strict) every wrong_sql is rejected, and no gold answer is empty or all zeros unless allow_trivial;
        otherwise the question does not discriminate on this dataset."""
    truth = load_truth(path)
    report = dict(ledger_mismatch=[], alt_rejected=[], wrong_accepted=[], trivial=[], checked=0)
    for c in load_cases():
        if c.get("expect_refusal"):
            continue
        con = open_snapshot(path, escalate=c.get("escalate", False))
        gold = _expected(con, c)
        report["checked"] += 1
        if not c.get("allow_trivial") and (not gold or all(all((_v or 0) == 0 for _v in r if not isinstance(_v, str))
                                                            for r in gold)):
            report["trivial"].append(c["id"])
        if c.get("ledger"):
            want = ledger_answer(c["ledger"], truth["tenant"], truth)
            spec = {k: v for k, v in _spec(c).items() if k not in ("order", "n", "metric_col", "optional_cols",
                                                                     "percent_cols", "sign_cols")}
            ok, why = match(want, gold, spec)
            if not ok:
                report["ledger_mismatch"].append(dict(id=c["id"], why=why, gold=gold[:8], ledger=sorted(want)[:8]))
        for alt in c.get("alt_sql", []):
            ok, why = match(gold, con.execute(alt).fetchall(), _spec(c))
            if not ok:
                report["alt_rejected"].append(dict(id=c["id"], sql=alt, why=why))
        for w in c.get("wrong_sql", []):
            ok, _ = match(gold, con.execute(w["sql"]).fetchall(), _spec(c))
            if ok:
                report["wrong_accepted"].append(dict(id=c["id"], label=w["label"]))
    oracle = run("gold", path)
    report["oracle_accuracy"] = oracle["accuracy"]
    report["data_layer_ok"] = not report["ledger_mismatch"]
    report["sound"] = (report["data_layer_ok"] and not report["alt_rejected"] and oracle["accuracy"] == 1.0
                       and (not strict or not (report["wrong_accepted"] or report["trivial"])))
    return report


def run(agent_name: str, path: Path, repeats: int = 1, use_judge: bool = False, only: str | None = None) -> dict:
    from bench.judge import judge
    cases = load_cases(only)
    agent = A.load(agent_name, cases)
    rows = []
    for c in cases:
        con = open_snapshot(path, escalate=c.get("escalate", False))
        gold = None if c.get("expect_refusal") else _expected(con, c)
        for rep in range(repeats):
            tools = A.Tools(con, escalate=c.get("escalate", False))
            t = time.perf_counter()
            r = dict(id=c["id"], category=c["category"], difficulty=c["difficulty"], repeat=rep)
            try:
                ans = agent.answer(c["question"], tools)
                r["agent_s"] = round(time.perf_counter() - t, 3)
                r.update(sql=ans.sql, refused=ans.refused, usage=ans.usage, sql_calls=len(tools.calls),
                         sql_errors=sum(not x["ok"] for x in tools.calls), text=ans.text[:500])
                if c.get("expect_refusal"):
                    r["correct"], r["why"] = ans.refused, "refused" if ans.refused else "answered a request it should refuse"
                elif ans.refused or ans.rows is None:
                    r["correct"], r["why"] = False, "refused or no result"
                else:
                    r["correct"], r["why"] = match(gold, ans.rows, _spec(c))
                    r["matcher"] = r["correct"]
                    if not r["correct"] and use_judge:
                        v = judge(c["question"], c["gold_sql"], gold, ans.sql, ans.rows,
                                  top_n=_spec(c).get("n") if _spec(c).get("order") == "topn" else None)
                        r["judge"] = v
                        r["correct"] = v["correct"]
            except Exception as e:                                    # noqa: BLE001 - agent crash = wrong
                r.update(correct=False, why=f"{type(e).__name__}: {e}"[:300], agent_s=round(time.perf_counter() - t, 3),
                         sql_calls=len(tools.calls), sql_errors=sum(not x["ok"] for x in tools.calls),
                         usage=getattr(e, "usage", {}), crashed=True)
            rows.append(r)
    return summarize(agent_name, path, rows, repeats, use_judge)


def summarize(agent_name, path, rows, repeats, use_judge) -> dict:
    by_q = defaultdict(list)
    for r in rows:
        by_q[r["id"]].append(r)
    acc = lambda rs: round(sum(r["correct"] for r in rs) / len(rs), 3) if rs else None
    out = dict(agent=agent_name, dataset=json.loads((path / "meta.json").read_text())["dataset_id"], repeats=repeats,
               judge=use_judge, n_questions=len(by_q), accuracy=acc(rows),
               pass_all_repeats=round(sum(all(r["correct"] for r in rs) for rs in by_q.values()) / len(by_q), 3),
               by_category={k: acc([r for r in rows if r["category"] == k]) for k in sorted({r["category"] for r in rows})},
               by_difficulty={k: acc([r for r in rows if r["difficulty"] == k]) for k in ("easy", "medium", "hard")},
               median_agent_s=statistics.median([r.get("agent_s", 0) for r in rows]),
               mean_sql_calls=round(statistics.mean([r.get("sql_calls", 0) for r in rows]), 2),
               tokens=dict(input=sum((r.get("usage") or {}).get("input_tokens", 0) for r in rows),
                           output=sum((r.get("usage") or {}).get("output_tokens", 0) for r in rows)),
               judge_overrides=sum(1 for r in rows if r.get("judge") and r["judge"]["correct"]),
               results=rows)
    return out


def write(result: dict) -> Path:
    runs = CACHE / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y%m%dT%H%M%S}-{result['agent'].replace(':', '_').replace('/', '_')}"
    (runs / f"{stem}.json").write_text(json.dumps(result, indent=1, default=str))
    md = [f"# {result['agent']} on dataset {result['dataset']}", "",
          f"accuracy **{result['accuracy']:.1%}** · pass^{result['repeats']} {result['pass_all_repeats']:.1%} · "
          f"median {result['median_agent_s']} s/question · {result['mean_sql_calls']} SQL calls/question · "
          f"tokens in {result['tokens']['input']:,} / out {result['tokens']['output']:,}"
          + (f" · judge overrides {result['judge_overrides']}" if result["judge"] else ""), "",
          "| category | accuracy |", "|---|---|"]
    md += [f"| {k} | {v:.0%} |" for k, v in result["by_category"].items()]
    md += ["", "| question | correct | why | sql |", "|---|---|---|---|"]
    for r in result["results"]:
        sql = (r.get("sql") or "").replace("\n", " ").replace("|", "\\|")[:160]
        md.append(f"| {r['id']} r{r['repeat']} | {'✓' if r['correct'] else '✗'} | {str(r.get('why', ''))[:80]} | `{sql}` |")
    (runs / f"{stem}.md").write_text("\n".join(md) + "\n")
    return runs / f"{stem}.md"


def calibrate(path: Path) -> dict:
    from bench.judge import judge
    stats = dict(alt_total=0, alt_accepted=0, wrong_total=0, wrong_rejected=0, disagreements=[])
    for c in load_cases():
        if c.get("expect_refusal"):
            continue
        con = open_snapshot(path, escalate=c.get("escalate", False))
        gold = _expected(con, c)
        for label, sql, should in [("alt", s, True) for s in c.get("alt_sql", [])] + \
                                  [(w["label"], w["sql"], False) for w in c.get("wrong_sql", [])]:
            rows = con.execute(sql).fetchall()
            v = judge(c["question"], c["gold_sql"], gold, sql, rows,
                      top_n=_spec(c).get("n") if _spec(c).get("order") == "topn" else None)
            key = "alt" if should else "wrong"
            stats[f"{key}_total"] += 1
            stats["alt_accepted" if should else "wrong_rejected"] += v["correct"] == should
            if v["correct"] != should:
                stats["disagreements"].append(dict(id=c["id"], label=label, verdict=v))
    stats["agreement"] = round((stats["alt_accepted"] + stats["wrong_rejected"]) /
                               max(1, stats["alt_total"] + stats["wrong_total"]), 3)
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["selftest", "run", "calibrate"])
    ap.add_argument("--agent", default="gold")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    path = snapshot(rebuild=a.rebuild)
    if a.cmd == "selftest":
        rep = selftest(path)
        print(json.dumps(rep, indent=1, default=str))
        sys.exit(0 if rep["sound"] else 1)
    if a.cmd == "calibrate":
        print(json.dumps(calibrate(path), indent=1, default=str))
        return
    res = run(a.agent, path, a.repeats, a.judge, a.only)
    md = write(res)
    print(json.dumps({k: v for k, v in res.items() if k != "results"}, indent=1))
    print("report:", md)


if __name__ == "__main__":
    main()
