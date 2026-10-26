"""The eleven analyzer bundles B0-B10 (research/analyzer_bundles_v1.md section 1.1), for the subagent route.

Each bundle maps to:
- ``technique_config``: the dict ``bench.eval_subagents.prepare(--agent analyzer)`` takes. Keys are
  ``bench.agent_cli.DEFAULT_TECHNIQUE_CONFIG`` settings; omitted keys stay at the B0 (off) level.
- ``orchestration``: for a multi-agent bundle, which roles run, with which model, effort and briefing, when, and
  the decision rule. ``analyzer.orchestration`` executes it from disk. ``None`` means one subagent per session.
- ``parameter_levels``: all 26 parameters, the level the bundle asks for, as the doc's cell text.
- ``approximations``: every parameter whose level is not implemented exactly as the doc states, with the reason.
  ``test_bundles`` checks each bundle names all 26 parameters, so nothing is dropped silently.

    python3 -m analyzer.bundles configs B0,B6,B7 > configs.json   # what prepare --configs reads
    python3 -m analyzer.bundles show B8                           # one bundle, as JSON

Fixed in every bundle and not a technique setting on this route: prompt caching (the doc's section 0 asks the runner
to turn it on; Claude Code subagents cache their own prefix, nothing here can set it), PHI and injection guards in
code, no schema pruning, Haiku as the executor model of the scored answer except where a role says otherwise.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field

from analyzer.trigger import CURRENT_ANALYZER_TRIGGER, FAILURE_SIGNAL_TRIGGER, LINT_REPAIR_CONFIDENCE_TRIGGER

PARAMETER_NAMES = {
    1: "glossary", 2: "playbook", 3: "dictionary", 4: "profile", 5: "lookup", 6: "few-shot", 7: "style",
    8: "generation", 9: "explore", 10: "plan", 11: "assumptions", 12: "effort", 13: "repair", 14: "checks",
    15: "candidates", 16: "abstention", 17: "tier", 18: "multi-agent", 19: "tool design", 20: "budgets",
    21: "clarification", 22: "domain lint", 23: "reconciliation", 24: "two routes", 25: "restate", 26: "trigger",
}

# Which technique settings carry each parameter on this route. "orchestration" means the level is a role in the
# bundle's orchestration spec. #16 is at its B0 level in every bundle (the doc's section 2 says why); #21 is on in
# B1 only, adopted after research/clarify_results.md.
PARAMETER_SETTINGS = {
    1: ("glossary",), 2: ("playbook",), 3: ("data_dictionary", "dictionary_detail"),
    4: ("data_profile", "profile_detail"), 5: ("enable_lookup", "lookup_mandatory_before_literals"),
    6: ("enable_examples", "examples_k"), 7: ("context_style",), 8: ("generation_mode",), 9: ("explore_probes",),
    10: ("plan_mode",), 11: ("state_assumptions", "plan_mode"), 12: ("effort", "orchestration"),
    13: ("self_correct", "sql_error_hints", "verify", "max_repairs"), 14: ("result_checks", "orchestration"),
    15: ("orchestration",), 16: ("refusal_prompt",), 17: ("orchestration",), 18: ("orchestration", "plan_mode"),
    19: ("actionable_errors", "digest_results", "rewritten_tool_descriptions"),
    20: ("max_turns", "max_tool_calls", "rows_shown_to_model"), 21: ("enable_ask",), 22: ("domain_lint",),
    23: ("reconciliation_checks",), 24: ("orchestration", "generation_mode"),
    25: ("restate_question", "plan_mode"), 26: ("report_confidence", "orchestration"),
}

# B0 levels from the doc's section 0; a bundle states only the cells that differ.
B0_LEVELS = {
    1: "basic", 2: "off", 3: "types", 4: "none", 5: "off", 6: "0", 7: "goal", 8: "free SQL", 9: "off",
    10: "none", 11: "off", 12: "low", 13: "none", 14: "off", 15: "1", 16: "prompt", 17: "Haiku", 18: "single",
    19: "raw", 20: "8 / 20", 21: "never", 22: "off", 23: "off", 24: "one", 25: "off", 26: "n/a",
}
B0_TECHNIQUE_CONFIG = {"effort": "low", "self_correct": "none", "max_repairs": 0}

# Approximations that hold for every bundle at that parameter's level, so each bundle lists them too.
_TURN_BUDGET_REASON = ("#20 turns: a Claude Code subagent has no turn counter the CLI can enforce; the CLI caps "
                       "commands instead. 8 turns is the calibrated 12-command budget (B0), 4 turns is 6 commands "
                       "(same 1.5 commands per turn). max_turns is stored for the in-process loop.")
_EFFORT_REASON = ("#12 effort is not a CLI setting: prepare() writes it on the manifest entry (and each role's "
                  "effort in its action) and the runner must pass it to the subagent. A runner that cannot set "
                  "effort runs every bundle at its default effort.")
_ERRORS_REPAIR_REASON = ("#13 'errors': on this route a failing answer SQL is never recorded and the model sees the "
                         "error (inherent to the loop, the same in B0). The level adds the repair budget, the "
                         "'repair k of N' instruction and the repair count the trigger reads.")


@dataclass(frozen=True)
class Role:
    """One subagent a bundle's orchestration may launch.

    kind: answerer (submits an answer), planner (writes the interpret record), verifier (A05 critic verdict),
    selector (A04-style choice among disagreeing results). run_when names a condition analyzer.orchestration
    evaluates; technique_overrides are applied on top of the bundle's technique_config for this role only.
    """

    role_name: str
    kind: str
    model: str
    effort: str
    run_when: str
    technique_overrides: dict = field(default_factory=dict)
    briefing: str = ""


@dataclass(frozen=True)
class Orchestration:
    decision_rule: str
    decision_rule_text: str
    roles: tuple[Role, ...]
    trigger_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Bundle:
    bundle_id: str
    title: str
    technique_config: dict
    levels: dict
    approximations: dict
    orchestration: Orchestration | None = None
    waiting_on: str | None = None

    @property
    def parameter_levels(self) -> dict[int, str]:
        return {**B0_LEVELS, **self.levels}


def _repair(level: str, max_repairs: int) -> dict:
    """#13 levels onto the CLI settings. 'sig' needs verify so a blocking result warning bounces the answer."""
    settings = {
        "errors": dict(self_correct="errors_only", sql_error_hints=False),
        "errors+hints": dict(self_correct="errors_only", sql_error_hints=True),
        "errors+hints+sig": dict(self_correct="errors+result_signals", sql_error_hints=True, verify=True),
    }[level]
    return {**settings, "max_repairs": max_repairs}


