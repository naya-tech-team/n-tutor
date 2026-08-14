"""06 · Streaming responses — show progress instead of a spinner.

Screening a bench takes seconds. A recruiter watching a blank screen assumes it
hung; a recruiter watching names appear one by one waits happily.

Run:  uv run app/06_streaming/main.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, tool

from _shared import get_employee, get_job, make_model, match, rank_candidates


@tool
def compare_to_job(employee_id: str, job_id: str) -> str:
    """Score one employee against one job.

    Args:
        employee_id: e.g. "E1002"
        job_id: e.g. "J2001"
    """
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return "unknown employee or job"
    result = match(employee, job)
    gaps = ", ".join(f"{g['skill']} {g['actual']}/{g['required']}" for g in result["gaps"]) or "none"
    return f"{result['name']}: {result['score']}% ({result['verdict']}), gaps: {gaps}"


PROMPT = (
    "Compare E1002 and E1005 against requisition J2002 and say in two sentences who to interview first."
)


async def demo_text_only() -> None:
    """The 90% case: print tokens as they arrive."""
    print("=== 1. Token-by-token text ===")
    agent = Agent(model=make_model(), tools=[compare_to_job], callback_handler=None)

    async for event in agent.stream_async(PROMPT):
        if "data" in event:
            print(event["data"], end="", flush=True)
    print("\n")


async def demo_event_taxonomy() -> None:
    """Every lifecycle event, labelled. Run this once and read the output."""
    print("=== 2. The full event stream ===")
    agent = Agent(model=make_model(), tools=[compare_to_job], callback_handler=None)

    async for event in agent.stream_async(PROMPT):
        if "init_event_loop" in event:
            print("[loop] starting")
        elif "start_event_loop" in event:
            print("[loop] new cycle")
        elif "data" in event:
            print(f"[text] {event['data']!r}")
        elif "current_tool_use" in event and event["current_tool_use"].get("name"):
            print(f"[tool] building call -> {event['current_tool_use']['name']}")
        elif "tool_stream_event" in event:
            print(f"[tool] progress -> {event['tool_stream_event']['data']}")
        elif "message" in event:
            print(f"[msg ] {event['message']['role']} message committed to history")
        elif "result" in event:
            print(f"[done] stop_reason={event['result'].stop_reason}")
    print()


def demo_callback_handler() -> None:
    """The synchronous alternative: a function called with the same events."""
    print("=== 3. callback_handler (sync API, same events) ===")
    seen_tools: set[str] = set()

    def handler(**kwargs) -> None:
        if "data" in kwargs:
            print(kwargs["data"], end="", flush=True)
        elif tool_use := kwargs.get("current_tool_use"):
            if tool_use.get("name") and tool_use["toolUseId"] not in seen_tools:
                seen_tools.add(tool_use["toolUseId"])
                print(f"\n<scoring: {tool_use['name']}>")

    agent = Agent(model=make_model(), tools=[compare_to_job], callback_handler=handler)
    agent(PROMPT)
    print("\n")


async def demo_web_shape() -> None:
    """What you would actually put behind an HTTP endpoint (SSE / WebSocket).

    This is the shape of the recruiter-facing "screening" screen: text streams
    into the panel, tool events drive a progress row.
    """
    print("=== 4. Server-sent-events shape ===")
    agent = Agent(model=make_model(), tools=[compare_to_job], callback_handler=None)

    async def sse_chunks(prompt: str):
        async for event in agent.stream_async(prompt):
            if "data" in event:
                yield f"data: {event['data']}\n\n"
            elif "current_tool_use" in event and event["current_tool_use"].get("name"):
                yield f"event: tool\ndata: {event['current_tool_use']['name']}\n\n"
            elif "result" in event:
                yield "event: done\ndata: {}\n\n"

    async for chunk in sse_chunks("Score E1006 against J2003 in one sentence."):
        print(repr(chunk))


def demo_no_model_needed() -> None:
    """A reminder: streaming is a *presentation* choice, not an intelligence one.

    The ranking below is deterministic Python. Stream the model's *explanation*
    of a shortlist, never the shortlist itself.
    """
    print("\n=== 5. What NOT to stream from a model ===")
    for result in rank_candidates("J2002", limit=3):
        print(f"  {result['name']:<22} {result['score']:>3}%  {result['verdict']}")


async def main() -> None:
    await demo_text_only()
    await demo_event_taxonomy()
    demo_callback_handler()
    await demo_web_shape()
    demo_no_model_needed()


if __name__ == "__main__":
    asyncio.run(main())
