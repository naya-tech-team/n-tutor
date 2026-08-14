"""People Compliance's reviewer, published as an A2A server.

The other three agents in this lesson are objects in your process. This one is a
*service another team runs*. That is the whole distinction:

    Agent-as-tool / Swarm / Graph   one process, one deployment, your code
    A2A                             separate process, separate owner, over HTTP

Compliance is exactly the case for it. The rules about what a recruiter may say
to a candidate belong to the People Compliance team: they change them without
asking you, and every hiring pipeline in the company must get the *same* answer.
A local copy of `fairness_reviewer` is a policy fork waiting to drift.

`A2AServer` turns an ordinary Strands agent into that service — it builds the
Agent Card, mounts the protocol endpoints, and runs the task lifecycle. You never
write an AgentExecutor by hand.

Run it (terminal 1) and leave it running:

    uv run app/07_multi_agents/a2a_server.py

Then look at the business card other agents read to find it:

    curl http://127.0.0.1:9007/.well-known/agent-card.json

With it up, `main.py` runs a fourth pattern: the same hiring graph, with the
review step executed over the wire.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a2a.types import AgentSkill
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer

from _shared import get_employee, get_job, make_model, match

HOST, PORT = "127.0.0.1", 9007


@tool
def verify_match_claim(employee_id: str, job_id: str) -> str:
    """Check what the HR record actually says about a candidate-to-role match.

    Use this whenever an outreach note states a score, a skill level, or claims
    someone is a strong fit — before approving it.

    Args:
        employee_id: e.g. "E1002"
        job_id: e.g. "J2001"
    """
    # Prints in *this* terminal, not the caller's — watch the delegation land.
    # flush=True because stdout is block-buffered whenever the server's output is
    # piped to a file rather than a terminal, and a delayed log is a missing log.
    print(f"  [compliance] verify_match_claim({employee_id!r}, {job_id!r})", flush=True)
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return f"NOT ON RECORD: no employee {employee_id!r} or requisition {job_id!r}."
    result = match(employee, job)
    return (
        f"{result['name']} vs {result['title']}: score {result['score']}%, "
        f"verdict {result['verdict']}, blockers {result['blockers'] or 'none'}, "
        f"meets_experience {result['meets_experience']}."
    )


def build_reviewer(context_id: str) -> Agent:
    """Build a fresh reviewer per A2A conversation.

    `A2AServer` calls this once per *context* — one caller's ongoing conversation —
    so two hiring pipelines reviewing two different notes never share history.
    Passing a single `agent=` instead works, but is not multi-tenant safe.
    """
    return Agent(
        # Not cosmetic: A2AServer copies both onto the Agent Card, which is what
        # a calling agent reads to decide this is the right service to ask.
        name="People Compliance Reviewer",
        description=(
            "Reviews internal recruiting outreach for bias, over-promising and "
            "claims the HR record does not support."
        ),
        model=make_model(),
        tools=[verify_match_claim],
        system_prompt=(
            "You are the company's compliance review for internal recruiting outreach. "
            "You review notes; you never write them.\n\n"
            "Reject any note that:\n"
            "- references age, gender, family status, health, or nationality;\n"
            "- promises pay, promotion, a specific project, or an offer;\n"
            "- states a match score or skill level that verify_match_claim does not confirm.\n\n"
            "The note begins with a line naming an employee id (E####) and a requisition id "
            "(J####). Your FIRST action is always to call verify_match_claim with those two "
            "ids. Only after it returns may you judge the note.\n\n"
            "Reply with exactly 'APPROVED' or 'REJECTED:' followed by the specific edits "
            "required. Two sentences maximum."
        ),
        callback_handler=None,
    )


# A declared skill is how another agent finds you. Strands can infer one, but
# writing it gives the description and examples a remote model matches against.
REVIEW_SKILL = AgentSkill(
    id="outreach_compliance_review",
    name="Recruiting outreach compliance review",
    description=(
        "Review an internal recruiting outreach note for discriminatory language, "
        "over-promising, and unverifiable claims about a candidate's match. Returns "
        "APPROVED or the required edits."
    ),
    tags=["recruiting", "compliance", "fairness", "review"],
    examples=[
        "Review this note to E1002 about J2001: 'Hi Priya, you're a perfect 100% fit...'",
        "Is this outreach note compliant?",
    ],
)


if __name__ == "__main__":
    server = A2AServer(
        agent_factory=build_reviewer,
        host=HOST,
        port=PORT,
        version="1.0.0",
        skills=[REVIEW_SKILL],
        # Off by default today, the default in the next major version — without it
        # the SDK warns on every request that its stream does not match the A2A spec.
        enable_a2a_compliant_streaming=True,
    )
    print(f"People Compliance Reviewer on http://{HOST}:{PORT}")
    print(f"  card: http://{HOST}:{PORT}/.well-known/agent-card.json")
    server.serve()