_CANDIDATE_SHAPES = ("direct", "query_plan", "divide_and_conquer")


def _b2_roles() -> tuple[Role, ...]:
    roles = [Role(f"candidate_{shape}", "answerer", "haiku", "low", "always", {"candidate_shape": shape},
                  f"answerer briefing with the A04 {shape} shape block") for shape in _CANDIDATE_SHAPES]
    roles += [Role(f"retry_high_effort_{shape}", "answerer", "haiku", "high", f"trigger_fired:candidate_{shape}",
                   {"candidate_shape": shape}, "same briefing, fresh session, effort high")
              for shape in _CANDIDATE_SHAPES]
    roles += [Role(f"verifier_{shape}", "verifier", "haiku", "low", f"answered:{shape}", {},
                   "A05 critic rubric over the candidate's SQL and result digest") for shape in _CANDIDATE_SHAPES]
    roles.append(Role("selector", "selector", "sonnet", "low", "results_disagree", {},
                      "A04 selector rules over one representative per distinct executed result"))
    return tuple(roles)


def _b10_roles() -> tuple[Role, ...]:
    plan = {"plan_mode": "external_plan"}
    # The planner never answers, so the answer-side gates (compiler first, mandatory lookups) do not apply to it.
    roles = [Role("planner", "planner", "sonnet", "high", "always",
                  {"generation_mode": "free_sql", "lookup_mandatory_before_literals": False},
                  "planner briefing: explore, then interpret with restatement, assumptions and steps")]
    roles += [Role(f"candidate_{shape}", "answerer", "haiku", "high", "planned",
                   {**plan, "candidate_shape": shape}, f"executor of the plan, compiler route, A04 {shape} shape")
              for shape in _CANDIDATE_SHAPES]
    roles.append(Role("route_free_sql", "answerer", "haiku", "high", "planned",
                      {**plan, "generation_mode": "free_sql", "lookup_mandatory_before_literals": True},
                      "executor of the plan, free-SQL route (#24 second route)"))
    roles += [Role(f"verifier_{name}", "verifier", "haiku", "low", f"answered:{name}", {},
                   "A05 critic rubric") for name in (*_CANDIDATE_SHAPES, "free_sql")]
    roles.append(Role("selector", "selector", "sonnet", "high", "results_disagree", {},
                      "A04 selector rules over one representative per distinct executed result"))
    roles.append(Role("escalation_sonnet", "answerer", "sonnet", "high", "selector_abstained_or_trigger_fired",
                      plan, "the whole B10 executor briefing on Sonnet, fresh session"))
    return tuple(roles)


