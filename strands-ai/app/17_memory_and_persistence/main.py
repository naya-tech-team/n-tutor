"""17 · Memory & persistence — everything a resourcing desk has to remember.

The scenario: recruiter R-8812 works requisition J2001 (Senior Data Engineer,
Bengaluru) across two days, then opens J2004 (ML Engineer) the following week.
Eight different things have to survive eight different amounts of time:

    what                                  survives            mechanism
    -----------------------------------   -----------------   ------------------
    who is asking (recruiter, tenant)     one invocation      invocation_state
    the open req, the shortlist           the conversation    agent.state
    the dialogue itself                   the context window  conversation manager
    a 2,000-token candidate dossier       the context window  context manager
    all of the above                      a process restart   session manager
    a screening decision you regret       a bad idea          snapshots
    "this manager never takes <6 yrs"     the requisition     memory
    every byte the seven above write      the disk            storage

Each part is self-contained and prints what it proves. Read alongside README.md.

    uv run app/17_memory_and_persistence/main.py        # all eight parts
    uv run app/17_memory_and_persistence/main.py 6      # just memory
    uv run app/17_memory_and_persistence/main.py 8      # just the end-to-end story
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # put app/ on sys.path

from strands import Agent, Snapshot, ToolContext, tool
from strands.agent.conversation_manager import (
    NullConversationManager,
    SlidingWindowConversationManager,
    SummarizingConversationManager,
)
from strands.memory import IntervalTrigger, MemoryManager
from strands.session import FileSessionManager, SnapshotSessionManager
from strands.storage import InMemoryStorage, LocalFileStorage
from strands.vended_memory_stores import TestMemoryStore
from strands.vended_plugins.context_offloader import ContextOffloader

from _shared import (
    EMPLOYEES,
    JOBS,
    SKILLS,
    canonical_skill,
    employees_with_skill,
    get_employee,
    get_job,
    make_model,
    match,
    rank_candidates,
    settings,
    skill_level,
)

# The requisition the whole lesson is working on, and the one it moves to at the end.
JOB_ID = "J2001"
NEXT_JOB_ID = "J2004"

# Everything this lesson writes lands under one directory, so `rm -rf` resets it
# without touching the other lessons' data. Note the four sub-trees: they are the
# four persistence subsystems, and every one of them is bytes under a Storage.
LESSON_DIR = settings.run_dir / "lesson17"
SESSIONS_DIR = LESSON_DIR / "sessions"  # session managers
BLOBS_DIR = LESSON_DIR / "storage"  # raw Storage + snapshot blobs
MEMORY_FILE = LESSON_DIR / "memory" / "hiring-desk.json"  # the memory store
OFFLOAD_DIR = LESSON_DIR / "offload"  # oversized tool results

SYSTEM_PROMPT = (
    "You are the resourcing desk assistant for the Data & Analytics business unit.\n"
    "Available tools: open_requisition (set the active requisition), find_candidates "
    "(search the bench by skill), screen_candidate (score and shortlist one person), "
    "shortlist_summary (report the shortlist).\n"
    # The tool list above is a menu, not a checklist. Spell that out: a small model
    # reads four tool names as four steps it is expected to perform, and keeps going
    # long after the user's actual request was satisfied.
    "Do only what the user asked for. As soon as that is done, stop calling tools and "
    "reply in at most two sentences. Do not volunteer the next step.\n"
    "If a tool returns an error, read it and fix the arguments. Never call a tool twice "
    "with the same arguments — if you cannot make it work, say so instead.\n"
    "Never invent a skill level, a match score or an employee id."
)

DOSSIER_PROMPT = SYSTEM_PROMPT + (
    "\nAlso available: candidate_dossier (one candidate's full detail), for when it is asked for."
)


def fresh(path: Path) -> None:
    """Delete a directory tree so a re-run starts from a known state."""
    if path.exists():
        shutil.rmtree(path)


def banner(text: str) -> None:
    print(f"\n--- {text} ---")


# The agent loop *recurses* once per cycle, so a model that keeps calling tools
# does not spin — it walks off Python's stack and you get
# `EventLoopException: maximum recursion depth exceeded`, several hundred model
# calls after the run stopped being useful. A weak local model does this readily:
# screen_candidate(E1148), screen_candidate(E1149), screen_candidate(E1150)...
#
# `limits` is the fix. Caps are checked at the top of each cycle, so tools already
# requested still finish, `agent.messages` is left in a state you can invoke again,
# and you get stop_reason="limit_turns" instead of a traceback.
TURN_CAP = 8
LIMITS = {"turns": TURN_CAP}


def run(agent: Agent, prompt: str, **kwargs):
    """Invoke the agent with a turn cap. Returns the AgentResult."""
    return agent(prompt, limits=LIMITS, **kwargs)


def ask(agent: Agent, prompt: str, **kwargs) -> str:
    """Invoke the agent with a turn cap and return its reply, cap noted if it fired."""
    result = run(agent, prompt, **kwargs)
    reply = str(result).strip()
    if result.stop_reason == "limit_turns":
        return f"[hit the {TURN_CAP}-turn cap — the model was looping] {reply}"
    return reply


# ==========================================================================
# The desk's tools. Five functions; between them they touch every store.
# ==========================================================================


@tool(context=True)
def open_requisition(job_id: str, tool_context: ToolContext) -> str:
    """Set the requisition this conversation is working on.

    Args:
        job_id: e.g. "J2001"
    """
    job = get_job(job_id)
    if job is None:
        return f"No such requisition {job_id!r}."
    # The canonical pattern: the model extracts the id from a sentence, the tool
    # stores it. State is where a fact goes when it must still be exactly right
    # twenty turns later, after the conversation manager has thrown those turns away.
    tool_context.agent.state.set("job_id", job["job_id"])
    return f"Now working {job['job_id']} — {job['title']} in {job['location']}, {job['min_experience_years']}+ years."


@tool(context=True)
def find_candidates(tool_context: ToolContext, skill: str = "", min_level: int = 3) -> str:
    """Find bench employees who have a skill at or above a proficiency level.

    Args:
        skill: Skill name or alias, e.g. "pyspark" or "Apache Spark". Omit to be
            told which skills the open requisition needs.
        min_level: Minimum proficiency, 1 (aware) to 5 (expert). Default 3.
    """
    # `skill` is optional on purpose, and this branch is why. A model working a
    # requisition reaches for find_candidates(job_id=..., location=...) — the
    # frame it is reasoning in — and a required `skill` turns that into a pydantic
    # validation error. The model has no new information after that error, so it
    # reshuffles the same wrong guess until the turn cap fires. A tool whose
    # arguments do not match how the model conceives the task is a loop generator.
    #
    # So: accept the call, and answer it with the one fact that unblocks it.
    if not canonical_skill(skill):
        job = get_job(tool_context.agent.state.get("job_id") or "")
        if job is None:
            return "find_candidates needs a skill name, e.g. skill='Apache Spark'. Open a requisition first."
        wanted = ", ".join(
            f"{req['skill']} (level {req['min_level']}{'+, mandatory' if req['mandatory'] else '+'})"
            for req in job["required_skills"]
        )
        return (
            f"find_candidates searches by skill, not by requisition. {job['job_id']} requires: {wanted}. "
            f"Call find_candidates again with one of those skill names."
        )

    people = employees_with_skill(skill, min_level=min_level, available_only=True)
    if not people:
        return f"Nobody on the bench is at level {min_level}+ in {skill!r}."
    return "\n".join(
        f"{e['employee_id']} {e['name']} — {skill} level {skill_level(e, skill)}, "
        f"{e['location']}, {e['experience_years']} yrs"
        for e in people
    )


@tool(context=True)
def screen_candidate(employee_id: str, tool_context: ToolContext) -> str:
    """Score a candidate against the open requisition and add them to the shortlist.

    Args:
        employee_id: Employee id from find_candidates, e.g. "E1002"
    """
    agent = tool_context.agent

    job_id = agent.state.get("job_id")
    if not job_id:
        return "No requisition is open. Call open_requisition first."

    employee = get_employee(employee_id)
    if employee is None:
        # Directive, not just descriptive. A bare "not found" invites a model to
        # try E1148, E1149, E1150... A tool's error message is a prompt too.
        return (
            f"No employee {employee_id!r} exists. Do not guess employee ids — "
            "call find_candidates and screen only the ids it returned."
        )

    result = match(employee, get_job(job_id))
    if result["blockers"]:
        return (
            f"{result['name']} cannot be shortlisted for {job_id}: "
            f"missing mandatory {', '.join(result['blockers'])}."
        )

    # invocation_state is request scope: the model never sees it, so it cannot
    # leak the recruiter id, confuse it, or be talked into changing it.
    recruiter = tool_context.invocation_state.get("recruiter_id", "unattributed")

    shortlist = agent.state.get("shortlist") or []
    # Idempotent on purpose. A model re-reads its own history and re-acts on it;
    # a tool that writes must survive being called twice with the same argument.
    if any(c["employee_id"] == employee_id for c in shortlist):
        return f"{result['name']} is already on the shortlist ({len(shortlist)} candidate(s))."

    shortlist.append(
        {
            "employee_id": employee_id,
            "name": result["name"],
            "score": result["score"],
            "verdict": result["verdict"],
            "screened_by": recruiter,
        }
    )
    # state.get() hands back a copy, so the mutation above changed nothing until here.
    agent.state.set("shortlist", shortlist)
    return f"Shortlisted {result['name']} at {result['score']}% ({result['verdict']}). {len(shortlist)} on the list."


@tool(context=True)
def shortlist_summary(tool_context: ToolContext) -> str:
    """Report who is currently on the shortlist for the open requisition."""
    shortlist = tool_context.agent.state.get("shortlist") or []
    if not shortlist:
        return "The shortlist is empty."
    job_id = tool_context.agent.state.get("job_id") or "the open requisition"
    lines = ", ".join(f"{c['name']} ({c['score']}%)" for c in shortlist)
    average = sum(c["score"] for c in shortlist) / len(shortlist)
    return f"{len(shortlist)} candidate(s) for {job_id}: {lines}. Average score {average:.0f}%."


@tool
def candidate_dossier(employee_id: str) -> str:
    """Full screening dossier: profile, every rated skill, fit against every open
    requisition, the whole bench for comparison, and the skill catalogue.

    Deliberately enormous. This is the tool result that eats a context window —
    and the reason context management (part 5) exists.

    Args:
        employee_id: e.g. "E1002"
    """
    employee = get_employee(employee_id)
    if employee is None:
        return f"No employee {employee_id!r}."

    out = [
        f"CANDIDATE DOSSIER — {employee['name']} ({employee['employee_id']})",
        f"Designation: {employee['designation']} | Department: {employee['department']}",
        f"Location: {employee['location']} | Availability: {employee['availability']} "
        f"| On bench since: {employee['bench_since'] or 'n/a'}",
        f"Total experience: {employee['experience_years']} years | Contact: {employee['email']}",
        "",
        "RATED SKILLS",
    ]
    for entry in employee["skills"]:
        catalog = next((c for c in SKILLS if c["skill"] == entry["skill"]), {})
        aliases = ", ".join(catalog.get("aliases") or []) or "none"
        out.append(
            f"  - {entry['skill']} [{catalog.get('category', 'uncategorised')}]: "
            f"level {entry['level']}/5 over {entry['years']} years of practice. "
            f"Catalogue aliases: {aliases}."
        )

    out += ["", "FIT AGAINST EVERY OPEN REQUISITION"]
    for job in JOBS:
        scored = match(employee, job)
        out.append(
            f"  {job['job_id']} {job['title']} ({job['location']}, "
            f"{job['min_experience_years']}+ yrs) -> {scored['score']}% {scored['verdict']}"
        )
        for req in job["required_skills"]:
            have = skill_level(employee, req["skill"])
            verdict = "meets" if have >= req["min_level"] else ("BLOCKER" if req["mandatory"] else "gap")
            out.append(
                f"      {req['skill']}: needs level {req['min_level']} "
                f"({'mandatory' if req['mandatory'] else 'optional'}, weight {req['weight']}); "
                f"candidate has {have} -> {verdict}"
            )

    out += ["", f"BENCH COMPARISON against {JOB_ID}"]
    for other in EMPLOYEES:
        scored = match(other, get_job(JOB_ID))
        out.append(
            f"  {other['employee_id']} {other['name']:<22} {other['location']:<10} "
            f"{other['availability']:<10} {scored['score']:>3}% {scored['verdict']}"
        )
        out.append(
            "      rated: "
            + "; ".join(f"{s['skill']} L{s['level']}/{s['years']}y" for s in other["skills"])
        )
        out.append(
            f"      gaps: {', '.join(g['skill'] for g in scored['gaps']) or 'none'} | "
            f"blockers: {', '.join(scored['blockers']) or 'none'} | "
            f"meets experience: {scored['meets_experience']} | same location: {scored['same_location']}"
        )

    out += ["", "SKILL CATALOGUE REFERENCE"]
    for catalog in SKILLS:
        out.append(
            f"  {catalog['skill']} [{catalog['category']}] "
            f"aliases: {', '.join(catalog['aliases']) or 'none'}"
        )

    return "\n".join(out)


# The desk's everyday toolbox. `candidate_dossier` is deliberately NOT in it: a
# tool the model can see is a tool the model will try, and a small local model
# reaches for the most impressive-sounding one. Parts 5 and 8, which are about
# oversized results, load it explicitly.
DESK_TOOLS = [open_requisition, find_candidates, screen_candidate, shortlist_summary]
DOSSIER_TOOLS = [*DESK_TOOLS, candidate_dossier]


def build_agent(**overrides) -> Agent:
    """The desk assistant. Every part overrides exactly the one thing it teaches."""
    kwargs = dict(
        model=make_model(),
        agent_id="resourcing-desk",  # part of every persistence key — keep it stable
        system_prompt=SYSTEM_PROMPT,
        tools=DESK_TOOLS,
        callback_handler=None,  # silence the default printer; we print our own output
    )
    kwargs.update(overrides)
    return Agent(**kwargs)


# ==========================================================================
# Part 1 — State: three stores, three lifetimes, one audience
# ==========================================================================
def part_1() -> None:
    """messages, state, invocation_state. Only the first is visible to the model."""
    agent = build_agent(state={"business_unit": "Data & Analytics", "max_shortlist": 3})

    banner("1. Seeded at construction — config the desk starts with")
    print("state at boot        :", agent.state.get())

    banner("2. Written by a tool — the model extracts, the tool stores")
    result = run(agent, "We're filling J2001. Open it.")
    reply = str(result).strip()
    if result.stop_reason == "limit_turns":
        reply = f"[hit the {TURN_CAP}-turn cap — the model was looping] {reply}"
    print("answer               :", reply)
    print("Tools called         :", list(result.metrics.tool_metrics.keys()))
    print("job_id in state      :", agent.state.get("job_id"))

    banner("3. invocation_state — request scope, invisible to the model")
    result = run(
        agent,
        "Screen E1002 for the open requisition.",
        invocation_state={"tenant_id": "acme-prod", "recruiter_id": "R-8812"},
    )
    print("answer               :", str(result).strip())
    print("shortlist in state   :", agent.state.get("shortlist"))
    print("attributed to        : R-8812 — a value the model never saw and cannot invent")
    print("Tools called         :", list(result.metrics.tool_metrics.keys()))

    banner("4. The three stores side by side")
    print("agent.messages       :", len(agent.messages), "messages  <- the model reads THIS")
    print("agent.state keys     :", sorted(agent.state.get()))
    print("invocation_state     : gone — it lived for exactly one call")

    banner("5. Two rules that bite")
    agent.state.get("shortlist").append({"employee_id": "E9999", "name": "Nobody"})
    print("state.get() is a copy:", [c["employee_id"] for c in agent.state.get("shortlist")], "<- E9999 never landed")
    try:
        agent.state.set("seen", {"E1002", "E1003"})  # a set is not JSON
    except ValueError as exc:
        print("state validates JSON :", type(exc).__name__, "— because state is what gets persisted")


# ==========================================================================
# Part 2 — Storage: the byte layer every other part sits on
# ==========================================================================
async def part_2_async() -> None:
    fresh(BLOBS_DIR)

    banner("1. Four operations. That is the entire interface")
    root = LocalFileStorage(str(BLOBS_DIR))
    for job_id in (JOB_ID, "J2003"):
        await root.write(f"shortlists/{job_id}.json", json.dumps(rank_candidates(job_id, limit=3)).encode())
    await root.write("audit/2026-08-12.log", b"R-8812 opened J2001")

    top = json.loads(await root.read(f"shortlists/{JOB_ID}.json"))[0]
    print("  write + read  ->", f"{top['name']} {top['score']}% {top['verdict']}")
    print("  missing key   ->", await root.read("shortlists/J9999.json"), "(None, not an exception)")
    print("  list prefix   ->", await root.list("shortlists/"))
    await root.delete("shortlists/J2003.json")
    print("  after delete  ->", await root.list("shortlists/"))

    banner("2. Namespaces — one deployment, many hiring desks")
    memory_only = InMemoryStorage()
    data_bu = memory_only.namespace("bu/data-analytics")
    platform_bu = memory_only.namespace("bu/platform")
    await data_bu.write("open_reqs.json", b'["J2001","J2003"]')
    await platform_bu.write("open_reqs.json", b'["J2005"]')
    print("  data BU sees    :", await data_bu.list(""))
    print("  platform BU sees:", await platform_bu.list(""))
    print("  root sees       :", await memory_only.list(""), "<- and neither desk can list the other's")

    banner("3. The same interface, three subsystems")
    print("  SnapshotSessionManager(storage=...)  sessions and checkpoints")
    print("  ContextOffloader(storage=...)        oversized tool results")
    print("  TestMemoryStore(path=...)            long-term memory")
    print("  Swap LocalFileStorage for S3Storage and all three move to S3. No other change.")

    print("\n  on disk under", BLOBS_DIR)
    for path in sorted(BLOBS_DIR.rglob("*")):
        if path.is_file():
            print("   ", path.relative_to(BLOBS_DIR))


def part_2() -> None:
    """Storage is bytes in, bytes out — and it is underneath everything that follows."""
    asyncio.run(part_2_async())


# ==========================================================================
# Part 3 — Session: the conversation survives the process
# ==========================================================================
def part_3() -> None:
    """Two agent objects, one session id. The second wakes up remembering.

    A requisition stays open for weeks. This is what makes "where were we on
    J2001?" a question the desk can answer on Thursday.
    """
    store_dir = SESSIONS_DIR / "part3"
    fresh(store_dir)  # so a re-run of this part starts from an empty session

    banner("Run A — Monday")
    first = build_agent(
        state={"business_unit": "Data & Analytics"},
        session_manager=FileSessionManager(session_id="req-J2001", storage_dir=str(store_dir)),
    )
    print("  messages at boot :", len(first.messages))
    print("  ->", ask(first, "We're filling J2001. Open it, then screen E1002."))
    print("  messages after   :", len(first.messages))
    print("  state after      :", first.state.get())

    banner("Run B — Thursday, brand new process")
    # A new manager instance and a new Agent object: this is what a redeploy looks
    # like. Same session_id + same agent_id is the whole trick.
    second = build_agent(
        session_manager=FileSessionManager(session_id="req-J2001", storage_dir=str(store_dir)),
    )
    print("  messages at boot :", len(second.messages), " <- restored from disk")
    print("  state at boot    :", second.state.get(), " <- restored too")
    result = run(second, "Remind me which requisition is open and who is already shortlisted.")
    print("  ->", str(result).strip())
    print("  tools called     :", list(result.metrics.tool_metrics.keys()))
    banner("What the session wrote")
    files = sorted(p for p in store_dir.rglob("*") if p.is_file())
    print("  files:", len(files))
    for path in files[:6]:
        print("   ", path.relative_to(store_dir))
    print("  ... one file per message, plus agent.json holding state and conversation-manager state.")


# ==========================================================================
# Part 4 — Conversation management: what survives a full context window
# ==========================================================================
# The first two turns carry the constraints. The last turn needs them back.
# Everything between is the noise a real screening session generates.
SCREENING_TURNS = [
    "I'm Naveen, hiring manager for requisition J2001, Senior Data Engineer in Bengaluru.",
    "Hard rule for this req: Spark level 4 minimum, and the person must be on the bench today.",
    "Priya Raman scored 100% — Spark 5, Python 4, SQL 5, Airflow 4.",
    "Rahul Menon scored 61% — his Spark is only level 3 and he has no Airflow.",
    "Vikram Iyer is Chennai-based with Kafka 4 but no SQL on record.",
    "Remind me: which requisition am I filling, and what was my hard rule?",
]


def _run_screening(label: str, agent: Agent) -> None:
    print(f"\n  {label}")
    result = None
    for turn in SCREENING_TURNS:
        result = run(agent, turn)
    print(f"    messages kept : {len(agent.messages)}")
    print(f"    tools called    : {list(result.metrics.tool_metrics.keys())}")
    print(f"    context size  : {result.context_size} tokens")
    print(f"    can it answer : {str(result).strip()[:150]}")


def part_4() -> None:
    """The conversation manager decides which messages the model still gets to read.

    It runs on `agent.messages` before every model call. It is the only one of the
    seven that *destroys* context rather than moving it somewhere.
    """
    system = "You are a resourcing assistant. Answer in one sentence."

    banner("Null — nothing is ever dropped, full audit fidelity, eventual overflow")
    _run_screening(
        "NullConversationManager()",
        Agent(
            model=make_model(),
            system_prompt=system,
            conversation_manager=NullConversationManager(),
            callback_handler=None,
        ),
    )

    banner("Sliding window — cheap, and forgetful in exactly the wrong direction")
    _run_screening(
        "SlidingWindowConversationManager(window_size=4)",
        Agent(
            model=make_model(),
            system_prompt=system,
            conversation_manager=SlidingWindowConversationManager(window_size=4),
            callback_handler=None,
        ),
    )

    banner("Sliding window + pin_first — the constraints are what you pin")
    # pin_first counts MESSAGES, not turns. One turn is a user message plus an
    # assistant message (more, once tools are involved), so the two opening turns
    # that carry the requisition and the hard rule are pin_first=4, not 2. Pin 2
    # and the hard rule — stated in turn two — is trimmed like anything else.
    #
    # Expect "all messages in trim range are pinned, unable to reduce" in the log,
    # and a final count above window_size. That is the trade, not a bug: pinning
    # 4 of a 6-message window leaves only 2 messages the trimmer is allowed to
    # reclaim. Pin a smaller fraction of a larger window in production.
    _run_screening(
        "SlidingWindowConversationManager(window_size=6, pin_first=4)",
        Agent(
            model=make_model(),
            system_prompt=system,
            conversation_manager=SlidingWindowConversationManager(window_size=6, pin_first=4),
            callback_handler=None,
        ),
    )

    banner("Summarizing — old turns compressed, not deleted. Costs an extra model call")
    _run_screening(
        "SummarizingConversationManager(summary_ratio=0.5, preserve_recent_messages=2)",
        Agent(
            model=make_model(),
            system_prompt=system,
            conversation_manager=SummarizingConversationManager(
                summary_ratio=0.5,
                preserve_recent_messages=2,
                proactive_compression={"compression_threshold": 0.6},
            ),
            callback_handler=None,
        ),
    )

    print("\n  The shortlist itself belongs in agent.state, not in the transcript.")
    print("  Nothing above can lose a fact that lives in state.")


# ==========================================================================
# Part 5 — Context management: the strategy, not just the trimmer
# ==========================================================================
def part_5() -> None:
    """`context_manager="auto"` = summarizing conversation manager + context offloader.

    The conversation manager shrinks the dialogue. The offloader intercepts the
    other half of the problem: one tool result so big it does not fit at all.
    """
    fresh(OFFLOAD_DIR)

    dossier = candidate_dossier("E1002")
    print(f"  candidate_dossier('E1002') returns {len(dossier):,} chars (~{len(dossier) // 4:,} tokens)")
    print("  'auto' offloads any tool result over 1,500 tokens.\n")

    agent = build_agent(
        system_prompt=DOSSIER_PROMPT,
        tools=DOSSIER_TOOLS,
        context_manager="auto",
        # Durable offload storage. The offloader "auto" builds for you is
        # in-memory, which is fine until you pair it with a session and restart.
        # Supplying your own means supplying its thresholds too: a bare
        # ContextOffloader() defaults to 2,500 tokens, not the 1,500 "auto" uses,
        # and this dossier would sail straight through.
        plugins=[
            ContextOffloader(
                storage=LocalFileStorage(str(OFFLOAD_DIR)),
                max_result_tokens=1_500,
                preview_tokens=750,
            )
        ],
        state={"job_id": JOB_ID},
    )
    print("  tools the model can see:", agent.tool_names)
    print("  ^ retrieve_offloaded_content was injected by the offloader, not written by us.\n")

    result = run(agent, "Pull the full dossier for E1002, then tell me in one sentence whether they fit J2001.")
    print("  answer      :", str(result).strip())
    print("  context size:", result.context_size, "tokens")

    offloaded = [
        block["toolResult"]
        for message in agent.messages
        for block in message["content"]
        if isinstance(block, dict) and "toolResult" in block
    ]
    for tool_result in offloaded:
        text = "".join(c.get("text", "") for c in tool_result["content"])
        if text.startswith("[Offloaded:"):
            print("\n  what the model actually got instead of 8,000 characters:")
            for line in text.splitlines()[:3]:
                print("   ", line)
            print("    ...")

    print("\n  offloaded blobs on disk:")
    for path in sorted(p for p in OFFLOAD_DIR.rglob("*") if p.is_file())[:5]:
        print("   ", path.relative_to(OFFLOAD_DIR), f"({path.stat().st_size:,} bytes)")
    print("  The full text is one tool call away, and costs nothing until the model asks.")


# ==========================================================================
# Part 6 — Memory: what outlives the requisition
# ==========================================================================
def _memory_store() -> TestMemoryStore:
    """One JSON file on disk. Swap for BedrockKnowledgeBaseStore and nothing else changes."""
    return TestMemoryStore(
        name="hiring-desk",
        description="Standing rules, manager preferences and past hiring decisions for this desk.",
        path=str(MEMORY_FILE),
    )


def _memory_entries() -> list[str]:
    """Read the store's file directly, so the demo can show ground truth."""
    if not MEMORY_FILE.exists():
        return []
    return [record["content"] for record in json.loads(MEMORY_FILE.read_text())]


