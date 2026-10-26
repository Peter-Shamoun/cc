"""Adapter for ``bench.eval_subagents``.

``prepare --agent analyzer`` writes each manifest entry's prompt from this module
and attaches the Anthropic tool spec for the same flags. A Claude Code subagent
follows the prompt and drives ``python -m analyzer.cli``. An API driver can ignore
the shell prompt and call ``Analyzer`` with ``tool_spec`` plus ``analyzer_config``.

Candidates and the Haiku-then-Sonnet cascade belong to the in-process loop. On the
subagent route one briefing is one trajectory; a multi-agent bundle (analyzer/bundles.py)
is a set of role sessions that ``analyzer.orchestration`` creates and decides between.

The briefing is assembled from blocks that are empty at a parameter's off level, so the B0
briefing is the one calibration scored. Role sessions (``meta["role"]``) get the planner,
verifier or selector briefing instead of the answerer's.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import replace
from pathlib import Path

from analyzer import protocols
from analyzer.config import HAIKU_MODEL, SONNET_MODEL, AnalyzerConfig
from analyzer.prompts import procedure_text, refusal_text
from analyzer.tools import REWRITTEN_TOOL_DESCRIPTIONS, _glossary_body, tool_spec

_MODEL_IDS = {
    "haiku": HAIKU_MODEL,
    "sonnet": SONNET_MODEL,
    "claude-haiku-5-5": HAIKU_MODEL,
    "claude-sonnet-5-5": SONNET_MODEL,
}
_COMMAND_TOOL = {
    "list": "list_relations", "describe": "describe_relation", "sample": "sample_rows",
    "dictionary": "lookup_dictionary", "profile": "profile", "glossary": "lookup_glossary", "sql": "run_sql",
    "metrics": "query_metrics", "lookup": "lookup_values", "examples": "lookup_examples",
    "escalate": "request_escalation", "answer": "final_answer", "refuse": "refuse", "ask": "ask",
}
_ROLE_HEADER = ("You are CC, an analytics assistant for one clinical lab's insurance claims (837P) and "
                "remittances (835).\n")
_SHELL_RULES = ("Answer the user's question by running only the commands below with the Bash tool. Do not read, list, "
                "or open any file, and do not run any other program. The answer is scored by re-running the SQL you "
                "submit, so that SQL must compute the answer from the data.\n\n")


class OrchestratedSession(RuntimeError):
    """A multi-agent bundle's scored session is answered through its role sessions, never directly."""


def config_from_meta(meta: dict, *, for_subagent: bool = False) -> AnalyzerConfig:
    from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
    merged = {**DEFAULT_TECHNIQUE_CONFIG, **(meta.get("technique_config") or {})}
    model = _MODEL_IDS.get(meta.get("model") or "", meta.get("model") or HAIKU_MODEL)
    config = AnalyzerConfig.from_technique(merged, model=model)
    if for_subagent:
        config = replace(config, candidates=1, cascade="off")
    return config


def cli_prefix(session_dir: Path) -> str:
    return f"python3 -m analyzer.cli --session {shlex.quote(str(session_dir))}"


def _described(config: AnalyzerConfig, command_name: str, line: str) -> str:
    """#19 descriptions level: a one-line purpose after the command."""
    if not config.rewritten_tool_descriptions or command_name not in _COMMAND_TOOL:
        return line
    return f"{line}   # {REWRITTEN_TOOL_DESCRIPTIONS[_COMMAND_TOOL[command_name]]}"


def _investigative_commands(config: AnalyzerConfig, cli: str) -> list[tuple[str, str]]:
    commands = []
    if config.context_style != "minimal":
        commands += [("list", f"{cli} list"), ("describe", f"{cli} describe sem.claims"),
                     ("sample", f"{cli} sample sem.claims")]
    else:
        commands.append(("describe", f"{cli} describe sem.claims"))
    if config.data_dictionary:
        commands.append(("dictionary", f"{cli} dictionary"))
    if config.data_profile:
        commands.append(("profile", f"{cli} profile"))
    if config.glossary != "none" and config.context_style != "minimal":
        commands.append(("glossary", f'{cli} glossary "denial"'))
    if config.enable_sql:
        commands.append(("sql", f'{cli} sql "SELECT ..."'))
    if config.enable_metrics:
        commands.append(("metrics", f"{cli} metrics '{{\"metrics\": [\"denial_rate\"], \"group_by\": [\"payer_name\"]}}'"))
    if config.enable_lookup:
        commands.append(("lookup", f'{cli} lookup payer_name "north"'))
    commands.append(("escalate", f'{cli} escalate "<why sem.* cannot answer>"'))
    return commands


