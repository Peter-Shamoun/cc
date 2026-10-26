"""Versioned chat stream contract (see api/CONTRACT.md)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CONTRACT_VERSION = "1.13.0"
SCHEMA_PATH = Path(__file__).with_name("v1.schema.json")


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def validator():
    from jsonschema import Draft202012Validator
    return Draft202012Validator(schema())


def validate_event(event: dict) -> None:
    """Raise ``jsonschema.ValidationError`` if ``event`` is off-contract."""
    from jsonschema import Draft202012Validator
    Draft202012Validator(schema()).validate(event)
