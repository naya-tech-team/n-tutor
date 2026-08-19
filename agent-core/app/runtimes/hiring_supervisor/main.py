"""Hiring Supervisor — the front door, and the only runtime reachable from outside.

Locally this is a script you run in a third terminal. Deployed it is an HTTP
runtime on 8080/`/invocations`, and that change is the whole point of the file: a
supervisor anything can call needs an identity to check, a bounded turn count and
a session id, none of which a script in terminal 3 ever needed.

It owns no HR data. Every fact in its answer arrived from a delegation.

**The session id is the conversation.** It keys AgentCore Memory, it rides the A2A
hops as `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`, and it is what makes five
runtimes produce one CloudWatch trace instead of five unrelated ones. Run this
from the CLI and it is the requisition id; call it from the chat UI and it is the
chat thread, so follow-up questions remember the answers to earlier ones.

Two payload shapes, because there are two callers:

    {"job_id": "J2001"}                     the fixed pipeline — what `make supervisor` does
    {"prompt": "who is on the bench?"}      free-form, from ui/

Deployed, the entrypoint is an **async generator**, which is what makes the SSE
happen: bedrock_agentcore sees a generator and returns
`StreamingResponse(media_type="text/event-stream")` instead of a JSON body. Each
yielded dict arrives at the browser as one `data: {...}` line. A full run is three
remote delegations and a minute or two, so a single JSON reply would be a browser
staring at a spinner with nothing to show.

    uv run app/runtimes/hiring_supervisor/main.py
    uv run app/runtimes/hiring_supervisor/main.py J2002
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent

from _shared import install, make_model, model_banner, settings
from clients.a2a_call import agent_url, call_agent, describe_agent

install()

DEFAULT_JOB = "J2001"
TURN_CAP = 8

# What the chat UI shows while a delegation is in flight. Keyed by tool name, so
# adding a tool without adding a label degrades to showing the tool name rather
# than showing nothing.
TOOL_LABELS = {
    "ask_screening_agent": "Screening Agent — ranking candidates",
    "ask_outreach_agent": "Outreach Agent — drafting the note",
    "ask_compliance_reviewer": "Compliance Reviewer — checking it against the record",
}

# Two paths, and the second one is why this is not simply "do all three steps".
#
# The chat UI lets anyone ask anything, and a pipeline that always runs to the end
# answers "who is on the bench in Bengaluru?" by sending a bench list to an agent
# whose job is to write a courtship note. That agent has no HR access at all, so
# it does the only thing it can and invents a candidate — observed: a fabricated
# "Jane Doe, score 8.5" for a dataset whose scores are integers.
#
# Steps 2 and 3 are therefore conditional on someone having asked for a note.
SYSTEM_PROMPT = (
    "You answer hiring questions by delegating to three remote specialists. You "
    "have no HR data of your own: every fact in your answer must have come back "
    "from a tool, in this conversation.\n\n"
    "If you are asked to FILL a requisition or to DRAFT or SEND a note, do all "
    "three steps, in this order, without stopping to ask permission:\n"
    "1. ask_screening_agent — who the candidates are, and their scores.\n"
    "2. ask_outreach_agent — pass the screening answer VERBATIM as one string. Do "
    "not summarise it, retype the numbers, or drop the requisition line.\n"
    "3. ask_compliance_reviewer — pass the drafted note, plus the employee id and "
    "requisition id it is about. A note that has not been reviewed is not finished.\n"
    "Then answer with the note itself and the reviewer's verdict.\n\n"
    "For ANY OTHER question — who is available, what a requisition requires, how "
    "someone scores — call ask_screening_agent ONCE, passing the user's question "
    "verbatim as `question`. Report what it said and stop. Do not draft a note "
    "nobody asked for, and do not turn their question into a different one.\n\n"
    "Never invent a person, a score or a requisition. If screening did not name "
    "someone, they do not exist; say what you were told and no more. Never ask "
    "the user whether to proceed — you were already asked."
)


def _screening_url() -> str:
    return agent_url(settings.screening_url, settings.screening_arn)


def _outreach_url() -> str:
    return agent_url(settings.outreach_url, settings.outreach_arn)


def _compliance_url() -> str:
    return agent_url(settings.compliance_url, settings.compliance_arn)


# ---------------------------------------------------------------------------
# A remote agent becomes a tool. The docstrings below ARE the routing logic —
# nothing in this file sequences the calls.
# ---------------------------------------------------------------------------


def make_tools(session_id: str) -> list:
    """Build the delegation tools bound to one requisition's session."""

    @tool
    async def ask_screening_agent(job_id: str = "", question: str = "") -> str:
        """Ask the remote Screening Agent about candidates, scores or availability.

        Use this FIRST for any hiring question — you must know who the candidates are,
        and their scores, before you can ask for an outreach note.

        Pass `job_id` to rank candidates for a requisition. For anything else — who is
        on the bench, who is free in a city, how one person scores — pass the user's
        own words as `question` instead.

        Args:
            job_id: The requisition id, e.g. "J2001", when ranking for a role.
            question: The user's question, verbatim, for anything that is not that.
        """
        # The default is the tuned phrasing the three-step pipeline relies on, and
        # `question` overrides it rather than reshaping it.
        #
        # This used to be the *only* phrasing: the tool took a job_id and threw the
        # user's words away. That is invisible while every question is "fill J2001",
        # and wrong the moment one is not — "who is on the bench in Bengaluru?" went
        # to the screener as a requisition query, came back "no candidates", and was
        # reported as "no one is available" about a bench with two people on it.
        text = question.strip() or f"Who are the top 2 available candidates for {job_id}?"

        print(f"\n  → delegating to Screening Agent: {text}", flush=True)
        return await call_agent(_screening_url(), text, session_id=session_id)

    @tool
    async def ask_outreach_agent(screening_facts: str = "") -> str:
        """Ask the remote Outreach Agent to draft a note to a candidate.

        Pass the WHOLE reply from ask_screening_agent as one string in
        `screening_facts` — names, ids, scores and blockers together. This agent has
        no HR access and cannot look any of that up itself.

        Args:
            screening_facts: The full screening output to write from, verbatim.
        """
        # Optional on purpose. A small model reaches for the shape it is thinking
        # in — candidate_name=..., score=... — and a *required* parameter turns
        # that into a bare validation error it cannot recover from. Answering the
        # call it actually made, with instructions, ends the loop instead of
        # starting one.
        if not screening_facts.strip():
            return (
                "ask_outreach_agent needs the screening text. Call it again as "
                'ask_outreach_agent(screening_facts="<the entire reply you got from '
                'ask_screening_agent, copied verbatim>") — one string, not separate '
                "fields, and do not summarise or retype the numbers."
            )

        print("\n  → delegating to Outreach Agent", flush=True)
        return await call_agent(
            _outreach_url(),
            f"Draft an outreach note to the strongest candidate, using only these "
            f"screening facts:\n{screening_facts}",
            session_id=session_id,
        )

    @tool
    async def ask_compliance_reviewer(note: str = "", employee_id: str = "", job_id: str = "") -> str:
        """Have the remote Compliance Reviewer check a drafted note before you return it.

        Use this LAST, once you have a note. It re-derives the match from the HR
        record and rejects anything the record does not support.

        Args:
            note: The drafted outreach note, verbatim.
            employee_id: Who the note is addressed to, e.g. "E1002"
            job_id: The requisition, e.g. "J2001"
        """
        if not note.strip():
            return (
                "ask_compliance_reviewer needs the drafted note. Call it again as "
                'ask_compliance_reviewer(note="<the note>", employee_id="E1002", '
                'job_id="J2001").'
            )
        print("\n  → delegating to Compliance Reviewer", flush=True)
        return await call_agent(
            _compliance_url(),
            f"Review this note. {employee_id} / {job_id}.\n{note}",
            session_id=session_id,
        )

    return [ask_screening_agent, ask_outreach_agent, ask_compliance_reviewer]


