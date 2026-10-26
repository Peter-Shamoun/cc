"""The per-user run cap holds across tabs: two tabs (two clients, same user) share one budget, and the
check-then-register in POST /api/chat has no await between them, so tabs firing at the same instant can't both slip in."""
import ast
import inspect
import textwrap

from fastapi.testclient import TestClient

from api import app as app_module
from api.app import Settings, _Run, create_app
from api.tests.conftest import DX, LABS, ask

Q = {"messages": [{"id": "u1", "role": "user", "text": "How many claims?"}]}


def test_two_tabs_share_one_cap():
    app = create_app(settings=Settings(heartbeat_seconds=5, max_concurrent_runs=2))
    tab_a, tab_b = TestClient(app), TestClient(app)
    principal = app.state.auth.authenticate(LABS["Authorization"].split(" ", 1)[1], "t").principal
    app.state.runs["m_tabA"] = _Run("m_tabA", principal)  # tab A: one answer streaming
    _, evs = ask(tab_b, "How many claims?")  # tab B: a second one is fine
    assert evs[-1]["type"] == "message.end"
    app.state.runs["m_tabB"] = _Run("m_tabB", principal)
    for tab in (tab_a, tab_b):  # now both tabs are at the shared cap
        r = tab.post("/api/chat", json=Q, headers=LABS)
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert ask(TestClient(app), "How many claims?", headers=DX)[1][-1]["type"] == "message.end"  # other user
    app.state.runs["m_tabA"].done = True  # tab A's answer finishes: tab B gets the slot
    assert ask(tab_b, "How many claims?")[1][-1]["type"] == "message.end"


def _chat_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(textwrap.dedent(inspect.getsource(app_module.create_app)))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "chat")


def test_cap_check_and_run_registration_are_atomic():
    fn = _chat_fn()
    check = next(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "active" for t in n.targets))
    register = next(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "runs" for t in n.targets))
    assert check < register
    awaits = [n.lineno for n in ast.walk(fn) if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
              and check <= n.lineno <= register]
    # An await here would let a second tab's request count the same runs before this one registers.
    assert awaits == [], f"await between cap check and run registration at lines {awaits}"
