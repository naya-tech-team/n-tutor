"""The supervisor's event stream is the chat UI's only contract.

`ui/` renders four event types and ignores the rest. Nothing in Python or
JavaScript checks the two against each other, so these tests are the seam: break
the shape here and the browser shows a blank bubble with no error anywhere.

The subtle one is dedupe. `stream_async` re-yields `current_tool_use` on every
chunk while a tool's arguments accumulate, so a naive pass-through announces
"asking the Screening Agent" thirty times. Keying on the tool *name* fixes that
and introduces a worse bug — a second, legitimate call to the same tool silently
stops appearing, which is exactly what happens when the outreach agent is asked
again with the corrective message.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path


import pytest

MAIN = Path(__file__).resolve().parents[1] / "app/runtimes/hiring_supervisor/main.py"


@pytest.fixture(scope="module")
def supervisor():
    spec = importlib.util.spec_from_file_location("hiring_supervisor_main", MAIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeAgent:
    """Replays a canned `stream_async` sequence. No model, no network."""

    def __init__(self, events):
        self._events = events

    async def stream_async(self, question, **kwargs):
        for event in self._events:
            yield event


def drain(module, events, **kwargs) -> list[dict]:
    """Run stream_pipeline over a canned agent and collect what it emits."""
    module._session_manager = lambda session_id: None
    module.build_supervisor = lambda session_id, manager=None: FakeAgent(events)

    async def go():
        return [e async for e in module.stream_pipeline("J2001", **kwargs)]

    return asyncio.run(go())


def tool_use(tool_use_id, name, arguments=""):
    return {"current_tool_use": {"toolUseId": tool_use_id, "name": name, "input": arguments}}


class FakeResult:
    """`str(result)` is how the real AgentResult yields its text, and dunder lookup
    is on the type — so this cannot be a SimpleNamespace."""

    def __init__(self, text, stop_reason):
        self.text, self.stop_reason = text, stop_reason

    def __str__(self):
        return self.text


def result(text, stop_reason="end_turn"):
    return {"result": FakeResult(text, stop_reason)}


# --- the four event types ----------------------------------------------------


def test_the_stream_opens_with_the_question_it_actually_asked(supervisor):
    """The UI echoes this. A prompt silently replaced by the J2001 default is the
    exact bug this makes visible."""
    events = drain(supervisor, [], prompt="who is on the bench in Bengaluru?")
    assert events[0]["type"] == "start"
    assert events[0]["question"] == "who is on the bench in Bengaluru?"


def test_no_prompt_falls_back_to_the_fixed_pipeline(supervisor):
    """What every caller from before the chat UI sends: `{"job_id": "J2001"}`."""
    events = drain(supervisor, [])
    assert events[0]["question"] == supervisor.default_question("J2001")


def test_a_delegation_becomes_one_status_event_with_a_human_label(supervisor):
    events = drain(supervisor, [tool_use("t1", "ask_screening_agent")])
    status = [e for e in events if e["type"] == "status"]
    assert len(status) == 1
    assert status[0]["tool"] == "ask_screening_agent"
    assert status[0]["text"] == supervisor.TOOL_LABELS["ask_screening_agent"]


def test_model_text_streams_as_tokens(supervisor):
    events = drain(supervisor, [{"data": "Hi "}, {"data": "Priya"}])
    assert [e["text"] for e in events if e["type"] == "token"] == ["Hi ", "Priya"]


def test_the_last_event_carries_the_authoritative_answer(supervisor):
    """The UI replaces its streamed buffer with this, so a run that streamed
    intermediate reasoning still ends showing only the final answer."""
    events = drain(supervisor, [{"data": "partial"}, result("the finished note")])
    assert events[-1] == {
        "type": "done",
        "text": "the finished note",
        "stop_reason": "end_turn",
    }


# --- dedupe ------------------------------------------------------------------


def test_one_delegation_announced_once_however_many_chunks_it_takes(supervisor):
    """`current_tool_use` re-yields as the arguments accumulate."""
    chunks = [tool_use("t1", "ask_screening_agent", a) for a in ('{"job', '{"job_id"', '{"job_id":"J2001"}')]
    events = drain(supervisor, chunks)
    assert len([e for e in events if e["type"] == "status"]) == 1


def test_the_same_tool_called_twice_is_announced_twice(supervisor):
    """The retry path is real: ask_outreach_agent returns instructions when it is
    called with no screening text, and the model calls it again. Keying dedupe on
    the tool name would hide the second call and the UI would look stuck."""
    events = drain(
        supervisor,
        [tool_use("t1", "ask_outreach_agent"), tool_use("t2", "ask_outreach_agent")],
    )
    assert len([e for e in events if e["type"] == "status"]) == 2


# --- failure -----------------------------------------------------------------


def test_a_crash_mid_stream_becomes_an_error_event(supervisor):
    """Otherwise the SSE connection just stops, and in the browser a dead stream
    is indistinguishable from an agent that is still thinking."""

    class Exploding:
        async def stream_async(self, question, **kwargs):
            yield {"data": "starting"}
            raise RuntimeError("screening agent unreachable")

    supervisor._session_manager = lambda session_id: None
    supervisor.build_supervisor = lambda session_id, manager=None: Exploding()

    async def go():
        return [e async for e in supervisor.stream_pipeline("J2001")]

    events = asyncio.run(go())
    assert events[-1]["type"] == "error"
    assert "screening agent unreachable" in events[-1]["text"]


def test_hitting_the_turn_cap_says_so_in_the_answer(supervisor):
    """A truncated loop and a finished thought must not look the same."""
    events = drain(supervisor, [result("half an answer", stop_reason="limit_turns")])
    assert "turn cap" in events[-1]["text"]
    assert events[-1]["stop_reason"] == "limit_turns"
