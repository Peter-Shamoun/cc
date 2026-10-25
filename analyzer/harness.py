"""Adapter for ``bench.eval_subagents``.

``prepare --agent analyzer`` writes each manifest entry's prompt from this module
and attaches the Anthropic tool spec for the same flags. A Claude Code subagent
follows the prompt and drives ``python -m analyzer.cli``. An API driver can ignore
the shell prompt and call ``Analyzer`` with ``tool_spec`` plus ``analyzer_config``.

Candidates and the Haiku-then-Sonnet cascade belong to the in-process loop. The
manifest already crosses models, so the subagent briefing is one trajectory.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import replace
from pathlib import Path

from analyzer.config import HAIKU_MODEL, SONNET_MODEL, AnalyzerConfig
from analyzer.prompts import procedure_text, refusal_text
from analyzer.tools import _glossary_body, tool_spec

_MODEL_IDS = {
    "haiku": HAIKU_MODEL,
    "sonnet": SONNET_MODEL,
    "claude-haiku-5-5": HAIKU_MODEL,
    "claude-sonnet-5-5": SONNET_MODEL,
}


def config_from_meta(meta: dict, *, for_subagent: bool = False) -> AnalyzerConfig:
    from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
    merged = {**DEFAULT_TECHNIQUE_CONFIG, **(meta.get("technique_config") or {})}
    model = _MODEL_IDS.get(meta.get("model") or "", meta.get("model") or HAIKU_MODEL)
    config = AnalyzerConfig.from_technique(merged, model=model)
    if for_subagent:
        config = replace(config, candidates=1, cascade="off")
    return config


def build_subagent_prompt(session_dir: Path) -> str:
    """Briefing for one evaluated subagent. Same contract as ``bench.agent_session.build_prompt``."""
    session_dir = Path(session_dir)
    meta = json.loads((session_dir / "session.json").read_text())
    config = config_from_meta(meta, for_subagent=True)
    cli = f"python3 -m analyzer.cli --session {shlex.quote(str(session_dir))}"
    commands = [
        f"{cli} list",
        f"{cli} describe sem.claims",
        f"{cli} sample sem.claims",
    ]
    if config.data_dictionary:
        commands.append(f"{cli} dictionary")
    if config.data_profile:
        commands.append(f"{cli} profile")
    if config.glossary != "none":
        commands.append(f'{cli} glossary "denial"')
    if config.enable_sql:
        commands.append(f'{cli} sql "SELECT ..."')
    if config.enable_metrics:
        commands.append(f"{cli} metrics '{{\"metrics\": [\"denial_rate\"], \"group_by\": [\"payer_name\"]}}'")
    if config.enable_lookup:
        commands.append(f'{cli} lookup payer_name "north"')
    if config.enable_examples:
        commands.append(f'{cli} examples "<the question>"')
    commands.append(f'{cli} escalate "<why sem.* cannot answer>"')
    finals = [f'{cli} answer --sql "SELECT ..." --text "<one-paragraph answer>"',
              f'{cli} refuse "<reason>"']
    if config.enable_ask:
        finals.append(f'{cli} ask "<clarifying question>"')
    glossary = _glossary_body(config).strip()
    return (
        "You are ClaimClarity, an analytics assistant for one clinical lab's insurance claims (837P) and remittances (835).\n"
        "Answer the user's question by running only the commands below with the Bash tool. Do not read, list, or open any file, "
        "and do not run any other program. The answer is scored by re-running the SQL you submit, so that SQL must compute the "
        "answer from the data.\n\n"
        + refusal_text(config) + "\n" + procedure_text(config) + "\n"
        + (("Business definitions:\n" + glossary + "\n\n") if glossary else "")
        + "Commands:\n" + "\n".join(commands) + "\n\nFinish with exactly one of:\n" + "\n".join(finals)
        + f"\n\nYou may make at most {config.max_tool_calls} commands besides the final one. "
        + f"Then reply with the single word DONE.\n\nUser question: {meta['question']}\n"
    )


def manifest_fields(session_dir: Path) -> dict:
    """What ``eval_subagents.prepare --agent analyzer`` stores on each entry."""
    session_dir = Path(session_dir)
    meta = json.loads((session_dir / "session.json").read_text())
    full = config_from_meta(meta, for_subagent=False)
    shown = config_from_meta(meta, for_subagent=True)
    return {
        "prompt": build_subagent_prompt(session_dir),
        "tool_spec": tool_spec(shown),
        "analyzer_config": full.screen_dict(),
    }
