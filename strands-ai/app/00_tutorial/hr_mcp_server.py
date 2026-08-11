"""The HR department's MCP server — the other side of step 11.

An MCP server publishes three different things, and the difference matters:

    TOOLS      actions the model chooses to take, with arguments  (find_by_skill)
    RESOURCES  data the *client* reads by URI, no model involved  (hr://employees)
    PROMPTS    reusable instructions the server authors, not you  (screen_candidate)

Tools are the famous one, but the other two are why MCP is more than a remote
function call. A resource costs no tokens until you decide to spend them; a
prompt lets the team that owns the data also own the wording of the question —
"how do we screen a candidate" is HR's policy, not the agent author's.

This file imports nothing from Strands. Any MCP client — the tutorial, Claude
Desktop, an IDE — gets the same tools, the same data, and the same scores.

**Which FastMCP is this?** Two packages carry the name and both work:

    from mcp.server.fastmcp import FastMCP     # bundled with the `mcp` SDK
    from fastmcp import FastMCP                # the standalone project — used here

They speak the same protocol, so the client never notices. The standalone
`fastmcp` is the one that moves fastest; this file uses it and flags the three
places where its API differs from the bundled copy. Both are in this venv, which
makes a side-by-side comparison easy — swap the imports and the three spots
marked "fastmcp:", and every client keeps working unchanged.

Smoke-test it by hand (it should sit there waiting on stdin, speaking JSON-RPC):

    uv run app/00_tutorial/hr_mcp_server.py

Normally you never run it yourself — `tutorial.py 11` launches it as a subprocess.

Related: app/02_mcp/ is the same idea trimmed to tools only, plus transports,
lifecycle and the failure modes worth knowing.
"""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # put app/ on sys.path

from fastmcp import FastMCP
from fastmcp.prompts import Message
from fastmcp.utilities.logging import configure_logging

from _shared import (
    EMPLOYEES,
    JOBS,
    SKILLS,
    employees_with_skill,
    get_employee,
    get_job,
    match,
    rank_candidates,
    rank_jobs_for_employee,
    summarize_match,
)

# fastmcp: no `log_level=` on the constructor — logging is a package-level setting,
# configured at import from FASTMCP_LOG_LEVEL. A stdio server's stderr lands in the
# client's console, so a chatty server buries the client's own output.
configure_logging(level="WARNING")

# `instructions` is the server telling clients what it is for, in one line. Claude
# Desktop shows it; an agent can put it in a system prompt.
mcp = FastMCP(
    "hr-directory",
    instructions=(
        "Employee skills, open requisitions, and the company's one scoring rule for "
        "matching people to roles. Every score here is arithmetic, not a judgement."
    ),
)


# ==========================================================================
# Resources — addressable data. The client reads these; the model does not
# have to ask for them, and reading one costs no model call at all.
#
# A URI with a {placeholder} is a *template*: it advertises a shape
# ("hr://employees/{employee_id}") rather than one fixed document.
# ==========================================================================


@mcp.resource(
    "hr://skills",
    name="skill-catalog",
    description="The controlled skill vocabulary, with the aliases that resolve to each skill.",
    mime_type="text/plain",
)
def skill_catalog() -> str:
    """Why a resource and not a tool: it never changes per question."""
    return "\n".join(
        f"{s['skill']} ({s['category']})" + (f" — also written: {', '.join(s['aliases'])}" if s["aliases"] else "")
        for s in SKILLS
    )


@mcp.resource(
    "hr://employees",
    name="employee-directory",
    description="Every employee: id, designation, location, availability, years of experience.",
    mime_type="text/plain",
)
def employee_directory() -> str:
    return "\n".join(
        f"{e['employee_id']}  {e['name']:<22} {e['designation']:<32} "
        f"{e['location']:<12} {e['availability']:<10} {e['experience_years']}y"
        for e in EMPLOYEES
    )


@mcp.resource(
    "hr://employees/{employee_id}",
    name="employee-profile",
    description="One employee's full profile including rated skills. e.g. hr://employees/E1002",
    mime_type="application/json",
)
def employee_profile(employee_id: str) -> dict[str, Any]:
    """Returning a dict is fine — FastMCP serialises it to JSON for the wire."""
    employee = get_employee(employee_id)
    return employee or {"error": f"no employee {employee_id!r}"}


@mcp.resource(
    "hr://jobs",
    name="open-requisitions",
    description="Every open requisition: id, title, location, openings, minimum experience.",
    mime_type="text/plain",
)
def open_requisitions() -> str:
    return "\n".join(
        f"{j['job_id']}  {j['title']:<28} {j['location']:<12} "
        f"{j['openings']} opening(s), {j['min_experience_years']}y+ — {j['description']}"
        for j in JOBS
        if j["status"] == "open"
    )


