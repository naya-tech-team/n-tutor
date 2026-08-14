"""01 · Quickstart — the smallest agent that does real work.

The domain for the whole course: employees have rated skills, jobs require
skills, and somebody has to decide who fits. Here that is one agent, two tools.

Run:  uv run app/01_quickstart/main.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, tool
from strands_tools import calculator

from _shared import find_employee_by_name, get_job, make_model, skill_level

log = logging.getLogger("strands.quickstart")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(name)s - %(levelname)s - %(message)s"))
log.addHandler(handler)
log.setLevel(logging.INFO)


# --------------------------------------------------------------------------
# A tool is a Python function. The name, the docstring and the type hints are
# the entire contract the model sees — write them for the model, not for you.
# --------------------------------------------------------------------------


@tool
def employee_skill_level(employee_name: str, skill: str) -> int:
    """Return an employee's proficiency in one skill, from 0 to 5.

    Args:
        employee_name (str): Full or partial name, e.g. "Priya"
        skill (str): Skill name or a common alias, e.g. "pyspark"

    Returns:
        int: 0 if the employee does not have the skill at all, else 1-5.
    """
    employee = find_employee_by_name(employee_name)
    if employee is None:
        return 0
    return skill_level(employee, skill)


@tool
def job_bar(job_id: str, skill: str) -> str:
    """Return the minimum level a job requires for one skill.

    Args:
        job_id (str): Requisition id, e.g. "J2001"
        skill (str): Skill name, e.g. "Apache Spark"

    Returns:
        str: A line describing the bar, or a message if the job does not need it.
    """
    job = get_job(job_id)
    if job is None:
        return f"No such job {job_id}."
    for req in job["required_skills"]:
        if req["skill"].lower() == skill.strip().lower():
            need = "mandatory" if req["mandatory"] else "nice to have"
            return f"{job['title']} needs {req['skill']} at level {req['min_level']} ({need})."
    return f"{job['title']} does not list {skill} as a requirement."


tool_use_ids: list[str] = []


def callback_handler(**kwargs) -> None:
    """The synchronous view of the stream. Logs text and tool selections."""
    if "data" in kwargs:
        log.info(kwargs["data"])
    elif "current_tool_use" in kwargs:
        tool_use = kwargs["current_tool_use"]
        if tool_use["toolUseId"] not in tool_use_ids:
            log.info(f"[Using tool: {tool_use.get('name')}]")
            tool_use_ids.append(tool_use["toolUseId"])


DEFAULT_QUESTION = "Does Priya clear the Apache Spark bar for job J2001? By how many levels?"


async def main(message: str = DEFAULT_QUESTION) -> None:
    agent = Agent(
        name="SkillMatchAgent",
        description="Answers questions about employee skills and job requirements.",
        system_prompt=(
            "You are a skills-matching assistant. Never guess a skill level — "
            "call the tools. State the numbers you got back."
        ),
        tools=[employee_skill_level, job_bar, calculator],
        model=make_model(),
        callback_handler=callback_handler,
    )

    # stream_async is the async view of the same events the callback_handler sees.
    async for event in agent.stream_async(message):
        if "data" in event:
            log.info(event["data"])
        elif "current_tool_use" in event and event["current_tool_use"].get("name"):
            log.info(f"[Tool use delta for: {event['current_tool_use']['name']}]")


if __name__ == "__main__":
    asyncio.run(main(input(f"Ask about a skill or a job (enter for: {DEFAULT_QUESTION!r}): ") or DEFAULT_QUESTION))
