"""11 · Session management — a hiring conversation that survives the process.

A requisition stays open for weeks. The recruiter closes the laptop mid-screening
and comes back on Thursday. `session_id` is the requisition; everything else is
plumbing.

Run it TWICE. The second run remembers the first.

    uv run app/11_session_management/main.py
    uv run app/11_session_management/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, ToolContext, tool
from strands.session import FileSessionManager, SnapshotSessionManager
from strands.storage import LocalFileStorage

from _shared import make_model, rank_candidates, settings

# The session key IS the business key. One conversation per open requisition.
SESSION_ID = "req-J2001"


@tool(context=True)
def note_constraint(topic: str, value: str, tool_context: ToolContext) -> str:
    """Record a hiring constraint the manager gave us, so later runs honour it.

    Args:
        topic: What it is about, e.g. "location" or "notice_period"
        value: The constraint, e.g. "Bengaluru or Chennai only"
    """
    constraints = tool_context.agent.state.get("constraints") or {}
    constraints[topic] = value
    tool_context.agent.state.set("constraints", constraints)
    return f"Recorded {topic}={value}"


@tool
def shortlist_for(job_id: str) -> str:
    """Rank the available candidates for a requisition.

    Args:
        job_id: e.g. "J2001"
    """
    return "\n".join(
        f"{r['employee_id']} {r['name']}: {r['score']}% {r['verdict']}"
        for r in rank_candidates(job_id, limit=3)
    ) or f"no requisition {job_id}"


def build_agent(session_manager) -> Agent:
    return Agent(
        model=make_model(),
        agent_id="resourcing-desk",  # part of the storage key — keep it stable
        system_prompt=(
            "You are a resourcing desk assistant working requisition J2001. "
            "Record any hiring constraint the manager states."
        ),
        tools=[note_constraint, shortlist_for],
        session_manager=session_manager,
        callback_handler=None,
    )


def demo_message_log() -> None:
    """FileSessionManager: every message written as its own file."""
    print("=== 1. FileSessionManager (message log) ===")
    agent = build_agent(FileSessionManager(session_id=SESSION_ID, storage_dir=str(settings.sessions_dir)))

    print("  restored messages:", len(agent.messages))
    print("  restored state   :", agent.state.get())

    if not agent.messages:
        print("  (first run — seeding the conversation)")
        agent(
            "I'm Naveen, hiring manager for J2001. Note that this role is Bengaluru-only, "
            "and I will not consider anyone below level 4 in Spark."
        )
    else:
        print("  (returning run — asking it to recall)")
        print("  ->", str(agent("Remind me: who am I, and what constraints did I set on J2001?")).strip())

    print("  messages now:", len(agent.messages))
    print("  state now   :", agent.state.get(), "\n")


def demo_snapshot_session() -> None:
    """SnapshotSessionManager: the whole agent as one versioned blob."""
    print("=== 2. SnapshotSessionManager (single blob + history) ===")
    agent = build_agent(
        SnapshotSessionManager(
            session_id=f"{SESSION_ID}-snap",
            storage=LocalFileStorage(str(settings.storage_dir)),
            save_latest_on="invocation",  # "message" | "invocation" | "trigger"
            # Append an immutable checkpoint whenever the conversation passes 4 messages.
            snapshot_trigger=lambda agent_data, **_: len(agent_data.messages) > 4,
        )
    )

    print("  restored messages:", len(agent.messages))
    agent("Also note that we can accept a 60-day notice period for the right person.")
    print("  messages now:", len(agent.messages))
    print("  state now   :", agent.state.get(), "\n")


def show_layout() -> None:
    print("=== 3. What landed on disk ===")
    for root in (settings.sessions_dir, settings.storage_dir):
        if not root.exists():
            continue
        print(f"  {root}")
        for path in sorted(root.rglob("*"))[:14]:
            if path.is_file():
                print("    ", path.relative_to(root))


def main() -> None:
    demo_message_log()
    demo_snapshot_session()
    show_layout()
    print(f"\nReset everything with:  rm -rf {settings.run_dir}")


if __name__ == "__main__":
    main()
