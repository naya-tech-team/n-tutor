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


# ==========================================================================
# Step 7 — A typed object instead of prose
# ==========================================================================
class ShortlistReport(BaseModel):
    """A machine-readable summary of the shortlist for one requisition."""

    job_id: str = Field(description="The requisition id, e.g. J2001")
    candidate_count: int = Field(description="How many candidates are on the shortlist")
    top_candidate: str = Field(description="Name of the highest-scoring candidate")
    top_score: int = Field(ge=0, le=100, description="That candidate's score, copied from the tool")


def step_7() -> None:
    """Hand it a Pydantic class, get a validated instance. No JSON parsing."""
    agent = build_agent()

    report = agent(
        "Shortlist E1002. Summarise the shortlist.", structured_output_model=ShortlistReport
    ).structured_output

    print("type:", type(report).__name__)
    print(report.model_dump_json(indent=2))
    print("usable immediately:", f"{report.top_candidate} -> interview slot 1")


# ==========================================================================
# Step 8 — Survive a restart
# ==========================================================================
def step_8() -> None:
    """Two agents, one session id. The second one wakes up remembering.

    A requisition stays open for weeks. This is what makes "where were we on
    J2001?" a question the assistant can answer on Thursday.
    """
    session_dir = str(settings.sessions_dir)

    first = build_agent(
        agent_id="resourcing",
        session_manager=FileSessionManager(session_id="tutorial-demo", storage_dir=session_dir),
    )
    print("run A — messages at boot:", len(first.messages))
    print("run A —", str(first("Shortlist E1002.")).strip())
    print("run A — state restored  :", first.state.get("shortlist"))
    print("run A — messages after :", len(first.messages))

    input("Press Enter to simulate a restart:")

    # Simulate a redeploy: brand new object, same session id.
    second = build_agent(
        agent_id="resourcing",
        session_manager=FileSessionManager(session_id="tutorial-demo", storage_dir=session_dir),
    )
    print("run B — messages at boot:", len(second.messages), "  ← restored from disk")
    print("run B — state restored  :", second.state.get("shortlist"))
    print("run B —", str(second("Who is on the shortlist?")).strip())


# ==========================================================================
# Step 9 — A guardrail the model cannot talk its way past
# ==========================================================================
def step_9() -> None:
    """Policy lives in a hook, not in the prompt. The tool never runs.

    The policy: you may not approach someone who is staffed on a project without
    their manager's sign-off. E1002 is on the bench; E1007 is allocated.
    """

    def protect_allocated_staff(event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] != "shortlist_candidate":
            return
        employee = get_employee(event.tool_use["input"].get("employee_id", ""))
        if employee and employee["availability"] == "allocated":
            print(f"  [guardrail] blocked {employee['name']} — staffed on a project")
            event.cancel_tool = (
                f"{employee['name']} is allocated to a project. Approaching staffed employees "
                "needs their manager's sign-off, so they were not shortlisted."
            )

    agent = build_agent(hooks=[protect_allocated_staff])
    print("bench    :", str(agent("Shortlist E1002.")).strip())
    print("allocated:", str(agent("Shortlist E1007 as well.")).strip())
    print("state (E1007 is absent):", agent.state.get("shortlist"))


# ==========================================================================
# Step 10 — Everything at once: the finished assistant
# ==========================================================================
def step_10() -> None:
    """Tools + state + session + guardrail + typed output, in one agent."""

    def protect_allocated_staff(event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] != "shortlist_candidate":
            return
        employee = get_employee(event.tool_use["input"].get("employee_id", ""))
        if employee and employee["availability"] == "allocated":
            event.cancel_tool = f"{employee['name']} is staffed on a project and needs manager sign-off."

    agent = build_agent(
        agent_id="resourcing-final",
        session_manager=FileSessionManager(
            session_id="tutorial-final", storage_dir=str(settings.sessions_dir)
        ),
        hooks=[protect_allocated_staff],
    )

    turns = (
        "Who on the bench knows pyspark at level 4 or better?",
        "Shortlist E1002.",
        "Shortlist E1007 too.",  # allocated — the guardrail stops this
        "Shortlist E1005.",      # bench, but blocked on mandatory SQL — the tool stops this
    )
    for turn in turns:
        print(f"> {turn}\n  {str(agent(turn)).strip()}")

    report = agent("Summarise the shortlist.", structured_output_model=ShortlistReport).structured_output
    print("\nfinal report:", report.model_dump_json())
    print("persisted at:", settings.sessions_dir / "session_tutorial-final")


# ==========================================================================
# Step 11 — Tools you never wrote, from a process you do not own
# ==========================================================================
HR_MCP_SERVER = str(Path(__file__).with_name("hr_mcp_server.py"))


