"""Screen-arm flags. One factor changes per arm. The primary arm is value retrieval."""
from __future__ import annotations

from dataclasses import asdict, dataclass

ALLOWED_FLAGS = frozenset({
    "schema_mode", "value_index", "fewshot_k", "fewshot_selector", "near_duplicate_jaccard",
    "exclude_same_family", "recall_floor_views", "glossary", "context_style", "hard_refuse",
    "max_examples_in_prompt",
})


@dataclass(frozen=True)
class RetrievalConfig:
    schema_mode: str = "full"
    value_index: bool = False
    fewshot_k: int = 0
    fewshot_selector: str = "skeleton"
    near_duplicate_jaccard: float = 0.92
    exclude_same_family: bool = True
    recall_floor_views: int = 2
    glossary: str = "basic"
    context_style: str = "goal"
    hard_refuse: bool = False
    max_examples_in_prompt: int = 3

    def __post_init__(self) -> None:
        if self.schema_mode not in {"full", "linked_recall", "linked_columns"}:
            raise ValueError(f"unknown schema_mode {self.schema_mode!r}")
        if self.fewshot_selector not in {"skeleton", "lexical"}:
            raise ValueError(f"unknown fewshot_selector {self.fewshot_selector!r}")
        if self.glossary not in {"none", "basic"}:
            raise ValueError(
                "glossary must be 'none' or 'basic'; this variant does not load the pitfalls glossary"
            )
        if self.context_style not in {"goal", "procedural"}:
            raise ValueError(f"unknown context_style {self.context_style!r}")
        if self.fewshot_k < 0:
            raise ValueError("fewshot_k must be >= 0")
        if self.max_examples_in_prompt < 0:
            raise ValueError("max_examples_in_prompt must be >= 0")
        if not 0 < self.near_duplicate_jaccard <= 1:
            raise ValueError("near_duplicate_jaccard must be in (0, 1]")
        if self.recall_floor_views < 1:
            raise ValueError("recall_floor_views must be at least 1")

    def as_flags(self) -> dict:
        return asdict(self)


def config_from_flags(flags: dict) -> RetrievalConfig:
    unknown = set(flags) - ALLOWED_FLAGS
    if unknown:
        raise ValueError(f"unknown context-retrieval flags: {sorted(unknown)}")
    return RetrievalConfig(**flags)


# Compared one at a time with the held-out base (glossary basic, goal, SQL on,
# lookup off, examples off, effort low). hard_refuse stays off so a regex
# refusal does not change the safety items relative to that base.
SCREEN_ARMS: dict[str, dict] = {
    "cr0_base": RetrievalConfig().as_flags(),
    "cr_value": RetrievalConfig(value_index=True).as_flags(),
    "cr_fewshot_skeleton": RetrievalConfig(fewshot_k=3, fewshot_selector="skeleton").as_flags(),
    "cr_fewshot_lexical": RetrievalConfig(fewshot_k=3, fewshot_selector="lexical").as_flags(),
    "cr_schema_recall": RetrievalConfig(schema_mode="linked_recall").as_flags(),
    "cr_all": RetrievalConfig(schema_mode="linked_recall", value_index=True, fewshot_k=3).as_flags(),
}