BUNDLES: dict[str, Bundle] = {}


def _register(bundle: Bundle) -> None:
    BUNDLES[bundle.bundle_id] = bundle


_register(Bundle(
    "B0", "Base", dict(B0_TECHNIQUE_CONFIG), {},
    approximations={12: _EFFORT_REASON, 20: _TURN_BUDGET_REASON},
))

_register(Bundle(
    "B1", "Knowledge only",
    {**B0_TECHNIQUE_CONFIG, "glossary": "invariants", "playbook": True, "data_dictionary": True,
     "dictionary_detail": "derivations", "data_profile": True, "profile_detail": "enums", "enable_lookup": True,
     "enable_examples": True, "examples_k": 3, "enable_ask": True},
    {1: "inv", 2: "on", 3: "deriv", 4: "enums", 5: "on", 6: "k3 BM25", 21: "ask (multi-question)"},
    approximations={
        3: "#3 derivations come from the semantic model's metric expressions (03_semantic_model.yaml), not from "
           "02_semantic_layer_views.sql: the view SQL is written over raw EDI tables the session cannot read.",
        12: _EFFORT_REASON, 20: _TURN_BUDGET_REASON},
    waiting_on="the #2 playbook file (analyzer/playbook/frozen_playbook.md) is empty until TRAIN distillation and "
               "human review (build item 10); prepare() refuses a playbook bundle until then",
))

_register(Bundle(
    "B2", "Compute only",
    {**B0_TECHNIQUE_CONFIG, **_repair("errors+hints+sig", 4), "result_checks": True, "orchestration_bundle": "B2"},
    {12: "low→high on failure", 13: "errors+hints+sig, max 4", 14: "rules + Haiku verifier",
     15: "3 + Sonnet selector"},
    approximations={
        12: "#12 low→high: a candidate whose failure signal fires (no answer, repairs spent, blocking warning) is "
            "rerun in a fresh Haiku session at effort high; the in-process loop would retry inside one "
            "trajectory. " + _EFFORT_REASON,
        14: "#14 Haiku verifier: a separate Haiku session runs A05's critic rubric on each answered candidate. An "
            "objection drops that candidate from the vote (unless every candidate drew one); it does not send the "
            "candidate back for a revision as A05's in-process loop does.",
        15: "#15 selector: one Sonnet call sees one representative per distinct executed result (up to three) "
            "instead of A04's pairwise comparison. No abstention until #16 is scored: if the selector abstains "
            "the largest agreeing group is answered.",
        20: _TURN_BUDGET_REASON},
    orchestration=Orchestration(
        "candidates_verify_select",
        "Run the three candidates. Rerun a candidate at effort high when its failure signal fires. Verify each "
        "answered candidate. Group verifier-accepted answers by executed rows (all answered ones if none was "
        "accepted): one group is the answer; several groups go to the Sonnet selector; an abstention answers "
        "the largest group. No answer anywhere leaves the session unanswered; all refusals answer refusal.",
        _b2_roles(), FAILURE_SIGNAL_TRIGGER),
))

