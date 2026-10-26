"""Make A06 a config that bench/eval_subagents.py can prepare.

The harness scores whatever SQL the subagent submits. It has no hook for a
probe stage, so this adapter does two things and does not edit bench/:

- configs_document() is the dict --configs expects. The flags match the
  held-out base (free SQL, basic glossary, no static profile, goal briefing).
  Exploration is not confounded with the screen-1 pitfalls text or with
  data_profile.
- prepare_eval_run() calls eval_subagents.prepare, then rewrites each
  manifest prompt so the subagent runs the four probe shapes before it answers.

The code path that compresses notes itself is protocol.run. Use that when a
backend can return a plan and a final SQL. The rewritten prompt is the fallback
for the existing one-subagent-per-question loop.
"""
from __future__ import annotations

import json
from pathlib import Path

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG

from .prompts import PROTOCOL_HEADER, harness_protocol

APPROACH_ID = "a06_explore_first"

# Written out so a manifest shows the arm. The test locks this to the harness
# defaults: the treatment is the protocol, not a flag.
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
    "effort": "default",
    "max_tool_calls": 12,
}


def technique_config() -> dict:
    # The base flags A06 pins must still be the defaults; settings added to the defaults later are not drift.
    drifted = {k for k, v in TECHNIQUE_CONFIG.items() if DEFAULT_TECHNIQUE_CONFIG.get(k) != v}
    if drifted:
        raise RuntimeError(f"technique config drifted from the held-out base flags in bench.agent_cli: {sorted(drifted)}")
    return dict(DEFAULT_TECHNIQUE_CONFIG)


def configs_document() -> dict:
    return {APPROACH_ID: technique_config()}


def augment_prompt(harness_prompt: str) -> str:
    """Insert the probe protocol just before the user question, and leave the question last."""
    if PROTOCOL_HEADER in harness_prompt:
        return harness_prompt
    protocol = harness_protocol().rstrip() + "\n\n"
    marker = "User question:"
    if marker in harness_prompt:
        head, matched, tail = harness_prompt.rpartition(marker)
        return head + protocol + matched + tail
    return harness_prompt.rstrip() + "\n\n" + protocol


def apply_protocol(manifest: dict) -> dict:
    """Return a copy of an eval_subagents manifest whose prompts carry the protocol."""
    if "entries" not in manifest:
        raise ValueError("manifest has no entries")
    entries = []
    for entry in manifest["entries"]:
        if "prompt" not in entry:
            raise ValueError("manifest entry has no prompt")
        updated = dict(entry)
        updated["prompt"] = augment_prompt(entry["prompt"])
        updated["approach"] = APPROACH_ID
        entries.append(updated)
    rewritten = dict(manifest)
    rewritten["entries"] = entries
    rewritten["approach"] = APPROACH_ID
    return rewritten


def prepare_eval_run(run_label: str, models: list[str], repeats: int, only: str | None = None,
                     snapshot_path: str | Path | None = None) -> Path:
    """prepare() a run, then rewrite its manifest prompts. Scoring is unchanged."""
    from bench import eval_subagents
    manifest_path = Path(eval_subagents.prepare(
        run_label, configs_document(), list(models), repeats, only,
        Path(snapshot_path) if snapshot_path else None))
    manifest = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps(apply_protocol(manifest), indent=1))
    return manifest_path
