"""Batch evaluation of technique configurations with real Haiku / Sonnet subagents.

    python -m bench.eval_subagents prepare RUN --configs configs.json --models haiku,sonnet --repeats 3 [--only cat]
        -> .ccsim/agent_sessions/RUN/manifest.json  (one entry per session: path, model, prompt)
    (the orchestrator runs one subagent per manifest entry, with the entry's model and prompt)
    python -m bench.eval_subagents score RUN
        -> .ccsim/agent_sessions/RUN/results.json + results.md (accuracy by config x model, pass^k, calls, errors)

configs.json: {"<config name>": {<technique settings for bench/agent_cli.py>}, ...}
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.agent_session import SESSIONS_ROOT, build_prompt, create_session, score_session  # noqa: E402
from bench.dataset import open_snapshot, snapshot  # noqa: E402
from bench.eval import load_cases  # noqa: E402


def prepare(run_label: str, configs: dict, models: list[str], repeats: int, only: str | None = None,
            snapshot_path: Path | None = None) -> Path:
    snap = Path(snapshot_path) if snapshot_path else snapshot()
    run_dir = SESSIONS_ROOT / run_label
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} exists; choose a new run label")
    entries = []
    for case in load_cases(only, snapshot_path=snap):
        for config_name, technique_config in configs.items():
            for model in models:
                for repeat in range(repeats):
                    s = create_session(snap, case, technique_config, model, run_label)
                    meta = json.loads((s / "session.json").read_text())
                    meta.update(config_name=config_name, repeat=repeat)
                    (s / "session.json").write_text(json.dumps(meta, indent=1))
                    entries.append(dict(session=str(s), model=model, config=config_name, question_id=case["id"],
                                        repeat=repeat, prompt=build_prompt(s)))
    (run_dir / "manifest.json").write_text(json.dumps(dict(run=run_label, snapshot=str(snap), configs=configs,
                                                           entries=entries), indent=1))
    return run_dir / "manifest.json"


def score(run_label: str) -> dict:
    run_dir = SESSIONS_ROOT / run_label
    manifest = json.loads((run_dir / "manifest.json").read_text())
    cases = {c["id"]: c for c in load_cases(snapshot_path=Path(manifest["snapshot"]))}
    gold_cache = {}
    rows = []
    for e in manifest["entries"]:
        case = cases[e["question_id"]]
        if not case.get("expect_refusal") and case["id"] not in gold_cache:
            gold_cache[case["id"]] = open_snapshot(Path(manifest["snapshot"]), escalate=case.get("escalate", False)) \
                .execute(case["gold_sql"]).fetchall()
        r = score_session(Path(e["session"]), case, gold_cache.get(case["id"]))
        r.update(config=e["config"], repeat=e["repeat"], category=case["category"], difficulty=case["difficulty"])
        rows.append(r)
    groups = defaultdict(list)
    for r in rows:
        groups[(r["config"], r["model"])].append(r)
    summary = []
    for (config, model), rs in sorted(groups.items()):
        by_q = defaultdict(list)
        for r in rs:
            by_q[r["question_id"]].append(r["correct"])
        summary.append(dict(config=config, model=model, runs=len(rs), accuracy=round(sum(r["correct"] for r in rs) / len(rs), 3),
                            pass_all_repeats=round(sum(all(v) for v in by_q.values()) / len(by_q), 3),
                            mean_tool_calls=round(statistics.mean(r["tool_calls"] for r in rs), 2),
                            mean_tool_errors=round(statistics.mean(r["tool_errors"] for r in rs), 2),
                            no_answer=sum(r["final_kind"] is None for r in rs)))
    out = dict(run=run_label, summary=summary, results=rows)
    (run_dir / "results.json").write_text(json.dumps(out, indent=1, default=str))
    md = ["| config | model | runs | accuracy | pass^k | tool calls | tool errors | no answer |", "|---|---|---|---|---|---|---|---|"]
    md += [f"| {s['config']} | {s['model']} | {s['runs']} | {s['accuracy']:.1%} | {s['pass_all_repeats']:.1%} | "
           f"{s['mean_tool_calls']} | {s['mean_tool_errors']} | {s['no_answer']} |" for s in summary]
    (run_dir / "results.md").write_text("\n".join(md) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["prepare", "score"])
    ap.add_argument("run")
    ap.add_argument("--configs")
    ap.add_argument("--models", default="haiku,sonnet")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only")
    ap.add_argument("--snapshot")
    a = ap.parse_args()
    if a.cmd == "prepare":
        print(prepare(a.run, json.loads(Path(a.configs).read_text()), a.models.split(","), a.repeats, a.only, a.snapshot))
    else:
        out = score(a.run)
        print((SESSIONS_ROOT / a.run / "results.md").read_text())


if __name__ == "__main__":
    main()
