"""Run a multi-agent bundle (B2, B3, B8, B9, B10) as a deterministic state machine over session directories.

    python3 -m analyzer.orchestration next --session <scored session dir>

``next`` reads what the role sessions have written and returns either
    {"status": "run", "actions": [{"role", "session", "model", "effort", "prompt"}, ...]}
or
    {"status": "done", "decision": {...}}
after writing the scored session's final.json (a copy of the chosen role's answer, plus the decision) so
``bench.eval_subagents score`` grades it unchanged. Contract for the runner: launch every returned action as one
subagent with that model and effort and send it ``prompt``; when all of them have ended, call ``next`` again. A
role is issued once; an issued role that wrote nothing counts as finished without an answer.

Role sessions live in ``<scored>/roles/<role_name>/`` and copy the scored session's meta, with the role's
technique overrides, model and effort, and ``meta["role"]`` (kind, plus the plan, reviewed answer or candidates
the role's briefing shows). A role never reads another session's files; what it needs is in its own meta.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyzer.bundles import Role, bundle
from analyzer.harness import bootstrap_instruction, config_from_meta
from analyzer.trigger import evaluate_trigger

ROLES_DIRECTORY = "roles"
STATE_FILE = "orchestration_state.json"
DECISION_FILE = "decision.json"
ROWS_SHOWN_TO_REVIEWERS = 20


class Orchestrator:
    def __init__(self, scored_session_dir: Path):
        self.scored = Path(scored_session_dir)
        self.meta = json.loads((self.scored / "session.json").read_text())
        bundle_id = self.meta["technique_config"].get("orchestration_bundle")
        if not bundle_id:
            raise ValueError(f"{self.scored} is not an orchestrated session (no orchestration_bundle)")
        self.bundle = bundle(bundle_id)
        self.spec = self.bundle.orchestration
        self.roles = {role.role_name: role for role in self.spec.roles}
        state_path = self.scored / STATE_FILE
        self.state = json.loads(state_path.read_text()) if state_path.exists() else {"issued": []}
        self.pending: list[dict] = []

    # ---- role sessions -------------------------------------------------------------------------------------
    def role_dir(self, role_name: str) -> Path:
        return self.scored / ROLES_DIRECTORY / role_name

    def issued(self, role_name: str) -> bool:
        return role_name in self.state["issued"]

    def issue(self, role_name: str, role_extra: dict | None = None, extra_overrides: dict | None = None) -> None:
        """Create the role session and queue it. A role is issued at most once per scored session."""
        if self.issued(role_name):
            raise RuntimeError(f"role {role_name} was already issued")
        role: Role = self.roles[role_name]
        directory = self.role_dir(role_name)
        directory.mkdir(parents=True)
        technique = {key: value for key, value in self.meta["technique_config"].items()
                     if key != "orchestration_bundle"}
        technique.update(role.technique_overrides, **(extra_overrides or {}), effort=role.effort)
        meta = {**self.meta, "technique_config": technique, "model": role.model,
                "role": {"kind": role.kind, "name": role_name, "scored_session": str(self.scored),
                         **(role_extra or {})}}
        (directory / "session.json").write_text(json.dumps(meta, indent=1, default=str))
        self.state["issued"].append(role_name)
        self.pending.append({"role": role_name, "session": str(directory), "model": role.model,
                             "effort": role.effort, "prompt": bootstrap_instruction(directory)})

    def final(self, role_name: str) -> dict | None:
        path = self.role_dir(role_name) / "final.json"
        return json.loads(path.read_text()) if self.issued(role_name) and path.exists() else None

    def answered(self, role_name: str) -> bool:
        final = self.final(role_name)
        return final is not None and final.get("kind") == "answer"

    def concluded(self, role_name: str) -> bool:
        """An answer or a refusal: something the scored session can carry."""
        final = self.final(role_name)
        return final is not None and final.get("kind") in {"answer", "refusal"}

    def trigger(self, role_name: str) -> dict:
        return evaluate_trigger(self.role_dir(role_name), self.spec.trigger_reasons)

    def executed(self, role_name: str) -> dict:
        """Columns and rows of the role's final SQL, executed the way the scorer executes it."""
        from bench.dataset import open_snapshot
        from bench.guard import guard
        final = self.final(role_name)
        connection = open_snapshot(Path(self.meta["snapshot_path"]), escalate=self.meta.get("escalate", False))
        cursor = connection.execute(guard(final["sql"], 1000, self.meta.get("escalate", False)))
        return {"columns": [item[0] for item in cursor.description], "rows": cursor.fetchall()}

    def fingerprint(self, role_name: str) -> tuple:
        from analyst.approaches.a04_multi_candidate.selector import fingerprint
        result = self.executed(role_name)
        return fingerprint(result["columns"], result["rows"])

    # ---- decisions -----------------------------------------------------------------------------------------
    def save(self) -> None:
        (self.scored / STATE_FILE).write_text(json.dumps(self.state, indent=1))

    def commit(self, role_name: str | None, **decision) -> dict:
        decision = {"bundle": self.bundle.bundle_id, "rule": self.spec.decision_rule, "chosen_role": role_name,
                    **decision}
        if role_name is not None:
            final = dict(self.final(role_name))
            final["orchestration"] = decision
            (self.scored / "final.json").write_text(json.dumps(final, default=str))
        (self.scored / DECISION_FILE).write_text(json.dumps(decision, indent=1, default=str))
        return {"status": "done", "decision": decision}

    def run_pending(self) -> dict:
        self.save()
        return {"status": "run", "actions": self.pending}

    def next(self) -> dict:
        if (self.scored / DECISION_FILE).exists():
            return {"status": "done", "decision": json.loads((self.scored / DECISION_FILE).read_text())}
        rule = DECISION_RULES[self.spec.decision_rule]
        return rule(self)


