"""docs/frontend_handoff.md §5 (contract section) must stay true: every function it names exists where it says,
the paid switch phrase matches the code, every env var it lists is documented in frontend_deploy.md, and the adapter
is still unwired (no paid calls). Docs-only checks: nothing here builds a model client."""
import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "frontend_handoff.md").read_text()
SECTION = DOC[DOC.index("<!-- SECTION: contract (analyzer adapter)"): DOC.index("<!-- /SECTION: contract -->")]


def test_section_replaced_placeholder():
    assert "Until the Contract team's section lands" not in DOC
    for heading in ("5.2 Functions", "5.4 The paid switch", "5.5 Environment variables", "5.6 Analyzer configuration",
                    "5.7", "5.8"):
        assert heading in SECTION


def test_every_named_function_exists_in_its_file():
    rows = re.findall(r"^\| `([A-Za-z_]+)[^`]*`(?:, `[^`]+`)* \| `([a-z_]+)\.py` \|", SECTION, re.M)
    assert len(rows) >= 8
    for name, mod in rows:
        assert hasattr(importlib.import_module(f"api.{mod}"), name), (name, mod)


def test_paid_switch_phrase_matches_code():
    from api import analyzer_adapter
    assert f"CC_API_ALLOW_PAID_API={analyzer_adapter.PAID_SWITCH_VALUE}" in SECTION
    assert analyzer_adapter.paid_api_enabled({}) is False


def test_env_vars_are_the_documented_ones():
    deploy = (ROOT / "docs" / "frontend_deploy.md").read_text()
    names = set(re.findall(r"`(CC_API_[A-Z_]+)`", SECTION)) - {"CC_API_MODE"}  # documented as not read by any code
    assert names and all(n in deploy for n in names), sorted(n for n in names if n not in deploy)


def test_adapter_is_still_unwired():
    assert "analyzer_adapter" not in (ROOT / "api" / "app.py").read_text()
