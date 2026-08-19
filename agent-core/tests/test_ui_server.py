"""The local proxy and the deployed proxy must put the same bytes on the wire.

`ui/src/api.js` is written once and runs against both: scripts/ui_server.py when
you `npm run dev`, and ui/proxy/index.mjs behind CloudFront when you deploy. The
two share no code, so nothing but these tests stops them drifting — and the way
drift shows up is a UI that works perfectly in dev and renders empty bubbles in
production.

What is pinned here is the framing, not the content: `data: ` + one JSON object +
a blank line, a `session` frame first, and SSE even when the run fails.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def server():
    spec = importlib.util.spec_from_file_location(
        "ui_server", ROOT / "scripts/ui_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def frames(body: str) -> list[dict]:
    """Parse an SSE body the way ui/src/api.js does."""
    out = []
    for frame in body.split("\n\n"):
        line = next((l for l in frame.split("\n") if l.startswith("data:")), None)
        if line:
            out.append(json.loads(line[5:].strip()))
    return out


def canned(server, events):
    """Replace the pipeline with a fixed event list. No model, no agents."""

    async def fake(job_id, *, session_id=None, prompt=""):
        for event in events:
            yield event

    server.supervisor.stream_pipeline = fake
    return TestClient(server.app)


# --- login -------------------------------------------------------------------


def test_login_returns_the_shape_the_browser_stores(server):
    """api.js computes `expiresAt` from `expiresIn`. A missing field there becomes
    NaN, which compares false against everything and logs you out immediately.

    `token`, not `accessToken`: deployed this is a Cognito ID token, because that
    is the token an API Gateway Cognito authorizer with no scopes accepts.
    """
    body = TestClient(server.app).post("/api/login", json={}).json()
    assert isinstance(body["token"], str) and body["token"]
    assert isinstance(body["expiresIn"], int)


# --- chat framing ------------------------------------------------------------


def test_chat_is_an_event_stream(server):
    response = canned(server, []).post("/api/chat", json={"prompt": "hi"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_the_session_id_comes_back_first(server):
    """The browser stores it and sends it on the next turn, which is what keeps a
    follow-up question in the same memory session."""
    client = canned(server, [{"type": "done", "text": "ok"}])
    parsed = frames(client.post("/api/chat", json={"prompt": "hi", "sessionId": "s-123"}).text)
    assert parsed[0] == {"type": "session", "sessionId": "s-123"}


def test_every_supervisor_event_survives_the_relay(server):
    sent = [
        {"type": "start", "question": "hi", "session_id": "s"},
        {"type": "status", "tool": "ask_screening_agent", "text": "Screening Agent"},
        {"type": "token", "text": "Hi "},
        {"type": "done", "text": "Hi Priya", "stop_reason": "end_turn"},
    ]
    client = canned(server, sent)
    parsed = frames(client.post("/api/chat", json={"prompt": "hi"}).text)
    assert parsed[1:] == sent


def test_frames_are_blank_line_terminated(server):
    """api.js splits on \\n\\n and buffers across chunk boundaries. A single
    newline here would make it hold every event until the connection closed."""
    client = canned(server, [{"type": "token", "text": "a"}, {"type": "token", "text": "b"}])
    body = client.post("/api/chat", json={"prompt": "hi"}).text
    assert body.endswith("\n\n")
    assert "}\n{" not in body


def test_text_with_newlines_stays_in_one_frame(server):
    """An outreach note is multi-line. Emitting it raw would split one event
    across several frames and the JSON parse on each half would fail."""
    note = "Hi Priya,\n\nWe have a role.\n\n— HR"
    client = canned(server, [{"type": "done", "text": note}])
    parsed = frames(client.post("/api/chat", json={"prompt": "hi"}).text)
    assert parsed[-1]["text"] == note


def test_a_failing_run_still_arrives_as_sse(server):
    """The supervisor turns its own exceptions into an error event. If one escaped
    anyway the response would be a 500 with an HTML body, and api.js would throw
    on a stream it had already started reading."""

    async def explode(job_id, *, session_id=None, prompt=""):
        yield {"type": "error", "text": "RuntimeError: screening agent unreachable"}

    server.supervisor.stream_pipeline = explode
    response = TestClient(server.app).post("/api/chat", json={"prompt": "hi"})
    assert response.status_code == 200
    assert frames(response.text)[-1]["type"] == "error"
