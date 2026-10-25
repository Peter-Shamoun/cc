"""Screen-2 arm. The yaml file is the same object, for the main dev to copy into a run config."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class CompilerConfig:
    """Flags that define one screen arm. Defaults are the gated typed lane."""

    interface: str = "typed_spec_then_sql"   # typed_only | typed_spec_then_sql
    date_basis_required: bool = True
    cohort_compiler: bool = True
    payer_received_basis: bool = True
    # file_received is never compiled. The spec may name it; the compiler returns cannot_express.
    file_received: str = "cannot_express"
    # Catalog default is 20 whenever a rate is grouped. This arm does not apply that floor
    # unless the question states one (min_group_size on the spec).
    min_group_size_when_unspecified: int = 0
    project_requested_only: bool = True
    phi_lexical: bool = True
    block_identifier_groupby: bool = True
    block_identifier_projection: bool = True
    session_customer: str = "acme_labs"
    other_customer_names: tuple[str, ...] = ("Acme Diagnostics", "acme_diagnostics")
    repair_catalog_errors: int = 1
    fallback: str = "guarded_sem_sql"        # guarded_sem_sql | abstain
    on_ambiguous_basis: str = "abstain"      # the model must name a basis; the compiler does not guess
    default_limit: int = 500

    def fallback_enabled(self) -> bool:
        return self.interface == "typed_spec_then_sql" and self.fallback == "guarded_sem_sql"


def config_from_mapping(raw: dict) -> CompilerConfig:
    known = {f.name for f in CompilerConfig.__dataclass_fields__.values()}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown compiler flags: {sorted(unknown)}")
    data = dict(raw)
    if "other_customer_names" in data and not isinstance(data["other_customer_names"], tuple):
        data["other_customer_names"] = tuple(data["other_customer_names"])
    return CompilerConfig(**data)


def load_screen_arm(path: Path | None = None) -> dict:
    path = path or Path(__file__).with_name("screen2.yaml")
    return yaml.safe_load(path.read_text())


# The arm a screen-2 run should contrast with free SQL. Kept next to the yaml.
SCREEN2_FLAGS: dict = {
    "interface": "typed_spec_then_sql",
    "date_basis_required": True,
    "cohort_compiler": True,
    "payer_received_basis": True,
    "file_received": "cannot_express",
    "min_group_size_when_unspecified": 0,
    "project_requested_only": True,
    "phi_lexical": True,
    "block_identifier_groupby": True,
    "block_identifier_projection": True,
    "repair_catalog_errors": 1,
    "fallback": "guarded_sem_sql",
    "on_ambiguous_basis": "abstain",
    "default_limit": 500,
}
