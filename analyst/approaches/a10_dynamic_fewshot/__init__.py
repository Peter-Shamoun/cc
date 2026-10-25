"""A10 dynamic few-shot: three similar train questions, with their reference SQL.

Public surface: ``retrieve``, ``ExampleIndex``, ``exclusion_reason``,
``SplitAssignment``, ``render_prompt``, ``technique_config``, ``attach_to_manifest``.
"""
from analyst.approaches.a10_dynamic_fewshot.adapter import (
    CONFIG_NAME,
    attach_to_manifest,
    technique_config,
)
from analyst.approaches.a10_dynamic_fewshot.leakage import (
    HELD_OUT_QUESTION_SET,
    LeakageError,
    SplitAssignment,
    exclusion_reason,
)
from analyst.approaches.a10_dynamic_fewshot.prompts import render_prompt
from analyst.approaches.a10_dynamic_fewshot.retriever import (
    DEFAULT_K,
    ExampleIndex,
    RetrievedExample,
    retrieve,
)

__all__ = [
    "CONFIG_NAME",
    "DEFAULT_K",
    "HELD_OUT_QUESTION_SET",
    "ExampleIndex",
    "LeakageError",
    "RetrievedExample",
    "SplitAssignment",
    "attach_to_manifest",
    "exclusion_reason",
    "render_prompt",
    "retrieve",
    "technique_config",
]
