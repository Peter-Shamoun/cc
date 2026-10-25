"""Run A05 as a bench.eval_subagents configuration.

eval_subagents.prepare builds a manifest from technique settings and a single-agent
briefing. It has no slot for a second model context, and create_session rejects unknown
settings. This adapter does three things:

  1. TECHNIQUE_CONFIG uses only settings create_session already accepts. The factor under
     test is the protocol, so the config stays on the held-out base: basic glossary, goal
     briefing, free SQL, low effort. Pitfalls text, examples, metrics, and result checks
     stay off.
  2. prepare() calls eval_subagents.prepare, then rewrites each a05 manifest prompt so the
     orchestrator dispatches the session to run_session instead of a free-form subagent.
  3. run_session executes the protocol against the session sandbox and writes final.json.
     python -m bench.eval_subagents score RUN then scores it unchanged.

Passing config.json to eval_subagents.prepare alone leaves the standard briefing in place.
The critic runs only through prepare() / run_session() in this module.
"""
from __future__ import annotations

import json
from pathlib import Path

from analyst.approaches.a05_verifier.backend import LanguageModel
from analyst.approaches.a05_verifier.checklist import checklist_sha256
from analyst.approaches.a05_verifier.executor import BenchSandboxExecutor
from analyst.approaches.a05_verifier.protocol import run_protocol

CONFIG_NAME = "a05_verifier"
RUNNER = "analyst.approaches.a05_verifier.adapter.run_session"

# Keys are a subset of bench.agent_cli.DEFAULT_TECHNIQUE_CONFIG. max_tool_calls is 3:
# the initial SELECT, one execution repair, and one critic-driven revision.
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
    "max_tool_calls": 3,
}


def configs() -> dict:
    return {CONFIG_NAME: dict(TECHNIQUE_CONFIG)}


def protocol_briefing(base_prompt: str) -> str:
    return (
        f"A05_VERIFIER_RUNNER {RUNNER}\n"
        "This session is scored by that runner: the analyst writes a plan and one SELECT, the sandbox "
        "executes it, a critic in a fresh context checks semantics, population, units, and invariants, "
        "and the analyst may revise once. Dispatch this entry to the runner. A free-form agent that "
        "answers the question itself is a different approach.\n\n"
        "Harness briefing the runner's sandbox enforces:\n\n" + base_prompt
    )


def prepare(run_label: str, models: list[str], repeats: int = 1, only: str | None = None,
            snapshot_path: Path | None = None) -> Path:
    """Create the eval_subagents manifest for this config, then install the runner prompts."""
    from bench.eval_subagents import prepare as eval_prepare

    manifest_path = eval_prepare(run_label, configs(), models, repeats, only, snapshot_path)
    install_protocol(manifest_path.parent)
    return manifest_path


def install_protocol(run_dir: Path) -> Path:
    """Rewrite a05 entries in an existing manifest. Safe to call once; a second call leaves them."""
    manifest_path = Path(run_dir) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["entries"]:
        if entry.get("config") != CONFIG_NAME or entry.get("approach") == CONFIG_NAME:
            continue
        entry["prompt"] = protocol_briefing(entry["prompt"])
        entry["approach"] = CONFIG_NAME
        entry["runner"] = RUNNER
    manifest["approach"] = CONFIG_NAME
    manifest["checklist_sha256"] = checklist_sha256()
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return manifest_path


def run_session(session_dir: Path, model: LanguageModel) -> dict:
    """Execute the protocol for one manifest entry. Writes final.json and a05_trace.json."""
    session_dir = Path(session_dir)
    meta = json.loads((session_dir / "session.json").read_text())
    trace = run_protocol(meta["question"], model, BenchSandboxExecutor(session_dir))
    payload = trace.to_dict()
    (session_dir / "a05_trace.json").write_text(json.dumps(payload, indent=1, default=str))
    return payload


def run_manifest(run_dir: Path, model: LanguageModel) -> list[dict]:
    """Run every a05 entry. The same backend instance is used in manifest order."""
    manifest = json.loads((Path(run_dir) / "manifest.json").read_text())
    traces = []
    for entry in manifest["entries"]:
        if entry.get("config") != CONFIG_NAME:
            continue
        traces.append(run_session(entry["session"], model))
    return traces
