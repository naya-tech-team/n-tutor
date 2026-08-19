"""Where the screening agent's tools come from — and the Gateway decision.

On AgentCore the screener holds **two** MCP connections:

    hr_skills_mcp   direct, one hop     bare names: score_match, shortlist
    hr-gateway      Lambda over S3      prefixed:  hrdata___find_by_skill

The Gateway is a protocol adapter, not a router. A Lambda cannot speak MCP so it
needs one; `hr_skills_mcp` already can, and routing it through anyway would add a
permanent hop to the call that runs once per candidate, plus a second copy of its
tool schema in HCL. **Put a thing behind the Gateway when it cannot speak MCP on
its own.**

Locally there is no Gateway and no Lambda, so the same capabilities are ordinary
in-process functions. The agent above this line never learns which world it is in.

`screening_toolset()` is a context manager on purpose. A `MCPClient` outside its
`with` block is a dead connection, and a dead connection does not raise — the
agent still has the tool *names* in its history, so it quietly starts making
plausible answers up. Making the tools unobtainable outside the block is the only
version of this that cannot be got wrong.
"""

from __future__ import annotations

from contextlib import contextmanager

from _shared import settings

from .a2a_call import auth_headers, runtime_url, signer


def _mcp_client(url: str):
    """One MCP connection, signed with this container's execution role.

    Both ends take SigV4 now — `hr_skills_mcp` is a runtime with no authorizer,
    and the Gateway runs `authorizer_type = "AWS_IAM"`.

    That is what makes it safe to open these at start-up. A bearer token would
    have to exist before the first request, and none does: nothing in a container
    can obtain a Cognito token, because AgentCore consumes the caller's
    Authorization header at its edge. SigV4 signs each request as it is sent
    instead, so a connection opened once at boot keeps working — and botocore
    refreshes the role's credentials without anyone asking.
    """
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    return MCPClient(
        lambda: streamablehttp_client(url, headers=auth_headers(), auth=signer())
    )


@contextmanager
def screening_toolset(local_tools: list):
    """Yield the screening agent's tools, in whichever world we are in.

    Args:
        local_tools: the in-process `@tool` functions to use when not deployed.
    """
    if not settings.agentcore:
        yield local_tools
        return

    if not settings.skills_mcp_arn or not settings.gateway_url:
        raise RuntimeError(
            "AGENTCORE=true needs SKILLS_MCP_ARN and GATEWAY_URL. Both are terraform "
            "outputs — 05_runtimes and 03_gateway. Put them in agent-core/.env."
        )

    skills = _mcp_client(runtime_url(settings.skills_mcp_arn))
    gateway = _mcp_client(settings.gateway_url)

    # Both blocks are load-bearing. Leaving either one is silent.
    with skills, gateway:
        yield skills.list_tools_sync() + gateway.list_tools_sync()