@mcp.resource(
    "hr://jobs/{job_id}",
    name="requisition",
    description="One requisition with its required skills, minimum levels and weights. e.g. hr://jobs/J2001",
    mime_type="application/json",
)
def requisition(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    return job or {"error": f"no requisition {job_id!r}"}


@mcp.resource(
    "hr://bench",
    name="bench-report",
    description="Who is unallocated right now, and since when.",
    mime_type="text/plain",
)
def bench_report() -> str:
    bench = [e for e in EMPLOYEES if e["availability"] == "bench"]
    return f"{len(bench)} on the bench:\n" + "\n".join(
        f"{e['employee_id']}  {e['name']:<22} since {e['bench_since']}  ({e['designation']})" for e in bench
    )


# ==========================================================================
# Tools — the model picks these, so the description is the whole interface.
# Same four facts every client gets, computed by the same code: the point of
# putting `match()` behind a server is that nobody can score E1010 differently.
# ==========================================================================


@mcp.tool(description="Find employees who have a skill at or above a given proficiency level.")
def find_by_skill(skill: str, min_level: int = 3, available_only: bool = True) -> list[dict[str, Any]]:
    """Args: skill (name or alias, e.g. "pyspark"), min_level 1-5, available_only."""
    return [
        {
            "employee_id": e["employee_id"],
            "name": e["name"],
            "designation": e["designation"],
            "location": e["location"],
            "availability": e["availability"],
            "experience_years": e["experience_years"],
        }
        for e in employees_with_skill(skill, min_level=min_level, available_only=available_only)
    ]


@mcp.tool(description="Score one employee against one requisition and explain the matches, gaps and blockers.")
def score_match(employee_id: str, job_id: str) -> dict[str, Any]:
    """Args: employee_id e.g. "E1002", job_id e.g. "J2001"."""
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return {"error": f"unknown employee {employee_id!r} or requisition {job_id!r}"}
    return match(employee, job)


@mcp.tool(description="Rank the best available candidates for one requisition, best score first.")
def rank_for_job(job_id: str, limit: int = 3, available_only: bool = True) -> list[dict[str, Any]]:
    """Args: job_id e.g. "J2001", limit, available_only (bench only when true)."""
    return rank_candidates(job_id, available_only=available_only, limit=limit)


@mcp.tool(description="The mirror image: which open requisitions suit one employee best?")
def rank_jobs_for_person(employee_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """Args: employee_id e.g. "E1006", limit."""
    return rank_jobs_for_employee(employee_id, limit=limit)


# ==========================================================================
# Prompts — HR authors the question, the client's model answers it.
#
# Each one is rendered *with the data already in it*: the server did the
# lookups, so the model cannot get the score wrong before it starts writing.
# ==========================================================================


@mcp.prompt(description="Screen one candidate against one requisition, using the server's own score.")
def screen_candidate(employee_id: str, job_id: str) -> str:
    """Args: employee_id e.g. "E1010", job_id e.g. "J2003"."""
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return f"Reply exactly: 'No such employee {employee_id!r} or requisition {job_id!r}.'"

    result = match(employee, job)
    return (
        f"Write a screening note for {result['name']} ({employee_id}) against "
        f"{result['title']} ({job_id}).\n\n"
        f"The matching engine has already decided this — do not recompute it:\n"
        f"{json.dumps(result, indent=2)}\n\n"
        "Cover, in this order and in three sentences total:\n"
        "1. the score and verdict, in plain language;\n"
        "2. the strongest matched skill and the most damaging gap;\n"
        "3. a recommendation — interview, develop, or decline.\n\n"
        "A blocker is a mandatory skill below its bar; if there is one, say so first "
        "and never recommend an interview. Invent nothing that is not above."
    )


@mcp.prompt(description="Brief a hiring manager on the current shortlist for a requisition.")
def shortlist_brief(job_id: str, limit: str = "3") -> list[Message]:
    """Args: job_id e.g. "J2001", limit (how many candidates to cover).

    Returns a two-message conversation rather than one string: the server can
    pre-load an assistant turn, which is how you steer format without the
    client having to know anything about hiring.

    fastmcp: one `Message(content, role=...)` class here. The bundled copy splits
    it into `base.UserMessage` / `base.AssistantMessage`.
    """
    job = get_job(job_id)
    if job is None:
        return [Message(f"Reply exactly: 'No requisition {job_id!r}.'")]

    ranked = rank_candidates(job_id, available_only=True, limit=int(limit))
    table = "\n".join(summarize_match(r) for r in ranked)
    return [
        Message(
            f"Brief me on hiring for {job['title']} in {job['location']} ({job_id}), "
            f"{job['openings']} opening(s), {job['min_experience_years']}+ years.\n\n"
            f"Ranked bench candidates:\n{table}"
        ),
        Message(
            "I will give you one line per candidate — name, score, and the single thing "
            "that decides them — then one line on whether this requisition can be filled "
            "from the bench at all.",
            role="assistant",
        ),
    ]


@mcp.prompt(description="Turn a near-miss candidate's gaps into a development plan.")
def skill_gap_plan(employee_id: str, job_id: str) -> str:
    """Args: employee_id e.g. "E1006", job_id e.g. "J2001"."""
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return f"Reply exactly: 'No such employee {employee_id!r} or requisition {job_id!r}.'"

    result = match(employee, job)
    if not result["gaps"]:
        return (
            f"{result['name']} has no skill gaps for {job_id} (score {result['score']}%). "
            "Reply in one sentence saying they are ready today."
        )

    gaps = "\n".join(
        f"- {g['skill']}: at level {g['actual']}, needs {g['required']}"
        + (" (MANDATORY — this blocks the move)" if g["mandatory"] else "")
        for g in result["gaps"]
    )
    return (
        f"{result['name']} scores {result['score']}% against {result['title']} ({job_id}).\n"
        f"Gaps:\n{gaps}\n\n"
        "Write a development plan: for each gap, one concrete way to close it and a "
        "realistic timeframe in months. Order mandatory gaps first. End with one sentence "
        "on whether this person is worth developing for this role or a different one. "
        "Use only the gaps listed above."
    )


if __name__ == "__main__":
    # fastmcp: `show_banner=False` — it prints a startup banner by default, which on
    # stdio would be the first thing the client's console sees.
    # mcp.run(transport="stdio", show_banner=False )
    mcp.run(transport="http", host="localhost", port=8000, show_banner=False)
