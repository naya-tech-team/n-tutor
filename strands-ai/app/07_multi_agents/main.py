"""07 · Multi agents — four ways to split hiring work across specialists.

One agent that screens, writes outreach AND polices tone does all three badly.
Three narrow agents, wired four different ways — the fourth of which puts the
review step in someone else's process entirely, over A2A.

Run:  uv run app/07_multi_agents/main.py

Pattern 4 needs the compliance service running in another terminal:
      uv run app/07_multi_agents/a2a_server.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from strands import Agent, tool
from strands.agent import A2AAgent
from strands.multiagent import GraphBuilder, Swarm

from _shared import get_employee, get_job, make_model, match, rank_candidates


@tool
def screen_for_job(job_id: str, limit: int = 3) -> str:
    """Rank the best available candidates for a requisition.

    Args:
        job_id: e.g. "J2001"
        limit: How many candidates to return
    """
    results = rank_candidates(job_id, available_only=True, limit=limit)
    if not results:
        return f"unknown job {job_id!r}"
    return "\n".join(
        f"{r['employee_id']} {r['name']}: {r['score']}% {r['verdict']}, "
        f"blockers={r['blockers'] or 'none'}"
        for r in results
    )


@tool
def candidate_gaps(employee_id: str, job_id: str) -> str:
    """Explain exactly which skills a candidate is missing for a job.

    Args:
        employee_id: e.g. "E1003"
        job_id: e.g. "J2001"
    """
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return "unknown employee or job"
    result = match(employee, job)
    gaps = "; ".join(
        f"{g['skill']}: has L{g['actual']}, needs L{g['required']}{' (mandatory)' if g['mandatory'] else ''}"
        for g in result["gaps"]
    )
    return f"{result['name']} {result['score']}% — {gaps or 'no gaps'}"


def make_specialists() -> tuple[Agent, Agent, Agent]:
    """Three narrow agents. Note that `name` and `description` are load-bearing:
    they are what another agent reads when deciding whom to call."""
    screener = Agent(
        name="skills_screener",
        description="Ranks candidates for a requisition and names their exact skill gaps.",
        system_prompt=(
            "You screen candidates. Call the tools, then report ids, scores and gaps in one "
            "paragraph. Numbers only — never speculate about someone's ability."
        ),
        model=make_model(),
        tools=[screen_for_job, candidate_gaps],
        callback_handler=None,
    )
    recruiter = Agent(
        name="outreach_writer",
        description="Turns a screening result into a short, warm note inviting a candidate to talk.",
        system_prompt=(
            "You write 3-sentence internal outreach notes. Mention the role and one genuine "
            "reason this person fits. No salary, no promises, no hype.\n"
            # The id line is not decoration: it is what lets a reviewer — local or
            # remote — check the note's claims against the HR record instead of
            # taking the writer's word for them.
            "Start every note with the line 'Candidate: <employee_id> · Requisition: <job_id>'."
        ),
        model=make_model(),
        callback_handler=None,
    )
    reviewer = Agent(
        name="fairness_reviewer",
        description="Checks an outreach note for bias, over-promising and unverifiable claims.",
        system_prompt=(
            "You review internal recruiting notes. Reject anything referencing age, gender, "
            "family, or a guarantee about promotion or pay. Reply APPROVED or the exact edits."
        ),
        model=make_model(),
        callback_handler=None,
    )
    return screener, recruiter, reviewer


TASK = "Requisition J2001 is open. Find the best available candidate and produce an outreach note."


# --------------------------------------------------------------------------
# Pattern 1 — Agent as tool. A coordinator "hires" specialists.
#             The coordinator's model decides who to call and when.
# --------------------------------------------------------------------------
def demo_agent_as_tool() -> None:
    print("=== 1. Agent-as-tool (hierarchical) ===")
    screener, recruiter, _ = make_specialists()

    coordinator = Agent(
        model=make_model(),
        system_prompt=(
            "You run resourcing for open requisitions. Use skills_screener to find who fits, "
            "then outreach_writer to draft the note. Return the final note."
        ),
        tools=[screener.as_tool(), recruiter.as_tool()],
        callback_handler=None,
    )
    print(coordinator(TASK), "\n")


# --------------------------------------------------------------------------
# Pattern 2 — Swarm. Peers hand off to each other. No coordinator.
# --------------------------------------------------------------------------
def demo_swarm() -> None:
    print("=== 2. Swarm (peer handoff) ===")
    screener, recruiter, reviewer = make_specialists()

    swarm = Swarm(
        nodes=[screener, recruiter, reviewer],
        entry_point=screener,
        max_handoffs=6,
        max_iterations=6,
        node_timeout=120.0,
        # Break screener->writer->screener ping-pong, which small models fall into constantly.
        repetitive_handoff_detection_window=4,
        repetitive_handoff_min_unique_agents=3,
    )
    result = swarm(TASK)
    print("status:", result.status)
    print("path:", " -> ".join(node.node_id for node in result.node_history))
    print(result, "\n")


# --------------------------------------------------------------------------
# Pattern 3 — Graph. YOU decide the order. The model does not route.
#             Hiring is exactly the case where you want this: the fairness
#             review must run, every time, last.
# --------------------------------------------------------------------------
def demo_graph() -> None:
    print("=== 3. Graph (deterministic DAG) ===")
    screener, recruiter, reviewer = make_specialists()

    builder = GraphBuilder()
    builder.add_node(screener, "screen")
    builder.add_node(recruiter, "write")
    builder.add_node(reviewer, "review")
    builder.add_edge("screen", "write")
    builder.add_edge("write", "review")
    
    # builder.add_edge("screen", "review", condition=lambda result: result.results["screen"] is not None ) # function to add conditional edge based on screening result

    builder.set_entry_point("screen")

    graph = builder.build()
    result = graph(TASK)

    print("status:", result.status, "| nodes completed:", result.completed_nodes)
    print("order:", " -> ".join(node.node_id for node in result.execution_order))
    print("review says:", str(result.results["review"]).strip()[:200])


# --------------------------------------------------------------------------
# Pattern 4 — Graph with a remote node. Same DAG, but `review` is a service
#             People Compliance runs. Patterns 1-3 are all one process; this
#             one crosses an ownership boundary.
# --------------------------------------------------------------------------
COMPLIANCE_URL = "http://127.0.0.1:9007"

def a2a_text(node_result) -> str:
    """Join a remote node's answer back into one string.

    A spec-compliant A2A server streams its reply, and every chunk arrives as its
    own content block — 25 of them for two sentences. `str(result)` would put each
    on its own line. A local Agent node returns one block, so this is the one place
    the wire shows through.
    """
    return "".join(block.get("text", "") for block in node_result.result.message["content"]).strip()


async def demo_graph_a2a() -> None:
    print("=== 4. Graph with an A2A node (remote, another team's service) ===")
    # A2AAgent is a *client*. It implements the same AgentBase protocol a local
    # Agent does, which is why it drops into the graph as an ordinary node — no
    # model of its own, no tool call for the routing model to forget. The edge
    # fires, the HTTP request happens.
    compliance = A2AAgent(
        endpoint=COMPLIANCE_URL,
        name="compliance_reviewer",
        description="People Compliance's review service for recruiting outreach.",
    )

    card = await compliance.get_agent_card()
    if card is None:
        print(f"No agent answering at {COMPLIANCE_URL}. Start it first:")
        print("  uv run app/07_multi_agents/a2a_server.py\n")
        return
    print(f"discovered: {card.name} — skills: {card.skills}")

    screener, recruiter, _ = make_specialists()

    

    builder = GraphBuilder()
    builder.add_node(screener, "screen")
    builder.add_node(recruiter, "write")
    builder.add_node(compliance, "review")     # <- lives in another process
    builder.add_edge("screen", "write")
    builder.add_edge("write", "review")
    builder.set_entry_point("screen")

    graph = builder.build()
    result = graph(TASK)

    print("status:", result.status, "| nodes completed:", result.completed_nodes)
    print("order:", " -> ".join(node.node_id for node in result.execution_order))
    print("screening says:", str(result.results["screen"]).strip())
    print("=====================================================================\n")
    print("outreach says:", str(result.results["write"]).strip())
    print("=====================================================================\n")
    print("compliance says:", a2a_text(result.results["review"]))


def main() -> None:
    # demo_agent_as_tool()
    # demo_swarm()
    print("***************************************************************************\n")
    # demo_graph()
    print("***************************************************************************\n")
    import asyncio
    asyncio.run(demo_graph_a2a())


if __name__ == "__main__":
    main()