_register(Bundle(
    "B3", "Typed route",
    {**B0_TECHNIQUE_CONFIG, "data_dictionary": True, "dictionary_detail": "derivations", "enable_lookup": True,
     "lookup_mandatory_before_literals": True, "generation_mode": "compiler_sql_fallback",
     **_repair("errors+hints", 2), "actionable_errors": True, "rewritten_tool_descriptions": True,
     "orchestration_bundle": "B3"},
    {3: "deriv", 5: "mandatory", 8: "compiler, SQL fallback", 13: "errors+hints, max 2",
     19: "actionable errors, descriptions", 24: "compiler + free SQL"},
    approximations={
        3: "#3 derivations come from the semantic model's metric expressions, not the view SQL (see B1).",
        5: "#5 mandatory applies to text dimensions whose stored spelling can differ from the question (payer, "
           "payer class, provider name, status, CARC/remark/PLB descriptions); codes, dates and flags are not "
           "gated. A LIKE pattern needs any lookup on that dimension; = and IN need a returned spelling.",
        12: _EFFORT_REASON, 20: _TURN_BUDGET_REASON},
    orchestration=Orchestration(
        "two_routes_compiler_preferred",
        "Run the compiler route (spec first, SQL only after cannot_express) and the free-SQL route. Same executed "
        "rows: answer the compiler route. Different: answer the compiler route, logged low confidence. Compiler "
        "route unanswered: answer the free-SQL route.",
        (Role("route_compiler", "answerer", "haiku", "low", "always", {}, "answerer briefing with the A03 spec "
              "contract"),
         Role("route_free_sql", "answerer", "haiku", "low", "always", {"generation_mode": "free_sql"},
              "answerer briefing, free SQL")),
    ),
))

_register(Bundle(
    "B4", "Explore freely",
    {**B0_TECHNIQUE_CONFIG, "data_dictionary": True, "data_profile": True, "profile_detail": "full",
     "enable_lookup": True, "explore_probes": True, **_repair("errors", 2), "rows_shown_to_model": 50},
    {3: "desc", 4: "full", 5: "on", 9: "probe", 13: "errors, max 2", 20: "8 / 50"},
    approximations={
        9: "#9 probes: A06's four probe shapes are run by the model through the probe command (not charged to "
           "the command budget, capped at A06's maximum of 13); the notes are the model's own, not compressed "
           "in code as A06's in-process protocol does.",
        12: _EFFORT_REASON, 13: _ERRORS_REPAIR_REASON, 20: _TURN_BUDGET_REASON},
))

_register(Bundle(
    "B5", "Deliberate workflow",
    {**B0_TECHNIQUE_CONFIG, "data_dictionary": True, "enable_examples": True, "examples_k": 3,
     "context_style": "procedural", "generation_mode": "decompose_cte", "plan_mode": "plan_and_replan",
     "state_assumptions": True, "effort": "medium", **_repair("errors+hints", 2), "restate_question": True},
    {3: "desc", 6: "k3 BM25", 7: "procedural", 8: "decompose CTE", 10: "plan + replan", 11: "on", 12: "medium",
     13: "errors+hints, max 2", 25: "on"},
    approximations={
        8: "#8 decompose: A07's roles and check rules are in the briefing and the model runs each step's check "
           "query through sql (charged); A07's in-process controller that enforces one repair per failed step "
           "is not on this route.",
        10: "#10 replan is enforced as a gate: after a rejected answer the CLI refuses sql and answer until a new "
            "interpret record is written. The plan itself is the model's own steps list.",
        12: _EFFORT_REASON, 20: _TURN_BUDGET_REASON},
))

_register(Bundle(
    "B6", "Lean",
    {**B0_TECHNIQUE_CONFIG, "context_style": "minimal", **_repair("errors", 1), "actionable_errors": True,
     "digest_results": True, "rewritten_tool_descriptions": True, "max_turns": 4, "max_tool_calls": 6},
    {7: "minimal", 13: "errors, max 1", 19: "actionable, digest, descriptions", 20: "4 / 20"},
    approximations={
        19: "#19 digest results show 5 rows plus a per-column summary (nulls, distinct, min, max, sum) of every "
            "returned row; there is no published digest format to match.",
        12: _EFFORT_REASON, 13: _ERRORS_REPAIR_REASON, 20: _TURN_BUDGET_REASON},
))

_register(Bundle(
    "B7", "Traps in code",
    {**B0_TECHNIQUE_CONFIG, "glossary": "invariants", "data_dictionary": True, **_repair("errors+hints+sig", 2),
     "result_checks": True, "actionable_errors": True, "domain_lint": True, "reconciliation_checks": True},
    {1: "inv", 3: "desc", 13: "errors+hints+sig, max 2", 14: "rules", 19: "actionable errors",
     22: "on → repair", 23: "on"},
    approximations={
        23: "#23 reconciliation checks three templates on the executed result: check totals vs claim payments "
            "(BPR02 = ΣCLP04 − ΣPLB, only when PLB amounts exist), a rate vs the two counts shown beside it, and "
            "shares of a total summing to the whole. It does not re-derive totals the answer did not show.",
        12: _EFFORT_REASON, 20: _TURN_BUDGET_REASON},
))

