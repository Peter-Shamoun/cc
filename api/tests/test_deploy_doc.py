"""docs/frontend_deploy.md is the production checklist; keep it in step with the code."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "frontend_deploy.md").read_text()


def _env_vars_read_by_api() -> set[str]:
    found: set[str] = set()
    for f in (ROOT / "api").rglob("*.py"):
        if "tests" in f.parts:
            continue
        found |= set(re.findall(r"[\"'](CC_[A-Z0-9_]*[A-Z0-9])[\"']", f.read_text()))
    return found


def test_every_api_env_var_is_in_the_deploy_checklist():
    names = _env_vars_read_by_api()
    assert {"CC_API_AUTH", "CC_API_LLM", "CC_API_ALLOW_PAID_API"} <= names  # the scan itself works
    missing = sorted(n for n in names if f"`{n}`" not in DOC)
    assert missing == [], f"add these to docs/frontend_deploy.md §8: {missing}"


def test_peter_items_stay_in_the_checklist():
    for must in ("GlobalSignOut", "Nothing rewrites `/assets/`", "Subresource Integrity", "__CC_CSP_NONCE__",
                 "Clear-Site-Data", "text/event-stream", "**S1**", "**S4**", "30 days"):
        assert must in DOC, must