def _issue_missing(orchestrator: Orchestrator, role_names: list[str], **kwargs) -> bool:
    for name in role_names:
        if not orchestrator.issued(name):
            orchestrator.issue(name, **kwargs)
    return bool(orchestrator.pending)


def cascade_on_trigger(o: Orchestrator) -> dict:
    """B8."""
    if _issue_missing(o, ["primary"]):
        return o.run_pending()
    fired = o.trigger("primary")
    if not fired["escalate"]:
        return o.commit("primary" if o.concluded("primary") else None, trigger=fired)
    if _issue_missing(o, ["escalation_sonnet"]):
        return o.run_pending()
    chosen = "escalation_sonnet" if o.concluded("escalation_sonnet") else (
        "primary" if o.concluded("primary") else None)
    return o.commit(chosen, trigger=fired, escalated=True)


def planner_then_executor(o: Orchestrator) -> dict:
    """B9."""
    from analyzer.cli import latest_interpretation
    if _issue_missing(o, ["planner"]):
        return o.run_pending()
    planner_final = o.final("planner")
    if planner_final is not None and planner_final.get("kind") == "refusal":
        return o.commit("planner", planner="refused")
    plan = latest_interpretation(o.role_dir("planner"))
    if not o.issued("executor"):
        if plan is None:
            o.issue("executor", extra_overrides={"plan_mode": "none"})
        else:
            o.issue("executor", role_extra={"plan": plan})
        return o.run_pending()
    return o.commit("executor" if o.concluded("executor") else None, planner_wrote_plan=plan is not None)


def two_routes_compiler_preferred(o: Orchestrator) -> dict:
    """B3."""
    if _issue_missing(o, ["route_compiler", "route_free_sql"]):
        return o.run_pending()
    if o.answered("route_compiler"):
        if o.answered("route_free_sql"):
            agree = o.fingerprint("route_compiler") == o.fingerprint("route_free_sql")
            return o.commit("route_compiler", routes_agree=agree, low_confidence=not agree)
        return o.commit("route_compiler", routes_agree=None, low_confidence=True)
    if o.concluded("route_compiler"):
        return o.commit("route_compiler", routes_agree=None)
    return o.commit("route_free_sql" if o.concluded("route_free_sql") else None, compiler_route_unanswered=True)