def _final_commands(config: AnalyzerConfig, cli: str) -> list[tuple[str, str]]:
    confidence = " --confidence <high|medium|low>" if config.report_confidence else ""
    finals = [("answer", f'{cli} answer --sql "SELECT ..." --text "<one-paragraph answer>"{confidence}')]
    if config.generation_mode.startswith("compiler"):
        finals.append(("answer", f"{cli} answer --spec '<json>' --text \"<one-paragraph answer>\"{confidence}"))
    finals.append(("refuse", f'{cli} refuse "<reason>"'))
    if config.enable_ask:
        finals.append(("ask", f'{cli} ask "<clarifying question>"'))
    return finals


def _method_blocks(config: AnalyzerConfig, meta: dict, cli: str) -> str:
    """Blocks that change how the answer is produced. Each is empty at its off level."""
    blocks = []
    if config.candidate_shape != "none":
        blocks.append(protocols.candidate_shape_block(config.candidate_shape))
    if config.playbook:
        from analyzer.playbook import frozen_text_for
        blocks.append(protocols.playbook_block(frozen_text_for(meta)))
    if config.plan_mode == "external_plan":
        plan = (meta.get("role") or {}).get("plan")
        if not plan:
            raise OrchestratedSession("plan_mode external_plan needs the planner's record in meta['role']['plan']")
        blocks.append(protocols.external_plan_block(plan))
    interpretation = protocols.interpretation_block(config, cli)
    if interpretation:
        blocks.append(interpretation)
    if config.explore_probes:
        blocks.append(protocols.probe_block(cli))
    if config.generation_mode.startswith("compiler"):
        blocks.append(protocols.compiler_block(config, cli))
    if config.generation_mode in {"decompose_cte", "compiler_decompose_fallback"}:
        blocks.append(protocols.decompose_block(cli))
    if config.report_confidence:
        blocks.append(protocols.confidence_block())
    return "".join(block + "\n" for block in blocks)


def _examples(config: AnalyzerConfig, meta: dict) -> str:
    if not config.enable_examples:
        return ""
    from analyzer.examples import LIVE_QUESTION_ID, train_examples_for, train_examples_for_live_question
    if meta["question_id"] == LIVE_QUESTION_ID:
        examples = train_examples_for_live_question(meta["snapshot_path"], meta["question"], config.examples_k)
    else:
        examples = train_examples_for(meta["snapshot_path"], meta["question_id"], config.examples_k)
    return protocols.examples_block(examples) + "\n"


def _answerer_prompt(session_dir: Path, meta: dict, config: AnalyzerConfig) -> str:
    cli = cli_prefix(session_dir)
    commands = [_described(config, name, line) for name, line in _investigative_commands(config, cli)]
    finals = [_described(config, name, line) for name, line in _final_commands(config, cli)]
    glossary = _glossary_body(config).strip()
    uncharged_names = (["probe"] if config.explore_probes else []) + (
        ["interpret"] if protocols.required_interpretation_parts(config) else [])
    uncharged = f" {' and '.join(uncharged_names).capitalize()} commands do not count." if uncharged_names else ""
    return (
        _ROLE_HEADER + _SHELL_RULES
        + refusal_text(config) + "\n" + procedure_text(config) + "\n"
        + (("Business definitions:\n" + glossary + "\n\n") if glossary else "")
        + _method_blocks(config, meta, cli)
        + "Commands:\n" + "\n".join(commands) + "\n\nFinish with exactly one of:\n" + "\n".join(finals)
        + f"\n\nYou may make at most {config.max_tool_calls} commands besides the final one.{uncharged} "
        + "Then reply with the single word DONE.\n\n"
        + _examples(config, meta)
        + f"User question: {meta['question']}\n"
    )


