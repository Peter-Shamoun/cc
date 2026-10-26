"""Run prepared subagent runs with local headless Claude Code (`claude -p`) workers, resumably.

    python -m bench.claude_print_runner RUN [RUN ...] --workers 12

Each run goes round by round (bench.subagent_queue builds a round; a round is finished before the next is built,
because analyzer.orchestration treats an issued role that wrote nothing as finished). Progress lives on disk in
<run>/runner_state.json, so a killed runner, an interrupted session or a restarted machine picks up the unfinished
indices of the current round. When a run has no more work it is scored (bench.eval_subagents score).

A worker is locked to the analyzer CLI: its only allowed tools are Bash(python3 -m analyzer.cli ...) and cd, in dontAsk mode,
started outside the repository so it reads no project instructions. Each worker's JSON result (cost, usage, turns)
is kept next to the session as claude_print.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.agent_session import SESSIONS_ROOT  # noqa: E402
from bench.subagent_queue import build_round  # noqa: E402

STATE_FILE = "runner_state.json"
WORKER_DIRECTORY = Path("/tmp/cc-claude-print")
WORKER_TIMEOUT_SECONDS = 900
DISALLOWED_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch", "Task", "NotebookEdit"]


def worker_command(session: str, model: str, effort: str) -> list[str]:
    # The sandbox allows only the CLI itself, so a shell loop, variable or pipe around it is denied: say so up front.
    prompt = ("Run this command with the Bash tool and follow the instructions it prints exactly:\n"
              f"python3 -m analyzer.cli --session {session} briefing\n"
              "Run each command exactly as printed, one command per Bash call: no shell variables, loops, pipes "
              "or redirection. Put any argument that contains a $ sign (dollar amounts) in single quotes.")
    command = ["claude", "-p", prompt, "--model", model, "--permission-mode", "dontAsk",
               "--allowedTools", "Bash(python3 -m analyzer.cli:*)", "Bash(cd:*)", "--add-dir", "/tmp", str(ROOT),   # cd outside the start directory needs the directory added
               "--disallowedTools", *DISALLOWED_TOOLS,
               "--output-format", "json"]
    if effort and effort != "default":
        command += ["--effort", effort]
    return command


SESSION_OUTPUT_FILES = ("audit.jsonl", "calls.jsonl", "final.json", "claude_print.json")


def run_worker(action: dict) -> dict:
    # an unfinished run (killed, interrupted) may have left calls behind; every run starts from the prepared session
    for name in SESSION_OUTPUT_FILES:
        Path(action["session"], name).unlink(missing_ok=True)
    WORKER_DIRECTORY.mkdir(exist_ok=True)
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    started = time.time()
    try:
        completed = subprocess.run(worker_command(action["session"], action["model"], action["effort"]),
                                   cwd=WORKER_DIRECTORY, env=environment, capture_output=True, text=True,
                                   timeout=WORKER_TIMEOUT_SECONDS)
        output = completed.stdout
    except subprocess.TimeoutExpired:
        output = json.dumps({"is_error": True, "result": f"timed out after {WORKER_TIMEOUT_SECONDS} s"})
    record = {"seconds": round(time.time() - started, 1)}
    try:
        record.update(json.loads(output))
    except json.JSONDecodeError:
        record.update(is_error=True, result=output[-2000:])
    Path(action["session"], "claude_print.json").write_text(json.dumps(record, indent=1))
    return record


class RunState:
    def __init__(self, run_label: str):
        self.path = SESSIONS_ROOT / run_label / STATE_FILE
        self.lock = threading.Lock()
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"queue": None, "completed": []}

    def save(self):
        self.path.write_text(json.dumps(self.data))

    def mark_completed(self, index: int):
        with self.lock:
            self.data["completed"].append(index)
            self.save()


def drive(run_label: str, pool: ThreadPoolExecutor) -> None:
    state = RunState(run_label)
    while True:
        if state.data["queue"] is None or len(set(state.data["completed"])) >= len(
                json.loads(Path(state.data["queue"]).read_text())):
            round_info = build_round(run_label)
            if round_info["actions"] == 0:
                Path(round_info["queue"]).unlink()
                break
            state.data = {"queue": round_info["queue"], "completed": []}
            state.save()
        queue = json.loads(Path(state.data["queue"]).read_text())
        pending = [i for i in range(len(queue)) if i not in set(state.data["completed"])]
        print(f"{run_label}: {Path(state.data['queue']).name}, {len(pending)} of {len(queue)} to run", flush=True)
        futures = {pool.submit(run_worker, queue[i]): i for i in pending}
        for future in as_completed(futures):           # record each as it ends, so a crash re-runs only unfinished ones
            future.result()
            state.mark_completed(futures[future])
    subprocess.run([sys.executable, "-m", "bench.eval_subagents", "score", run_label], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    print(f"{run_label}: done and scored", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    with ThreadPoolExecutor(args.workers) as pool:
        threads = [threading.Thread(target=drive, args=(run, pool)) for run in args.runs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