def _verifier_name(candidate_role: str) -> str:
    base = candidate_role.removeprefix("retry_high_effort_").removeprefix("candidate_").removeprefix("route_")
    return f"verifier_{base}"


def _reviewed_payload(o: Orchestrator, role_name: str) -> dict:
    from analyst.approaches.a05_verifier.prompts import result_digest
    from analyzer.cli import latest_interpretation
    executed = o.executed(role_name)
    rows = [list(row) for row in executed["rows"]]
    plan = latest_interpretation(o.role_dir(role_name)) or (
        json.loads((o.role_dir(role_name) / "session.json").read_text())["role"].get("plan"))
    return {"sql": o.final(role_name)["sql"], "digest": result_digest(executed["columns"], rows),
            "interpretation": plan}


def _schema_text(o: Orchestrator) -> str:
    from analyzer.opener import SnapshotOpener
    from analyzer.tools import ToolLayer, _glossary_body
    config = config_from_meta(o.meta, for_subagent=True)
    tools = ToolLayer(SnapshotOpener(o.meta["snapshot_path"]), config, restored_escalation=o.meta.get("escalate", False))
    lines = []
    for relation in tools.list_relations(charge=False):
        columns = tools.describe_relation(relation, charge=False)["columns"]
        lines.append(f"{relation}(" + ", ".join(f"{c['name']} {c['type']}" for c in columns) + ")")
    return "\n".join(lines) + "\n\n" + _glossary_body(config)


def _verdict(o: Orchestrator, candidate_role: str) -> dict | None:
    path = o.role_dir(_verifier_name(candidate_role)) / "verdict.json"
    return json.loads(path.read_text()) if path.exists() else None


def _candidates_core(o: Orchestrator, candidate_roles: list[str], *, retry_on_failure: bool,
                     plan: dict | None) -> dict | tuple[str | None, dict]:
    """Shared by B2 and B10: run, (retry,) verify, group, select. Returns run actions or (chosen role, decision)."""
    from analyst.approaches.a04_multi_candidate.selector import sanity_warnings
    extra = {"plan": plan} if plan is not None else None
    if _issue_missing(o, candidate_roles, role_extra=extra):
        return o.run_pending()
    effective = []
    for name in candidate_roles:
        retry = name.replace("candidate_", "retry_high_effort_", 1)
        if retry_on_failure and retry in o.roles:
            if o.trigger(name)["escalate"] and not o.issued(retry):
                o.issue(retry, role_extra=extra)
                continue
            if o.issued(retry) and o.concluded(retry):
                effective.append(retry)
                continue
        effective.append(name)
    if o.pending:
        return o.run_pending()
    answered = [name for name in effective if o.answered(name)]
    if not answered:
        refusals = [name for name in effective if o.concluded(name)]
        chosen = refusals[0] if refusals and len(refusals) == len(effective) else None
        return chosen, {"candidates": effective, "outcome": "refusal" if chosen else "no candidate answered"}
    for name in answered:
        verifier = _verifier_name(name)
        if verifier in o.roles and not o.issued(verifier):
            o.issue(verifier, role_extra={"reviewed": _reviewed_payload(o, name), "schema_text": _schema_text(o)})
    if o.pending:
        return o.run_pending()
    accepted = [name for name in answered if (_verdict(o, name) or {}).get("accept")]
    voters = accepted or answered
    groups: dict[tuple, list[str]] = {}
    for name in voters:
        groups.setdefault(o.fingerprint(name), []).append(name)
    ranked = sorted(groups.values(), key=lambda members: (-len(members), voters.index(members[0])))
    decision = {"candidates": effective, "answered": answered, "verifier_accepted": accepted,
                "groups": [members for members in ranked]}
    if len(ranked) == 1:
        return ranked[0][0], {**decision, "agreement": True}
    labels = "abcdefgh"
    representatives = {labels[index]: members[0] for index, members in enumerate(ranked)}
    if not o.issued("selector"):
        shown = []
        for label, name in representatives.items():
            executed = o.executed(name)
            verdict = _verdict(o, name)
            shown.append({"label": label, "role": name, "sql": o.final(name)["sql"],
                          "columns": executed["columns"], "row_count": len(executed["rows"]),
                          "rows": [list(row) for row in executed["rows"][:ROWS_SHOWN_TO_REVIEWERS]],
                          "warnings": list(sanity_warnings(o.final(name)["sql"], executed["columns"],
                                                           executed["rows"])),
                          "verifier": None if verdict is None else ("accept" if verdict["accept"] else
                                                                    "object: " + "; ".join(verdict["objections"]))})
        o.issue("selector", role_extra={"candidates": json.loads(json.dumps(shown, default=str)),
                                        "representatives": representatives})
        return o.run_pending()
    selection_path = o.role_dir("selector") / "selection.json"
    selection = json.loads(selection_path.read_text()) if selection_path.exists() else {"choice": "abstain",
                                                                                          "reason": "no choice"}
    if selection["choice"] in representatives:
        return representatives[selection["choice"]], {**decision, "agreement": False, "selection": selection}
    return ranked[0][0], {**decision, "agreement": False, "selection": selection, "selector_abstained": True}


