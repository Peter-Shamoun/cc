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
