"""02 · MCP — borrow tools that live in someone else's process.

The HR team owns employee data. They publish `hr_mcp_server.py`; we never import
their code, we speak MCP to it. Same three tools would work from Claude Desktop
or an IDE — the agent is just one more client.

Run:  uv run app/02_mcp/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient

from _shared import make_model

SERVER = str(Path(__file__).with_name("hr_mcp_server.py"))

# The client owns the subprocess. `sys.executable` is this venv's python, so the
# server sees the same dependencies we do.
hr_server = MCPClient(
    lambda: stdio_client(StdioServerParameters(command=sys.executable, args=[SERVER]))
)


def main() -> None:
    # The `with` block is load-bearing: MCP tools are only callable while the
    # session to the server is open. Leaving it kills the subprocess.
    with hr_server:
        tools = hr_server.list_tools_sync()
        print("tools discovered over MCP:", [t.tool_name for t in tools], "\n")

        agent = Agent(
            model=make_model(),
            system_prompt=(
                "You are a resourcing assistant. Use the HR tools for every fact; "
                "never invent an employee, a score or a skill level."
            ),
            tools=tools,
            callback_handler=None,
        )

        print("=== 1. One requisition, explained ===")
        print(agent("What does requisition J2001 require? List the mandatory skills only."), "\n")

        print("=== 2. A shortlist, from the server's own scoring ===")
        print(agent("Who are the top 3 available candidates for J2001, and what are their gaps?"), "\n")

        print("=== 3. A single pairing ===")
        # E1010 scores 52% on J2003 and is blocked by exactly one mandatory skill.
        print(agent("Score employee E1010 against job J2003 and tell me the one thing blocking them."))


if __name__ == "__main__":
    main()
