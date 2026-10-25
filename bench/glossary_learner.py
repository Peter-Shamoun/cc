"""Learn glossary entries from train failures and accept them only on a validation contrast.

The loop may read train failures. It must not read validation questions, gold SQL, or per-question correctness.
The validation callable returns the same aggregate the bench already reports (mean paired difference and the
question-level bootstrap interval from bench.eval_subagents). An entry is accepted only when that contrast
clears a threshold: the point effect is at least ``effect_min``, or the CI lower bound is above ``ci_low_min``.
Every decision, accept or reject, is appended to a log. Accepted entries are a new version file whose provenance
names the parent version and the train failure ids, not the validation items.

No path in this module calls a paid API. ScriptedProposer emits a fixed general sentence per error type.
LLMProposer calls only a client the caller injects; with no client it refuses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bench.eval_subagents import _bootstrap_interval
from bench.glossary_versions import load_version, render_document, write_version

_PUBLIC_CONTRAST_KEYS = {"effect", "ci95", "questions"}

# General sentences. They do not name a question, a payer, a code, a month, or an answer. Keyed by the error
# types the judge already uses, plus a few the sandbox failure notes use.
GENERAL_ENTRY_BY_ERROR_TYPE = {
    "grain": "Count at the grain the question names. A submission-level view has one row per 837, so a claim total uses one row per claim (latest submission, or distinct claim) rather than summing every submission.",
    "definition": "Use the stated definition of a denial: a recorded status-4 denial or the zero-pay signal with a reviewed denial reason. A zero payment by itself is not a denial, and a reversal is not a current adjudication.",
    "join": "Aggregate the many side of a join before attaching claim-level amounts, so a claim total is not repeated once per service line.",
    "filter": "Filter on the population and the time basis the question names. Primary payer is the payer billed with payer responsibility P, not the remittance status sequence.",
    "aggregation": "Sum money from current adjudications only. Adding every remittance event double-counts reversals and superseded payments.",
    "rounding": "Compute ratios in real arithmetic and round only the final figure, the way the question asks.",
    "other": "Re-read the question's grain, population, time basis and output columns before the final query, and do not answer a nearby question instead.",
}


class LearnerError(ValueError):
    pass


class LLMClientMissing(LearnerError):
    pass


@dataclass(frozen=True)
class GateThresholds:
    effect_min: float = 0.05
    ci_low_min: float = 0.0
    policy: str = "any"          # any | all models must clear
    combine: str = "either"      # either: effect OR ci; both: effect AND ci
    min_questions: int = 1

    def __post_init__(self):
        if self.policy not in ("any", "all"):
            raise LearnerError(f"unknown gate policy {self.policy!r}")
        if self.combine not in ("either", "both"):
            raise LearnerError(f"unknown gate combine {self.combine!r}")


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    text: str
    section: str = "pitfalls"
    train_failure_ids: tuple = ()
    error_type: str = ""
    proposer: str = "scripted"


@dataclass
class Decision:
    candidate_id: str
    accept: bool
    reason: str
    parent_id: str
    models: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    version_id: str | None = None

    def public_record(self) -> dict:
        return dict(candidate_id=self.candidate_id, accept=self.accept, reason=self.reason,
                    parent_id=self.parent_id, models=self.models, thresholds=self.thresholds,
                    version_id=self.version_id)


def contrasts_from_diffs(diffs_by_model: dict[str, list[float]], *, resamples: int = 2000, seed: int = 0) -> dict:
    """Paired contrast per model. The per-question differences are not part of the returned record."""
    if resamples < 100:
        raise LearnerError("bootstrap resamples must be at least 100")
    contrasts = {}
    for model, diffs in diffs_by_model.items():
        if not diffs:
            raise LearnerError(f"no paired differences for {model}")
        effect = sum(diffs) / len(diffs)
        low, high = _bootstrap_interval(list(diffs), resamples, seed)
        contrasts[model] = dict(effect=round(effect, 4), ci95=[low, high], questions=len(diffs))
    return contrasts


def assert_public_contrast(contrasts: dict) -> None:
    """Validation may cross this boundary only as an aggregate. Question text and per-question rows are refused."""
    if not isinstance(contrasts, dict) or not contrasts:
        raise LearnerError("validation contrast must be a non-empty dict of model -> {effect, ci95, questions}")
    for model, contrast in contrasts.items():
        if not isinstance(contrast, dict):
            raise LearnerError(f"contrast for {model} is not a dict")
        extra = set(contrast) - _PUBLIC_CONTRAST_KEYS
        if extra:
            raise LearnerError(
                f"validation contrast for {model} includes {sorted(extra)}; the learner cannot read validation detail")
        missing = _PUBLIC_CONTRAST_KEYS - set(contrast)
        if missing:
            raise LearnerError(f"validation contrast for {model} is missing {sorted(missing)}")
        low, high = contrast["ci95"]
        if len(contrast["ci95"]) != 2 or low > high:
            raise LearnerError(f"ci95 for {model} must be [low, high]")
        if not isinstance(contrast["questions"], int) or contrast["questions"] < 1:
            raise LearnerError(f"questions for {model} must be a positive int")


def model_clears(contrast: dict, thresholds: GateThresholds) -> tuple[bool, str]:
    effect = contrast["effect"]
    low, _high = contrast["ci95"]
    if contrast["questions"] < thresholds.min_questions:
        return False, f"{contrast['questions']} validation questions is below min_questions {thresholds.min_questions}"
    effect_ok = effect >= thresholds.effect_min
    ci_ok = low > thresholds.ci_low_min
    if thresholds.combine == "both":
        ok = effect_ok and ci_ok
    else:
        ok = effect_ok or ci_ok
    if ok and effect_ok and ci_ok:
        return True, "effect and CI both clear their thresholds"
    if ok and effect_ok:
        return True, "effect clears its threshold"
    if ok and ci_ok:
        return True, "CI lower bound clears its threshold"
    return False, "neither the effect nor the CI clears its threshold"


def gate(contrasts: dict, thresholds: GateThresholds | None = None) -> dict:
    thresholds = thresholds or GateThresholds()
    assert_public_contrast(contrasts)
    per_model = {}
    for model, contrast in contrasts.items():
        accept, reason = model_clears(contrast, thresholds)
        per_model[model] = dict(accept=accept, reason=reason, effect=contrast["effect"],
                                ci95=list(contrast["ci95"]), questions=contrast["questions"])
    votes = [row["accept"] for row in per_model.values()]
    if thresholds.policy == "all":
        accept = all(votes)
        reason = "all models clear their threshold" if accept else "policy is all, and a model did not clear"
    else:
        accept = any(votes)
        reason = "a model clears its threshold" if accept else "policy is any, and no model clears"
    return dict(accept=accept, reason=reason, models=per_model, thresholds=asdict(thresholds))


def _content_ngrams(text: str, n: int = 6) -> set[str]:
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    return {" ".join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))}


def question_specific_overlap(entry_text: str, train_failures: list[dict]) -> str | None:
    """Reject an entry that copies a 6-gram from exactly one train question. General entries do not do this.

    Only train failures are inspected. Validation text is not available here.
    """
    questions = [(f.get("question_id") or "", f.get("question") or "") for f in train_failures if f.get("question")]
    if not questions:
        return None
    grams = _content_ngrams(entry_text)
    if not grams:
        return None
    owners: dict[str, set[str]] = {}
    for qid, text in questions:
        for gram in _content_ngrams(text) & grams:
            owners.setdefault(gram, set()).add(qid)
    copied = [gram for gram, ids in owners.items() if len(ids) == 1]
    if copied:
        qid = next(iter(owners[copied[0]]))
        return f"entry copies a 6-word span from train question {qid}"
    for qid, _text in questions:
        if qid and qid in entry_text:
            return f"entry names train question {qid}"
    return None


class ScriptedProposer:
    """Fixed general entries from error types, or an explicit candidate list. No model call."""

    name = "scripted"

    def __init__(self, candidates: list[Candidate] | None = None):
        self.candidates = candidates

    def propose(self, train_failures: list[dict], glossary_text: str) -> list[Candidate]:
        if self.candidates is not None:
            return list(self.candidates)
        seen = set()
        out = []
        for failure in train_failures:
            error_type = failure.get("error_type") or "other"
            if error_type in seen:
                continue
            seen.add(error_type)
            sentence = GENERAL_ENTRY_BY_ERROR_TYPE.get(error_type)
            if sentence is None:
                raise LearnerError(f"no scripted entry for error type {error_type!r}")
            if sentence in glossary_text:
                continue
            ids = tuple(sorted({f.get("question_id") for f in train_failures
                                if (f.get("error_type") or "other") == error_type and f.get("question_id")}))
            out.append(Candidate(candidate_id=f"scripted-{error_type}", text="- " + sentence, section="pitfalls",
                                 train_failure_ids=ids, error_type=error_type, proposer=self.name))
        return out


def build_llm_prompt(train_failures: list[dict], glossary_text: str) -> str:
    """Prompt for a proposer. It contains train failures only; there is no validation argument."""
    lines = ["Current glossary:", glossary_text.strip(), "",
             "Train failures (the only questions you may look at). Write general rules, not question-specific ones.",
             "Do not name a question id, a payer, a CPT code, a month, a date, or a numeric answer.",
             "Reply with a JSON list of objects {\"text\": \"- ...\", \"error_type\": \"...\"}.", ""]
    for failure in train_failures:
        lines.append(json.dumps(dict(question_id=failure.get("question_id"), error_type=failure.get("error_type"),
                                     question=failure.get("question"), note=failure.get("note")), ensure_ascii=False))
    return "\n".join(lines)


class LLMProposer:
    """Calls ``complete(prompt) -> str`` when one was injected. Never opens a network client itself."""

    name = "llm"

    def __init__(self, complete: Callable[[str], str] | None = None):
        self.complete = complete

    def propose(self, train_failures: list[dict], glossary_text: str) -> list[Candidate]:
        if self.complete is None:
            raise LLMClientMissing(
                "no LLM client configured; use ScriptedProposer. This scaffold does not make paid API calls.")
        raw = self.complete(build_llm_prompt(train_failures, glossary_text))
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LearnerError(f"LLM proposer did not return JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise LearnerError("LLM proposer JSON must be a list")
        out = []
        for i, item in enumerate(parsed):
            text = item.get("text") if isinstance(item, dict) else None
            if not text or not str(text).startswith("- "):
                raise LearnerError(f"candidate {i} must be an object with text starting with '- '")
            out.append(Candidate(candidate_id=f"llm-{i:02d}", text=str(text).strip(), section="pitfalls",
                                 train_failure_ids=tuple(sorted({f.get("question_id") for f in train_failures if f.get("question_id")})),
                                 error_type=str(item.get("error_type") or ""), proposer=self.name))
        return out


def _with_entry(parent: dict, candidate: Candidate, version_id: str, gate_record: dict) -> dict:
    sections = json.loads(json.dumps(parent["sections"]))
    provenance = dict(origin="learned", proposer=candidate.proposer, parent_id=parent["version_id"],
                      train_failure_ids=list(candidate.train_failure_ids), error_type=candidate.error_type,
                      gate=dict(accept=gate_record["accept"], reason=gate_record["reason"], models=gate_record["models"]))
    entry = dict(entry_id=f"{version_id}-{candidate.candidate_id}", section=candidate.section, kind="bullet",
                 text=candidate.text, provenance=provenance)
    placed = False
    for section in sections:
        if section["section"] == candidate.section:
            section["entries"].append(entry)
            placed = True
    if not placed:
        sections.append(dict(section=candidate.section, entries=[entry]))
    raw = json.dumps(dict(version_id=version_id, parent_id=parent["version_id"], sections=sections), sort_keys=True)
    return dict(version_id=version_id, parent_id=parent["version_id"], sections=sections,
                note=f"Accepted {candidate.candidate_id} on validation.", content_hash=hashlib.sha256(raw.encode()).hexdigest())


def _next_version_id(parent_id: str, candidate: Candidate, directory: Path) -> str:
    digest = hashlib.sha256(f"{parent_id}|{candidate.candidate_id}|{candidate.text}".encode()).hexdigest()[:8]
    version_id = f"v_{digest}"
    # Keep room if a previous accept used the same text.
    n = 2
    while (directory / f"{version_id}.json").exists():
        version_id = f"v_{digest}_{n}"
        n += 1
    return version_id


def run_learning_round(parent_id: str, train_failures: list[dict], proposer, validation_contrast: Callable[[dict], dict],
                       *, directory: Path, log_path: Path, thresholds: GateThresholds | None = None) -> list[Decision]:
    """Propose from train failures, gate each candidate on the validation aggregate, log every decision."""
    thresholds = thresholds or GateThresholds()
    parent = load_version(parent_id, directory)
    decisions = []
    for candidate in proposer.propose(train_failures, render_document(parent)):
        specific = question_specific_overlap(candidate.text, train_failures)
        if specific:
            decision = Decision(candidate.candidate_id, False, "rejected before validation: " + specific,
                                parent["version_id"], thresholds=asdict(thresholds))
            _log(log_path, decision)
            decisions.append(decision)
            continue
        tried_against = parent["version_id"]
        trial = _with_entry(parent, candidate, "trial", dict(accept=False, reason="trial", models={}))
        contrasts = validation_contrast(trial)
        # The contrast is the only validation information retained. Question-level fields are refused.
        verdict = gate(contrasts, thresholds)
        version_id = None
        if verdict["accept"]:
            version_id = _next_version_id(tried_against, candidate, directory)
            child = _with_entry(parent, candidate, version_id, verdict)
            write_version(child, directory)
            parent = child
        decision = Decision(candidate.candidate_id, verdict["accept"], verdict["reason"], tried_against,
                            models=verdict["models"], thresholds=verdict["thresholds"], version_id=version_id)
        _log(log_path, decision)
        decisions.append(decision)
    return decisions


def _log(path: Path, decision: Decision) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(decision.public_record(), sort_keys=True) + "\n")


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", choices=["gate"])
    parser.add_argument("--effect", type=float)
    parser.add_argument("--ci-low", type=float)
    parser.add_argument("--ci-high", type=float)
    parser.add_argument("--questions", type=int, default=30)
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--effect-min", type=float, default=0.05)
    parser.add_argument("--ci-low-min", type=float, default=0.0)
    parser.add_argument("--combine", default="either")
    parser.add_argument("--policy", default="any")
    args = parser.parse_args()
    if args.effect is None or args.ci_low is None or args.ci_high is None:
        raise SystemExit("gate needs --effect, --ci-low and --ci-high")
    verdict = gate({args.model: dict(effect=args.effect, ci95=[args.ci_low, args.ci_high], questions=args.questions)},
                   GateThresholds(effect_min=args.effect_min, ci_low_min=args.ci_low_min, policy=args.policy,
                                  combine=args.combine))
    print(json.dumps(verdict, indent=1))


if __name__ == "__main__":
    main()