def trace(event: BeforeToolCallEvent) -> None:
    """Locally this is a print. Deployed, ADOT turns the same tool calls into spans."""
    print(f"\nTool: {event.tool_use['name']}({event.tool_use['input']})", flush=True)


def _session_manager(session_id: str):
    """AgentCore Memory, keyed the way the business is keyed.

    actor = the recruiter, session = the requisition. Returns None when no memory
    resource is configured, which is every local run.
    """
    if not settings.memory_id:
        return None

    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
        RetrievalConfig,
    )
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        AgentCoreMemorySessionManager,
    )

    config = AgentCoreMemoryConfig(
        memory_id=settings.memory_id,
        actor_id="recruiter-default",
        session_id=session_id,
        batch_size=10,
        retrieval_config={
            f"/requisitions/{session_id}/facts": RetrievalConfig(top_k=5, relevance_score=0.4),
        },
    )
    return AgentCoreMemorySessionManager(config, region_name=settings.aws_region)


def build_supervisor(session_id: str, session_manager=None) -> Agent:
    return Agent(
        model=make_model(),
        tools=make_tools(session_id),
        hooks=[trace],
        system_prompt=SYSTEM_PROMPT,
        session_manager=session_manager,
        callback_handler=None,
    )


def default_question(job_id: str) -> str:
    """What the CLI asks when nobody typed anything. Also the fallback for a
    `{"job_id": ...}` payload with no `prompt`, which is how the pre-chat callers
    keep working unchanged."""
    return f"We need to fill {job_id}. Find the best candidate and draft a note to them."


def _capped(answer: str, stop_reason: str) -> str:
    """Say so when the answer is a truncated loop rather than a finished thought."""
    if stop_reason == "limit_turns":
        return f"[hit the {TURN_CAP}-turn cap — the model was looping]\n{answer}"
    return answer


