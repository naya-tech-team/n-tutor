"""Serve one Strands agent over A2A, locally or in AgentCore Runtime.

The three specialist agents differ only in their prompt, tools and declared
skill. How they get onto the wire is identical, and it is the one place the two
worlds actually diverge:

| | local | AgentCore Runtime |
|---|---|---|
| host:port | 127.0.0.1:9001/9002/9007 | 0.0.0.0:9000 |
| mount path | `/` | `/` |
| card | served by `A2AServer` | served by `serve_a2a` |

**The port stops being an address.** Locally it is how a caller tells the
screener from the writer. In Runtime all three listen on 9000 and the ARN
distinguishes them, so `serve_a2a` does not even accept a choice.

Both paths use the same `agent_factory`, called once per A2A context, so two
hiring pipelines never share message history.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from strands import Agent

from .config import settings

VERSION = "1.0.0"

# The A2A contract in AgentCore Runtime: port 9000, mounted at the root.
AGENTCORE_A2A_PORT = 9000


def build_card(*, name: str, description: str, skills: list[AgentSkill], url: str) -> AgentCard:
    """The public business card a calling agent reads before sending anything."""
    return AgentCard(
        name=name,
        description=description,
        url=url,
        version=VERSION,
        # Streaming is on: both servers stream, and a card that claims otherwise
        # makes a compliant client take the wrong branch.
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=skills,
    )


def serve(
    agent_factory: Callable[[str], Agent],
    *,
    name: str,
    description: str,
    skills: list[AgentSkill],
    local_port: int,
) -> None:
    """Run this agent as an A2A server, in whichever world we are in."""
    from strands.multiagent.a2a import A2AServer

    if not settings.agentcore:
        server = A2AServer(
            agent_factory=agent_factory,
            host="127.0.0.1",
            port=local_port,
            version=VERSION,
            skills=skills,
            # Off by default today, the default in the next major version —
            # without it the SDK warns on every request that its stream does not
            # match the A2A spec.
            enable_a2a_compliant_streaming=True,
        )
        print(f"{name} on http://127.0.0.1:{local_port}")
        print(f"  card: http://127.0.0.1:{local_port}/.well-known/agent-card.json")
        server.serve()
        return

    from bedrock_agentcore.runtime import serve_a2a
    from strands.multiagent.a2a.executor import StrandsA2AExecutor

    # Runtime injects the public URL. Putting it on the card matters: a client
    # resolves the card and then sends to the `url` it finds there, so a card
    # advertising 0.0.0.0 sends the second request into a black hole.
    url = os.environ.get("AGENTCORE_RUNTIME_URL", f"http://0.0.0.0:{AGENTCORE_A2A_PORT}/")

    executor = StrandsA2AExecutor(
        agent_factory=agent_factory,
        enable_a2a_compliant_streaming=True,
    )
    print(f"{name} on 0.0.0.0:{AGENTCORE_A2A_PORT} (AgentCore A2A)")
    serve_a2a(executor, build_card(name=name, description=description, skills=skills, url=url))
