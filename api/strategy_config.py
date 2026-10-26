"""Strategy → ``AnalyzerConfig`` overrides (adapter spec R4). Pure functions; nothing here runs a model.

Two inputs pick how the real analyzer runs:

* the **contract options** a client sends: ``ChatRequest.options.strategy`` / ``.model`` (ids from
  ``GET /api/capabilities``, see ``api.capabilities``), and
* optionally a **384-grid cell**: the research factors from ``research/grid/GRID_PROTOCOL.md``
  (``generation`` ∈ {direct_sql, metric_spec, decompose_cte} × the binary flags fewshot, explore, candidates3, verifier,
  repair, assumptions, multiagent = 3 × 2⁷ = 384 cells). Field names are read from ``analyzer/config.py``
  (``AnalyzerConfig``) and never written.

``strategy_config`` returns a ``StrategyConfig`` whose ``overrides`` dict can be passed to ``AnalyzerConfig(**overrides)``
or ``AnalyzerConfig.from_technique(overrides)``. Every grid factor is tagged with how faithfully the analyzer can
express it, so a caller never silently runs something other than the cell it asked for:

* ``exact``: an ``AnalyzerConfig`` field does exactly what the protocol describes.
* ``approximate``: the closest analyzer feature (listed in ``FACTORS``); a run is comparable but not identical.
* ``unsupported``: no analyzer field exists (``decompose_cte``, ``multiagent=1``). ``strict=True`` raises instead.

Not wired: ``api.app`` validates and echoes the strategy but still runs the demo scenario (spec "Rollout").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Mapping

from api.capabilities import DEFAULT_MODEL, DEFAULT_STRATEGY, STRATEGIES, check_options

GENERATIONS = ("direct_sql", "metric_spec", "decompose_cte")
FLAGS = ("fewshot", "explore", "candidates3", "verifier", "repair", "assumptions", "multiagent")
GRID_FACTORS = ("generation",) + FLAGS

#: factor → level → (fidelity, AnalyzerConfig overrides, note). Both levels of every flag are explicit, so a cell
#: fully determines the fields it touches regardless of the analyzer's defaults.
FACTORS: dict[str, dict] = {
    "generation": {
        "direct_sql": ("exact", {}, "The analyzer writes SQL directly."),
        "metric_spec": ("approximate", {"enable_metrics": True},
                        "Closest: the typed metric layer (query_metrics). The grid's free-form spec step is a prompt "
                        "protocol the analyzer has no switch for."),
        "decompose_cte": ("unsupported", {}, "No AnalyzerConfig field builds or checks CTE sub-steps."),
    },
    "fewshot": {
        True: ("approximate", {"enable_examples": True},
               "The analyzer offers a lookup_examples tool instead of pasting the 3 nearest solved questions."),
        False: ("exact", {"enable_examples": False}, ""),
    },
    "explore": {
        True: ("exact", {"data_profile": True, "enable_lookup": True},
               "profile + lookup_values tools (describe/sample are always available)."),
        False: ("exact", {"data_profile": False, "enable_lookup": False}, ""),
    },
    "candidates3": {
        True: ("exact", {"candidates": 3, "candidate_selection": "execution_vote"},
               "3 executed candidates, picked by agreement of their result rows."),
        False: ("exact", {"candidates": 1}, ""),
    },
    "verifier": {
        True: ("approximate", {"verify": True, "result_checks": True},
               "The analyzer's verifier is a deterministic result check that bounces the answer, not a fresh-context "
               "critic model pass."),
        False: ("exact", {"verify": False}, ""),
    },
    "repair": {
        True: ("exact", {"self_correct": "errors+result_signals", "max_repairs": 2},
               "Up to 2 repairs on errors and suspicious results."),
        False: ("exact", {"self_correct": "none", "max_repairs": 0}, ""),
    },
    "assumptions": {
        True: ("approximate", {}, "final_answer always accepts an assumptions list; there is no switch that forces it."),
        False: ("approximate", {}, "final_answer always accepts an assumptions list; it cannot be turned off."),
    },
    "multiagent": {
        True: ("unsupported", {}, "The analyzer is a single-context tool loop; there is no planner/writer split."),
        False: ("exact", {}, ""),
    },
}


@dataclass(frozen=True)
class StrategyConfig:
    strategy: str
    model: str
    overrides: dict
    fidelity: dict = field(default_factory=dict)       # grid factor → "exact" | "approximate" | "unsupported"
    notes: dict = field(default_factory=dict)          # grid factor → note (only for non-exact levels)

    @property
    def unsupported(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.fidelity.items() if v == "unsupported")

    @property
    def approximate(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.fidelity.items() if v == "approximate")


def normalize_cell(cell: Mapping) -> dict:
    """A grid cell as ``{generation: str, <flag>: bool}``. Accepts ``research/grid/design.json`` rows, the ``config``
    object of a ``research/grid/C*.json`` result (flags as 0/1 or bools), and an ``id`` key, which is ignored."""
    data = {k: v for k, v in dict(cell).items() if k not in ("id", "cell")}
    unknown = sorted(set(data) - set(GRID_FACTORS))
    if unknown:
        raise ValueError(f"unknown grid factors {unknown}")
    missing = [k for k in GRID_FACTORS if k not in data]
    if missing:
        raise ValueError(f"grid cell is missing {missing}")
    if data["generation"] not in GENERATIONS:
        raise ValueError(f"unknown generation {data['generation']!r}")
    out = {"generation": data["generation"]}
    for flag in FLAGS:
        value = data[flag]
        if value not in (0, 1, True, False) or isinstance(value, float):
            raise ValueError(f"grid flag {flag} must be 0/1 or a bool, got {value!r}")
        out[flag] = bool(value)
    return out


def grid_overrides(cell: Mapping) -> tuple[dict, dict, dict]:
    """(overrides, fidelity, notes) for one 384-grid cell."""
    norm = normalize_cell(cell)
    overrides: dict = {}
    fidelity: dict = {}
    notes: dict = {}
    for factor in GRID_FACTORS:
        level, values, note = FACTORS[factor][norm[factor]]
        overrides.update(values)
        fidelity[factor] = level
        if level != "exact":
            notes[factor] = note
    if norm["verifier"] and not norm["repair"]:
        notes["verifier"] += (" With repair=0 the bounce is inert: the analyzer only bounces when self_correct is "
                              "errors+result_signals, so only the result checks remain.")
    return overrides, fidelity, notes


def strategy_config(strategy: str = DEFAULT_STRATEGY, model: str = DEFAULT_MODEL, *, grid: Mapping | None = None,
                    customer: str | None = None, strict: bool = False) -> StrategyConfig:
    """``AnalyzerConfig`` overrides for a contract strategy + model, optionally refined by a 384-grid cell.

    Raises ``ValueError`` for an unknown/incompatible/disabled strategy or model (the same rule as the API's 422,
    ``api.capabilities.check_options``), for a grid cell that contradicts the strategy (``candidates`` with
    ``candidates3=0``), and, with ``strict=True``, for a cell that has unsupported factors."""
    problem = check_options({"strategy": strategy, "model": model}, customer)
    if problem:
        raise ValueError(problem)
    overrides = dict(next(s for s in STRATEGIES if s["id"] == strategy)["config"])
    if model != DEFAULT_MODEL:
        overrides["model"] = model
    fidelity: dict = {}
    notes: dict = {}
    if grid is not None:
        cell, fidelity, notes = grid_overrides(grid)
        if strategy == "candidates" and cell.get("candidates") == 1:
            raise ValueError("strategy 'candidates' contradicts grid candidates3=0")
        if strict and any(v == "unsupported" for v in fidelity.values()):
            bad = sorted(k for k, v in fidelity.items() if v == "unsupported")
            raise ValueError(f"grid factors not supported by the analyzer: {bad}")
        overrides.update(cell)
    return StrategyConfig(strategy=strategy, model=model, overrides=overrides, fidelity=fidelity, notes=notes)


def all_grid_cells() -> list[dict]:
    """All 384 cells in a stable order (generation-major, then the flags as binary, fewshot most significant)."""
    return [{"generation": g, **dict(zip(FLAGS, bits))}
            for g in GENERATIONS for bits in product((False, True), repeat=len(FLAGS))]