def part_6() -> None:
    """A session remembers one conversation. Memory remembers the desk.

    Sessions are keyed by conversation, so the rule a manager stated on J2001 is
    invisible the moment you open J2004. Memory is the store that is not.
    """
    fresh(MEMORY_FILE.parent)

    banner("1. Two facts worth keeping past the requisition")
    store = _memory_store()
    for fact in (
        "Hiring manager Naveen (Data & Analytics) never accepts a candidate below 6 years "
        "of total experience for J2001-class senior roles, whatever the match score says.",
        "Naveen will not approach employees who are allocated to a project without their "
        "own manager's written sign-off.",
    ):
        asyncio.run(store.add(fact, metadata={"source": "J2001 kickoff", "recruiter": "R-8812"}))
    print("  entries on disk:", len(_memory_entries()))
    print("  file           :", MEMORY_FILE)

    banner("2. Retrieval, with no agent involved")
    manager = MemoryManager(stores=[store], add_tool_config=True)
    hits = asyncio.run(manager.search("experience floor for senior roles"))
    for entry in hits:
        print(f"  [{entry.store_name}] {entry.content[:90]}...")

    banner("3. A different requisition, a different session — the rule still arrives")
    # Nothing below mentions J2001. Injection searches the store with the user's
    # turn and folds the hits into the model input, without touching agent.messages.
    question = (
        f"I'm opening {NEXT_JOB_ID}, ML Engineer in Bengaluru. Any standing rules about "
        "candidate experience or approaching allocated employees before I screen anyone?"
    )
    # Injection's default query is the latest user message, so this is exactly
    # what it is about to retrieve. Proving the layer, not the model's manners.
    print("  injection will retrieve:")
    for entry in asyncio.run(manager.search(question)):
        print("   ", entry.content[:100], "...")

    agent = build_agent(
        memory_manager=MemoryManager(stores=[_memory_store()], add_tool_config=True),
        state={"job_id": NEXT_JOB_ID},
    )
    print("  ->", ask(agent, question))
    print("  durable messages:", len(agent.messages), "— the injected memory is not one of them")

    banner("4. Automatic extraction — the desk writing its own notes")
    # extraction on a store that implements add() runs a ModelExtractor: one model
    # call that distils the turn into facts. Fidelity tracks the model you gave it.
    watching_store = TestMemoryStore(
        name="hiring-desk",
        description="Standing rules and manager preferences for this desk.",
        path=str(MEMORY_FILE),
        extraction={"trigger": IntervalTrigger(turns=1)},
    )
    before = len(_memory_entries())
    noting_agent = build_agent(
        memory_manager=MemoryManager(stores=[watching_store]),
        state={"job_id": JOB_ID},
    )
    run(
        noting_agent,
        "One more standing rule for my requisitions: I only interview candidates based in "
        "Bengaluru or Hyderabad, because the team is co-located.",
    )
    after = _memory_entries()
    print(f"  entries {before} -> {len(after)}")
    for content in after[before:]:
        print("   +", content[:120])
    if len(after) == before:
        print("   (the extractor found nothing worth storing — small local models are unreliable here)")


