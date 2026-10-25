"""Qualitative dry run: the author plays the model, the simulator executes the SQL.

    python3 -m analyst.variants.multi_candidate.dry_run

Builds a small one-customer world, opens the reader through ``ccsim.query.open_session``, and runs
``MultiCandidateAgent`` on ten original questions. Candidate SQL is the hand-written script in
``dry_run_scripts.py``. The selector, when the pool has no majority, is the three written rules in
``HandSelector`` — a person following the selector prompt, not a second model call.

The world is smaller than the 120-day lake the cost model uses. Correctness here is a procedure
check. Latency dollars stay in ``cost.py``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from bench.compare import match
from ccsim.edi_mock import Scenario
from ccsim.query import login
from ccsim.world import build_world

from analyst.variants.multi_candidate.agent import MultiCandidateAgent
from analyst.variants.multi_candidate.cost import TokenSketch, estimate
from analyst.variants.multi_candidate.dry_run_scripts import QUESTION_IDS, SCRIPTS
from analyst.variants.multi_candidate.llm import LLMResponse, ScriptedLLM, ToolCall
from analyst.variants.multi_candidate.prompts import shared_system
from analyst.variants.multi_candidate.secure import SecureQueryTools

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = ROOT / "bench" / "questions.yaml"
RECORD = Path(__file__).with_name("dry_run_record.json")


class HandSelector:
    """Selector procedure used in the dry run, applied by hand and then encoded so the run repeats.

    1. If the question says "current", keep clusters whose SQL filters ``is_current_adjudication``.
    2. If the question asks both how many and how much, keep clusters with at least two result columns.
    3. If one cluster remains, take it. Otherwise abstain.
    """

    def __init__(self):
        self.calls = 0
        self.traces: list[dict] = []

    def choose(self, question: str, clusters) -> str | None:
        self.calls += 1
        pool = list(clusters)
        notes = [cluster.cluster_id for cluster in pool]
        lowered = question.lower()
        if "current" in lowered:
            kept = [
                cluster for cluster in pool
                if cluster.representative.sql and "is_current_adjudication" in cluster.representative.sql.lower()
            ]
            if kept:
                pool = kept
        asks_count = "how many" in lowered
        asks_amount = "how much" in lowered or "total" in lowered
        if asks_count and asks_amount:
            kept = [
                cluster for cluster in pool
                if cluster.representative.rows and len(cluster.representative.rows[0]) >= 2
            ]
            if kept:
                pool = kept
        chosen = pool[0].cluster_id if len(pool) == 1 else None
        self.traces.append({"saw": notes, "kept": [cluster.cluster_id for cluster in pool], "chose": chosen})
        return chosen


def _responses(turns: list[tuple]) -> list[LLMResponse]:
    out = []
    for kind, payload, answer in turns:
        if kind == "run_sql":
            out.append(LLMResponse(tool_calls=[ToolCall("run_sql", {"sql": payload})]))
        elif kind == "final":
            out.append(LLMResponse(tool_calls=[ToolCall("final_answer", {"sql": payload, "answer": answer})]))
        elif kind == "refuse":
            out.append(LLMResponse(tool_calls=[ToolCall("refuse", {"reason": payload})]))
        else:
            raise ValueError(kind)
    return out


def _preview(rows, limit: int = 6):
    if rows is None:
        return None
    return [tuple(row) for row in rows[:limit]]


def main() -> None:
    cases = {case["id"]: case for case in yaml.safe_load(QUESTIONS.read_text())}
    missing = [qid for qid in QUESTION_IDS if qid not in cases]
    if missing:
        raise SystemExit(f"questions not in the original file: {missing}")

    scenario = Scenario(
        start=date(2026, 7, 13), days=16, volume_scale=1.0, seed=5,
        burn_in_days=21, p_corrupt_file=0.0,
    )
    world_root = Path("/tmp/cc-multi-candidate-world")
    print("building world...", flush=True)
    world = build_world(
        world_root, scenario, customers=("acme_labs",), users={"ana@xifin.example": "acme_labs"},
    )
    failed = [task for task in world.tasks() if task.exit_code]
    print(f"tasks {len(world.tasks())} failed {len(failed)}", flush=True)
    for task in failed[:4]:
        print(task.stopped_reason, flush=True)
    if failed:
        world.aws.shutdown()
        raise SystemExit("pipeline tasks failed; not opening a reader session")
    try:
        token = login(world.aws, "ana@xifin.example")
        tools = SecureQueryTools(world.aws, world.dep, token)
        schema = tools.describe()
        prefix_tokens = max(1, len(shared_system(schema)) // 4)
        print(f"schema prefix ~{prefix_tokens} tokens ({len(schema)} chars of describe)", flush=True)
        selector = HandSelector()
        recorded: list[tuple] = []

        class Recording:
            def describe(self):
                return tools.describe()

            def sql(self, query, _final=False):
                columns, result = tools.sql(query, _final=_final)
                recorded.append((query, result))
                return columns, result

        rows_out = []
        for qid in QUESTION_IDS:
            case = cases[qid]
            print(f"question {qid}", flush=True)
            llm = ScriptedLLM({name: _responses(turns) for name, turns in SCRIPTS[qid].items()})
            agent = MultiCandidateAgent(
                llm, n_candidates=3, selection="majority_then_pairwise",
                refusal_policy="any_veto", pairwise=selector, generator_model="hand",
            )
            start = len(recorded)
            answer = agent.answer(case["question"], Recording())
            executed = {}
            for query, result in recorded[start:]:
                executed[query] = result
            agent_calls = len(recorded) - start
            selection = answer.usage["selection"]
            expect_refusal = bool(case.get("expect_refusal"))
            gold_rows = None
            if not expect_refusal:
                gold_rows = tools.sql(case["gold_sql"])[1]
            spec = case.get("compare") or {}

            per_candidate = []
            for candidate in selection["candidates"]:
                kind = candidate["kind"]
                result_rows = executed.get(candidate.get("sql")) if kind == "rows" else None
                if expect_refusal:
                    ok = kind == "refusal"
                    why = "refused" if ok else "answered"
                elif kind != "rows":
                    ok, why = False, candidate.get("error") or kind
                else:
                    ok, why = match(gold_rows, result_rows, spec)
                per_candidate.append({
                    "strategy": candidate["strategy"],
                    "kind": kind,
                    "correct": ok,
                    "why": why,
                    "sql": candidate.get("sql"),
                    "error": candidate.get("error"),
                    "preview": _preview(result_rows),
                    "row_count": len(result_rows) if result_rows is not None else None,
                })

            if expect_refusal:
                chosen_ok = bool(answer.refused)
                chosen_why = "refused" if answer.refused else "did not refuse"
            elif answer.refused or answer.rows is None:
                chosen_ok, chosen_why = False, selection["reason"]
            else:
                chosen_ok, chosen_why = match(gold_rows, answer.rows, spec)
            upper = any(item["correct"] for item in per_candidate)
            record = {
                "id": qid,
                "difficulty": case.get("difficulty"),
                "chosen_correct": chosen_ok,
                "chosen_why": chosen_why,
                "chosen_strategy": selection["chosen_strategy"],
                "abstained": selection["abstained"],
                "refused": selection["refused"],
                "selector_called": selection["selector_called"],
                "cluster_sizes": selection["cluster_sizes"],
                "upper_bound": upper,
                "agent_sql_calls": agent_calls,
                "candidates": per_candidate,
            }
            rows_out.append(record)
            print(
                f"  chosen={'yes' if chosen_ok else 'NO'} upper={'yes' if upper else 'no'} "
                f"clusters={selection['cluster_sizes']} selector={selection['selector_called']} "
                f"strategy={selection['chosen_strategy']}",
                flush=True,
            )
        summary = {
            "questions": len(rows_out),
            "chosen_correct": sum(row["chosen_correct"] for row in rows_out),
            "upper_bound": sum(row["upper_bound"] for row in rows_out),
            "selector_calls": selector.calls,
            "selector_traces": selector.traces,
            "prefix_tokens_estimate": prefix_tokens,
            "scenario": {
                "days": scenario.days, "volume_scale": scenario.volume_scale,
                "burn_in_days": scenario.burn_in_days, "seed": scenario.seed, "customer": "acme_labs",
            },
        }
        sketch = TokenSketch(prefix_tokens=prefix_tokens, uncached_input_tokens=400, output_tokens=500)
        summary["cost_at_measured_prefix"] = {
            "haiku_n3": estimate(3, "claude-haiku-5-5", sketch=sketch),
            "sonnet_n3": estimate(3, "claude-sonnet-5-5", sketch=sketch),
            "haiku_n5": estimate(5, "claude-haiku-5-5", sketch=sketch),
            "sonnet_n5": estimate(5, "claude-sonnet-5-5", sketch=sketch),
        }
        payload = {"summary": summary, "results": rows_out}
        RECORD.write_text(json.dumps(payload, indent=1, default=str) + "\n")
        print(json.dumps(summary, indent=1, default=str))
        print(f"wrote {RECORD}")
    finally:
        world.aws.shutdown()


if __name__ == "__main__":
    main()
