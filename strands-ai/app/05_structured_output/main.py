"""05 · Structured responses — a typed object instead of prose.

The use case: a hiring manager sends a paragraph of wishful thinking. Somebody
has to turn it into a requisition row with a real skills list.

Run:  uv run app/05_structured_output/main.py
"""

import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.types.exceptions import StructuredOutputException

from _shared import get_employee, get_job, make_model, match

# --------------------------------------------------------------------------
# The schema IS the prompt. Field descriptions are read by the model.
# --------------------------------------------------------------------------


class SkillRequirement(BaseModel):
    """One skill a role needs."""

    skill: str = Field(description="Canonical skill name, e.g. 'Apache Spark' not 'pyspark'")
    min_level: int = Field(ge=1, le=5, description="1 aware, 3 working independently, 5 expert")
    mandatory: bool = Field(description="True only if a candidate without it cannot do the job")


class Requisition(BaseModel):
    """An open role, extracted from a hiring manager's message."""

    title: str = Field(description="Job title, under 6 words")
    department: str
    location: str = Field(description="City, or 'Remote' if none is stated")
    min_experience_years: int = Field(ge=0, le=25)
    seniority: Literal["junior", "mid", "senior", "lead"]
    required_skills: list[SkillRequirement] = Field(description="3 to 6 skills, mandatory ones first")


class GapReport(BaseModel):
    """Why one candidate does or does not clear the bar."""

    employee_id: str
    verdict: Literal["strong", "possible", "weak", "blocked"] = Field(
        description="Use exactly the verdict returned by the score_match tool"
    )
    score: int = Field(ge=0, le=100, description="The score returned by score_match, unchanged")
    blocking_skill: str | None = Field(description="The one mandatory skill they miss, or null")
    development_action: str = Field(description="One concrete step to close the gap, under 15 words")


@tool
def score_match(employee_id: str, job_id: str) -> str:
    """Score an employee against a job. Returns score, verdict and gaps.

    Args:
        employee_id: e.g. "E1010"
        job_id: e.g. "J2003"
    """
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return "unknown employee or job"
    result = match(employee, job)
    gaps = "; ".join(f"{g['skill']} has {g['actual']} needs {g['required']}" for g in result["gaps"])
    return (
        f"score={result['score']} verdict={result['verdict']} "
        f"blockers={result['blockers'] or 'none'} gaps: {gaps or 'none'}"
    )


HIRING_MANAGER_MESSAGE = (
    "We're drowning in the retail lakehouse work. I need someone senior in Bengaluru who can own "
    "our pyspark pipelines end to end — strong python, strong SQL, and they must be able to run "
    "Airflow without hand-holding. Databricks experience would be a bonus. Six-plus years ideally."
)


def demo_per_invocation() -> None:
    """Pass the model per call — the same agent can return different shapes.

    Guarded like the other two. Structured output is a tool call underneath, and
    a 3B model is unreliable at making one *at all* — so even this flat schema
    fails often on llama3.2. Letting it escape would kill the script before the
    lessons below ever run.
    """
    print("=== 1. Per-invocation structured output ===")
    agent = Agent(
        model=make_model(),
        system_prompt="You turn hiring requests into structured requisitions for an HR system.",
        callback_handler=None,
    )

    try:
        req: Requisition = agent(HIRING_MANAGER_MESSAGE, structured_output_model=Requisition).structured_output
        print(type(req).__name__, "->", req.model_dump_json(indent=2))
        print("seniority is a real enum value:", req.seniority, "\n")
    except StructuredOutputException as exc:
        print(f"  ⚠ even the flat schema failed: {exc}")
        print("  → this is a model limit, not a code bug. See the README.\n")


def demo_agent_default() -> None:
    """Pin the shape on the agent — every call returns it.

    This one nests (a list of SkillRequirements inside a Requisition). Nesting is
    where small local models start to fail, so the failure is caught and explained
    rather than hidden: it is the most important practical lesson in this file.
    """
    print("=== 2. Agent-level default shape (nested) ===")
    agent = Agent(
        model=make_model(),
        system_prompt=(
            "You turn hiring requests into requisitions. Normalise skill names to their "
            "canonical form: 'pyspark' is 'Apache Spark', 'py' is 'Python'."
        ),
        structured_output_model=Requisition,
        callback_handler=None,
    )
    try:
        req: Requisition = agent(HIRING_MANAGER_MESSAGE).structured_output
        for skill in req.required_skills:
            flag = "MUST" if skill.mandatory else "nice"
            print(f"  {flag:<4} {skill.skill:<18} L{skill.min_level}")
        print()
    except StructuredOutputException as exc:
        print(f"  ⚠ the model could not fill the nested schema: {exc}")
        print("  → shrink the schema, or use a larger model. See the README.\n")


def demo_with_tools() -> None:
    """Tools run first, then the answer is forced into the schema.

    E1010 is 52% on J2003 and blocked by exactly one mandatory skill — the shape
    of decision a recruiter actually has to make.
    """
    print("=== 3. Tools + structured output ===")
    agent = Agent(
        model=make_model(),
        tools=[score_match],
        system_prompt=(
            "Assess the candidate. Call score_match exactly once, then report. "
            "Copy the score and verdict from the tool — do not recompute them."
        ),
        callback_handler=None,
    )
    try:
        report = agent(
            "Can E1010 take the Analytics Engineer role J2003?",
            structured_output_model=GapReport,
        ).structured_output
        print(report.model_dump_json(indent=2))
    except StructuredOutputException as exc:
        print(f"  ⚠ tools + schema in one loop was too much for this model: {exc}")


def main() -> None:
    demo_per_invocation()
    demo_agent_default()
    demo_with_tools()


if __name__ == "__main__":
    main()
