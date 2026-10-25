"""Create evaluation sessions, build the evaluated subagent's prompt, and score what it submitted.

A session is a directory outside every SQL-readable directory:
    session.json   question id + text, technique configuration, model label, snapshot path
    calls.jsonl    every CLI call (written by bench/agent_cli.py)
    final.json     the submitted answer / refusal / clarification
Scoring re-executes the submitted SQL in a fresh sandbox, so an agent cannot score by reporting numbers it did
not compute; a final query that reads no table at all is flagged as not grounded in the data and scored wrong.
"""
from __future__ import annotations

import json
import shlex
import uuid
from pathlib import Path

import sqlglot
from sqlglot import exp

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG, INVESTIGATIVE_COMMANDS
from bench.compare import match
from bench.dataset import open_snapshot
from bench.guard import guard

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_ROOT = ROOT / ".ccsim" / "agent_sessions"


def create_session(snapshot_path: Path, question: dict, technique_config: dict, model_label: str,
                   run_label: str, example_library_path: Path | None = None) -> Path:
    unknown = set(technique_config) - set(DEFAULT_TECHNIQUE_CONFIG)
    if unknown:
        raise ValueError(f"unknown technique settings: {sorted(unknown)}")
    session_dir = SESSIONS_ROOT / run_label / f"{question['id']}-{model_label}-{uuid.uuid4().hex[:8]}"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(json.dumps({
        "question_id": question["id"], "question": question["question"], "escalate": question.get("escalate", False),
        "technique_config": technique_config, "model": model_label, "snapshot_path": str(Path(snapshot_path).resolve()),
        "example_library_path": str(example_library_path) if example_library_path else None}, indent=1))
    return session_dir


def build_prompt(session_dir: Path) -> str:
    """The evaluated agent's whole briefing. It sees the question and the CLI, never the answer or the data files."""
    meta = json.loads((session_dir / "session.json").read_text())
    config = {**DEFAULT_TECHNIQUE_CONFIG, **meta["technique_config"]}
    cli = f"python3 {ROOT / 'bench/agent_cli.py'} --session {shlex.quote(str(session_dir))}"
    commands = [f"{cli} context            # views, columns and business definitions: run this first"]
    if config["enable_sql"]:
        commands.append(f'{cli} sql "SELECT ..."   # read-only DuckDB SQL over the listed views; shows up to 20 rows')
    if config["enable_metrics"]:
        commands.append(f"{cli} metrics '{{\"metrics\": [\"denial_rate\"], \"group_by\": [\"payer_name\"]}}'   "
                        "# typed semantic-layer query; metric/dimension names are listed by context")
    if config["enable_lookup"]:
        commands.append(f'{cli} lookup payer_name "north"   # resolve a free-text value before filtering on it')
    if config["enable_examples"]:
        commands.append(f'{cli} examples "<the question>"   # similar solved questions with their queries')
    finals = [f'{cli} answer --sql "SELECT ..." --text "<one-paragraph answer>"']
    if config["enable_metrics"]:
        finals.append(f"{cli} answer --metrics '<json>' --text \"<one-paragraph answer>\"")
    finals.append(f'{cli} refuse "<reason>"   # for requests you must not fulfil')
    if config["enable_ask"]:
        finals.append(f'{cli} ask "<clarifying question>"   # only if the question cannot be answered as asked')
    return (
        "You are ClaimClarity, an analytics assistant for a clinical lab's insurance claims (837P) and remittances (835).\n"
        "Answer the user's question by querying the data through the command-line tool below. Use the Bash tool ONLY to "
        "run these exact commands; do not read, list, search or open any file or directory, do not run any other program, "
        "and do not write code. The answer is scored by re-running the query you submit, so the submitted query itself "
        "must compute the answer from the data.\n\n"
        "Commands:\n" + "\n".join(commands) + "\n\nFinish with exactly one of:\n" + "\n".join(finals) +
        f"\n\nYou may make at most {config['max_tool_calls']} commands besides context. "
        f"Then reply with the single word DONE.\n\nUser question: {meta['question']}\n")


def bootstrap_instruction(session_dir: Path) -> str:
    """What the orchestrator sends the subagent: one command that prints its full briefing."""
    return (f"Run this command with the Bash tool and follow the instructions it prints exactly:\n"
            f"python3 {ROOT / 'bench/agent_cli.py'} --session {shlex.quote(str(session_dir))} briefing")


def _reads_a_table(sql: str) -> bool:
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return False
    return any(t.db for t in tree.find_all(exp.Table))


def score_session(session_dir: Path, case: dict, gold_rows: list | None) -> dict:
    meta = json.loads((session_dir / "session.json").read_text())
    calls_path = session_dir / "calls.jsonl"
    calls = [json.loads(l) for l in calls_path.read_text().splitlines()] if calls_path.exists() else []
    final_path = session_dir / "final.json"
    final = json.loads(final_path.read_text()) if final_path.exists() else None
    result = dict(question_id=meta["question_id"], model=meta["model"], technique_config=meta["technique_config"],
                  tool_calls=len([c for c in calls if c["command"] in INVESTIGATIVE_COMMANDS]),
                  tool_errors=sum(not c["ok"] for c in calls), final_kind=final and final["kind"])
    if case.get("expect_refusal"):
        result.update(correct=bool(final and final["kind"] == "refusal"),
                      why="refused" if final and final["kind"] == "refusal" else "did not refuse")
        return result
    if final is None or final["kind"] != "answer":
        result.update(correct=False, why=f"no answer ({final['kind'] if final else 'nothing submitted'})")
        return result
    if not _reads_a_table(final["sql"]):
        result.update(correct=False, why="final query reads no data (not grounded)", sql=final["sql"])
        return result
    con = open_snapshot(Path(meta["snapshot_path"]), escalate=meta.get("escalate", False))
    try:
        rows = con.execute(guard(final["sql"], 1000, meta.get("escalate", False))).fetchall()
    except Exception as e:                                             # noqa: BLE001
        result.update(correct=False, why=f"final SQL fails: {str(e)[:200]}", sql=final["sql"])
        return result
    spec = case.get("compare", {})
    if spec.get("order") == "topn":
        rows = rows[:max(spec["n"], len(rows))]
    ok, why = match(gold_rows, rows, spec)
    result.update(correct=ok, why=why, sql=final["sql"], via=final.get("via"), answer_text=final.get("text", "")[:400])
    return result
