"""Question spec the model is allowed to emit. The compiler, not the model, owns SQL."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DATE_BASES = (
    "none",             # no time cut and no time grouping
    "payment",          # payment_date / payment_month on the fact row (cash / EFT date)
    "service",          # date of service on the fact row
    "submission",       # this 837 row's submission_date, not the claim's first submission
    "cohort_submission",  # population = claims whose first submission falls in the window
    "cohort_service",     # population = claims whose first service date falls in the window
    "payer_received",   # DTM*050, the date the payer says it received the claim
    "file_received",    # when the file arrived (raw received_date). Not a public sem.* column.
)

EVENT_BASES = ("payment", "service", "submission", "payer_received")
COHORT_BASES = ("cohort_submission", "cohort_service")
GRAINS = ("day", "week", "month", "quarter", "year")
INTENTS = ("aggregate", "refuse", "cannot_express")


class SpecError(ValueError):
    """The spec is malformed or names something outside the catalog. The model may repair it."""


class CannotExpress(ValueError):
    """The spec is valid enough to judge, and the typed lane cannot answer it."""

    def __init__(self, message: str, code: str = "cannot_express"):
        super().__init__(message)
        self.code = code


@dataclass
class QuestionSpec:
    metrics: list[str]
    date_basis: str
    group_by: list[Any] = field(default_factory=list)
    filters: list[dict] = field(default_factory=list)
    time_grain: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    order_by: list[dict] = field(default_factory=list)
    limit: int = 500
    min_group_size: int | None = None
    include_superseded: bool = False
    intent: str = "aggregate"
    reason: str | None = None
    assumptions: list[str] = field(default_factory=list)

    def group_names(self) -> list[str]:
        names = []
        for g in self.group_by:
            if isinstance(g, str):
                names.append(g)
            else:
                names.append(g["field"])
        return names


def spec_from_dict(raw: dict) -> QuestionSpec:
    if not isinstance(raw, dict):
        raise SpecError("submit_spec input must be an object")
    intent = raw.get("intent", "aggregate")
    if intent not in INTENTS:
        raise SpecError(f"intent must be one of {INTENTS}")
    if intent == "refuse":
        reason = raw.get("reason") or raw.get("refuse_reason")
        if not reason:
            raise SpecError("a refusal needs a reason")
        return QuestionSpec(metrics=[], date_basis="none", intent="refuse", reason=str(reason))
    if intent == "cannot_express":
        reason = raw.get("reason") or "the question is outside the typed fragment"
        raise CannotExpress(str(reason), code=str(raw.get("code") or "model_cannot_express"))
    basis = raw.get("date_basis")
    if basis not in DATE_BASES:
        raise SpecError(f"date_basis is required and must be one of {DATE_BASES}")
    metrics = raw.get("metrics") or []
    if not isinstance(metrics, list) or not metrics or not all(isinstance(m, str) for m in metrics):
        raise SpecError("metrics must be a non-empty list of catalog metric names")
    grain = raw.get("time_grain")
    if grain is not None and grain not in GRAINS:
        raise SpecError(f"time_grain must be one of {GRAINS} or omitted")
    filters = raw.get("filters") or []
    if not isinstance(filters, list):
        raise SpecError("filters must be a list")
    group_by = raw.get("group_by") or []
    if not isinstance(group_by, list):
        raise SpecError("group_by must be a list")
    return QuestionSpec(
        metrics=list(metrics),
        date_basis=basis,
        group_by=group_by,
        filters=filters,
        time_grain=grain,
        time_start=raw.get("time_start"),
        time_end=raw.get("time_end"),
        order_by=list(raw.get("order_by") or []),
        limit=int(raw.get("limit") or 500),
        min_group_size=raw.get("min_group_size"),
        include_superseded=bool(raw.get("include_superseded") or False),
        intent="aggregate",
        reason=raw.get("reason"),
        assumptions=list(raw.get("assumptions") or []),
    )
