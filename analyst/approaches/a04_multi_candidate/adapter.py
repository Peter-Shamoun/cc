"""Run A04 as a config of `bench.eval_subagents`.

`prepare` only accepts technique settings the sandbox already knows. A04 is not a new flag
(adding one would change the harness). It is the screen-2 base config — basic glossary,
goal briefing, free SQL, effort low, everything else off — plus this module filling the
session the harness already scores.

    python -c "import json; from analyst.approaches.a04_multi_candidate import configs; json.dump(configs(), open('a04.json','w'))"
    python -m bench.eval_subagents prepare RUN --configs a04.json --models haiku --repeats 1
    # then, instead of a paid subagent, complete the sessions with an injected backend:
    # complete_manifest('RUN', backend)
    python -m bench.eval_subagents score RUN

`MultiCandidateAnalyst` is the same protocol behind `bench.eval`'s `--agent` loader.
Neither constructor calls an API. Pass a backend, or the call fails.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from bench.agent_session import SESSIONS_ROOT
from bench.agents import Answer, Tools
from bench.dataset import open_snapshot

from .executor import ToolsExecutor
from .prompts import PURPOSE_SELECT
from .protocol import Decision, run_protocol

CONFIG_NAME = "a04_multi_candidate"

# Screen-2 base from the literature review. Held fixed so a later contrast measures
# multi-candidate selection, not a glossary or effort change.
TECHNIQUE_CONFIG = {
    "glossary": "basic",
    "data_dictionary": False,
    "data_profile": False,
    "enable_sql": True,
    "enable_metrics": False,
    "enable_lookup": False,
    "enable_examples": False,
    "enable_ask": False,
    "sql_error_hints": False,
    "result_checks": False,
    "context_style": "goal",
    "effort": "low",
    "max_tool_calls": 12,
}


def technique_config() -> dict:
    return dict(TECHNIQUE_CONFIG)


def configs() -> dict:
    """The `configs.json` object `bench.eval_subagents prepare` accepts."""
    return {CONFIG_NAME: technique_config()}


class MultiCandidateAnalyst:
    """`python -m bench.eval run --agent analyst.approaches.a04_multi_candidate:MultiCandidateAnalyst`

    `bench.agents.load` constructs this with no arguments. Set `backend` on the instance
    before `answer`, or pass one in. There is no default model client.
    """

    def __init__(self, backend=None):
        self.backend = backend

    def answer(self, question, tools) -> Answer:
        if self.backend is None:
            raise RuntimeError(
                "A04 has no completion backend. Pass a ScriptedBackend or another local backend. "
                "This module does not call a paid API."
            )
        decision = run_protocol(question, tools.describe(), self.backend, ToolsExecutor(tools),
                                escalate=bool(getattr(tools, "escalate", False)))
        return Answer(sql=decision.sql, rows=None if decision.abstained or decision.refused else decision.rows,
                      text=decision.text, refused=decision.refused, usage=decision.usage, trace=decision.trace)


def complete_session(session_dir: Path, backend) -> Decision:
    """Execute A04 inside one prepared session and write the files `score_session` reads."""
    session_dir = Path(session_dir)
    final_path = session_dir / "final.json"
    if final_path.exists() or (session_dir / "calls.jsonl").exists():
        raise FileExistsError(f"{session_dir} already has a completed or partial A04 transcript")
    meta = json.loads((session_dir / "session.json").read_text())
    connection = open_snapshot(Path(meta["snapshot_path"]), escalate=meta.get("escalate", False))
    try:
        tools = Tools(connection, escalate=meta.get("escalate", False))
        decision = run_protocol(meta["question"], tools.describe(), backend, ToolsExecutor(tools),
                                escalate=meta.get("escalate", False))
    finally:
        connection.close()
    _write_calls(session_dir, decision)
    (session_dir / "a04_decision.json").write_text(json.dumps(decision.record(), indent=1, default=str))
    if decision.refused:
        final = {"kind": "refusal", "text": decision.text}
    elif decision.abstained:
        final = {"kind": "abstain", "text": decision.text}
    else:
        final = {"kind": "answer", "sql": decision.sql, "text": decision.text, "via": "a04"}
    final_path.write_text(json.dumps(final))
    return decision


def complete_manifest(run_label: str, backend, only_config: str = CONFIG_NAME) -> int:
    """Complete every manifest entry whose config name is A04. Returns how many it wrote."""
    manifest = json.loads((SESSIONS_ROOT / run_label / "manifest.json").read_text())
    completed = 0
    for entry in manifest["entries"]:
        if entry.get("config") != only_config:
            continue
        complete_session(Path(entry["session"]), backend)
        completed += 1
    return completed


def _write_calls(session_dir: Path, decision: Decision) -> None:
    path = session_dir / "calls.jsonl"
    with path.open("a") as handle:
        for candidate in decision.candidates:
            if candidate.refused:
                handle.write(_line("a04_refuse", {"strategy": candidate.strategy, "reason": candidate.refusal_reason},
                                    True, "refused"))
            elif candidate.sql:
                ok = candidate.error is None
                result = {"row_count": len(candidate.rows)} if ok else candidate.error
                handle.write(_line("sql", {"query": candidate.sql, "strategy": candidate.strategy}, ok, result))
            else:
                handle.write(_line("a04_generate", {"strategy": candidate.strategy}, False, candidate.error))
        if decision.selector_used:
            handle.write(_line("a04_select", {"purpose": PURPOSE_SELECT}, decision.kind == "answer",
                                decision.selector_reason))


def _line(command: str, argument, ok: bool, result) -> str:
    return json.dumps(dict(t=round(time.time(), 3), command=command, argument=argument, ok=ok, result=result),
                      default=str) + "\n"


__all__ = ["CONFIG_NAME", "MultiCandidateAnalyst", "complete_manifest", "complete_session", "configs",
           "technique_config"]