def candidates_verify_select(o: Orchestrator) -> dict:
    """B2."""
    outcome = _candidates_core(o, [f"candidate_{shape}" for shape in ("direct", "query_plan", "divide_and_conquer")],
                               retry_on_failure=True, plan=None)
    if isinstance(outcome, dict):
        return outcome
    chosen, decision = outcome
    return o.commit(chosen, **decision)


def anchor_plan_candidates_select_escalate(o: Orchestrator) -> dict:
    """B10."""
    from analyzer.cli import latest_interpretation
    if _issue_missing(o, ["planner"]):
        return o.run_pending()
    planner_final = o.final("planner")
    if planner_final is not None and planner_final.get("kind") == "refusal":
        return o.commit("planner", planner="refused")
    plan = latest_interpretation(o.role_dir("planner"))
    if plan is None:
        return o.commit(None, outcome="planner wrote no plan")
    candidates = [f"candidate_{shape}" for shape in ("direct", "query_plan", "divide_and_conquer")] + ["route_free_sql"]
    outcome = _candidates_core(o, candidates, retry_on_failure=False, plan=plan)
    if isinstance(outcome, dict):
        return outcome
    chosen, decision = outcome
    fired = o.trigger(chosen) if chosen and o.answered(chosen) else {"escalate": False, "fired": []}
    if not (decision.get("selector_abstained") or fired["escalate"] or chosen is None):
        return o.commit(chosen, **decision, trigger=fired)
    if not o.issued("escalation_sonnet"):
        o.issue("escalation_sonnet", role_extra={"plan": plan})
        return o.run_pending()
    final_role = "escalation_sonnet" if o.concluded("escalation_sonnet") else chosen
    return o.commit(final_role, **decision, trigger=fired, escalated=True)


DECISION_RULES = {
    "cascade_on_trigger": cascade_on_trigger,
    "planner_then_executor": planner_then_executor,
    "two_routes_compiler_preferred": two_routes_compiler_preferred,
    "candidates_verify_select": candidates_verify_select,
    "anchor_plan_candidates_select_escalate": anchor_plan_candidates_select_escalate,
}


def next_actions(scored_session_dir: Path | str) -> dict:
    return Orchestrator(Path(scored_session_dir)).next()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Drive one orchestrated bundle session")
    parser.add_argument("cmd", choices=["next"])
    parser.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(next_actions(args.session), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