def hr_mcp_client() -> MCPClient:
    """A client for the HR team's MCP server, launched as a subprocess.

    The lambda is deliberate: MCPClient needs to be able to *re-open* the
    transport, so it takes a factory rather than a live connection.
    `sys.executable` is this venv's python, so the server sees our dependencies.

    Nothing below names the server's framework. It happens to be built with
    `fastmcp`; it could be the `mcp` SDK, TypeScript, or a vendor's hosted
    endpoint. A client speaks the protocol, not the other side's library.
    """
    # return MCPClient(
    #     lambda: stdio_client(StdioServerParameters(command=sys.executable, args=[HR_MCP_SERVER]))
    # )
    return MCPClient(lambda: streamablehttp_client("http://localhost:8000/mcp/"))


def step_11() -> None:
    """Steps 1-10 owned every tool. Real teams do not: HR owns employee data.

    They publish one MCP server; we speak the protocol to it. Nothing in this
    step imports their code, and the four tool names below were never typed in
    this file — they came off the wire.

    A server publishes three kinds of thing, and only the first is famous:
      tools     — actions the model chooses, with arguments
      resources — data *we* read by URI; no model call, no tokens
      prompts   — the question itself, authored by the team that owns the data
    """
    with hr_mcp_client() as hr:  # the block is load-bearing: leaving it kills the server
        tools = hr.list_tools_sync()
        resources = hr.list_resources_sync().resources
        templates = hr.list_resource_templates_sync().resourceTemplates
        prompts = hr.list_prompts_sync().prompts

        print("tools     :", [t.tool_name for t in tools])
        print("resources :", [str(r.uri) for r in resources])
        print("templates :", [t.uriTemplate for t in templates])
        print("prompts   :", [p.name for p in prompts])

        # --- Resources: data, fetched by URI. The model is not involved. ---
        bench = hr.read_resource_sync("hr://bench").contents[0].text
        print("\nhr://bench —\n" + bench)

        # A template resource: one URI shape, many documents.
        profile = json.loads(hr.read_resource_sync("hr://employees/E1002").contents[0].text)
        print(f"\nhr://employees/E1002 — {profile['name']}, {profile['designation']}, "
              f"{len(profile['skills'])} rated skills")

        # --- A tool, called directly. Useful in tests: no model, no guessing. ---
        result = hr.call_tool_sync(
            tool_use_id="step11-1", name="score_match",
            arguments={"employee_id": "E1010", "job_id": "J2003"},
        )
        scored = result.get("structuredContent", {})
        print(f"\nscore_match(E1010, J2003) -> {scored.get('score')}% "
              f"{scored.get('verdict')}, blocked on {scored.get('blockers')}")

        # --- The agent: HR's four tools plus one of ours, in one toolbox. ---
        agent = Agent(
            model=make_model(),
            system_prompt=(
                "You are a resourcing assistant. Use find_by_skill, score_match, rank_for_job "
                "and rank_jobs_for_person for every fact about people and requisitions, and "
                "shortlist_candidate to add someone to the shortlist. Never invent an employee, "
                "a skill level or a score. Keep replies to two sentences."
            ),
            tools=[*tools, shortlist_candidate],  # remote and local mix freely
            callback_handler=None,
        )
        print("\nthe agent's toolbox:", agent.tool_names)
        print("> " + str(agent("Who are the top 2 available candidates for J2001?")).strip())

        # --- Prompts: HR wrote the question, with the score already in it. ---
        screening = hr.get_prompt_sync("screen_candidate", {"employee_id": "E1010", "job_id": "J2003"})
        brief = hr.get_prompt_sync("shortlist_brief", {"job_id": "J2001", "limit": "3"})
        print("\nscreen_candidate renders:", [m.role for m in screening.messages])
        print("shortlist_brief renders  :", [m.role for m in brief.messages],
              "  ← a prompt can pre-seed the assistant's turn too")

        # Send the rendered prompt as the turn. The model is being told what to
        # write, by the team that owns the definition of a good screening note.
        print("> " + str(agent(screening.messages[0].content.text)).strip())

    # Outside the block the subprocess is gone and those tools are unusable —
    # which is why a dead MCP server looks like an agent that quietly improvises.
    print("\nserver stopped; MCP tools are no longer callable.")


STEPS = {
    1: ("The smallest thing that works", step_1),
    2: ("You did not get a string back", step_2),
    3: ("Give it a hand: one tool", step_3),
    4: ("Three tools, and the model chains them", step_4),
    5: ("Watch the loop", step_5),
    6: ("Stream it", step_6),
    7: ("A typed object instead of prose", step_7),
    8: ("Survive a restart", step_8),
    9: ("A guardrail it cannot talk past", step_9),
    10: ("Everything at once", step_10),
    11: ("Someone else's tools, over MCP", step_11),
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