def _planner_prompt(session_dir: Path, meta: dict, config: AnalyzerConfig) -> str:
    """#18 Sonnet planner: explore, then write the interpret record a Haiku executor follows. No answer."""
    cli = cli_prefix(session_dir)
    commands = [line for name, line in _investigative_commands(config, cli) if name != "escalate"]
    glossary = _glossary_body(config).strip()
    parts = protocols.required_interpretation_parts(config, planner=True)
    return (
        _ROLE_HEADER
        + "You are the planner. A separate executor with the same tools will write and submit the SQL from your plan. "
          "Run only the commands below with the Bash tool; do not read files or run any other program.\n\n"
        + refusal_text(config) + "\n"
        + (("Business definitions:\n" + glossary + "\n\n") if glossary else "")
        + protocols.interpretation_block(config, cli, planner=True) + "\n"
        + "Commands:\n" + "\n".join(commands)
        + f"\n\nFinish with exactly one of:\n{cli} interpret '{protocols.interpretation_example(parts)}'\n"
        + f'{cli} refuse "<reason>"\n\n'
        + f"You may make at most {config.max_tool_calls} commands besides the final one. "
        + "Then reply with the single word DONE.\n\n"
        + f"User question: {meta['question']}\n"
    )


def _verifier_prompt(session_dir: Path, meta: dict) -> str:
    role = meta["role"]
    cli = cli_prefix(session_dir)
    return (protocols.verifier_briefing(meta["question"], role["schema_text"], role["reviewed"])
            + "\nRecord the verdict with the Bash tool, running only this command, then reply with the single word "
              "DONE:\n"
            + f"{cli} verdict '{protocols.VERDICT_EXAMPLE}'\n")


def _selector_prompt(session_dir: Path, meta: dict) -> str:
    role = meta["role"]
    cli = cli_prefix(session_dir)
    labels = "|".join(candidate["label"] for candidate in role["candidates"])
    return (protocols.selector_briefing(meta["question"], role["candidates"])
            + "\nRecord the choice with the Bash tool, running only this command, then reply with the single word "
              "DONE:\n"
            + f'{cli} select <{labels}|abstain> --reason "<one sentence>"\n')


def build_subagent_prompt(session_dir: Path) -> str:
    """Briefing for one evaluated subagent. Same contract as ``bench.agent_session.build_prompt``."""
    session_dir = Path(session_dir)
    meta = json.loads((session_dir / "session.json").read_text())
    config = config_from_meta(meta, for_subagent=True)
    role_kind = (meta.get("role") or {}).get("kind", "answerer")
    if config.orchestration_bundle and not meta.get("role"):
        raise OrchestratedSession(
            f"this session is bundle {config.orchestration_bundle}'s scored session: drive it with "
            f"python3 -m analyzer.orchestration next --session {session_dir}")
    if role_kind == "answerer":
        return _answerer_prompt(session_dir, meta, config)
    if role_kind == "planner":
        return _planner_prompt(session_dir, meta, config)
    if role_kind == "verifier":
        return _verifier_prompt(session_dir, meta)
    if role_kind == "selector":
        return _selector_prompt(session_dir, meta)
    raise ValueError(f"unknown role kind {role_kind!r}")


def bootstrap_instruction(session_dir: Path) -> str:
    """What a runner sends one subagent: one command that prints its full briefing."""
    return ("Run this command with the Bash tool and follow the instructions it prints exactly:\n"
            f"{cli_prefix(session_dir)} briefing")


def stamp_session(session_dir: Path) -> None:
    """Freeze what a prepared session's briefing depends on into its meta (#2 playbook hash)."""
    session_dir = Path(session_dir)
    meta_path = session_dir / "session.json"
    meta = json.loads(meta_path.read_text())
    config = config_from_meta(meta)
    if config.playbook:
        from analyzer.playbook import stamp
        meta.update(stamp())
    meta_path.write_text(json.dumps(meta, indent=1))


def manifest_fields(session_dir: Path) -> dict:
    """What ``eval_subagents.prepare --agent analyzer`` stores on each entry."""
    session_dir = Path(session_dir)
    meta = json.loads((session_dir / "session.json").read_text())
    full = config_from_meta(meta, for_subagent=False)
    shown = config_from_meta(meta, for_subagent=True)
    if full.orchestration_bundle:
        from analyzer.bundles import orchestration_document
        return {
            "prompt": (f"Bundle {full.orchestration_bundle} is orchestrated. Do not brief one subagent with this "
                       f"session. Run: python3 -m analyzer.orchestration next --session {session_dir}"),
            "orchestration": orchestration_document(full.orchestration_bundle),
            "analyzer_config": full.screen_dict(),
        }
    return {
        "prompt": build_subagent_prompt(session_dir),
        "tool_spec": tool_spec(shown),
        "analyzer_config": full.screen_dict(),
    }
