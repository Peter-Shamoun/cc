"""Accept Opus-authored questions only when three independent results agree.

For each authored question: the author's gold SQL over sem.*, the author's answer-key SQL over ledger.*, and an
independent verifier's answer-key SQL over ledger.* must all give the same result (bench/compare.py), the result
must be non-trivial, and the verifier must not have flagged the question as ambiguous. Accepted questions are
dataset-independent SQL and join the hand-written set via bench/authored_questions.json.

    python -m bench.authored_questions ingest WORKFLOW_OUTPUT.json --snapshot DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.compare import match  # noqa: E402
from bench.dataset import open_snapshot  # noqa: E402

ACCEPTED_PATH = ROOT / "bench" / "authored_questions.json"


def _compare_spec(sql: str) -> dict:
    """Ranked outputs are compared as sets of rows (ties can legitimately reorder); LIMITs make them top-N."""
    spec = {"tol": 0.01}
    limit = re.search(r"\blimit\s+(\d+)\s*$", sql.strip().rstrip(";"), re.I)
    if limit:
        spec.update(order="topn", n=int(limit.group(1)), metric_col=1)
    return spec


def _strip_limit(sql: str) -> str:
    return re.sub(r"\blimit\s+\d+\s*;?\s*$", "", sql.strip(), flags=re.I)


def judge_question(q: dict, sem, ledger) -> tuple[bool, str]:
    if q.get("verifier", {}).get("ambiguous"):
        return False, "verifier: ambiguous"
    try:
        gold = sem.execute(_strip_limit(q["gold_sql"])).fetchall()
        author_key = ledger.execute(_strip_limit(q["answer_key_sql"])).fetchall()
        verifier_key = ledger.execute(_strip_limit(q["verifier"]["answer_key_sql"])).fetchall()
    except Exception as e:                                             # noqa: BLE001
        return False, f"sql error: {str(e)[:120]}"
    if not gold or all(all((v or 0) == 0 for v in r if not isinstance(v, str)) for r in gold):
        return False, "trivial result"
    spec = {k: v for k, v in _compare_spec(q["gold_sql"]).items() if k == "tol"}
    for label, other in (("author answer key", author_key), ("verifier answer key", verifier_key)):
        ok, why = match(other, gold, spec)
        if not ok:
            return False, f"gold != {label}: {why}"
    return True, "three-way agreement"


def ingest(workflow_output: Path, snapshot_path: Path) -> dict:
    data = json.loads(Path(workflow_output).read_text())
    data = data.get("result", data)                                   # a Workflow task output wraps the return value
    questions = data.get("questions", data)
    sem = open_snapshot(snapshot_path)
    ledger = duckdb.connect(str(Path(snapshot_path) / "answer_key.duckdb"), read_only=True)
    accepted = json.loads(ACCEPTED_PATH.read_text()) if ACCEPTED_PATH.exists() else []
    seen = {a["id"] for a in accepted}
    report = []
    for q in questions:
        ok, why = judge_question(q, sem, ledger)
        qid = "a_" + hashlib.sha1(q["question"].encode()).hexdigest()[:10]
        report.append(dict(id=qid, accepted=ok, why=why, question=q["question"][:120]))
        if ok and qid not in seen:
            accepted.append(dict(id=qid, category=q["category"].replace(" ", "_"), difficulty=q["difficulty"],
                                 question=q["question"], gold_sql=_strip_limit(q["gold_sql"]),
                                 answer_key_sql=_strip_limit(q["answer_key_sql"]), compare=_compare_spec(q["gold_sql"]),
                                 source="opus-authored, verified by independent answer key", interpretation=q["interpretation"]))
            seen.add(qid)
    ACCEPTED_PATH.write_text(json.dumps(accepted, indent=1))
    return dict(report=report, total_accepted=len(accepted))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest"])
    ap.add_argument("workflow_output")
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    print(json.dumps(ingest(Path(a.workflow_output), Path(a.snapshot)), indent=1))
