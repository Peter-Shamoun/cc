"""Five independent end-to-end attempts, and the config the harness already accepts.

Self-consistency (Wang et al. 2022; CHASE-SQL execution clustering) marginalizes
over several full answers. This harness cannot sample temperature: those
parameters return HTTP 400 (`research/technique_factors.yaml`). An attempt is
therefore a separate subagent session, with its own tool trace and its own
submitted SQL, not a second draw from one conversation.

The default schedule is one technique config repeated five times. The prompts
are identical, so diversity is whatever nondeterminism the model still has.
`prompt_shape_configs` is the temperature substitute the literature review
allows: five legal prompt shapes, none of which turns on the screen-1 pitfalls
text (that text did not survive held-out validation).
"""

from __future__ import annotations

APPROACH_ID = "a08_self_consistency"
CONFIG_NAME = "a08_self_consistency"
ATTEMPT_COUNT = 5

# Vote margin = (votes for the winner - votes for the runner-up) / scheduled attempts.
# For five attempts the margins that can occur with a strict majority are:
#   5-0 → 1.0,  4-1 → 0.6,  4-0 (one crash) → 0.8,
#   3-1-1 → 0.4,  3-2 → 0.2,  3-0 (two crashes) → 0.6.
# Accept only a margin that is at least a 4-1. A 3-2 still has a majority, but
# the cascade rule sends it to a larger model instead of shipping it.
ACCEPT_MARGIN = 0.6
ESCALATE_MARGIN = 0.2


def base_technique_config() -> dict:
    """Screen-2 base, written out in full so the manifest records every knob.

    Same keys as `bench.agent_cli.DEFAULT_TECHNIQUE_CONFIG`. `create_session`
    rejects anything else, which is what makes this a legal eval config.
    Basic glossary, goal briefing, free SQL over sem.*, effort low: the held-out
    base, not the screen-1 winner.
    """
    return {
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


def prompt_shape_configs() -> list[dict]:
    """Five prompt shapes that stay inside the legal technique keys.

    They change the briefing or the tool feedback. They do not change the
    glossary. The pitfalls text is a knowledge artifact, not a sample, and
    held-out validation did not support it for Haiku.
    """
    direct = base_technique_config()
    procedural = {**direct, "context_style": "procedural"}
    high_effort = {**direct, "effort": "high"}
    with_checks = {**direct, "sql_error_hints": True, "result_checks": True}
    procedural_with_checks = {**procedural, "sql_error_hints": True, "result_checks": True}
    return [direct, procedural, high_effort, with_checks, procedural_with_checks]


def configs_for_eval_subagents(*, diverse_prompts: bool = False) -> dict[str, dict]:
    """The `configs.json` object `python -m bench.eval_subagents prepare` reads.

    Default (one config, pass `--repeats 5`):

        {"a08_self_consistency": { ...base... }}

    `diverse_prompts=True` (pass `--repeats 1`) writes five configs named
    `a08_self_consistency__1` .. `__5`. The voter groups either shape.
    """
    if not diverse_prompts:
        return {CONFIG_NAME: base_technique_config()}
    return {
        f"{CONFIG_NAME}__{index}": config
        for index, config in enumerate(prompt_shape_configs(), start=1)
    }


def is_self_consistency_config(config_name: str) -> bool:
    return config_name == CONFIG_NAME or config_name.startswith(f"{CONFIG_NAME}__")


def cost_multiplier() -> dict:
    """What N=5 does to cost, before a cascade spends anything more.

    Parallel sessions share a cached prefix when the prompt is the same
    (repeat mode). Prompt-shape mode splits the cache wherever the briefing
    diverges. The vote itself is local: it re-executes SQL that was already
    submitted. It does not call a model.
    """
    cached_input_relative = 1.0 + (ATTEMPT_COUNT - 1) * 0.1  # one write, four reads at 0.1x
    return {
        "attempts": ATTEMPT_COUNT,
        "model_calls": ATTEMPT_COUNT,
        "output_tokens": ATTEMPT_COUNT,
        "input_tokens_if_uncached": ATTEMPT_COUNT,
        "input_tokens_if_prefix_cached": round(cached_input_relative, 2),
        "latency_if_serial": ATTEMPT_COUNT,
        "latency_if_parallel": 1.3,
        "selector_model_calls": 0,
        "notes": (
            "input_tokens_if_prefix_cached is the repeat-mode figure: one cache write and four "
            "reads at 0.1x (technique_factors.yaml), and only when the cached prefix is essentially "
            "the whole prompt. Prompt-shape mode changes the briefing, so those five calls are closer "
            "to 5x input. Output tokens stay at 5x either way. The vote re-executes SQL locally and "
            "adds no model call. A cascade to a larger model is extra and fires only when the margin "
            f"is below {ACCEPT_MARGIN}."
        ),
    }