async def run_pipeline(job_id: str, *, session_id: str | None = None, prompt: str = "") -> str:
    """One question, start to finish, as a single string. Used by the CLI."""
    session_id = session_id or job_id
    manager = _session_manager(session_id)
    supervisor = build_supervisor(session_id, manager)
    question = prompt.strip() or default_question(job_id)
    print(f"\nAsking: {question}")

    # The agent loop recurses once per cycle, so a model that keeps calling tools
    # walks off the stack rather than spinning. This is the call site the A2A
    # agents do not have — which is why they carry a ToolBudget hook instead.
    result = await supervisor.invoke_async(question, limits={"turns": TURN_CAP})

    print(f"\n[stop_reason: {result.stop_reason}]")
    return _capped(str(result).strip(), result.stop_reason)


async def stream_pipeline(
    job_id: str, *, session_id: str | None = None, prompt: str = ""
) -> AsyncIterator[dict]:
    """The same run, as events, for a caller that cannot wait two minutes in silence.

    `stream_async` yields one dict per step of the agent loop. Three keys matter:

        current_tool_use   a delegation is starting — this is the progress feed
        data               model text, token by token
        result             the final AgentResult, once

    Dedupe is on `toolUseId`, not on the tool name. `current_tool_use` is re-yielded
    on every chunk as the tool's arguments accumulate, so keying on the name would
    announce each delegation once — but it would also *hide* a second, legitimate
    call to the same tool, which is exactly what happens when outreach is asked
    again with the corrective message.
    """
    session_id = session_id or job_id
    manager = _session_manager(session_id)
    supervisor = build_supervisor(session_id, manager)
    question = prompt.strip() or default_question(job_id)

    yield {"type": "start", "question": question, "session_id": session_id}

    announced: set[str] = set()
    result = None
    try:
        async for event in supervisor.stream_async(question, limits={"turns": TURN_CAP}):
            tool = event.get("current_tool_use") or {}
            key = tool.get("toolUseId") or tool.get("name")
            if key and key not in announced:
                announced.add(key)
                name = tool.get("name", "")
                yield {"type": "status", "tool": name, "text": TOOL_LABELS.get(name, name)}

            if event.get("data"):
                yield {"type": "token", "text": event["data"]}

            if "result" in event:
                result = event["result"]
    except Exception as exc:  # noqa: BLE001 — the browser gets one line either way
        # Without this the stream just stops, and a dead SSE connection is
        # indistinguishable in the UI from an agent that is still thinking.
        yield {"type": "error", "text": f"{type(exc).__name__}: {exc}"}
        return

    stop_reason = getattr(result, "stop_reason", "unknown")
    yield {
        "type": "done",
        "text": _capped(str(result).strip() if result is not None else "", stop_reason),
        "stop_reason": stop_reason,
    }


# ---------------------------------------------------------------------------
# Local: a script. Deployed: an HTTP service on 8080 /invocations.
# ---------------------------------------------------------------------------


async def cli_main(job_id: str) -> None:
    print("Discovering agents:")
    for url in (_screening_url(), _outreach_url(), _compliance_url()):
        try:
            name, skills = await describe_agent(url)
            print(f"  {name} at {url} — skills: {', '.join(skills)}")
        except Exception as exc:  # noqa: BLE001 — any failure here is the same advice
            print(f"  ✗ nothing at {url} ({type(exc).__name__}). Start it first.")
            return
    print(f"\n{await run_pipeline(job_id)}")


if __name__ == "__main__":
    if settings.agentcore:
        from bedrock_agentcore.runtime import BedrockAgentCoreApp

        # First line in this container's log group, and the one that answers "did
        # my BEDROCK_MODEL_ID change actually reach the runtime?" without a
        # redeploy to find out.
        print(f"data_source={settings.data_source} {model_banner()}", flush=True)

        app = BedrockAgentCoreApp()

        @app.entrypoint
        async def invoke(payload, context):
            """One question per call, answered as a stream.

            `async def` + `yield` is the whole trick: bedrock_agentcore inspects the
            entrypoint, sees an async generator, and returns text/event-stream. Make
            this a plain `def` returning a dict and the UI still works — it just
            shows nothing at all until the last delegation finishes.

            AgentCore hands us the session id it was invoked with. From the CLI that
            is the requisition; from the chat UI it is the thread, which is why a
            follow-up question can refer to the answer before it.
            """
            payload = payload or {}
            job_id = payload.get("job_id") or DEFAULT_JOB
            session_id = getattr(context, "session_id", None) or job_id

            async for event in stream_pipeline(
                job_id, session_id=session_id, prompt=payload.get("prompt") or ""
            ):
                yield event

        app.run(port=8080)
    else:
        asyncio.run(cli_main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_JOB))
