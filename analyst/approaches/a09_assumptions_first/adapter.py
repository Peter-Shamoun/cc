"""Adapter from A09 onto `bench.eval_subagents`.

`eval_subagents` accepts a config dict whose keys are technique settings in
`bench.agent_cli.DEFAULT_TECHNIQUE_CONFIG`. It builds the briefing itself, and
it has no slot for a custom protocol. Two entry points, on purpose:

- `eval_config.json` / `TECHNIQUE_CONFIG` is a legal config. Loading it with
  `python -m bench.eval_subagents prepare` runs the screen-2 base settings
  (basic glossary, goal briefing, free SQL, ask off, effort low). It does not
  install this protocol. The harness cannot reject an early SQL command.
- `prepare_run` calls that prepare, then rewrites every manifest prompt with
  the assumptions-first briefing. The orchestrator that reads `prompt` is the
  one that sees the protocol. Scoring is unchanged: the harness still
  re-executes the submitted SQL.

The code gate that actually refuses SQL-before-interpret lives in `protocol.run`,
which is what the scripted test drives. The manifest rewrite is the part a
subagent run can see without an edit to the harness.
"""
from __future__ import annotations

import json
from pathlib import Path

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG

from .prompts import augment_harness_prompt

CONFIG_NAME = "a09_assumptions_first"

# Screen-2 base from the SOTA refresh: do not launder the screen-1 pitfalls text
# into this arm. Ask stays off because a clarification scores as no answer.
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

_CONFIG_PATH = Path(__file__).with_name("eval_config.json")


def technique_config() -> dict:
    unknown = set(TECHNIQUE_CONFIG) - set(DEFAULT_TECHNIQUE_CONFIG)
    if unknown:
        raise ValueError(f"A09 config uses technique settings the harness rejects: {sorted(unknown)}")
    return dict(TECHNIQUE_CONFIG)


def load_eval_config() -> dict:
    return {CONFIG_NAME: technique_config()}


def augment_prompt(harness_prompt: str) -> str:
    return augment_harness_prompt(harness_prompt)


def apply_approach_to_manifest(manifest: dict) -> dict:
    """Return a copy of a prepare() manifest whose prompts carry the protocol."""
    rewritten = json.loads(json.dumps(manifest))
    for entry in rewritten.get("entries", []):
        entry["prompt"] = augment_prompt(entry.get("prompt", ""))
        entry["approach"] = CONFIG_NAME
    rewritten["approach"] = CONFIG_NAME
    configs = rewritten.get("configs")
    if isinstance(configs, dict):
        configs.setdefault(CONFIG_NAME, technique_config())
    return rewritten


def prepare_run(run_label: str, models: list[str], repeats: int = 1, only: str | None = None,
                snapshot_path=None) -> Path:
    """`eval_subagents.prepare`, then install the assumptions-first prompt on every entry."""
    from bench.eval_subagents import prepare

    manifest_path = prepare(run_label, load_eval_config(), models, repeats, only, snapshot_path)
    manifest = json.loads(Path(manifest_path).read_text())
    Path(manifest_path).write_text(json.dumps(apply_approach_to_manifest(manifest), indent=1))
    return Path(manifest_path)