_register(Bundle(
    "B8", "Cascade",
    {**B0_TECHNIQUE_CONFIG, "data_dictionary": True, **_repair("errors+hints", 2), "result_checks": True,
     "domain_lint": True, "report_confidence": True, "orchestration_bundle": "B8"},
    {3: "desc", 13: "errors+hints, max 2", 14: "rules", 17: "H→S", 22: "on → repair, trigger",
     26: "lint after repair, failed repair, low self-confidence"},
    approximations={
        17: "#17 Haiku then Sonnet: Sonnet answers in a fresh session with the same briefing; it does not see the "
            "Haiku transcript (the in-process cascade also restarts the trajectory).",
        26: "#26 also escalates a Haiku session that recorded no answer at all (nothing to score, nothing to lose).",
        12: _EFFORT_REASON, 20: _TURN_BUDGET_REASON},
    orchestration=Orchestration(
        "cascade_on_trigger",
        "Run Haiku. If the trigger fires (lint still firing after repair, repair budget spent, self-rated "
        "confidence low, or no answer), run Sonnet on the same briefing and answer with Sonnet's answer when it "
        "recorded one, else Haiku's. Otherwise answer Haiku's.",
        (Role("primary", "answerer", "haiku", "low", "always", {}, "answerer briefing with --confidence"),
         Role("escalation_sonnet", "answerer", "sonnet", "low", "trigger_fired:primary", {},
              "same briefing on Sonnet, fresh session")),
        LINT_REPAIR_CONFIDENCE_TRIGGER),
))

_register(Bundle(
    "B9", "Sonnet planner, Haiku executor",
    {**B0_TECHNIQUE_CONFIG, "data_dictionary": True, **_repair("errors", 2), "plan_mode": "external_plan",
     "orchestration_bundle": "B9"},
    {3: "desc", 10: "plan (by Sonnet)", 11: "on (in plan)", 13: "errors, max 2", 18: "Sonnet planner → Haiku",
     25: "on (in plan)"},
    approximations={
        18: "#18 the planner is a Sonnet subagent with the read-only tools (it may query) that writes one interpret "
            "record; the Haiku executor sees that record in its briefing. A planner refusal answers refusal.",
        12: _EFFORT_REASON, 13: _ERRORS_REPAIR_REASON, 20: _TURN_BUDGET_REASON},
    orchestration=Orchestration(
        "planner_then_executor",
        "Run the Sonnet planner. If it refused, answer refusal. Otherwise run the Haiku executor with the plan "
        "and answer its answer.",
        (Role("planner", "planner", "sonnet", "low", "always", {}, "planner briefing"),
         Role("executor", "answerer", "haiku", "low", "planned", {}, "answerer briefing with the plan")),
    ),
))

