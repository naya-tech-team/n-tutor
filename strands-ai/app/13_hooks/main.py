"""13 · Hooks — observe and change the loop without forking the SDK.

Hiring is a regulated, audited process. Hooks are where "every profile read is
logged", "nobody is rejected by a bot" and "salary never reaches the model" live
— none of which belong inside the tools themselves.

Run:  uv run app/13_hooks/main.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, tool
from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookOrder,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

from _shared import get_employee, make_model


@tool
def read_profile(employee_id: str) -> str:
    """Read an employee's profile: skills, availability and current band.

    Args:
        employee_id: e.g. "E1002"
    """
    employee = get_employee(employee_id)
    if employee is None:
        return f"no such employee {employee_id}"
    skills = ", ".join(f"{s['skill']} L{s['level']}" for s in employee["skills"])
    # The band is deliberately in the payload — hook #3 strips it before the
    # model ever sees it. Tools stay simple; policy lives in the hook.
    return f"{employee['name']} ({employee['designation']}), band B4, {employee['availability']}. Skills: {skills}"


@tool
def reject_candidate(employee_id: str, reason: str) -> str:
    """Permanently reject a candidate for the open requisition.

    Args:
        employee_id: e.g. "E1003"
        reason: Why they were rejected
    """
    return f"Rejected {employee_id}: {reason}"


# --------------------------------------------------------------------------
# 1) OBSERVE — a plain function. The type hint tells Strands which event it wants.
#    This is your compliance log: who was looked at, with what arguments.
# --------------------------------------------------------------------------
def audit_tool_calls(event: BeforeToolCallEvent) -> None:
    print(f"  [audit] {event.tool_use['name']} {event.tool_use['input']}")


# --------------------------------------------------------------------------
# 2) BLOCK — set cancel_tool and the tool never runs; your text goes to the model.
#    A model may recommend a rejection. It may not execute one.
# --------------------------------------------------------------------------
def block_adverse_actions(event: BeforeToolCallEvent) -> None:
    if event.tool_use["name"].startswith("reject_"):
        event.cancel_tool = (
            "Rejections require a human recruiter. Recommend the decision and explain why, "
            "but state clearly that it has not been actioned."
        )


# --------------------------------------------------------------------------
# 3) REWRITE — mutate the result the model will see. Compensation data leaves
#    the tool result before it can leak into a candidate-facing sentence.
# --------------------------------------------------------------------------
def redact_compensation(event: AfterToolCallEvent) -> None:
    for block in event.result.get("content", []):
        if "text" in block and "band B4" in block["text"]:
            before = block["text"]
            block["text"] = before.replace("band B4", "band [REDACTED]")
            print("  [redact] compensation band stripped from tool result")


# --------------------------------------------------------------------------
# 4) A provider bundles related callbacks and can hold state between them.
# --------------------------------------------------------------------------
class Telemetry(HookProvider):
    def __init__(self) -> None:
        self.t0 = 0.0
        self.tool_calls = 0
        self.messages = 0

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(BeforeInvocationEvent, self._start)
        registry.add_callback(BeforeToolCallEvent, self._count_tool)
        registry.add_callback(MessageAddedEvent, self._count_message)
        # order: lower runs first. Put the summary last so other After hooks finish.
        registry.add_callback(AfterInvocationEvent, self._finish, order=HookOrder.SDK_LAST)

    def _start(self, event: BeforeInvocationEvent) -> None:
        self.t0 = time.perf_counter()
        self.tool_calls = self.messages = 0

    def _count_tool(self, event: BeforeToolCallEvent) -> None:
        self.tool_calls += 1

    def _count_message(self, event: MessageAddedEvent) -> None:
        self.messages += 1

    def _finish(self, event: AfterInvocationEvent) -> None:
        print(
            f"  [telemetry] {time.perf_counter() - self.t0:.2f}s "
            f"tools={self.tool_calls} messages={self.messages}"
        )


def main() -> None:
    telemetry = Telemetry()
    agent = Agent(
        model=make_model(),
        system_prompt="You are a screening assistant. Use tools for every fact about a person.",
        tools=[read_profile, reject_candidate],
        hooks=[audit_tool_calls, block_adverse_actions, redact_compensation, telemetry],
        callback_handler=None,
    )

    print("=== 1. A normal profile read (audited + redacted) ===")
    print("  ->", str(agent("What skills does E1002 have, and what band are they on?")).strip(), "\n")

    print("=== 2. A blocked adverse action ===")
    print("  ->", str(agent("Reject E1003 — their Spark level is too low for J2001.")).strip(), "\n")

    print("=== 3. Registering a hook after construction ===")
    agent.add_hook(lambda event: print(f"  [late] message #{len(event.agent.messages)}"), MessageAddedEvent)
    agent("Say ok.")


if __name__ == "__main__":
    main()
