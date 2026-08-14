"""04 · Using tools — who decides when a tool runs, and how it runs.

Run:  uv run app/04_using_tools/main.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, tool
from strands.tools.executors import SequentialToolExecutor

from _shared import EMPLOYEES, get_employee, get_job, make_model, match


@tool
def get_profile(employee_id: str) -> dict:
    """Fetch an employee's skill profile.

    Args:
        employee_id: e.g. "E1002"
    """
    employee = get_employee(employee_id)
    if employee is None:
        # An explicit error result. The model reads this and can correct itself —
        # note it is handed the valid ids, so the recovery is possible, not luck.
        known = ", ".join(e["employee_id"] for e in EMPLOYEES[:6]) + ", ..."
        return {
            "status": "error",
            "content": [{"text": f"Unknown employee {employee_id!r}. Known ids: {known}"}],
        }
    skills = ", ".join(f"{s['skill']} L{s['level']}" for s in employee["skills"])
    return {
        "status": "success",
        "content": [{"text": f"{employee['name']} ({employee['designation']}): {skills}"}],
    }


@tool
async def score_against_job(employee_id: str, job_id: str) -> str:
    """Score an employee against a job requisition.

    Args:
        employee_id: e.g. "E1002"
        job_id: e.g. "J2001"
    """
    await asyncio.sleep(0.5)  # pretend the scoring service is a network hop
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return f"unknown employee {employee_id!r} or job {job_id!r}"
    result = match(employee, job)
    return f"{result['name']} vs {result['title']}: {result['score']}% ({result['verdict']})"


@tool
async def screen_shortlist(job_id: str) -> str:
    """Screen every bench employee against a job, reporting progress as it goes.

    Args:
        job_id: e.g. "J2001"
    """
    # An async *generator* tool: every yield is streamed to the caller,
    # and the LAST yield becomes the tool result the model sees.
    job = get_job(job_id)
    if job is None:
        yield f"unknown job {job_id!r}"
        return

    bench = [e for e in EMPLOYEES if e["availability"] == "bench"]
    best: dict | None = None
    for index, employee in enumerate(bench, start=1):
        result = match(employee, job)
        yield f"screened {index}/{len(bench)}: {result['name']} {result['score']}%"
        if best is None or result["score"] > best["score"]:
            best = result
    yield f"Screened {len(bench)} candidates for {job['title']}. Best: {best['name']} at {best['score']}%."


def demo_direct_call() -> None:
    """Deterministic path: you call the tool, the model is not involved."""
    print("=== 1. Direct tool call (no model in the loop) ===")
    agent = Agent(model=make_model(), tools=[get_profile])

    result = agent.tool.get_profile(employee_id="E1002")
    print("raw ToolResult:", result)

    # By default the call IS recorded in agent.messages, so the model sees it
    # on the next turn. Opt out when the call is plumbing, not conversation.
    agent.tool.get_profile(employee_id="E1008", record_direct_tool_call=False)
    print("messages recorded:", len(agent.messages), "\n")


def demo_model_driven() -> None:
    """Normal path: the model chooses. Note it recovers from the error result."""
    print("=== 2. Model-driven, with error recovery ===")
    agent = Agent(
        model=make_model(),
        system_prompt="You are a resourcing assistant. Use tools; never invent skills or ids.",
        tools=[get_profile, score_against_job],
        callback_handler=None,
    )
    print(agent("What skills does employee E9999 have? If you cannot find them, say which ids exist."), "\n")


def demo_execution_order() -> None:
    """Two independent tool calls in one turn run concurrently by default."""
    print("=== 3. Sequential executor (default is concurrent) ===")
    agent = Agent(
        model=make_model(),
        tools=[get_profile, score_against_job],
        tool_executor=SequentialToolExecutor(),
        callback_handler=None,
    )
    print(agent("Show me E1002's profile and their score against J2001."), "\n")


def demo_limits() -> None:
    """A hard budget on the loop — the safety valve for runaway agents."""
    print("=== 4. Per-invocation limits ===")
    agent = Agent(model=make_model(), tools=[get_profile, score_against_job], callback_handler=None)
    # turns=1 means: one model call plus its tools, then stop. The model never gets
    # to see the tool results, so it cannot produce a final answer.
    result = agent("Get E1005's profile, then score them against J2002.", limits={"turns": 1})
    print("stop_reason:", result.stop_reason)  # limit_turns
    print("messages so far:", len(agent.messages), "(re-invoke to continue)\n")


async def demo_streaming_tool() -> None:
    """Tool progress surfaces as tool_stream events while the tool is still running."""
    print("=== 5. A tool that streams its progress ===")
    agent = Agent(model=make_model(), tools=[screen_shortlist], callback_handler=None)
    async for event in agent.stream_async("Screen the bench for J2001."):
        if "tool_stream_event" in event:
            print("  progress:", event["tool_stream_event"]["data"])


def main() -> None:
    demo_direct_call()
    demo_model_driven()
    demo_execution_order()
    demo_limits()
    asyncio.run(demo_streaming_tool())


if __name__ == "__main__":
    main()
