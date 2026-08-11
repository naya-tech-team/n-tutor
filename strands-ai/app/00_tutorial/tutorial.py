"""Strands Quick Start — one resourcing assistant, built up in 11 runnable steps.

The job: requisition J2001 is open, twelve people are in the directory, and
somebody has to decide who to interview. By step 10 the assistant searches by
skill, scores candidates against the role, remembers its shortlist across a
restart, and refuses to approach staffed employees. Step 11 stops writing tools
altogether and borrows HR's, over MCP.

Read alongside TUTORIAL.md. Each step is self-contained and prints what it proves.

    uv run app/00_tutorial/tutorial.py          # every step, in order
    uv run app/00_tutorial/tutorial.py 3        # just step 3
    uv run app/00_tutorial/tutorial.py 3 4 5    # a range you care about
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # put app/ on sys.path

from mcp import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, Field
from strands import Agent, ToolContext, tool
from strands.hooks import BeforeToolCallEvent
from strands.session import FileSessionManager
from strands.tools.mcp import MCPClient

from _shared import (
    employees_with_skill,
    get_employee,
    get_job,
    make_model,
    match,
    settings,
    skill_level,
)

# The requisition every step is working on: Senior Data Engineer, Bengaluru.
JOB_ID = "J2001"

SYSTEM_PROMPT = (
    f"You are a resourcing assistant filling requisition {JOB_ID} (Senior Data Engineer, Bengaluru). "
    "Use find_candidates to search, shortlist_candidate to add someone, and shortlist_summary "
    "to report. Never invent a skill level or a match score. Keep replies to one sentence."
)

SYSTEM_PROMPT_V1 = (
        """You are a resourcing assistant. Use find_candidates to search the bench, 
        shortlist_candidate to add someone, and shortlist_summary to report one sentence,
        if emp_match is set, use it to explain an already-computed employee-to-job match.

        The matching engine has already calculated:
        - match score
        - verdict
        - matched skills
        - skill gaps
        - blockers
        - experience eligibility
        - location compatibility
        - availability

        You MUST NOT recalculate the score or make independent hiring decisions.

        Your job is to translate the matching result into a clear, concise, professional Markdown summary that a recruiter or hiring manager can understand quickly.

        INPUT:

        {{emp_match}}

        ANALYSIS RULES:

        1. Start with the overall match.
        Explain the score and supplied verdict in plain language.

        2. Explain why the employee matches.
        Use matched_skills and highlight the most relevant skills.

        3. Explain what is missing.
        Use gaps and clearly identify important skill deficiencies.

        4. Explain blockers.
        Blockers are more important than ordinary skill gaps.
        If blockers exist, make them prominent.
        If there are no blockers, state "No identified blockers."

        5. Explain experience.
        Use meets_experience exactly as provided.
        Do not infer years of experience.

        6. Explain location.
        Use same_location exactly as provided.

        7. Explain availability.
        Report the supplied availability without making assumptions.

        8. Provide a final recommendation.
        Base it on the supplied score, verdict, gaps, blockers, experience, location, and availability.
        Do not create new facts.

        IMPORTANT:

        - Never invent information.
        - Never infer a skill that is not present in matched_skills.
        - Never assume a gap is insignificant unless the data supports that conclusion.
        - Never change the supplied score.
        - Never recalculate the score.
        - Never override blockers.
        - Empty arrays mean there is no information in that category.
        - Keep the language professional and neutral.
        - Avoid discriminatory or protected-attribute-based reasoning.
        - The output is decision support, not an autonomous hiring decision.

        OUTPUT:

        {
        "summary": "2-4 sentence executive summary",
        "strengths": [
            "..."
        ],
        "gaps": [
            "..."
        ],
        "experience": "...",
        "location": "...",
        "availability": "...",
        "blockers": [
            "..."
        ],
        "recommendation": "...",
        "recruiter_note": "..."
        }

        The JSON must be valid JSON.
        Do not include markdown.
        """
    )


# ==========================================================================
# The tools our assistant will have. Three functions, nothing more.
# ==========================================================================


@tool
def find_candidates(skill: str, min_level: int = 3) -> str:
    """Find bench employees who have a skill at or above a proficiency level.

    Args:
        skill: Skill name or alias, e.g. "pyspark" or "Apache Spark"
        min_level: Minimum proficiency, 1 (aware) to 5 (expert). Default 3.
    """
    people = employees_with_skill(skill, min_level=min_level, available_only=True)
    if not people:
        return f"Nobody on the bench is at level {min_level}+ in {skill!r}."
    return "\n".join(
        f"{e['employee_id']} {e['name']} — {skill} level {skill_level(e, skill)}, {e['location']}"
        for e in people
    )


@tool(context=True)
def shortlist_candidate(employee_id: str, tool_context: ToolContext) -> str:
    """Score a candidate against the open requisition and add them to the shortlist.

    Args:
        employee_id: Employee id from find_candidates, e.g. "E1002"
    """
    employee = get_employee(employee_id)
    if employee is None:
        return f"No employee {employee_id!r}. Use an id returned by find_candidates."

    result = match(employee, get_job(JOB_ID))
    if result["blockers"]:
        return (
            f"{result['name']} cannot be shortlisted for {JOB_ID}: "
            f"missing mandatory {', '.join(result['blockers'])}."
        )

    shortlist = tool_context.agent.state.get("shortlist") or []
    # Idempotent on purpose. A model re-reads its own history and re-acts on it;
    # a tool that writes must survive being called twice with the same argument.
    if any(c["employee_id"] == employee_id for c in shortlist):
        return f"{result['name']} is already on the shortlist ({len(shortlist)} candidate(s))."

    shortlist.append({"employee_id": employee_id, "name": result["name"], "score": result["score"]})
    tool_context.agent.state.set("shortlist", shortlist)
    tool_context.agent.state.set("emp_match", result)  # for step 7, to show the model can return structured data
    return f"Shortlisted {result['name']} at {result['score']}%. {len(shortlist)} candidate(s) on the list."


@tool(context=True)
def shortlist_summary(tool_context: ToolContext) -> str:
    """Report who is currently on the shortlist for the open requisition."""
    shortlist = tool_context.agent.state.get("shortlist") or []
    if not shortlist:
        return "The shortlist is empty."
    lines = ", ".join(f"{c['name']} ({c['score']}%)" for c in shortlist)
    average = sum(c["score"] for c in shortlist) / len(shortlist)
    return f"{len(shortlist)} candidate(s) for {JOB_ID}: {lines}. Average score {average:.0f}%."



def build_agent(**overrides) -> Agent:
    """The assistant used by most steps. Overrides let a step change one thing."""
    kwargs = dict(
        model=make_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[find_candidates, shortlist_candidate, shortlist_summary],
        callback_handler=None,  # silence the default printer; we print our own output
    )
    kwargs.update(overrides)
    return Agent(**kwargs)


# ==========================================================================
# Step 1 — The smallest thing that works
# ==========================================================================
def step_1() -> None:
    agent = Agent(
                   model=make_model(),
                   callback_handler=None
                   )
    result = agent("In one sentence: what is an AI agent?")
    print(result)


# ==========================================================================
# Step 2 — You did not get a string back
# ==========================================================================
def step_2() -> None:
    """agent(...) returns an AgentResult. Learn its fields now, debug faster later."""
    """https://strandsagents.com/docs/api/typescript/AgentResult/"""
    agent = Agent(model=make_model())
    result = agent("Say 'hello' and nothing else.")

    print("type          :", type(result).__name__)
    print("str(result)   :", str(result).strip())
    print("stop_reason   :", result.stop_reason)  # branch on THIS, never on the text
    print("message role  :", result.message["role"])
    print("tokens        :", result.metrics.accumulated_usage)
    print("cycles        :", result.metrics.cycle_count)


