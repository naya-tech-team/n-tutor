"""The HR skills server, spoken over MCP.

This file has no idea Strands exists. That is the entire point of MCP: the team
that owns employee data publishes tools once, and every agent framework —
Strands, Claude Desktop, an IDE — consumes the same three tools.

Run it directly to smoke-test it:

    uv run app/02_mcp/hr_mcp_server.py     # waits on stdin, speaking JSON-RPC

Normally you do not run it yourself — `main.py` launches it as a subprocess.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import FastMCP
from fastmcp.prompts import Message
from fastmcp.utilities.logging import configure_logging

from _shared import employees_with_skill, get_employee, get_job, match, rank_candidates

mcp = FastMCP("hr-skills")


@mcp.tool(description="Find employees who have a skill at or above a given level.")
def find_by_skill(skill: str, min_level: int = 3, available_only: bool = True) -> list[dict]:
    """Args: skill (name or alias, e.g. "pyspark"), min_level 1-5, available_only."""
    return [
        {
            "employee_id": e["employee_id"],
            "name": e["name"],
            "designation": e["designation"],
            "location": e["location"],
            "availability": e["availability"],
        }
        for e in employees_with_skill(skill, min_level=min_level, available_only=available_only)
    ]


@mcp.tool(description="Get one open job requisition and the skills it requires.")
def get_requisition(job_id: str) -> dict:
    """Args: job_id, e.g. "J2001"."""
    job = get_job(job_id)
    return job or {"error": f"no requisition {job_id}"}


@mcp.tool(description="Score one employee against one job and explain the gaps.")
def score_match(employee_id: str, job_id: str) -> dict:
    """Args: employee_id e.g. "E1002", job_id e.g. "J2001"."""
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return {"error": f"unknown employee {employee_id!r} or job {job_id!r}"}
    return match(employee, job)


@mcp.tool(description="Rank the best available candidates for a job.")
def shortlist(job_id: str, limit: int = 3) -> list[dict]:
    """Args: job_id, limit (how many candidates to return)."""
    return rank_candidates(job_id, available_only=True, limit=limit)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)  # waits on stdin, speaking JSON-RPC
