"""Run A08 through `bench.eval_subagents` without changing that harness.

Prepare (repeat mode, the default)::

    python -m analyst.approaches.a08_self_consistency.adapter write-configs a08.json
    python -m bench.eval_subagents prepare RUN --configs a08.json --models haiku,sonnet --repeats 5

Prepare (prompt-shape mode, the temperature substitute)::

    python -m analyst.approaches.a08_self_consistency.adapter write-configs a08.json --diverse-prompts
    python -m bench.eval_subagents prepare RUN --configs a08.json --models haiku,sonnet --repeats 1

After the orchestrator has finished the sessions and, optionally, after
`python -m bench.eval_subagents score RUN`::

    python -m analyst.approaches.a08_self_consistency.adapter vote RUN

`vote` re-executes each submitted SQL in the snapshot sandbox (`open_snapshot`
+ `guard`), the same check the scorer uses. It does not call a model. The
report is `a08_vote.json` next to the run manifest: one decision per question
and model, the vote margin, and a calibration table when the question has gold.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.agent_session import SESSIONS_ROOT, _reads_a_table  # noqa: E402
from bench.dataset import open_snapshot  # noqa: E402
from bench.eval import load_cases  # noqa: E402
from bench.guard import guard  # noqa: E402

from .protocol import (  # noqa: E402
    APPROACH_ID,
    ATTEMPT_COUNT,
    CONFIG_NAME,
    configs_for_eval_subagents,
    cost_multiplier,
    is_self_consistency_config,
)
from .vote import Attempt, calibrate, decide  # noqa: E402


def write_configs(path: Path, *, diverse_prompts: bool = False) -> dict:
    document = configs_for_eval_subagents(diverse_prompts=diverse_prompts)
    path.write_text(json.dumps(document, indent=1) + "\n")
    return document


def vote_run(run_label: str, *, sessions_root: Path | None = None, expected_attempts: int = ATTEMPT_COUNT) -> dict:
    """Group this run's A08 sessions, re-execute, vote, and write `a08_vote.json`."""
    run_dir = Path(sessions_root) if sessions_root else SESSIONS_ROOT
    run_dir = run_dir / run_label
    manifest = json.loads((run_dir / "manifest.json").read_text())
    entries = [entry for entry in manifest["entries"] if is_self_consistency_config(entry["config"])]
    if not entries:
        raise ValueError(f"{run_dir} has no {CONFIG_NAME} sessions")
    snapshot = Path(manifest["snapshot"])
    cases = {case["id"]: case for case in load_cases(snapshot_path=snapshot)}
    grouped: dict[tuple, list] = defaultdict(list)
    for entry in entries:
        grouped[(entry["question_id"], entry["model"])].append(entry)

    questions = []
    for (question_id, model), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda entry: (entry.get("repeat", 0), entry["config"], entry["session"]))
        attempts = [_load_attempt(index, entry) for index, entry in enumerate(ordered)]
        case = cases.get(question_id, {})
        spec = dict(case.get("compare") or {})
        expect_refusal = bool(case.get("expect_refusal"))
        gold_rows = None
        if case.get("gold_sql") and not expect_refusal:
            connection = open_snapshot(snapshot, escalate=bool(case.get("escalate")))
            gold_rows = connection.execute(case["gold_sql"]).fetchall()
        decision = decide(
            attempts,
            spec=spec,
            gold_rows=gold_rows,
            expect_refusal=expect_refusal,
            expected_attempts=expected_attempts,
        )
        decision.update(question_id=question_id, model=model, gold_known=bool(expect_refusal or gold_rows is not None))
        questions.append(decision)

    labeled = [question for question in questions if question["gold_known"]]
    report = {
        "approach": APPROACH_ID,
        "run": run_label,
        "snapshot": str(snapshot),
        "expected_attempts": expected_attempts,
        "cost_multiplier": cost_multiplier(),
        "questions": questions,
        "calibration": calibrate(labeled),
    }
    (run_dir / "a08_vote.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    return report


def _load_attempt(index: int, entry: dict) -> Attempt:
    session_dir = Path(entry["session"])
    meta = json.loads((session_dir / "session.json").read_text())

    def attempt(status: str, **fields) -> Attempt:
        return Attempt(attempt_index=index, status=status, config_name=entry.get("config"),
                       repeat=entry.get("repeat"), **fields)

    final_path = session_dir / "final.json"
    if not final_path.exists():
        return attempt("missing")
    final = json.loads(final_path.read_text())
    kind = final.get("kind")
    if kind == "refusal":
        return attempt("refusal", text=final.get("text"))
    if kind == "clarification":
        return attempt("clarification", text=final.get("text"))
    if kind != "answer":
        return attempt("missing", error=f"unexpected final kind {kind!r}")
    sql = final.get("sql") or ""
    if not _reads_a_table(sql):
        return attempt("ungrounded", sql=sql, text=final.get("text"))
    try:
        connection = open_snapshot(Path(meta["snapshot_path"]), escalate=bool(meta.get("escalate")))
        guarded = guard(sql, 1000, bool(meta.get("escalate")))
        rows = tuple(tuple(row) for row in connection.execute(guarded).fetchall())
    except Exception as exc:  # noqa: BLE001 — the attempt failed; the vote must keep going
        return attempt("error", sql=sql, text=final.get("text"), error=f"{type(exc).__name__}: {exc}"[:300])
    return attempt("executed", sql=sql, text=final.get("text"), rows=rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A08 self-consistency adapter for bench.eval_subagents")
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write-configs", help="write a configs.json the prepare command accepts")
    write.add_argument("path")
    write.add_argument("--diverse-prompts", action="store_true",
                       help="five prompt shapes instead of one config repeated five times")
    vote = sub.add_parser("vote", help="re-execute a finished run and write a08_vote.json")
    vote.add_argument("run")
    vote.add_argument("--sessions-root")
    vote.add_argument("--expected-attempts", type=int, default=ATTEMPT_COUNT)
    args = parser.parse_args(argv)
    if args.command == "write-configs":
        document = write_configs(Path(args.path), diverse_prompts=args.diverse_prompts)
        repeats = 1 if args.diverse_prompts else ATTEMPT_COUNT
        print(f"wrote {len(document)} config(s) to {args.path}; prepare with --repeats {repeats}")
        return 0
    report = vote_run(args.run, sessions_root=Path(args.sessions_root) if args.sessions_root else None,
                      expected_attempts=args.expected_attempts)
    calibration = report["calibration"]
    print(f"{len(report['questions'])} questions; "
          f"accept {calibration['accept_rate']}, escalate {calibration['escalate_rate']}, "
          f"abstain {calibration['abstain_rate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
