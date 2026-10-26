"""web/src/contract/v1.gen.ts is generated from the schema and checked in; it must be current.

The hand-written client types are then pinned to the generated ones at compile time by
web/src/contract/compat.test.ts (tsc -b fails on drift in either direction)."""
import copy

from api.contract import CONTRACT_VERSION, schema
from api.contract import gen_ts


def test_generated_ts_is_current():
    assert gen_ts.main(["--check"]) == 0, "run: python -m api.contract.gen_ts"


def test_generated_ts_carries_version_and_every_type():
    text = gen_ts.OUT.read_text()
    assert f"export const CONTRACT_VERSION = '{CONTRACT_VERSION}';" in text
    for name in schema()["$defs"]:
        assert f" {name} " in text, name


def test_generator_notices_schema_changes():
    changed = copy.deepcopy(schema())
    changed["$defs"]["Usage"]["properties"]["newField"] = {"type": "string"}
    assert gen_ts.generate(changed) != gen_ts.generate(schema())
