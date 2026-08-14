"""15 · Interrupts — pause the loop, ask a human, resume where you stopped.

The rule in hiring: an agent may screen, rank and draft all day, but a human
approves anything that touches a person's record. Two ways to enforce it.

Run:  uv run app/15_interrupts/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, ToolContext, tool
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from _shared import find_employee_by_name, get_employee, get_job, make_model, match

# Tools that change a person's record, rather than just reading it.
RISKY_TOOLS = {"reject_candidate"}


@tool
def find_employee(name: str) -> str:
    """Find an employee id by name.

    Args:
        name: Full or partial name, e.g. "Rahul"
    """
    employee = find_employee_by_name(name)
    return employee["employee_id"] if employee else "not found"


@tool
def reject_candidate(employee_id: str) -> str:
    """Permanently mark a candidate as rejected for the open requisition.

    Args:
        employee_id: e.g. "E1003"
    """
    return f"Candidate {employee_id} marked rejected."


@tool(context=True)
def send_offer(employee_id: str, job_id: str, band_jump: int, tool_context: ToolContext) -> str:
    """Send an internal offer. Jumping more than one band needs HR approval.

    Args:
        employee_id: Who the offer goes to
        job_id: The requisition being filled
        band_jump: How many salary bands this offer moves them up
    """
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return "unknown employee or job"

    result = match(employee, job)
    if result["blockers"]:
        return f"Cannot offer: {result['name']} is missing mandatory {', '.join(result['blockers'])}."

    if band_jump > 1:
        # Ask from INSIDE the tool. The first call raises; on resume it returns
        # the human's answer and execution continues from this same line.
        code = tool_context.interrupt(
            "hr_approval_code",
            reason=(
                f"Offer to {result['name']} for {job['title']} jumps {band_jump} bands "
                f"(match {result['score']}%). Policy allows 1 without HR sign-off."
            ),
        )
        if code != "HR-42":
            return "Offer declined: invalid HR approval code."
    return f"Offer sent to {result['name']} for {job['title']} ({result['score']}% match)."


class ApprovalGate(HookProvider):
    """Approval from OUTSIDE the tool — the tool stays unaware it is gated.

    Use this shape when the policy applies to a whole class of tools and you do
    not want to touch (or cannot touch) each implementation.
    """

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(BeforeToolCallEvent, self.approve)

    def approve(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name not in RISKY_TOOLS:
            return

        answer = event.interrupt(
            f"approve_{name}",
            reason={"tool": name, "input": event.tool_use["input"]},
        )
        if answer != "yes":
            event.cancel_tool = "The recruiter denied this action. Explain that no record was changed."


def answer(agent: Agent, result, responses: dict[str, str]):
    """Resume an interrupted agent by replying to each pending interrupt."""
    payload = [
        {"interruptResponse": {"interruptId": i.id, "response": responses[i.name]}}
        for i in result.interrupts
    ]
    return agent(payload)


def demo_hook_approval() -> None:
    print("=== 1. Approval gate in a hook ===")
    agent = Agent(
        model=make_model(),
        system_prompt="You are a screening assistant. Find the employee id first, then act.",
        tools=[find_employee, reject_candidate],
        hooks=[ApprovalGate()],
        callback_handler=None,
    )

    result = agent("Rahul Menon is short on Spark for J2001. Reject him.")

    while result.stop_reason == "interrupt":
        for i in result.interrupts:
            print(f"  ⏸ {i.name}: {i.reason}")
        # A real app would render this to a recruiter's queue and wait. We deny.
        result = answer(agent, result, {i.name: "no" for i in result.interrupts})

    print("  ->", str(result).strip(), "\n")


def demo_tool_interrupt() -> None:
    print("=== 2. Asking from inside a tool ===")
    agent = Agent(
        model=make_model(),
        system_prompt="You handle internal offers. Use the send_offer tool.",
        tools=[send_offer],
        callback_handler=None,
    )

    result = agent("Send E1002 an offer for J2001. It's a two-band jump.")

    while result.stop_reason == "interrupt":
        for i in result.interrupts:
            print(f"  ⏸ {i.name}: {i.reason}")
        result = answer(agent, result, {i.name: "HR-42" for i in result.interrupts})

    print("  ->", str(result).strip())


def main() -> None:
    demo_hook_approval()
    demo_tool_interrupt()


if __name__ == "__main__":
    main()
