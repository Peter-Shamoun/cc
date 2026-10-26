"""Screen 2 spec: one factor at a time, validation only, orchestrator scores."""
import copy

import pytest

from bench.screen2 import (
    ascent_stages, assert_scoring_unsealed, cascade_correct, choose_level, load_spec, lock_level,
    score_multi_candidate, session_config, validate_spec,
)
from bench.splits import SealedSplitError


def test_every_factor_is_scored_on_validation_and_one_knob_moves():
    spec = load_spec()
    assert_scoring_unsealed(spec)
    baseline = session_config(spec["baseline_config"])
    stages = ascent_stages(spec)
    assert [stage["factor"] for stage in stages] == [
        "glossary_version", "semantic_layer", "examples", "multi_candidate", "cascade", "repeats"]
    for stage in stages:
        assert stage["split"] == "validation"
        for config in stage["configs"]:
            tech = config["technique_config"]
            assert "cascade" not in tech and "multi_candidate" not in tech
            diffs = [key for key in set(baseline) | set(tech) if baseline.get(key) != tech.get(key)]
            assert len(diffs) <= 1
            if stage["factor"] == "repeats":
                assert diffs == [] and config["repeats"] == config["level"]
            elif stage["factor"] in ("cascade", "multi_candidate"):
                assert diffs == [] and config["orchestrator"][stage["factor"]] == config["level"]
            else:
                assert config["repeats"] == spec["repeats"]
    examples = next(stage for stage in stages if stage["factor"] == "examples")
    assert examples["example_library_split"] == "train"
    glossary = next(stage for stage in stages if stage["factor"] == "glossary_version")
    assert glossary["learn_split"] == "train"
    cascade = next(stage for stage in stages if stage["factor"] == "cascade")
    models = {config["level"]: config["models"] for config in cascade["configs"]}
    assert models == {"haiku": ["haiku"], "sonnet": ["sonnet"], "haiku_then_sonnet": ["haiku", "sonnet"]}


def test_test_split_and_a_non_train_example_library_are_refused():
    spec = load_spec()
    sealed = copy.deepcopy(spec)
    sealed["factors"][0]["split"] = "test"
    with pytest.raises(SealedSplitError):
        assert_scoring_unsealed(sealed)
    assert_scoring_unsealed(sealed, unseal_test=True)
    moved = copy.deepcopy(spec)
    for factor in moved["factors"]:
        if factor["id"] == "examples":
            factor["example_library_split"] = "validation"
    with pytest.raises(ValueError, match="train"):
        validate_spec(moved)


def test_choose_and_lock_a_level_without_rewriting_the_spec():
    spec = load_spec()
    baseline = spec["baseline_config"]["glossary"]
    weak = {"haiku": dict(effect=0.01, ci95=[-0.02, 0.04], questions=20)}
    strong = {"haiku": dict(effect=0.08, ci95=[-0.01, 0.20], questions=20)}
    assert choose_level(baseline, {baseline: weak, "version:v000_basic": weak}) == baseline
    assert choose_level(baseline, {"version:v000_basic": strong}) == "version:v000_basic"
    locked = lock_level(spec, "semantic_layer", True)
    assert locked["baseline_config"]["enable_metrics"] is True
    assert spec["baseline_config"]["enable_metrics"] is False
    locked_repeats = lock_level(spec, "repeats", 1)
    assert locked_repeats["repeats"] == 1 and spec["repeats"] == 2
    locked_cascade = lock_level(spec, "cascade", "sonnet")
    assert locked_cascade["baseline_config"]["cascade"] == "sonnet"
    assert spec["baseline_config"]["cascade"] == "haiku"


def test_cascade_keeps_haikus_answer_and_majority_is_not_pass_at_k():
    assert cascade_correct(haiku_answered=True, haiku_correct=False, sonnet_correct=True) is False
    assert cascade_correct(haiku_answered=False, haiku_correct=False, sonnet_correct=True) is True
    assert score_multi_candidate([True, True, False]) is True
    assert score_multi_candidate([True, False, False]) is False
    with pytest.raises(ValueError, match="upper_bound_any"):
        score_multi_candidate([False, False, True], "upper_bound_any")
