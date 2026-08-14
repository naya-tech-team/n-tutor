"""12 · Snapshots — save a point in time, rewind to it later.

The recruiting version of undo: "widen the search to remote" is a branch you may
want to abandon without losing the twenty minutes of screening before it.

Run:  uv run app/12_snapshots/main.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, Snapshot
from strands.session import SnapshotSessionManager
from strands.storage import LocalFileStorage

from _shared import make_model, settings


def new_agent(session_manager=None) -> Agent:
    return Agent(
        model=make_model(),
        agent_id="shortlist-builder",
        system_prompt="You help build a hiring shortlist. Keep replies to one short sentence.",
        session_manager=session_manager,
        callback_handler=None,
    )


def demo_in_memory_branching() -> None:
    """take_snapshot / load_snapshot: fork a shortlist, then undo the fork."""
    print("=== 1. Branch and rewind, in memory ===")
    agent = new_agent()

    agent("We're screening for J2001, Senior Data Engineer in Bengaluru.")
    checkpoint = agent.take_snapshot(preset="session", app_data={"label": "before-widening-search"})
    print("  snapshot taken at", len(agent.messages), "messages")

    agent("Actually, open it up to any location and drop the experience floor to 3 years.")
    print("  after branch     :", len(agent.messages), "messages")

    # The manager changed their mind back. Rewind — the widened search never happened.
    agent.load_snapshot(checkpoint)
    print("  after rewind     :", len(agent.messages), "messages")
    print("  app_data survived:", checkpoint.app_data, "\n")


def demo_serialize() -> None:
    """A Snapshot is plain JSON — put it in Postgres, Redis, a queue, anywhere."""
    print("=== 2. Snapshot is JSON ===")
    agent = new_agent()
    agent("Shortlist E1002 for J2001.")

    blob = json.dumps(agent.take_snapshot(preset="session").to_dict())
    print("  serialized bytes:", len(blob))

    # This is how a screening handed from a recruiter to a hiring manager moves
    # between processes: one row in a table, rehydrated into a fresh agent.
    restored = new_agent()
    restored.load_snapshot(Snapshot.from_dict(json.loads(blob)))
    print("  restored into a brand new agent:", len(restored.messages), "messages")

    # Pick exactly what travels.
    slim = agent.take_snapshot(include=["state", "system_prompt"])
    print("  fields in a slim snapshot:", sorted(slim.data), "\n")


async def demo_time_travel() -> None:
    """Immutable history: every checkpoint is addressable and restorable."""
    print("=== 3. Time travel through session history ===")
    manager = SnapshotSessionManager(
        session_id="req-J2001-screening",
        storage=LocalFileStorage(str(settings.storage_dir)),
        save_latest_on="invocation",
    )
    agent = new_agent(manager)

    decisions = [
        "Priya Raman (E1002) — 100% match, invite to interview.",
        "Rahul Menon (E1003) — Spark is one level short, hold.",
        "Vikram Iyer (E1005) — no SQL on record, reject.",
    ]
    ids: list[str] = []
    for decision in decisions:
        await agent.invoke_async(f"Record this screening decision: {decision}")
        ids.append(await manager.save_snapshot(agent, is_latest=False))  # explicit checkpoint

    print("  checkpoints:", len(await manager.list_snapshot_ids(agent)))
    print("  messages now:", len(agent.messages))

    # "Undo the last two decisions" — restore the checkpoint after the first one.
    await manager.restore_snapshot(agent, snapshot_id=ids[0])
    print("  after restoring checkpoint #1:", len(agent.messages), "messages")

    await manager.delete_session()
    print("  session deleted\n")


async def main() -> None:
    demo_in_memory_branching()
    demo_serialize()
    await demo_time_travel()


if __name__ == "__main__":
    asyncio.run(main())