# ==========================================================================
# Step 3 — Give it a hand: one tool
# ==========================================================================
def step_3() -> None:
    """The model reads the docstring and decides to call the function."""
    agent = Agent(
        model=make_model(),
        # Note this prompt mentions ONLY the tool that is actually loaded. A prompt
        # that names a tool the agent does not have makes the model invent one.
        system_prompt=(
            "You are a resourcing assistant. Use find_candidates to search the bench. "
            "Keep replies to one sentence."
        ),
        tools=[find_candidates],
        callback_handler=None,
    )
    print("tools the model can see:", agent.tool_names)
    print(agent("who is PM of India?"))


# ==========================================================================
# Step 4 — Three tools, and the model chains them
# ==========================================================================
def step_4() -> None:
    """Nothing routes this. The model picks find_candidates, then shortlist_candidate."""
    agent = build_agent()
    print(agent("Find a Java 4+ person on the bench and shortlist them."))
    print("state:", agent.state.get("shortlist"))


# ==========================================================================
# Step 5 — Watch the loop
# ==========================================================================
def step_5() -> None:
    """One cycle = one model call + the tools it asked for. Repeat until done."""
    cycle = {"n": 0}

    def trace(event: BeforeToolCallEvent) -> None:
        cycle["n"] += 1
        print(f"  tool call {cycle['n']}: {event.tool_use['name']}({event.tool_use['input']})")

    agent = build_agent(hooks=[trace], system_prompt=SYSTEM_PROMPT_V1)
    # agent = build_agent(hooks=[trace])
    result = agent("Shortlist E1002, then E1003, " \
    "then tell me who is on the list and tell me why. " \
    "Get me detailed summary")

    # print(agent.system_prompt)
    print("state:", agent.state.get("shortlist"))
    print("answer      :", str(result).strip())
    print("model calls :", result.metrics.cycle_count)
    print("stop_reason :", result.stop_reason)


# ==========================================================================
# Step 6 — Stream it, so a human sees progress
# ==========================================================================
async def step_6_async() -> None:
    agent = build_agent(system_prompt=SYSTEM_PROMPT_V1)
    async for event in agent.stream_async("Find a Java 4+ person on the bench and shortlist them. " \
    "Get me detailed summary"):
        if "data" in event:
            print(event["data"], end="", flush=True)
        elif "current_tool_use" in event and event["current_tool_use"].get("name"):
            print(f"\n<calling {event['current_tool_use']['name']}>")
        elif "result" in event:
            print(f"\n<done: {event['result'].stop_reason}>")


def step_6() -> None:
    """The same run, delivered as events instead of one blocking call."""
    asyncio.run(step_6_async())



STEPS = {
    1: ("The smallest thing that works", step_1),
    2: ("You did not get a string back", step_2),
    3: ("Give it a hand: one tool", step_3),
    4: ("Three tools, and the model chains them", step_4),
    5: ("Watch the loop", step_5),
    6: ("Stream it", step_6),

}


def main() -> None:
    wanted = [int(input("Enter step: "))]
    if not wanted:
        wanted = [int(a) for a in sys.argv[1:]] or sorted(STEPS)
    for number in wanted:
        if number not in STEPS:
            print(f"No step {number}. Available: {sorted(STEPS)}")
            continue
        title, func = STEPS[number]
        print(f"\n{'=' * 70}\nSTEP {number} — {title}\n{'=' * 70}")
        func()


if __name__ == "__main__":
    main()