_register(Bundle(
    "B10", "Everything on (anchor)",
    {**B0_TECHNIQUE_CONFIG, "glossary": "pitfalls", "playbook": True, "data_dictionary": True,
     "dictionary_detail": "derivations", "data_profile": True, "profile_detail": "full", "enable_lookup": True,
     "lookup_mandatory_before_literals": True, "enable_examples": True, "examples_k": 5,
     "context_style": "procedural", "generation_mode": "compiler_decompose_fallback", "explore_probes": True,
     "plan_mode": "external_plan", "effort": "high", **_repair("errors+hints+sig", 4), "result_checks": True,
     "actionable_errors": True, "rewritten_tool_descriptions": True, "rows_shown_to_model": 50,
     "domain_lint": True, "reconciliation_checks": True, "report_confidence": True, "orchestration_bundle": "B10"},
    {1: "pit", 2: "on", 3: "deriv", 4: "full", 5: "mandatory", 6: "k5 BM25", 7: "procedural",
     8: "compiler, decompose fallback", 9: "probe", 10: "plan + replan", 11: "on", 12: "high",
     13: "errors+hints+sig, max 4", 14: "rules + Haiku verifier", 15: "3 + Sonnet selector", 17: "H→S",
     18: "Sonnet planner → Haiku", 19: "actionable, descriptions", 20: "8 / 50", 22: "on → repair, trigger",
     23: "on", 24: "compiler + free SQL", 25: "on", 26: "all four, incl. candidate/route disagreement"},
    approximations={
        3: "#3 derivations come from the semantic model's metric expressions, not the view SQL (see B1).",
        8: "#8 compiler with decompose fallback: after cannot_express the A07 decompose text applies; the "
           "free-SQL route is a separate candidate (#24).",
        9: "#9 probes are model-run through the probe command (see B4).",
        10: "#10 plan + replan with #18 on: the Sonnet planner writes the plan once and the executors follow it "
            "(plan_mode external_plan). The replan is the trigger's Sonnet escalation session, not a second "
            "planner call.",
        14: "#14 verifier objections drop a candidate from the vote, as in B2.",
        15: "#15 three plan-following candidates (one per A04 shape, compiler route) plus the free-SQL route; one "
            "Sonnet selector call over the distinct results; see B2.",
        24: "#24 two routes are not nested inside each candidate (that would be six executors): the three "
            "compiler-route candidates and one free-SQL route all vote, and route disagreement goes to the "
            "selector with the candidates'.",
        26: "#26 'all four': lint after repair, repair budget spent, low confidence (per the chosen session), and "
            "disagreement, which here means the selector abstained. A Sonnet escalation session then answers.",
        12: _EFFORT_REASON, 17: "#17 see B8: Sonnet escalation is a fresh session.", 20: _TURN_BUDGET_REASON,
        5: "#5 mandatory lookup on text dimensions only (see B3).",
        23: "#23 three identity templates on the executed result (see B7).",
    },
    orchestration=Orchestration(
        "anchor_plan_candidates_select_escalate",
        "Sonnet plans. Three compiler-route candidates and one free-SQL route execute the plan; each answered one "
        "is verified. Verifier-accepted answers that agree are the answer; disagreement goes to the Sonnet "
        "selector. If the selector abstains, or the chosen session's trigger fires (lint after repair, repair "
        "budget spent, low confidence), a Sonnet session runs the executor briefing and its answer is used when "
        "it recorded one.",
        _b10_roles(), LINT_REPAIR_CONFIDENCE_TRIGGER),
    waiting_on="the #2 playbook file is empty until TRAIN distillation and human review (see B1)",
))

RUNNABLE_ORDER = ("B0", "B6", "B7", "B2", "B8", "B4", "B3", "B5", "B9", "B1", "B10")
CURRENT_ANALYZER_TRIGGER_REASONS = CURRENT_ANALYZER_TRIGGER  # #26 off level with #17 on (no bundle uses it)


def bundle(bundle_id: str) -> Bundle:
    if bundle_id not in BUNDLES:
        raise KeyError(f"unknown bundle {bundle_id!r}; expected one of {', '.join(BUNDLES)}")
    return BUNDLES[bundle_id]


def configs_document(bundle_ids: list[str] | None = None) -> dict:
    """The configs.json body ``bench.eval_subagents prepare --configs`` reads, keyed by bundle id."""
    return {bundle_id: dict(bundle(bundle_id).technique_config) for bundle_id in (bundle_ids or list(BUNDLES))}


def orchestration_document(bundle_id: str) -> dict | None:
    spec = bundle(bundle_id).orchestration
    if spec is None:
        return None
    return {"bundle": bundle_id, **asdict(spec),
            "driver": "python3 -m analyzer.orchestration next --session <scored session dir>",
            "contract": "Call next; launch every returned action (its model, effort and prompt) as one subagent; "
                        "when all have ended call next again; stop at status done."}


def describe(bundle_id: str) -> dict:
    item = bundle(bundle_id)
    return {"bundle_id": item.bundle_id, "title": item.title, "technique_config": item.technique_config,
            "parameter_levels": {f"{n} {PARAMETER_NAMES[n]}": level for n, level in item.parameter_levels.items()},
            "approximations": {f"{n} {PARAMETER_NAMES[n]}": text for n, text in sorted(item.approximations.items())},
            "orchestration": orchestration_document(bundle_id), "waiting_on": item.waiting_on}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Analyzer bundles B0-B10")
    parser.add_argument("cmd", choices=["configs", "show"])
    parser.add_argument("bundle_ids", nargs="?", default="")
    args = parser.parse_args(argv)
    ids = [item for item in args.bundle_ids.split(",") if item] or list(BUNDLES)
    if args.cmd == "configs":
        print(json.dumps(configs_document(ids), indent=1))
    else:
        print(json.dumps([describe(bundle_id) for bundle_id in ids], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
