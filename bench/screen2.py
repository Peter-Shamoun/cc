"""Screen 2: one-factor-at-a-time ascent around the screen-1 winner.

The spec (research/screen2.yaml) lists the factors and the split each one is scored on. Glossary text is learned
on train and scored on validation. Few-shot examples are taken from train only. The test split is sealed: planning
a stage that scores on it raises SealedSplitError unless unseal_test=True.

`prepare_stage` builds the session manifests through bench.eval_subagents. It does not call a model. Cascade and
multi-candidate are orchestrator outcomes, scored by the hooks here, not by a new sandbox command.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import yaml

from bench.glossary_learner import GateThresholds, gate
from bench.splits import SPLITS, SealedSplitError

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "research" / "screen2.yaml"
ORCHESTRATOR_KEYS = ("cascade", "multi_candidate")


def load_spec(path: Path | None = None) -> dict:
    path = Path(path) if path else SPEC_PATH
    spec = yaml.safe_load(path.read_text())
    validate_spec(spec)
    return spec


def validate_spec(spec: dict) -> None:
    for key in ("name", "method", "baseline_config", "factors", "models", "repeats"):
        if key not in spec:
            raise ValueError(f"screen spec is missing {key}")
    if spec["method"] != "coordinate_ascent":
        raise ValueError(f"unsupported screen method {spec['method']!r}")
    if not spec["factors"]:
        raise ValueError("screen spec has no factors")
    seen = set()
    for factor in spec["factors"]:
        for key in ("id", "split", "levels"):
            if key not in factor:
                raise ValueError(f"factor {factor.get('id')!r} is missing {key}")
        if factor["id"] in seen:
            raise ValueError(f"duplicate factor {factor['id']}")
        seen.add(factor["id"])
        if factor["split"] not in SPLITS:
            raise ValueError(f"factor {factor['id']} split {factor['split']!r} is not one of {SPLITS}")
        if len(factor["levels"]) < 2:
            raise ValueError(f"factor {factor['id']} needs at least two levels")
        if factor["id"] == "examples" and factor.get("example_library_split") != "train":
            raise ValueError("the examples factor must draw its library from the train split")
        if factor["id"] == "glossary_version" and factor.get("learn_split") != "train":
            raise ValueError("glossary versions are learned on the train split and scored on the factor's split")
    if "glossary" not in spec["baseline_config"]:
        raise ValueError("baseline_config needs a glossary")


def assert_scoring_unsealed(spec: dict, *, unseal_test: bool = False) -> None:
    sealed = [factor["id"] for factor in spec["factors"] if factor["split"] == "test"]
    if sealed and not unseal_test:
        raise SealedSplitError(
            f"factors {sealed} are scored on the test split, which is sealed. "
            "Pass unseal_test=True only for the single end-of-protocol run.")


def session_config(config: dict) -> dict:
    from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
    return {key: value for key, value in config.items() if key in DEFAULT_TECHNIQUE_CONFIG}


def orchestrator_config(config: dict) -> dict:
    return {key: config[key] for key in ORCHESTRATOR_KEYS if key in config}


def _models_for(spec: dict, factor: dict, level) -> list[str]:
    if factor["id"] != "cascade":
        return list(spec["models"])
    if level == "haiku_then_sonnet":
        return ["haiku", "sonnet"]
    if level in ("haiku", "sonnet"):
        return [level]
    raise ValueError(f"unknown cascade level {level!r}")


def ascent_stages(spec: dict) -> list[dict]:
    """First round of coordinate ascent: each factor moves, the others stay at the baseline."""
    validate_spec(spec)
    baseline = spec["baseline_config"]
    stages = []
    for factor in spec["factors"]:
        configs = []
        for level in factor["levels"]:
            config = dict(baseline)
            if factor.get("orchestrator"):
                config[factor["id"]] = level
            else:
                config[factor.get("technique_key") or factor["id"]] = level
            repeats = level if factor["id"] == "repeats" else spec["repeats"]
            configs.append(dict(
                level=level, technique_config=session_config(config), orchestrator=orchestrator_config(config),
                repeats=repeats, models=_models_for(spec, factor, level), split=factor["split"]))
        stages.append(dict(
            factor=factor["id"], split=factor["split"], learn_split=factor.get("learn_split"),
            example_library_split=factor.get("example_library_split"), configs=configs))
    return stages


def lock_level(spec: dict, factor_id: str, level) -> dict:
    """A new spec with this factor's chosen level written into the baseline. The input spec is not modified."""
    validate_spec(spec)
    spec = copy.deepcopy(spec)
    factor = next((item for item in spec["factors"] if item["id"] == factor_id), None)
    if factor is None:
        raise ValueError(f"no factor {factor_id}")
    if level not in factor["levels"]:
        raise ValueError(f"{level!r} is not a level of {factor_id}")
    if factor.get("orchestrator"):
        spec["baseline_config"][factor_id] = level
    else:
        spec["baseline_config"][factor.get("technique_key") or factor_id] = level
    if factor_id == "repeats":
        spec["repeats"] = level
    return spec