# ==========================================================================
# Part 7 — Snapshots: a point in time you can go back to
# ==========================================================================
def part_7() -> None:
    """take_snapshot / load_snapshot is undo. A snapshot is also the unit a
    SnapshotSessionManager persists, which is what makes undo survive a restart."""

    banner("1. Branch and rewind, in memory")
    agent = build_agent(state={"job_id": JOB_ID})
    run(agent, "Screen E1002 for J2001.")
    checkpoint = agent.take_snapshot(preset="session", app_data={"label": "before-widening-search"})
    print("  snapshot taken at", len(agent.messages), "messages;", sorted(checkpoint.data), "captured")

    run(agent, "Actually, widen it: any location, and drop the experience floor to 3 years.")
    print("  after the branch  :", len(agent.messages), "messages")

    agent.load_snapshot(checkpoint)
    print("  after the rewind  :", len(agent.messages), "messages — the widening never happened")
    print("  app_data survived :", checkpoint.app_data)

    banner("2. A snapshot is plain JSON — hand the screening to someone else")
    blob = json.dumps(agent.take_snapshot(preset="session").to_dict())
    print("  serialized bytes  :", f"{len(blob):,}")
    receiving = build_agent()
    receiving.load_snapshot(Snapshot.from_dict(json.loads(blob)))
    print("  restored into a brand new agent:", len(receiving.messages), "messages,", receiving.state.get())
    print("  slim snapshot fields:", sorted(agent.take_snapshot(include=["state", "system_prompt"]).data))

    banner("3. Time travel through an addressable history")
    asyncio.run(_part_7_time_travel())


