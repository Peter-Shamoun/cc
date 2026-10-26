"""A backend that replays a stored plan and stored repairs. No model call."""

from __future__ import annotations

from .protocol import Backend, Plan, PlanRequest, RepairRequest, Step


class ScriptedBackend(Backend):
    """Return a plan chosen by question text, and a replacement step when a check fails.

    ``plans`` maps the question text to a plan. ``default_plan`` is used when the
    question is not in that map. ``repairs`` is keyed by ``(question, role)`` or,
    if that misses, by ``role``. A role with no repair is returned unchanged, so
    the check stays failed.
    """

    def __init__(self, plans: dict[str, Plan] | None = None, repairs: dict | None = None, default_plan: Plan | None = None):
        self.plans = plans or {}
        self.repairs = repairs or {}
        self.default_plan = default_plan
        self.plan_requests: list[PlanRequest] = []
        self.repair_requests: list[RepairRequest] = []

    def plan(self, request: PlanRequest) -> Plan:
        self.plan_requests.append(request)
        if request.question in self.plans:
            return self.plans[request.question]
        if self.default_plan is not None:
            return self.default_plan
        raise KeyError(request.question)

    def repair(self, request: RepairRequest) -> Step:
        self.repair_requests.append(request)
        replacement = self.repairs.get((request.question, request.step.role))
        if replacement is None:
            replacement = self.repairs.get(request.step.role)
        if replacement is None:
            return request.step
        return replacement
