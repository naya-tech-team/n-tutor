"""16 · Plugins — ship hooks + tools + setup as one installable unit.

Every agent that touches employee data in this company needs the same three
things: a cap on how many profiles it may open, an audit trail, and a way to
report its remaining quota. That is a plugin, not sixteen copy-pasted hooks.

Run:  uv run app/16_plugins/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, tool
from strands.hooks import (
    AfterInvocationEvent,
    AfterNodeCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
)
from strands.multiagent import GraphBuilder
from strands.plugins import MultiAgentPlugin, Plugin, hook

from _shared import get_employee, make_model


class ProfileAccessGuard(Plugin):
    """A complete, reusable capability: hooks, a tool, an audit trail and state.

    Drop it into any agent with `plugins=[ProfileAccessGuard(max_profiles=3)]`.
    The cap exists because "screen the whole company" is how a helpful assistant
    turns into a bulk PII export.
    """

    name = "profile-access-guard"  # stable id — required

    def __init__(self, max_profiles: int = 3) -> None:
        self.max_profiles = max_profiles
        self.opened: list[str] = []
        self.blocked: list[str] = []
        super().__init__()  # discovers @hook and @tool methods — call this LAST

    # --- hooks: auto-registered, event type read from the type hint ---------

    @hook
    def _reset(self, event: BeforeInvocationEvent) -> None:
        self.opened.clear()
        self.blocked.clear()

    @hook
    def _enforce(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] != "read_profile":
            return

        # Count PEOPLE, not calls. A model will cheerfully pass a list of five ids
        # to a single-id tool, and a quota that counts invocations reads that as
        # one lookup. Count the thing the policy is actually about.
        raw = event.tool_use["input"].get("employee_id", "?")
        ids = [str(i) for i in raw] if isinstance(raw, list) else [str(raw)]

        # Charge BEFORE the call, not after. Tools in one turn run concurrently,
        # so every Before hook fires before the first After hook — a counter that
        # only increments on AfterToolCallEvent is always zero when it is checked.
        if len(self.opened) + len(ids) > self.max_profiles:
            self.blocked.extend(ids)
            event.cancel_tool = (
                f"Profile access limit of {self.max_profiles} reached for this request "
                f"({len(self.opened)} already opened, {len(ids)} requested). "
                "Summarise what you already know and stop looking people up."
            )
            return
        self.opened.extend(ids)

    @hook
    def _reconcile(self, event: AfterToolCallEvent) -> None:
        # Where you would write the real audit row, with the tool's actual outcome.
        pass

    @hook
    def _report(self, event: AfterInvocationEvent) -> None:
        print(f"  [access-guard] opened={self.opened} blocked={self.blocked}")

    # --- tools: also auto-registered, and they can read plugin state --------

    @tool
    def remaining_profile_quota(self) -> str:
        """Report how many more employee profiles may be opened in this request."""
        return f"{max(0, self.max_profiles - len(self.opened))} profile lookups remaining"

    # --- optional: custom setup when the plugin attaches to an agent --------

    def init_agent(self, agent: Agent) -> None:
        agent.state.set("profile_access_cap", self.max_profiles)


class NodeTimer(MultiAgentPlugin):
    """The orchestrator-level counterpart. Hooks only — no tool registry up here."""

    name = "node-timer"

    def __init__(self) -> None:
        self.t0 = 0.0
        super().__init__()

    @hook
    def _done(self, event: AfterNodeCallEvent) -> None:
        print(f"  [node-timer] node '{event.node_id}' finished")


@tool
def read_profile(employee_id: str) -> str:
    """Read one employee's skill profile.

    Args:
        employee_id: e.g. "E1002"
    """
    employee = get_employee(employee_id)
    if employee is None:
        return f"no such employee {employee_id}"
    skills = ", ".join(f"{s['skill']} L{s['level']}" for s in employee["skills"])
    return f"{employee['name']}: {skills}"


def demo_agent_plugin() -> None:
    print("=== 1. Agent plugin ===")
    agent = Agent(
        model=make_model(),
        system_prompt="Read the profile of every employee the user names, one tool call each.",
        tools=[read_profile],
        plugins=[ProfileAccessGuard(max_profiles=3)],
        callback_handler=None,
    )
    print("  tools now available:", agent.tool_names)
    print("  state seeded by the plugin:", agent.state.get())
    agent("Summarise the skills of E1002, E1003, E1005, E1006 and E1008.")
    print()


def demo_multiagent_plugin() -> None:
    print("=== 2. Multi-agent plugin ===")
    screener = Agent(
        name="screener", model=make_model(), system_prompt="Reply with one word.", callback_handler=None
    )
    approver = Agent(
        name="approver", model=make_model(), system_prompt="Reply with one word.", callback_handler=None
    )

    builder = GraphBuilder()
    builder.add_node(screener, "screen")
    builder.add_node(approver, "approve")
    builder.add_edge("screen", "approve")
    builder.set_entry_point("screen")
    builder.set_plugins([NodeTimer()])  # Swarm takes plugins=[...] in its constructor
    graph = builder.build()

    graph("Say ok.")


def main() -> None:
    demo_agent_plugin()
    demo_multiagent_plugin()


if __name__ == "__main__":
    main()