async def _part_7_time_travel() -> None:
    manager = SnapshotSessionManager(
        session_id="req-J2001-screening",
        storage=LocalFileStorage(str(BLOBS_DIR)),
        save_latest_on="invocation",
    )
    agent = build_agent(session_manager=manager, state={"job_id": JOB_ID})

    ids: list[str] = []
    for decision in (
        "Priya Raman (E1002) — 100% match, invite to interview.",
        "Rahul Menon (E1003) — Spark one level short, hold.",
        "Vikram Iyer (E1005) — no SQL on record, reject.",
    ):
        await agent.invoke_async(f"Record this screening decision verbatim: {decision}", limits=LIMITS)
        ids.append(await manager.save_snapshot(agent, is_latest=False))  # an explicit checkpoint

    print("  checkpoints stored:", len(await manager.list_snapshot_ids(agent)))
    print("  messages now      :", len(agent.messages))

    await manager.restore_snapshot(agent, snapshot_id=ids[0])
    print("  restored #1       :", len(agent.messages), "messages — the last two decisions are undone")
    # restore_snapshot loads into the object; it does not move `snapshot_latest`.
    # Without this line the next restart would come back holding all three decisions.
    await manager.save_snapshot(agent, is_latest=True)
    print("  latest re-pointed at the restored state, so the rewind survives a restart")

    await manager.delete_session()
    print("  session deleted")


