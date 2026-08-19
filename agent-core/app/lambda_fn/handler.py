"""`hr-data-fn` — the AWS estate, published as MCP tools by the Gateway.

This function is the reason `hr-gateway` exists. A Lambda cannot speak MCP; the
Gateway translates for it. Anything that *already* speaks MCP — the
`hr_skills_mcp` runtime — is reached directly instead, one hop fewer.

The split of responsibility is enforced by IAM, not by convention:

  this function        reads S3, and is the ONLY thing that writes to it
  hr_skills_mcp        reads the same bucket, read-only, and does the scoring

**A scoring engine that can edit the employee record is a scoring engine nobody
will trust.**

Nothing here scores anything. `record_shortlist` stores a verdict that was
computed elsewhere; it does not compute one.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `_shared/` is vendored beside this file in the deployment zip.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _shared import get_employee, get_job, install, settings  # noqa: E402
from _shared.hr_data import employees_with_skill  # noqa: E402
from _shared.store import append_shortlist, read_shortlist  # noqa: E402

# The Gateway namespaces tools as `{target}___{tool}` so two targets can both
# export a `get_requisition`. Three underscores, not two: a handler that splits
# on `__` gets `_find_by_skill` and matches nothing.
DELIMITER = "___"

install()


# ---------------------------------------------------------------------------
# The tools. Each returns JSON-safe data, or {"error": ...} that says what to do.
# ---------------------------------------------------------------------------


def find_by_skill(skill: str = "", min_level: int = 3, available_only: bool = True) -> dict:
    """Everyone at or above a level in a skill. Accepts aliases."""
    if not skill.strip():
        return {"error": "find_by_skill needs a skill name, e.g. find_by_skill(skill='Python')."}
    people = employees_with_skill(skill, min_level=min_level, available_only=available_only)
    if not people:
        return {
            "skill": skill,
            "employees": [],
            "note": (
                f"nobody is at level {min_level}+ in {skill!r}, or that name is not in the "
                "catalog. Call hrskills___resolve_skill to check the name."
            ),
        }
    return {
        "skill": skill,
        "employees": [
            {
                "employee_id": e["employee_id"],
                "name": e["name"],
                "designation": e["designation"],
                "location": e["location"],
                "availability": e["availability"],
            }
            for e in people
        ],
    }


def get_requisition(job_id: str = "") -> dict:
    """One open role and the skills it requires, with min_level, mandatory and weight."""
    job = get_job(job_id) if job_id.strip() else None
    if job is None:
        return {"error": f"no requisition {job_id!r}. Ids look like J2001."}
    return job


def list_bench(location: str = "") -> dict:
    """Everyone currently unallocated, optionally in one location."""
    from _shared import EMPLOYEES

    people = [e for e in EMPLOYEES if e["availability"] == "bench"]
    if location.strip():
        people = [e for e in people if e["location"].lower() == location.strip().lower()]
    return {
        "count": len(people),
        "employees": [
            {
                "employee_id": e["employee_id"],
                "name": e["name"],
                "designation": e["designation"],
                "location": e["location"],
                "bench_since": e.get("bench_since"),
            }
            for e in people
        ],
    }


def record_shortlist(
    job_id: str = "",
    employee_id: str = "",
    score: int = 0,
    verdict: str = "",
) -> dict:
    """Write one shortlist decision to S3. Refuses blocked candidates."""
    job, employee = get_job(job_id), get_employee(employee_id)
    if job is None or employee is None:
        return {"error": f"unknown requisition {job_id!r} or employee {employee_id!r}."}

    # The blocker survives to the last possible moment. Everything upstream can
    # paraphrase; this cannot, because it is the thing that persists.
    if verdict.strip().lower() == "blocked":
        return {
            "error": (
                f"{employee['name']} is blocked for {job['job_id']} and cannot be "
                "shortlisted. A blocker is a missing mandatory skill, not a low score."
            ),
            "shortlisted": False,
        }

    entry = {
        "employee_id": employee["employee_id"],
        "name": employee["name"],
        "score": score,
        "verdict": verdict,
    }
    current = append_shortlist(job["job_id"], entry)
    return {"shortlisted": True, "job_id": job["job_id"], "shortlist": current}


def get_shortlist(job_id: str = "") -> dict:
    """Who has been shortlisted for a requisition so far."""
    if not job_id.strip():
        return {"error": "get_shortlist needs a job_id, e.g. J2001."}
    return {"job_id": job_id.strip().upper(), "shortlist": read_shortlist(job_id)}


TOOLS = {
    "find_by_skill": find_by_skill,
    "get_requisition": get_requisition,
    "list_bench": list_bench,
    "record_shortlist": record_shortlist,
    "get_shortlist": get_shortlist,
}


# ---------------------------------------------------------------------------
# The Gateway edge.
# ---------------------------------------------------------------------------


def tool_name(context) -> str:
    """The bare tool name, with the Gateway's `{target}___` prefix stripped."""
    raw = (context.client_context.custom or {}).get("bedrockAgentCoreToolName", "")
    return raw.split(DELIMITER, 1)[1] if DELIMITER in raw else raw


def lambda_handler(event, context):
    """One function, several tools, dispatched on the name the Gateway passed.

    `event` is a flat map of this tool's `inputSchema` properties — it is not an
    API Gateway envelope, so there is no `body` to parse.
    """
    name = tool_name(context)
    fn = TOOLS.get(name)
    if fn is None:
        # Returned, not raised. An MCP tool's return value is the next thing the
        # model reads; an unhandled exception is just a failed call it cannot act on.
        return {
            "error": f"unknown tool {name!r}",
            "available": sorted(TOOLS),
        }
    try:
        return fn(**(event or {}))
    except TypeError as exc:
        return {"error": f"{name} was called with the wrong arguments: {exc}"}
