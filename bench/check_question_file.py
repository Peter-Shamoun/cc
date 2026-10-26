"""Re-check one authored question file with the benchmark matcher.

Same rules as ``bench.eval.selftest`` for a single file: reference SQL over ``sem.*`` matches
the ledger answer key, every ``alt_sql`` is accepted, every ``wrong_sql`` is rejected, and the
result is not empty or all zeros. Top-N questions use ``compare.order = topn``, so a tie at the
cutoff is accepted. This does not read or write ``bench/authored_questions.json``.

    python bench/check_question_file.py bench/questions_grokbot_r1.json --snapshot .ccsim/bench/<id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.compare import match  # noqa: E402
from bench.dataset import open_snapshot  # noqa: E402


def _questions(doc):
    if isinstance(doc, list):
        return doc
    return doc["questions"]


def _trivial(rows) -> bool:
    if not rows:
        return True
    return all(all((v or 0) == 0 for v in row if not isinstance(v, str)) for row in rows)


def _stripped(spec: dict) -> dict:
    return {k: v for k, v in spec.items()
            if k not in ("order", "n", "metric_col", "optional_cols", "percent_cols", "sign_cols")}


def check(path: Path, snapshot: Path) -> dict:
    doc = json.loads(path.read_text())
    questions = _questions(doc)
    sem = open_snapshot(snapshot)
    ledger = duckdb.connect(str(snapshot / "answer_key.duckdb"), read_only=True)
    report = dict(passed=0, failed=[], checked=0)
    for q in questions:
        report["checked"] += 1
        spec = q.get("compare") or {"tol": 0.01}
        try:
            gold = sem.execute(q["gold_sql"]).fetchall()
            key = ledger.execute(q["answer_key_sql"]).fetchall()
        except Exception as e:  # noqa: BLE001
            report["failed"].append(dict(id=q["id"], why=f"sql error: {str(e).splitlines()[0][:200]}"))
            continue
        if _trivial(gold):
            report["failed"].append(dict(id=q["id"], why="trivial"))
            continue
        ok, why = match(key, gold, _stripped(spec))
        if not ok:
            report["failed"].append(dict(id=q["id"], why=f"gold != answer key: {why}"))
            continue
        bad = None
        for alt in q.get("alt_sql") or []:
            try:
                ok, why = match(gold, sem.execute(alt).fetchall(), spec)
            except Exception as e:  # noqa: BLE001
                ok, why = False, str(e).splitlines()[0][:200]
            if not ok:
                bad = f"alt rejected: {why}"
                break
        if bad:
            report["failed"].append(dict(id=q["id"], why=bad))
            continue
        if not q.get("wrong_sql"):
            report["failed"].append(dict(id=q["id"], why="no known-wrong SQL"))
            continue
        for wrong in q["wrong_sql"]:
            try:
                ok, _ = match(gold, sem.execute(wrong["sql"]).fetchall(), spec)
            except Exception as e:  # noqa: BLE001
                report["failed"].append(dict(id=q["id"], why=f"wrong sql error: {str(e).splitlines()[0][:160]}"))
                ok = None
                break
            if ok:
                report["failed"].append(dict(id=q["id"], why=f"wrong accepted: {wrong['label']}"))
                ok = None
                break
        if ok is None:
            continue
        report["passed"] += 1
    report["sound"] = report["passed"] == report["checked"] and not report["failed"]
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question_file")
    ap.add_argument("--snapshot", required=True)
    args = ap.parse_args()
    report = check(Path(args.question_file), Path(args.snapshot))
    print(json.dumps(dict(sound=report["sound"], passed=report["passed"], checked=report["checked"],
                          failed=report["failed"]), indent=1))
    sys.exit(0 if report["sound"] else 1)


if __name__ == "__main__":
    main()