# ==========================================================================
# Part 8 — All seven, one desk, across a week
# ==========================================================================
def desk_session(session_id: str) -> SnapshotSessionManager:
    """One requisition's session: a `snapshot_latest` blob plus a checkpoint history.

    Held separately from the agent because `Agent` does not hand its session
    manager back, and the checkpoint API (save_snapshot / restore_snapshot) lives
    on the manager.
    """
    return SnapshotSessionManager(
        session_id=session_id,
        storage=LocalFileStorage(str(BLOBS_DIR)),  # STORAGE — where the blob lands
        save_latest_on="invocation",
    )


def desk_agent(session: SnapshotSessionManager) -> Agent:
    """The production wiring. Every layer of the lesson, in one constructor call."""
    return Agent(
        model=make_model(),
        agent_id="resourcing-desk",
        system_prompt=DOSSIER_PROMPT,
        tools=DOSSIER_TOOLS,
        # STATE — config the desk starts every conversation with.
        state={"business_unit": "Data & Analytics", "max_shortlist": 3},
        # SESSION + SNAPSHOT — the whole agent as one versioned blob.
        session_manager=session,
        # MEMORY — a store keyed by the desk, not by the conversation. A fresh
        # object each time; the JSON file behind it is the thing that persists.
        memory_manager=MemoryManager(stores=[_memory_store()], add_tool_config=True),
        # CONTEXT MANAGEMENT — summarizing conversation manager + offloader...
        context_manager="auto",
        # ...with the offloader's bytes on disk, because this agent has a session
        # and the "auto" default offloader is in-memory only. Its thresholds are
        # restated because supplying the plugin means supplying its config.
        plugins=[
            ContextOffloader(
                storage=LocalFileStorage(str(OFFLOAD_DIR)),
                max_result_tokens=1_500,
                preview_tokens=750,
            )
        ],
        callback_handler=None,
    )


