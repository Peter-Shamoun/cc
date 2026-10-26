"""Rounds of subagent work for a prepared run, so a workflow only needs queue indices, not paths and prompts.

    python -m bench.subagent_queue build RUN      -> .ccsim/agent_sessions/RUN/queue_<n>.json, prints the round
    python -m bench.subagent_queue brief QUEUE I  -> the briefing the I-th subagent of that queue follows

A round holds every single-agent session that has not been attempted yet, and the actions ``analyzer.orchestration
next`` returns for every orchestrated session that is not done. Run all of a round's subagents to the end before
building the next round: ``next`` treats an issued role that wrote nothing as finished without an answer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.agent_session import SESSIONS_ROOT  # noqa: E402

ATTEMPTED_FILE = "attempted_single_agent_sessions.json"


def build_round(run_label: str) -> dict:
    from analyzer.orchestration import next_actions
    run_dir = SESSIONS_ROOT / run_label
    entries = json.loads((run_dir / "manifest.json").read_text())["entries"]
    attempted_path = run_dir / ATTEMPTED_FILE
    attempted = set(json.loads(attempted_path.read_text())) if attempted_path.exists() else set()
    actions, done = [], 0
    for entry in entries:
        if entry.get("orchestration"):
            result = next_actions(entry["session"])
            if result["status"] == "done":
                done += 1
            else:
                actions += [dict(session=a["session"], model=a["model"], effort=a["effort"], config=entry["config"])
                            for a in result["actions"]]
        elif entry["session"] in attempted:
            done += 1
        else:
            actions.append(dict(session=entry["session"], model=entry["model"], effort=entry["effort"],
                                config=entry["config"]))
            attempted.add(entry["session"])
    attempted_path.write_text(json.dumps(sorted(attempted)))
    actions.sort(key=lambda a: (a["model"], a["effort"]))     # each (model, effort) is one contiguous index range
    round_number = len(list(run_dir.glob("queue_*.json")))
    queue_path = run_dir / f"queue_{round_number}.json"
    queue_path.write_text(json.dumps(actions, indent=1))
    ranges = []
    for index, action in enumerate(actions):
        if ranges and (ranges[-1]["model"], ranges[-1]["effort"]) == (action["model"], action["effort"]):
            ranges[-1]["end"] = index + 1
        else:
            ranges.append(dict(model=action["model"], effort=action["effort"], start=index, end=index + 1))
    return dict(queue=str(queue_path), actions=len(actions), sessions_done=done, sessions=len(entries), ranges=ranges)


def briefing(queue_path: str, index: int) -> int:
    """Prints the briefing (analyzer.cli does); returns its exit code."""
    from analyzer.cli import main as analyzer_cli
    action = json.loads(Path(queue_path).read_text())[index]
    return analyzer_cli(["--session", action["session"], "briefing"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["build", "brief"])
    parser.add_argument("target")
    parser.add_argument("index", nargs="?", type=int)
    args = parser.parse_args()
    if args.cmd == "build":
        print(json.dumps(build_round(args.target)))
    else:
        sys.exit(briefing(args.target, args.index))
