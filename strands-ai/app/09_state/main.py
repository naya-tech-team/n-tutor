"""09 · State — the three places data lives inside an agent.

A recruiting session has facts the model must not re-derive or hallucinate:
which requisition we are filling, who is already shortlisted, and who is asking.
Each of those belongs in a different store.

Run:  uv run app/09_state/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, ToolContext, tool

from _shared import get_employee, get_job, make_model, match, rank_candidates


@tool(context=True)
def open_requisition(job_id: str, tool_context: ToolContext) -> str:
    """Set the requisition this conversation is working on.

    Args:
        job_id: e.g. "J2001"
    """
    job = get_job(job_id)
    if job is None:
        return f"No such requisition {job_id}."
    tool_context.agent.state.set("job_id", job["job_id"])
    return f"Now working {job['job_id']} — {job['title']} in {job['location']}."


@tool(context=True)
def shortlist(employee_id: str, tool_context: ToolContext) -> str:
    """Add a candidate to the shortlist for the open requisition.

    Args:
        employee_id: e.g. "E1002"
    """
    agent = tool_context.agent

    # 1) agent.state — durable, private, never shown to the model unless a tool returns it.
    job_id = agent.state.get("job_id")
    if not job_id:
        return "No requisition is open. Call open_requisition first."

    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None:
        return f"No such employee {employee_id}."

    result = match(employee, job)
    if result["blockers"]:
        return f"Cannot shortlist {result['name']}: missing mandatory {', '.join(result['blockers'])}."

    picked = agent.state.get("shortlist") or []
    picked.append({"employee_id": employee_id, "score": result["score"]})
    agent.state.set("shortlist", picked)

    # 2) invocation_state — per-call context YOU passed into agent(...).
    #    Perfect for the request-scoped things the model must never see or invent.
    recruiter = tool_context.invocation_state.get("recruiter_id", "unknown")
    tenant = tool_context.invocation_state.get("tenant_id", "unknown")

    return (
        f"Shortlisted {result['name']} at {result['score']}% for {job_id} "
        f"(by {recruiter}, tenant {tenant}). {len(picked)} on the list."
    )


@tool
def best_candidates(job_id: str) -> str:
    """Rank the top available candidates for a requisition.

    Args:
        job_id: e.g. "J2001"
    """
    return "\n".join(
        f"{r['employee_id']} {r['name']}: {r['score']}% {r['verdict']}"
        for r in rank_candidates(job_id, limit=3)
    ) or f"no requisition {job_id}"


def main() -> None:
    agent = Agent(
        model=make_model(),
        system_prompt=(
            "You are a resourcing assistant. Open the requisition before shortlisting anyone. "
            "Only shortlist people the tools returned."
        ),
        tools=[open_requisition, shortlist, best_candidates],
        # 3) Seed state at construction — config, policy, the caller's profile.
        state={"business_unit": "Data & Analytics", "max_shortlist": 3},
        callback_handler=None,
    )

    print("=== 1. Seeded state ===")
    print("state at boot:", agent.state.get(), "\n")

    print("=== 2. State written by a tool ===")
    agent("We're filling J2001. Open it.")
    print("job_id ->", agent.state.get("job_id"), "\n")

    print("=== 3. invocation_state: request-scoped, not persisted ===")
    result = agent(
        "Find the best candidate for J2001 and shortlist them.",
        invocation_state={"tenant_id": "acme-prod", "recruiter_id": "R-8812"},
    )
    print(str(result).strip(), "\n")

    print("=== 4. The three stores, side by side ===")
    print("agent.state       :", agent.state.get())
    print("agent.messages    :", len(agent.messages), "messages (this IS what the model sees)")
    print("invocation_state  : gone — it lived for one call only")

    print("\n=== 5. State is a JSON-validated store ===")
    agent.state.set("interview_slots", 2)
    agent.state.delete("max_shortlist")
    print("after set/delete:", agent.state.get())
    try:
        agent.state.set("bad", {"E1002", "E1003"})  # a set is not JSON serializable
    except ValueError as exc:
        print("rejected non-JSON value:", type(exc).__name__)


if __name__ == "__main__":
    main()