def part_8() -> None:
    """Monday morning to next Tuesday, on one desk."""
    fresh(LESSON_DIR)
    session_id = "desk-J2001"

    banner("Monday — open the requisition and screen the bench")
    monday_session = desk_session(session_id)
    monday = desk_agent(monday_session)
    print("  messages at boot:", len(monday.messages), "(new session)")
    # invocation_state rides along on every turn: the desk's identity, attached to
    # each screening, never visible to the model. Look for it in `screened_by` below.
    desk = {"tenant_id": "acme-prod", "recruiter_id": "R-8812"}
    print("  >", ask(monday, "We're filling J2001. Open it, then screen E1002.", invocation_state=desk))
    print("  >", ask(monday, "Screen E1003 as well.", invocation_state=desk))
    print("  state:", json.dumps(monday.state.get()))

    banner("Monday — a 2,000-token dossier the context window never has to hold")
    print("  >", ask(monday, "Pull the full candidate dossier for E1002."))
    print("  offloaded blobs:", len([p for p in OFFLOAD_DIR.rglob("*") if p.is_file()]))

    banner("Monday — a standing rule, which belongs to the desk and not to J2001")
    rule = (
        "Hiring manager Naveen never accepts a candidate below 6 years of total experience "
        "for senior data roles, whatever the match score says."
    )
    print("  >", ask(monday, f"Remember this for every requisition I open: {rule}"))
    if not _memory_entries():
        # Whether the model reaches for add_memory is a model-quality question, and
        # a small local one often will not. The lesson is the layer, not the model,
        # so write the rule directly and say out loud that that is what happened.
        asyncio.run(_memory_store().add(rule, metadata={"source": "J2001 kickoff", "recruiter": "R-8812"}))
        print("  (the model skipped add_memory; wrote the rule directly instead)")
    print("  memory entries:", len(_memory_entries()))

    banner("Monday afternoon — a checkpoint, an experiment, and an undo")
    checkpoint_id = asyncio.run(monday_session.save_snapshot(monday, is_latest=False))
    print("  checkpoint:", checkpoint_id)
    print("  >", ask(monday, "Widen the search: any location, and drop the experience floor to 3 years."))
    print("  messages after the experiment:", len(monday.messages))
    asyncio.run(monday_session.restore_snapshot(monday, snapshot_id=checkpoint_id))
    # Without this the rewind lives only in the object: `snapshot_latest` still
    # points at the widened state, and Tuesday would boot holding the experiment.
    asyncio.run(monday_session.save_snapshot(monday, is_latest=True))
    print("  messages after the undo      :", len(monday.messages))

    banner("Tuesday — new process, same requisition")
    tuesday = desk_agent(desk_session(session_id))  # a redeploy: new objects, same session id
    print("  messages at boot:", len(tuesday.messages), "<- restored, minus the experiment")
    print("  state at boot   :", json.dumps(tuesday.state.get()))
    print("  >", ask(tuesday, "Where were we? Summarise the shortlist."))

    banner("Next week — a different requisition, a different session")
    # A brand new session id: the conversation above is gone. What is not gone is
    # the desk's memory, which is keyed by the store, not by the session.
    next_week = desk_agent(desk_session("desk-J2004"))
    print("  messages at boot:", len(next_week.messages), "(new session — J2001's conversation is unreachable)")
    # TestMemoryStore ranks by keyword overlap, so a question that shares the
    # rule's vocabulary retrieves it. A semantic store (BedrockKnowledgeBaseStore)
    # is what removes that constraint.
    question = (
        f"I'm opening {NEXT_JOB_ID}, ML Engineer in Bengaluru. Before I screen any candidate, "
        "what does this desk expect on total experience for senior roles?"
    )
    print("  injection will retrieve:")
    for entry in asyncio.run(_memory_store().search(question)):
        print("   ", entry.content[:100], "...")
    print("  >", ask(next_week, question))

    banner("What survived what")
    print(f"  {'layer':<22}{'scope':<26}{'proof'}")
    print(f"  {'-' * 22}{'-' * 26}{'-' * 30}")
    screened_by = {c["screened_by"] for c in (tuesday.state.get("shortlist") or [])} or {"—"}
    print(f"  {'invocation_state':<22}{'one call':<26}gone, but attributed the shortlist to {screened_by}")
    print(f"  {'agent.state':<22}{'the conversation':<26}{json.dumps(sorted(tuesday.state.get()))}")
    print(f"  {'conversation manager':<22}{'the context window':<26}{len(tuesday.messages)} messages carried forward")
    blobs = len([p for p in OFFLOAD_DIR.rglob("*") if p.is_file()])
    print(f"  {'context offloader':<22}{'the context window':<26}{blobs} offloaded blob(s) on disk")
    print(f"  {'session + snapshot':<22}{'the requisition':<26}restored into a new process")
    print(f"  {'memory':<22}{'the desk':<26}{len(_memory_entries())} entry(s), readable from any session")
    print(f"  {'storage':<22}{'everything above':<26}{LESSON_DIR}")
    print(f"\n  Reset with:  rm -rf {LESSON_DIR}")


PARTS = {
    1: ("State — three stores, three lifetimes", part_1),
    2: ("Storage — the byte layer underneath", part_2),
    3: ("Session — surviving the process", part_3),
    4: ("Conversation management — surviving the context window", part_4),
    5: ("Context management — summarize the dialogue, offload the payload", part_5),
    6: ("Memory — outliving the requisition", part_6),
    7: ("Snapshots — a point in time you can return to", part_7),
    8: ("End to end — one desk, one week, all seven", part_8),
}


def main() -> None:
    # Arguments win when given (`main.py 3 4`), so the lesson stays scriptable.
    # With none, ask — a bare `main.py` is interactive. Blank answer runs them all.
    wanted = [int(arg) for arg in sys.argv[1:]]
    if not wanted:
        answer = input(f"Enter part {sorted(PARTS)}, or blank for all: ")
        wanted = [int(token) for token in answer.replace(",", " ").split()] or sorted(PARTS)
    for number in wanted:
        if number not in PARTS:
            print(f"No part {number}. Available: {sorted(PARTS)}")
            continue
        title, func = PARTS[number]
        print(f"\n{'=' * 78}\nPART {number} — {title}\n{'=' * 78}")
        func()


if __name__ == "__main__":
    main()