def choose_level(baseline_level, contrasts_by_level: dict, thresholds: GateThresholds | None = None):
    """Keep the baseline unless another level's validation contrast clears the learner gate.

    Among levels that clear, pick the largest mean effect across models. The contrasts are aggregates
    (effect, ci95, questions); per-question rows are not accepted.
    """
    thresholds = thresholds or GateThresholds()
    best = baseline_level
    best_effect = None
    for level, contrasts in contrasts_by_level.items():
        if level == baseline_level:
            continue
        verdict = gate(contrasts, thresholds)
        if not verdict["accept"]:
            continue
        effect = sum(row["effect"] for row in verdict["models"].values()) / len(verdict["models"])
        if best_effect is None or effect > best_effect:
            best, best_effect = level, effect
    return best


def cascade_correct(*, haiku_answered: bool, haiku_correct: bool, sonnet_correct: bool) -> bool:
    """Haiku's answer stands when Haiku submitted one. Otherwise the Sonnet run is the answer."""
    if haiku_answered:
        return haiku_correct
    return sonnet_correct


def score_multi_candidate(candidate_correct: list[bool], rule: str = "majority") -> bool:
    """Majority of the candidates. ``upper_bound_any`` is refused: it is pass@k, not a selector."""
    if not candidate_correct:
        raise ValueError("no candidates")
    if rule == "upper_bound_any":
        raise ValueError("upper_bound_any is not a score; it is 1 when any candidate is right, which overstates a selector")
    if rule != "majority":
        raise ValueError(f"unknown multi-candidate rule {rule!r}")
    return sum(bool(flag) for flag in candidate_correct) * 2 > len(candidate_correct)


def prepare_stage(spec: dict, factor_id: str, run_label: str, *, unseal_test: bool = False,
                  snapshot_path: Path | None = None) -> list[dict]:
    """Write one eval_subagents manifest per level, filtered to the factor's split. No model calls."""
    from bench.eval_subagents import prepare
    assert_scoring_unsealed(spec, unseal_test=unseal_test)
    stage = next((item for item in ascent_stages(spec) if item["factor"] == factor_id), None)
    if stage is None:
        raise ValueError(f"no factor {factor_id}")
    written = []
    for config in stage["configs"]:
        safe_level = str(config["level"]).replace(":", "_")
        label = f"{run_label}__{factor_id}__{safe_level}"
        manifest = prepare(label, {str(config["level"]): config["technique_config"]}, config["models"],
                           config["repeats"], snapshot_path=snapshot_path, question_split=stage["split"],
                           unseal_test=unseal_test)
        written.append(dict(level=config["level"], manifest=str(manifest), models=config["models"],
                            orchestrator=config["orchestrator"], split=stage["split"], repeats=config["repeats"]))
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=["plan", "check"])
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--unseal-test", action="store_true")
    args = parser.parse_args()
    spec = load_spec(args.spec)
    assert_scoring_unsealed(spec, unseal_test=args.unseal_test)
    if args.cmd == "check":
        print(json.dumps(dict(name=spec["name"], factors=[f["id"] for f in spec["factors"]],
                              splits=sorted({f["split"] for f in spec["factors"]})), indent=1))
        return
    plan = []
    for stage in ascent_stages(spec):
        plan.append(dict(factor=stage["factor"], split=stage["split"], learn_split=stage["learn_split"],
                         example_library_split=stage["example_library_split"],
                         levels=[dict(level=c["level"], models=c["models"], repeats=c["repeats"],
                                      technique_config=c["technique_config"], orchestrator=c["orchestrator"])
                                 for c in stage["configs"]]))
    print(json.dumps(plan, indent=1))


if __name__ == "__main__":
    main()
