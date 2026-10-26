import json
import os

# Tests run unpaced unless a test opts in (api/app.py demo pacing, QA-P1).
os.environ.setdefault("CC_API_DEMO_PACE", "0")

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app

LABS = {"Authorization": "Bearer demo:acme_labs:ana@xifin.example"}
DX = {"Authorization": "Bearer demo:acme_diagnostics:dev@xifin.example"}


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def ask(client, text, headers=LABS, conversation_id=None):
    body = {"messages": [{"id": "u1", "role": "user", "text": text}]}
    if conversation_id:
        body["conversationId"] = conversation_id
    response = client.post("/api/chat", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response, events(response)


def parts(evs):
    return [e["part"] for e in evs if e["type"] == "part"]


@pytest.fixture
def app():
    return create_app(settings=Settings(heartbeat_seconds=5))


@pytest.fixture
def client(app):
    return TestClient(app)


def cc(*words):
    """One scripted model turn: the analyzer's ``cc`` tool with this command line."""
    from analyzer.scripted_model import ScriptedTurn
    from api.demo_model import command
    return ScriptedTurn(tool_calls=[("cc", {"command": command(*words)})])


def scripted_analyzer(*turns, seconds_per_turn: float = 0.0):
    """The app's analyzer with a scripted Strands model replaying ``turns`` (fresh for every run), then DONE."""
    from analyzer.scripted_model import ScriptedModel, scripted_turns
    from api.strands_analyzer import AnalyzerSettings, StrandsAnalyzer
    return StrandsAnalyzer(AnalyzerSettings(model_kind="scripted"),
                           lambda scenario=None: ScriptedModel(scripted_turns(*turns), seconds_per_turn=seconds_per_turn),
                           "scripted")


@pytest.fixture(scope="session")
def bench_snapshot():
    """The cached benchmark snapshot (built on first use)."""
    from bench.dataset import snapshot
    return snapshot()
