"""Batch evaluation of technique configurations with real Haiku / Sonnet subagents.

    python -m bench.eval_subagents prepare RUN --configs configs.json --models haiku,sonnet --repeats 3 [--only cat]
        [--agent cli|analyzer]
        -> .ccsim/agent_sessions/RUN/manifest.json  (one entry per session: path, model, prompt)
        --agent analyzer writes the analyzer's briefing plus its tool spec (analyzer.harness). Default stays the CLI.
    (the orchestrator runs one subagent per manifest entry, with the entry's model and prompt)
    python -m bench.eval_subagents score RUN
        -> .ccsim/agent_sessions/RUN/results.json + results.md (accuracy by config x model, pass^k, calls, errors)
    python -m bench.eval_subagents contrast RUN --baseline base   (every other config minus the baseline, per model,
        paired by question, question-level bootstrap 95% interval)
    python -m bench.eval_subagents effects RUN --design research/screening_design.json
        -> main effect of each two-level factor per model: accuracy at high minus low, with a question-level
           bootstrap 95% interval (questions are resampled, so the interval reflects question-set variance)

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
            snapshot_path: Path | None = None, agent: str = "cli") -> Path:
    if agent not in {"cli", "analyzer"}:
        raise ValueError(f"unknown agent {agent!r}; expected cli or analyzer")
    snap = Path(snapshot_path) if snapshot_path else snapshot()
    run_dir = SESSIONS_ROOT / run_label
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} exists; choose a new run label")
    entries = []
    cases = load_cases(snapshot_path=snap)
    if only:
        wanted = set(only.split(","))
        cases = [c for c in cases if wanted & {c["id"], c["category"], c["difficulty"], c.get("family"), c.get("question_set")}]
    for case in cases:
        for config_name, technique_config in configs.items():
            for model in models:
                for repeat in range(repeats):
                    library = snap / "generated_questions.json" if technique_config.get("enable_examples") else None
                    s = create_session(snap, case, technique_config, model, run_label, library)
                    meta = json.loads((s / "session.json").read_text())
                    meta.update(config_name=config_name, repeat=repeat)
                    (s / "session.json").write_text(json.dumps(meta, indent=1))
                    if agent == "analyzer":
                        from analyzer.harness import manifest_fields
                        fields = manifest_fields(s)
                        prompt = fields.pop("prompt")
                    else:
                        fields = {}
                        prompt = build_prompt(s)
                    entries.append(dict(session=str(s), model=model, config=config_name, question_id=case["id"],
                                        repeat=repeat, effort=technique_config.get("effort", "default"),
                                        agent=agent, prompt=prompt, **fields))
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


def effects(run_label: str, design_path: Path, resamples: int = 2000) -> dict:
    import random
    design = json.loads(Path(design_path).read_text())
    factor_names = list(design["factors"])
    level_of = {r["config"]: r for r in design["runs"]}
    rows = json.loads((SESSIONS_ROOT / run_label / "results.json").read_text())["results"]
    out = {}
    for model in sorted({r["model"] for r in rows}):
        # per question: mean correctness at each level of each factor (balanced design: each level has 8 configs)
        by_question = defaultdict(lambda: defaultdict(list))
        for r in rows:
            if r["model"] == model and r["config"] in level_of:
                for f in factor_names:
                    by_question[r["question_id"]][(f, level_of[r["config"]][f])].append(r["correct"])
        questions = sorted(by_question)
        def effect(qs, f):
            diffs = [statistics.mean(by_question[q][(f, 1)]) - statistics.mean(by_question[q][(f, 0)]) for q in qs
                     if by_question[q][(f, 1)] and by_question[q][(f, 0)]]
            return statistics.mean(diffs) if diffs else None
        rng = random.Random(0)
        out[model] = {}
        for f in factor_names:
            boot = sorted(e for e in (effect([rng.choice(questions) for _ in questions], f) for _ in range(resamples))
                          if e is not None)
            out[model][f] = dict(effect=round(effect(questions, f), 4), ci95=[round(boot[int(.025 * len(boot))], 4),
                                                                            round(boot[int(.975 * len(boot)) - 1], 4)])
    return out


def _bootstrap_interval(values: list[float], resamples: int, seed: int = 0) -> list[float]:
    import random
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(values) for _ in values) for _ in range(resamples))
    return [round(means[int(.025 * resamples)], 4), round(means[int(.975 * resamples) - 1], 4)]


def contrast(run_label: str, baseline: str, resamples: int = 2000) -> dict:
    rows = json.loads((SESSIONS_ROOT / run_label / "results.json").read_text())["results"]
    by = defaultdict(list)
    for r in rows:
        by[(r["model"], r["config"], r["question_id"])].append(r["correct"])
    out = {}
    for model in sorted({r["model"] for r in rows}):
        questions = sorted({q for m, _, q in by if m == model})
        out[model] = {}
        for config in sorted({c for m, c, _ in by if m == model} - {baseline}):
            diffs = [statistics.mean(by[(model, config, q)]) - statistics.mean(by[(model, baseline, q)])
                     for q in questions if by[(model, config, q)] and by[(model, baseline, q)]]
            out[model][config] = dict(minus=baseline, effect=round(statistics.mean(diffs), 4),
                                      ci95=_bootstrap_interval(diffs, resamples), questions=len(diffs))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["prepare", "score", "effects", "contrast"])
    ap.add_argument("--baseline")
    ap.add_argument("--design")
    ap.add_argument("run")
    ap.add_argument("--configs")
    ap.add_argument("--models", default="haiku,sonnet")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only")
    ap.add_argument("--snapshot")
    ap.add_argument("--agent", choices=["cli", "analyzer"], default="cli",
                    help="cli: bench.agent_cli briefing. analyzer: analyzer prompt and tool spec")
    a = ap.parse_args()
    if a.cmd == "prepare":
        print(prepare(a.run, json.loads(Path(a.configs).read_text()), a.models.split(","), a.repeats, a.only,
                      a.snapshot, a.agent))
    elif a.cmd == "contrast":
        print(json.dumps(contrast(a.run, a.baseline), indent=1))
    elif a.cmd == "effects":
        print(json.dumps(effects(a.run, Path(a.design)), indent=1))
    else:
        out = score(a.run)
        print((SESSIONS_ROOT / a.run / "results.md").read_text())


if __name__ == "__main__":
    main()
