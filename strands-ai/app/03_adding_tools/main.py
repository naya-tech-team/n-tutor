"""03 · Adding tools — the four ways a capability gets into an agent.

Run:  uv run app/03_adding_tools/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # put app/ on sys.path

from strands import Agent, ToolContext, tool
from strands_tools import calculator  # 2) a whole module, imported

from _shared import employees_with_skill, get_employee, get_job, make_model, match, skill_level

# --------------------------------------------------------------------------
# 1) The plain decorator. Name, docstring and type hints ARE the tool spec.
# --------------------------------------------------------------------------


@tool
def find_candidates(skill: str, min_level: int = 3, available_only: bool = True) -> str:
    """Find employees who have a skill at or above a proficiency level.

    Args:
        skill: Skill name or alias, e.g. "pyspark" or "Apache Spark"
        min_level: Minimum proficiency, 1 (aware) to 5 (expert). Default 3.
        available_only: Only people on the bench, not staffed on a project.

    Returns:
        One line per candidate: id, name, their level, location and availability.
    """
    people = employees_with_skill(skill, min_level=min_level, available_only=available_only)
    if not people:
        return f"Nobody at level {min_level}+ in {skill!r}."
    return "\n".join(
        f"{e['employee_id']} {e['name']} — level {skill_level(e, skill)}, {e['location']}, {e['availability']}"
        for e in people
    )


# --------------------------------------------------------------------------
# 3) Overriding the generated spec. Use when the function name is an internal
#    detail and the model deserves a better one.
# --------------------------------------------------------------------------


@tool(
    name="score_candidate",
    description="Score one employee against one open job. Returns the match percentage, "
    "the skills that met the bar, and the gaps that did not.",
)
def _run_match_engine(employee_id: str, job_id: str) -> dict:
    """Internal scoring call — the model never sees this docstring, only the description above."""
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return {"error": f"unknown employee {employee_id!r} or job {job_id!r}"}
    return match(employee, job)


# --------------------------------------------------------------------------
# 4) A context-aware tool. `context=True` injects a ToolContext giving the tool
#    access to the agent that called it — its state, its history, its id.
# --------------------------------------------------------------------------


@tool(context=True)
def shortlist_candidate(employee_id: str, reason: str, tool_context: ToolContext) -> str:
    """Add a candidate to the shortlist for the requisition being worked on.

    Args:
        employee_id: Who to shortlist, e.g. "E1002"
        reason: One line on why they fit — this is what the hiring manager reads.
    """
    # Validate before writing. A small model will happily pass "<the best one>"
    # as an id; state that survives the conversation must not accept that.
    employee = get_employee(employee_id)
    if employee is None:
        return f"No employee {employee_id!r}. Pass an id like 'E1002' returned by find_candidates."

    agent = tool_context.agent
    shortlist = agent.state.get("shortlist") or []
    shortlist.append({"employee_id": employee["employee_id"], "name": employee["name"], "reason": reason})
    agent.state.set("shortlist", shortlist)
    return f"Shortlisted {employee['name']} ({len(shortlist)} on the list)."


def main() -> None:
    agent = Agent(
        model=make_model(),
        system_prompt=(
            "You are a resourcing assistant filling open requisitions. "
            "Use tools rather than guessing — never invent a skill level or a score."
        ),
        tools=[find_candidates, _run_match_engine, shortlist_candidate, calculator],
        # Silence the default printer so the only output is our own prints.
        callback_handler=None,
    )

    # Everything the model can see, in the order it was registered. Note the
    # renamed tool appears as `score_candidate`, not `_run_match_engine`.
    print("Registered tools:", agent.tool_names, "\n")

    # Two hops, deliberately: find, then shortlist. Small local models chain two
    # tool calls reliably and three unreliably — see the README.
    print(
        agent(
            "Which bench employees have pyspark at level 4 or higher? "
            "Shortlist the strongest one for J2001 with a one-line reason."
        )
    )
    print("\nAgent state after the run:", agent.state.get())


if __name__ == "__main__":
    main()
