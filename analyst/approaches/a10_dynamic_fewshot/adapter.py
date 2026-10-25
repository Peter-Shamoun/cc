"""Adapter from A10 onto a ``bench.eval_subagents`` manifest.

``prepare`` writes one prompt per session from ``bench.agent_session.build_prompt``.
That prompt has no examples. The screen-1 examples tool (``enable_examples``)
retrieves with Jaccard from the snapshot's whole ``generated_questions.json``
and only drops the question id and its family. A10 does not turn that tool on.

Usage, after the split manager has written its assignment::

    python -m bench.eval_subagents prepare RUN --configs configs.json --models haiku,sonnet --repeats 2
    python -m analyst.approaches.a10_dynamic_fewshot \\
        .ccsim/agent_sessions/RUN/manifest.json \\
        --questions pool.json \\
        --splits splits.json \\
        --target-splits validation

``configs.json`` carries ``technique_config()`` under the name
``a10_dynamic_fewshot``. ``attach_to_manifest`` rewrites the prompt on entries
with that config name and records the example ids. Other configs in the same
manifest are left alone, so the run can pair A10 against a base arm.
``score`` does not read the prompt; it re-executes the submitted SQL.

``target_splits`` defaults to train, validation, and test so a leave-one-out
debug call works. A validation screen should pass ``("validation",)``. The
confirmatory run should pass ``("test",)`` and should be the first time test
ids are targets. The pool rules do not depend on that setting: test and
validation questions are never examples.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from analyst.approaches.a10_dynamic_fewshot.leakage import (
    LeakageError,
    SplitAssignment,
    assert_no_leakage,
    require_target_split,
)
from analyst.approaches.a10_dynamic_fewshot.prompts import render_prompt
from analyst.approaches.a10_dynamic_fewshot.retriever import DEFAULT_K, METHODS, ExampleIndex

CONFIG_NAME = "a10_dynamic_fewshot"
POLICY_VERSION = "a10-leakage-1"
DEFAULT_TARGET_SPLITS = ("train", "validation", "test")

# Keys accepted by bench.agent_session.create_session (DEFAULT_TECHNIQUE_CONFIG).
# Kept here so this module does not import the sandbox. The test compares them.
_LEGAL_TECHNIQUE_KEYS = frozenset({
    "glossary",
    "data_dictionary",
    "data_profile",
    "enable_sql",
    "enable_metrics",
    "enable_lookup",
    "enable_examples",
    "enable_ask",
    "sql_error_hints",
    "result_checks",
    "context_style",
    "effort",
    "max_tool_calls",
})

_BASE_TECHNIQUE_CONFIG = {
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


def technique_config(base: Mapping | None = None, **overrides) -> dict:
    """A configs.json entry ``prepare`` will accept.

    Defaults match the screen-2 base (basic glossary, goal briefing, effort
    low, examples tool off) so the only A10 difference is the prompt block.
    ``enable_examples`` cannot be turned on: that tool's library is not
    split-aware.
    """
    config = dict(_BASE_TECHNIQUE_CONFIG)
    if base:
        config.update(base)
    config.update(overrides)
    if config.get("enable_examples"):
        raise ValueError(
            "A10 does not enable the agent_cli examples tool. That tool reads the "
            "unsplit snapshot library. attach_to_manifest inserts the train-pool examples."
        )
    config["enable_examples"] = False
    unknown = set(config) - _LEGAL_TECHNIQUE_KEYS
    if unknown:
        raise ValueError(f"unknown technique settings: {sorted(unknown)}")
    return config


def attach_to_manifest(
    manifest_path: Path | str,
    questions: Sequence[Mapping],
    assignment: SplitAssignment,
    *,
    k: int = DEFAULT_K,
    method: str = "tfidf",
    config_name: str = CONFIG_NAME,
    target_splits: Sequence[str] = DEFAULT_TARGET_SPLITS,
) -> dict:
    """Insert A10 examples into manifest prompts for ``config_name``.

    Returns the ``a10`` report written onto the manifest: policy version,
    method, k, and the example ids per question. Repeats of one question must
    retrieve the same ids.
    """
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; expected one of {METHODS}")
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        raise ValueError("manifest needs an object with an 'entries' list")
    by_id = _index_questions(questions)
    index = ExampleIndex.build(questions, assignment, method=method)
    report: dict[str, list[str]] = {}
    for entry in manifest["entries"]:
        if entry.get("config") != config_name:
            continue
        question_id = entry.get("question_id")
        if question_id not in by_id:
            raise LeakageError(f"manifest question {question_id} is not in the question pool")
        target = by_id[question_id]
        require_target_split(target, assignment, target_splits)
        picked = index.retrieve(target, k=k)
        assert_no_leakage(target, [by_id[example.question_id] for example in picked], assignment)
        prompt = entry.get("prompt") or ""
        user_line = f"User question: {target['question']}"
        if user_line not in prompt:
            raise ValueError(
                f"manifest prompt for {question_id} does not contain that question; "
                "refusing to attach examples to a mismatched prompt"
            )
        entry["prompt"] = render_prompt(prompt, picked)
        ids = [example.question_id for example in picked]
        previous = report.get(question_id)
        if previous is not None and previous != ids:
            raise LeakageError(f"repeat for {question_id} retrieved different examples: {previous} vs {ids}")
        report[question_id] = ids
        entry["a10_example_ids"] = ids
        entry["a10_example_scores"] = [example.score for example in picked]
    summary = {
        "policy": POLICY_VERSION,
        "config_name": config_name,
        "k": k,
        "method": method,
        "target_splits": list(target_splits),
        "examples": report,
    }
    manifest["a10"] = summary
    path.write_text(json.dumps(manifest, indent=1))
    return summary


def _index_questions(questions: Sequence[Mapping]) -> dict[str, Mapping]:
    by_id: dict[str, Mapping] = {}
    for question in questions:
        question_id = question.get("id")
        if not question_id:
            raise ValueError("every pool question needs an id")
        if question_id in by_id:
            raise ValueError(f"duplicate question id {question_id}")
        by_id[str(question_id)] = question
    return by_id
