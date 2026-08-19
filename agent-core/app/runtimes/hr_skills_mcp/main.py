"""The HR scoring engine, spoken over MCP — and hosted in AgentCore Runtime.

This is the one runtime that already spoke a protocol the world understands, so
it is the one the Gateway does **not** front. The screening agent connects to it
directly, one hop; everything that cannot speak MCP on its own — the Lambda over
S3 — goes through `hr-gateway` instead.

One line differs from `mcp-server/app/main.py`, and none of it is cosmetic:

    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000,
            path="/mcp", stateless_http=True)

`host="0.0.0.0"` because a container that binds loopback is unreachable from
outside itself. `stateless_http=True` because Runtime injects an `Mcp-Session-Id`
on any request lacking one and load-balances across instances — a stateful server
gets handed a second request by an instance that never saw the first.

**Note where those arguments go.** Every AWS example writes
`FastMCP(host=..., stateless_http=...)`, because it uses the FastMCP bundled
inside the `mcp` SDK. This repo uses the standalone `fastmcp` 3.x package, which
moved both to the `run()` call — passing them to the constructor raises
`TypeError: FastMCP() no longer accepts 'host'`.

The result is identical in both worlds, so this file runs unchanged on a laptop
and in Runtime. It is the only runtime here with no `settings.agentcore` branch.

Run it:

    uv run app/runtimes/hr_skills_mcp/main.py     # http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import sys
from pathlib import Path

# app/ on sys.path for a local run. In the deployment zip `_shared/` sits beside
# main.py at /var/task, which is already first on sys.path, so this is a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastmcp import FastMCP

from _shared import get_employee, get_job, install, match, rank_candidates, settings
from _shared.hr_data import canonical_skill

# Point the domain at S3 if that is what we were configured for. No-op locally.
install()

mcp = FastMCP("hr-skills")


@mcp.tool(description="Score one employee against one job and explain the gaps and blockers.")
def score_match(employee_id: str, job_id: str) -> dict:
    """Args: employee_id e.g. "E1002", job_id e.g. "J2001"."""
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        # Say what to do next. A weak model handed a bare "not found" will try
        # E1148, then E1149, until something stops it.
        return {
            "error": (
                f"unknown employee {employee_id!r} or job {job_id!r}. Call "
                "hrdata___find_by_skill or hrdata___list_bench and use an id it returned."
            )
        }
    return match(employee, job)


@mcp.tool(description="Rank the best available candidates for a job, best first.")
def shortlist(job_id: str, limit: int = 3) -> list[dict]:
    """Args: job_id e.g. "J2001", limit (how many candidates to return)."""
    ranked = rank_candidates(job_id, available_only=True, limit=limit)
    if not ranked:
        return [{"error": f"no requisition {job_id!r}, or nobody on the bench scores against it"}]
    return ranked


@mcp.tool(description="Resolve a free-text skill name to the catalog's canonical name.")
def resolve_skill(name: str) -> dict:
    """Args: name, e.g. "pyspark" -> "Apache Spark". Returns the canonical name or an error."""
    canon = canonical_skill(name)
    if canon is None:
        return {"error": f"{name!r} is not in the skills catalog", "input": name}
    return {"input": name, "skill": canon}


if __name__ == "__main__":
    # Same call in both worlds: 0.0.0.0:8000/mcp is exactly the MCP protocol
    # contract AgentCore Runtime expects, and it is a fine local address too.
    print(f"hr-skills MCP server on http://0.0.0.0:8000/mcp  (data_source={settings.data_source})")
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        path="/mcp",
        stateless_http=True,
        show_banner=False,
    )
