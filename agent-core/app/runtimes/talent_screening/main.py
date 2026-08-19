"""Talent Screening Agent — A2A, and the only agent that holds tools.

Locally it scores in-process against `hr_data.py`. Deployed, the same prompt gets
its tools from two MCP connections — `hr_skills_mcp` directly and `hr-gateway`
for the Lambda over S3 — and never learns the difference.

    uv run app/runtimes/talent_screening/main.py     # http://127.0.0.1:9001
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from a2a.types import AgentSkill
from strands import Agent, tool

from _shared import EMPLOYEES, ToolBudget, a2a_serve, get_employee, get_job, install, make_model, match
from _shared import model_banner, rank_candidates, settings
from clients.tools import screening_toolset

install()

LOCAL_PORT = 9001


# ---------------------------------------------------------------------------
# The local tools. Deployed, these are replaced by hrskills___* and hrdata___*
# over MCP — same capabilities, same output shape, different plumbing.
# ---------------------------------------------------------------------------


@tool
def rank_for_requisition(job_id: str, limit: int = 3) -> str:
    """Rank the best available candidates for an open requisition.

    Use this for any question about who could fill a role, who is the best fit,
    or who should be shortlisted.

    Args:
        job_id: The requisition id, e.g. "J2001"
        limit: How many candidates to return. Default 3.
    """
    # Prints in *this* terminal, not the caller's — watch the delegation land.
    # flush=True because stdout is block-buffered when piped, and a delayed log
    # is a missing log. Deployed, this line is a CloudWatch log event instead.
    print(f"  [screening] rank_for_requisition({job_id!r}, limit={limit})", flush=True)

    job = get_job(job_id)
    if job is None:
        return f"No requisition {job_id!r} exists."

    ranked = rank_candidates(job_id, available_only=True, limit=limit)
    if not ranked:
        return f"Nobody on the bench scores against {job_id}."

    lines = [f"{job['job_id']} {job['title']} in {job['location']}, {job['min_experience_years']}+ yrs:"]
    for result in ranked:
        blockers = ", ".join(result["blockers"]) or "none"
        # Include the matched skills, not just the score. Whoever consumes this
        # downstream — a human, or the outreach agent two hops away — needs a
        # concrete strength to cite. Give them facts or they will invent one.
        strengths = ", ".join(
            f"{m['skill']} L{m['actual']}" for m in result["matched_skills"][:3]
        ) or "none on record"
        lines.append(
            f"  {result['employee_id']} {result['name']} — {result['score']}% "
            f"{result['verdict']}, strengths: {strengths}, blockers: {blockers}"
        )
    return "\n".join(lines)


@tool
def score_one(employee_id: str, job_id: str) -> str:
    """Score one named employee against one requisition, with their gaps.

    Use this when a specific person has already been named.

    Args:
        employee_id: e.g. "E1002"
        job_id: e.g. "J2001"
    """
    print(f"  [screening] score_one({employee_id!r}, {job_id!r})", flush=True)

    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return f"No employee {employee_id!r} or requisition {job_id!r}."

    result = match(employee, job)
    gaps = ", ".join(f"{g['skill']} {g['actual']}/{g['required']}" for g in result["gaps"]) or "none"
    return (
        f"{result['name']} ({employee_id}) vs {result['title']} ({job_id}): "
        f"{result['score']}% {result['verdict']}. Gaps: {gaps}. "
        f"Blockers: {', '.join(result['blockers']) or 'none'}. "
        f"Meets experience: {result['meets_experience']}. Location: {employee['location']}."
    )


@tool
def list_bench(location: str = "") -> str:
    """Everyone currently unallocated, optionally in one location.

    Use this for availability questions — "who is on the bench", "who is free in
    Bengaluru" — where no requisition has been named.

    Args:
        location: optional city, e.g. "Bengaluru". Empty means everywhere.
    """
    print(f"  [screening] list_bench({location!r})", flush=True)

    people = [e for e in EMPLOYEES if e["availability"] == "bench"]
    if location.strip():
        people = [e for e in people if e["location"].lower() == location.strip().lower()]

    if not people:
        where = f" in {location}" if location.strip() else ""
        return f"Nobody is on the bench{where}."

    lines = [f"{len(people)} on the bench{f' in {location}' if location.strip() else ''}:"]
    lines += [
        f"{e['employee_id']} {e['name']} — {e['designation']}, {e['location']}, "
        f"bench since {e.get('bench_since') or 'unknown'}"
        for e in people
    ]
    return "\n".join(lines)


# The deployed screener gets `hrdata___list_bench` from the Gateway, over the same
# S3 records. This is the local half of that pair, and it exists because the chat
# UI lets people ask availability questions — without it the agent is told about a
# bench tool it does not have, and answers from nothing.
#
# Keep the two in step: app/lambda_fn/handler.py:list_bench is the other one.
LOCAL_TOOLS = [rank_for_requisition, score_one, list_bench]

SYSTEM_PROMPT = (
    "You are the talent screening desk. Answer questions about who fits an open "
    "requisition.\n\n"
    "Call the ranking tool ONCE for 'who could fill X', or the scoring tool ONCE when "
    "a person is already named. One tool call is enough — do not call it again to "
    "check your work.\n\n"
    "For a question about AVAILABILITY rather than a requisition — 'who is on the "
    "bench', 'who is free in Bengaluru' — call the bench tool instead, and list who "
    "it returns. There is no requisition in that case: say so plainly rather than "
    "answering about one nobody asked about, and never invent one to fill the gap.\n\n"
    "Start your answer with the tool's FIRST line, the one naming the requisition: "
    "its id, title and location. Whoever reads your answer may have no other way to "
    "learn which role these candidates are for, and a writer with no role name will "
    "invent one.\n\n"
    "Then copy the tool's line for each candidate straight into your answer: id, "
    "name, score, verdict, strengths AND blockers, as plain text lines. Do not drop "
    "the strengths — whoever reads your answer may have no other source of facts "
    "about this person.\n\n"
    "Use the tool's verdict word EXACTLY as given — 'strong', 'possible', 'weak' or "
    "'blocked'. Do not reword it: 'blocked' does not mean 'consider with caution', "
    "it means the candidate cannot be shortlisted at all. Never invent a score, a "
    "skill level or an employee id.\n\n"
    "State blockers explicitly: a candidate missing a mandatory skill cannot be "
    "shortlisted however high the score. Then stop."
)

DESCRIPTION = (
    "Scores and ranks internal candidates against open requisitions using "
    "the company's own matching engine."
)

# A declared skill is how another agent finds you. Strands can infer one, but
# writing it gives the description and examples a remote model matches against.
SCREENING_SKILL = AgentSkill(
    id="candidate_screening",
    name="Candidate screening and ranking",
    description=(
        "Score internal employees against an open requisition and rank the best "
        "available candidates. Returns match percentages, verdicts, skill gaps and "
        "blocking mandatory skills. Use for any question about who fits a role."
    ),
    tags=["recruiting", "screening", "matching", "hr"],
    examples=[
        "Who are the top candidates for J2001?",
        "Score E1002 against J2001.",
    ],
)


def make_factory(tools: list):
    """Build the per-context agent factory over a fixed tool list.

    A2A calls this once per *context* — one caller's ongoing conversation — so
    two hiring pipelines never share message history.
    """

    def build_screener(context_id: str) -> Agent:
        return Agent(
            # Not cosmetic: both end up on the Agent Card, which is what a calling
            # agent reads to decide this is the right service to ask.
            name="Talent Screening Agent",
            description=DESCRIPTION,
            model=make_model(),
            tools=tools,
            # There is no call site to pass limits={"turns": n} to — the A2A
            # server invokes the agent for us. A looping service just looks like
            # a request that never returns.
            hooks=[ToolBudget(max_calls=3)],
            system_prompt=SYSTEM_PROMPT,
            callback_handler=None,
        )

    return build_screener


if __name__ == "__main__":
    print(f"data_source={settings.data_source} {model_banner()}")
    with screening_toolset(LOCAL_TOOLS) as tools:
        a2a_serve.serve(
            make_factory(tools),
            name="Talent Screening Agent",
            description=DESCRIPTION,
            skills=[SCREENING_SKILL],
            local_port=LOCAL_PORT,
        )
