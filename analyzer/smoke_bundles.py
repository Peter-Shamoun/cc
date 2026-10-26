"""Model-free smoke check of every bundle on TRAIN questions.

    python3 -m analyzer.smoke_bundles [--bundles B0,B6] [--questions 2]

For each bundle: ``prepare`` (agent analyzer, split train) on the first N answerable TRAIN questions succeeds;
every briefing renders (the scored session's, or each role's when the bundle is orchestrated); every command the
briefing advertises runs; the gold SQL submitted through ``answer`` (after whatever gates the briefing names:
interpret, cannot_express, lookups, --confidence) is recorded without a repair request; and the scored session is
correct under ``bench.agent_session.score_session``. Orchestrated bundles are driven through
``analyzer.orchestration`` with scripted roles: the planner records a valid plan, verifiers accept, selectors pick
the first candidate. Nothing here calls a model. Sessions go to a temporary directory; B1 and B10 use a placeholder
playbook file, because the real one is empty until review.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import tempfile
from pathlib import Path

from analyzer import cli, playbook
from analyzer.bundles import BUNDLES, RUNNABLE_ORDER, configs_document
from analyzer.harness import config_from_meta
from analyzer.lint import filter_literals
from analyzer.protocols import VERDICT_EXAMPLE, interpretation_example, required_interpretation_parts

_ADVERTISED = re.compile(r"analyzer\.cli --session \S+ ([a-z]+)")
SMOKE_SQL = "SELECT count(DISTINCT claim_key) AS claims FROM sem.claims WHERE is_latest_submission"
CANNOT_EXPRESS_SPEC = json.dumps({"status": "cannot_express", "because": "smoke check: exercise the SQL fallback"})
PLACEHOLDER_PLAYBOOK = "- smoke-check placeholder; the reviewed playbook replaces this file.\n"
# Concrete arguments for each advertised investigative command, run in this order (escalate last: it is sticky).
_CONCRETE = {
    "list": ["list"], "describe": ["describe", "sem.claims"], "sample": ["sample", "sem.claims"],
    "dictionary": ["dictionary"], "profile": ["profile", "sem.payers"], "glossary": ["glossary", "denial"],
    "lookup": ["lookup", "payer_name", "north"], "probe": ["probe", "SELECT count(*) AS n_rows FROM sem.claims"],
    "metrics": ["metrics", json.dumps({"metrics": ["denial_rate"], "group_by": ["payer_name"]})],
    "sql": ["sql", SMOKE_SQL], "escalate": ["escalate", "smoke check: exercise the audited escalation"],
}
_ORDER = ("list", "describe", "sample", "dictionary", "profile", "glossary", "lookup", "probe", "metrics", "sql",
          "escalate")


class SmokeFailure(AssertionError):
    pass


def _cli(session_dir: Path, *args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(["--session", str(session_dir), *args])
    return code, buffer.getvalue()


def _must(session_dir: Path, *args: str) -> str:
    code, out = _cli(session_dir, *args)
    if code != 0:
        raise SmokeFailure(f"{session_dir.name}: `{' '.join(args)[:80]}` failed: {out.strip()[:400]}")
    return out


def advertised_commands(briefing: str) -> list[str]:
    return list(dict.fromkeys(_ADVERTISED.findall(briefing)))


def play_answerer(session_dir: Path, gold_sql: str) -> list[str]:
    """Run what the briefing advertises, satisfy its gates, then submit the gold SQL. Returns commands run."""
    meta = json.loads((session_dir / "session.json").read_text())
    config = config_from_meta(meta, for_subagent=True)
    briefing = _must(session_dir, "briefing")
    advertised = advertised_commands(briefing)
    ran = []
    if "interpret" in advertised:
        _must(session_dir, "interpret", interpretation_example(required_interpretation_parts(config)))
        ran.append("interpret")
    if "spec" in advertised:
        code, out = _cli(session_dir, "spec", CANNOT_EXPRESS_SPEC)
        if code == 0 or "cannot_express" not in out:
            raise SmokeFailure(f"{session_dir.name}: a cannot_express spec was not reported as cannot_express")
        ran.append("spec")
    for name in _ORDER:
        if name in advertised:
            _must(session_dir, *_CONCRETE[name])
            ran.append(name)
    if config.lookup_mandatory_before_literals:
        for dimension, literal, _is_pattern in filter_literals(gold_sql):
            _must(session_dir, "lookup", dimension, literal.strip("%"))
    answer = ["answer", "--sql", gold_sql, "--text", "smoke check"]
    if config.report_confidence:
        answer += ["--confidence", "high"]
    out = _must(session_dir, *answer)
    if json.loads(out).get("unresolved_findings"):
        raise SmokeFailure(f"{session_dir.name}: gold SQL left findings {json.loads(out)['unresolved_findings']}")
    unknown = set(advertised) - set(_ORDER) - {"interpret", "spec", "answer", "refuse", "ask"}
    if unknown:
        raise SmokeFailure(f"{session_dir.name}: briefing advertises commands the smoke does not know: {unknown}")
    return ran + ["answer"]


def play_role(action: dict, gold_sql: str) -> list[str]:
    session_dir = Path(action["session"])
    kind = json.loads((session_dir / "session.json").read_text())["role"]["kind"]
    if kind == "answerer":
        return play_answerer(session_dir, gold_sql)
    briefing = _must(session_dir, "briefing")
    if kind == "planner":
        config = config_from_meta(json.loads((session_dir / "session.json").read_text()), for_subagent=True)
        for name in _ORDER:
            if name in advertised_commands(briefing) and name != "escalate":
                _must(session_dir, *_CONCRETE[name])
        _must(session_dir, "interpret", interpretation_example(required_interpretation_parts(config, planner=True)))
        return ["interpret"]
    if kind == "verifier":
        _must(session_dir, "verdict", VERDICT_EXAMPLE)
        return ["verdict"]
    if kind == "selector":
        _must(session_dir, "select", "a", "--reason", "smoke check")
        return ["select"]
    raise SmokeFailure(f"unknown role kind {kind}")


def drive(scored_session: Path, gold_sql: str, max_rounds: int = 8) -> dict:
    from analyzer.orchestration import next_actions
    for _round in range(max_rounds):
        step = next_actions(scored_session)
        if step["status"] == "done":
            return step["decision"]
        for action in step["actions"]:
            play_role(action, gold_sql)
    raise SmokeFailure(f"{scored_session.name}: orchestration did not finish in {max_rounds} rounds")


def smoke_questions(snapshot_path: Path, count: int) -> list[dict]:
    from bench.eval import load_cases
    from bench.splits import select_cases
    cases = select_cases(load_cases(snapshot_path=snapshot_path), "train")
    usable = sorted((case for case in cases if case.get("gold_sql") and not case.get("expect_refusal")
                     and not case.get("escalate")), key=lambda case: case["id"])
    return usable[:count]


def smoke_bundle(bundle_id: str, questions: list[dict], snapshot_path: Path, run_label: str) -> list[dict]:
    from bench import eval_subagents
    from bench.agent_session import score_session
    from bench.dataset import open_snapshot
    manifest_path = eval_subagents.prepare(
        run_label, configs_document([bundle_id]), ["haiku"], 1, only=",".join(q["id"] for q in questions),
        snapshot_path=snapshot_path, agent="analyzer", question_split="train")
    manifest = json.loads(Path(manifest_path).read_text())
    by_id = {question["id"]: question for question in questions}
    if {entry["question_id"] for entry in manifest["entries"]} != set(by_id):
        raise SmokeFailure(f"{bundle_id}: prepare did not create one session per smoke question")
    results = []
    for entry in manifest["entries"]:
        case = by_id[entry["question_id"]]
        session = Path(entry["session"])
        if entry.get("orchestration"):
            decision = drive(session, case["gold_sql"])
        else:
            play_answerer(session, case["gold_sql"])
            decision = None
        gold = open_snapshot(snapshot_path).execute(case["gold_sql"]).fetchall()
        scored = score_session(session, case, gold)
        if not scored["correct"]:
            raise SmokeFailure(f"{bundle_id} {case['id']}: gold answer scored wrong: {scored.get('why')}")
        results.append({"bundle": bundle_id, "question_id": case["id"], "correct": True, "decision": decision})
    return results


def run_smoke(bundle_ids: list[str], question_count: int, snapshot_path: Path, sessions_root: Path) -> list[dict]:
    """Point prepare's session root and the playbook slot at temporary paths for the duration of the run."""
    from bench import agent_session, eval_subagents
    saved = (agent_session.SESSIONS_ROOT, eval_subagents.SESSIONS_ROOT, playbook.PLAYBOOK_PATH)
    placeholder = sessions_root / "placeholder_playbook.md"
    sessions_root.mkdir(parents=True, exist_ok=True)
    placeholder.write_text(PLACEHOLDER_PLAYBOOK)
    agent_session.SESSIONS_ROOT = eval_subagents.SESSIONS_ROOT = sessions_root
    playbook.PLAYBOOK_PATH = placeholder
    try:
        questions = smoke_questions(snapshot_path, question_count)
        results = []
        for bundle_id in bundle_ids:
            results += smoke_bundle(bundle_id, questions, snapshot_path, f"smoke-{bundle_id}")
        return results
    finally:
        agent_session.SESSIONS_ROOT, eval_subagents.SESSIONS_ROOT, playbook.PLAYBOOK_PATH = saved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundles", default=",".join(RUNNABLE_ORDER))
    parser.add_argument("--questions", type=int, default=2)
    parser.add_argument("--snapshot")
    args = parser.parse_args(argv)
    from bench.dataset import snapshot
    snapshot_path = Path(args.snapshot) if args.snapshot else snapshot()
    bundle_ids = [item for item in args.bundles.split(",") if item]
    unknown = set(bundle_ids) - set(BUNDLES)
    if unknown:
        raise SystemExit(f"unknown bundles {sorted(unknown)}")
    with tempfile.TemporaryDirectory() as directory:
        for result in run_smoke(bundle_ids, args.questions, snapshot_path, Path(directory)):
            print(json.dumps({key: value for key, value in result.items() if key != "decision"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
