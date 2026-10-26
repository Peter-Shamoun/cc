"""A03: metric-spec first. The model writes a spec; the compiler writes SQL over sem.*.

Free SQL is the fallback only after the compiler reports that the spec cannot express
the question. The eval adapter plugs that loop into ``bench.eval_subagents`` as the
``a03_metric_spec`` configuration.
"""

from .compiler import CannotExpress, CompiledSpec, SpecError, compile_spec
from .adapter import configs, estimate_question_cost, technique_config

__all__ = [
    "CannotExpress",
    "CompiledSpec",
    "SpecError",
    "compile_spec",
    "configs",
    "estimate_question_cost",
    "technique_config",
]
