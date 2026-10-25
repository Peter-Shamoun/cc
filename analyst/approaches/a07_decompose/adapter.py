"""Make A07 a config that ``bench.eval_subagents`` can prepare and score.

``prepare_run`` calls ``bench.eval_subagents.prepare`` with the technique
settings this approach needs, then replaces each manifest prompt with the
decomposition briefing. Scoring is unchanged: the subagent still finishes
through ``bench.agent_cli`` ``answer --sql``, and ``eval_subagents score``
re-executes that SQL.

The ``briefing`` subcommand of ``agent_cli`` rebuilds the standard prompt from
the technique settings. The orchestrator must send the manifest prompt this
adapter writes, and the prompt tells the subagent not to call ``briefing``.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG

from .prompts import subagent_procedure

CONFIG_NAME = "a07_decompose"
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def technique_config() -> dict:
    """Settings ``bench.agent_session.create_session`` accepts.

    The decomposition itself is the manifest prompt, not a new technique flag.
    The flags only open the tool surface the procedure uses: SQL, error hints,
    the existing result warnings, and the generated dictionary so grain is
    visible. The pitfalls glossary stays off. Held-out validation showed that
    text does not transfer, and this approach is not a glossary experiment.
    """
    config = {
        "glossary": "basic",
        "data_dictionary": True,
        "data_profile": False,
        "enable_sql": True,
        "enable_metrics": False,
        "enable_lookup": False,
        "enable_examples": False,
        "enable_ask": False,
        "sql_error_hints": True,
        "result_checks": True,
        "context_style": "goal",
        "effort": "low",
        "max_tool_calls": 18,
    }
    unknown = set(config) - set(DEFAULT_TECHNIQUE_CONFIG)
    if unknown:
        raise RuntimeError(f"technique_config drifted from the bench: {sorted(unknown)}")
    return config


def configs_document() -> dict:
    """The object ``eval_subagents prepare --configs`` reads."""
    return {CONFIG_NAME: technique_config()}


def briefing(session_dir: Path) -> str:
    """The prompt stored on one manifest entry. It names the session's CLI commands."""
    meta = json.loads(Path(session_dir).joinpath("session.json").read_text())
    config = {**DEFAULT_TECHNIQUE_CONFIG, **meta["technique_config"]}
    cli = f"python3 {_PACKAGE_ROOT / 'bench' / 'agent_cli.py'} --session {shlex.quote(str(session_dir))}"
    commands = [
        f"{cli} context            # views, grains, columns, glossary: run this first",
        f'{cli} sql "SELECT ..."   # one read-only SELECT; a check query returns one row, a preview shows 20',
    ]
    finals = [
        f'{cli} answer --sql "WITH ... SELECT ..." --text "<one paragraph from the check counts>"',
        f'{cli} refuse "<reason>"   # PHI, another customer, or a write',
    ]
    scope = "lossless.* and sem.*" if meta.get("escalate") else "sem.* only"
    return (
        "You are ClaimClarity, an analytics assistant for a clinical lab's insurance claims (837P) and "
        "remittances (835). Answer by building one SQL query as a chain of checked CTEs. Use the Bash tool "
        "ONLY to run the commands listed here.\n\n"
        f"This session can read {scope}. The database session is the per-request sandbox: sem views, "
        "external access off, configuration locked. The sql command rejects anything that is not one "
        "read-only SELECT.\n\n"
        f"You may make at most {config['max_tool_calls']} sql commands. Context does not count.\n\n"
        "Commands:\n" + "\n".join(commands) + "\n\nFinish with exactly one of:\n" + "\n".join(finals)
        + "\n\n" + subagent_procedure()
        + f"\nUser question: {meta['question']}\n"
    )


def prepare_run(run_label: str, models: list[str], repeats: int = 1, only: str | None = None,
                snapshot_path: Path | None = None) -> Path:
    """Prepare an eval run whose manifest prompts are the A07 procedure."""
    from bench.eval_subagents import prepare

    manifest_path = prepare(run_label, configs_document(), models, repeats, only, snapshot_path)
    return install_prompts(manifest_path)


def install_prompts(manifest_path: Path) -> Path:
    """Rewrite A07 entries in an existing manifest. Other configs are left alone."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    for entry in manifest["entries"]:
        if entry.get("config") != CONFIG_NAME:
            continue
        entry["prompt"] = briefing(Path(entry["session"]))
        entry["approach"] = CONFIG_NAME
    if manifest["entries"] and all(entry.get("config") == CONFIG_NAME for entry in manifest["entries"]):
        manifest["approach"] = CONFIG_NAME
    path.write_text(json.dumps(manifest, indent=1))
    return path
