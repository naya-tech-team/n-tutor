"""How an agent finds another agent, and how it proves who it is.

Locally the port is the address. On AgentCore all three A2A agents listen on
9000 and the ARN is the address — so the only thing that can go wrong is the URL,
and it goes wrong quietly: a half-encoded ARN gives a 404 that reads like the
agent is down.

Auth is the other half, and it is not the obvious one. The natural design is to
forward the caller's bearer token down the chain; it cannot be done. AgentCore
consumes `Authorization` at its edge and never passes it to the container, and a
workload access token is documented as usable only against first-party AgentCore
identity services. So machines authenticate as machines: the four inner runtimes
take SigV4, signed with the execution role. Only the supervisor — the front door,
with a person on the other side — keeps CUSTOM_JWT.
"""

from __future__ import annotations

import pytest

from _shared.config import settings

from clients.a2a_call import SESSION_HEADER, agent_url, auth_headers, runtime_url

ARN = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/talent_screening-xyz123"


def test_the_arn_is_fully_url_encoded():
    """Colons AND slashes. `quote(arn)` alone leaves the slash and 404s."""
    url = runtime_url(ARN)
    assert "%3A" in url
    assert "%2F" in url
    assert ":" not in url.split("/runtimes/", 1)[1]
    assert url.endswith("/invocations")


def test_runtime_url_uses_the_configured_region(monkeypatch):
    monkeypatch.setattr(settings, "aws_region", "eu-west-1")
    assert "bedrock-agentcore.eu-west-1.amazonaws.com" in runtime_url(ARN)


def test_locally_the_port_is_the_address():
    assert agent_url("http://127.0.0.1:9001", ARN) == "http://127.0.0.1:9001"


def test_deployed_the_arn_is_the_address(monkeypatch):
    monkeypatch.setattr(settings, "agentcore", True)
    assert agent_url("http://127.0.0.1:9001", ARN) == runtime_url(ARN)


def test_deployed_without_an_arn_falls_back_rather_than_building_garbage(monkeypatch):
    """A missing terraform output should not produce `/runtimes//invocations`."""
    monkeypatch.setattr(settings, "agentcore", True)
    assert agent_url("http://127.0.0.1:9001", "") == "http://127.0.0.1:9001"


# --- headers ----------------------------------------------------------------


def test_no_headers_locally():
    assert auth_headers("J2001") == {}


def test_the_session_id_rides_every_hop(monkeypatch):
    """This is what makes five runtimes produce one trace instead of five."""
    monkeypatch.setattr(settings, "agentcore", True)
    monkeypatch.setenv("BEARER_TOKEN", "tok")
    assert auth_headers("J2001")[SESSION_HEADER].startswith("J2001")


def test_no_session_id_means_no_session_header(monkeypatch):
    monkeypatch.setattr(settings, "agentcore", True)
    monkeypatch.setenv("BEARER_TOKEN", "tok")
    assert SESSION_HEADER not in auth_headers(None)


# --- SigV4 between services -------------------------------------------------


def test_locally_nothing_is_signed(monkeypatch):
    """An A2AServer on 127.0.0.1 authenticates nobody, which is exactly why you
    would not expose one."""
    from clients.a2a_call import signer

    monkeypatch.setattr(settings, "agentcore", False)
    assert signer() is None


def test_deployed_hops_are_signed(monkeypatch):
    """The four inner runtimes take SigV4, not a bearer token — there is no way for
    a container to hold a Cognito token."""
    import httpx

    from clients.a2a_call import signer

    monkeypatch.setattr(settings, "agentcore", True)
    auth = signer()
    assert isinstance(auth, httpx.Auth)
    # SigV4 signs a hash of the body. Without this httpx hands the hook a request
    # whose content is unread, the signature covers an empty body, and every call
    # is a 403 that looks like a permissions problem.
    assert auth.requires_request_body is True


def test_a_bearer_token_is_still_passed_through_when_set(monkeypatch):
    """Not required, but not dropped either.

    The Gateway takes SigV4 now (`authorizer_type = "AWS_IAM"`), so no hop in this
    system needs a token. Keeping the pass-through is what lets you flip
    `gateway_authorizer_type` back to CUSTOM_JWT without touching Python.
    """
    monkeypatch.setattr(settings, "agentcore", True)
    monkeypatch.setenv("BEARER_TOKEN", "tok")
    assert auth_headers()["Authorization"] == "Bearer tok"


# --- the 33-character session id --------------------------------------------


def test_a_short_session_id_is_padded_to_the_minimum():
    """InvokeAgentRuntime AND GetAgentCard both require 33 characters. The
    requisition id is five, so this is the difference between a CLI run that
    works and one rejected the moment it crosses a runtime boundary."""
    from clients.a2a_call import safe_session_id

    padded = safe_session_id("J2001")
    assert len(padded) >= 33
    assert padded.startswith("J2001")


def test_padding_is_deterministic():
    """The session id is what stitches five runtimes into one trace. A random
    suffix per hop would give five traces that share nothing."""
    from clients.a2a_call import safe_session_id

    assert safe_session_id("J2001") == safe_session_id("J2001")


def test_an_already_valid_session_id_is_untouched():
    """The chat thread id from ui/proxy is already long enough — repadding it
    would break continuity with the memory session it keys."""
    from clients.a2a_call import safe_session_id

    thread = "chat-" + "a" * 40
    assert safe_session_id(thread) == thread


def test_characters_the_pattern_forbids_are_replaced():
    """The response pattern is [a-zA-Z0-9][a-zA-Z0-9-_]*; a colon or a slash in a
    session id is a 400 that says nothing about which character offended."""
    from clients.a2a_call import safe_session_id

    cleaned = safe_session_id("J2001/req:1")
    assert ":" not in cleaned and "/" not in cleaned
    assert cleaned[0].isalnum()


# --- the toolset context manager -------------------------------------------


def test_local_toolset_yields_the_in_process_tools():
    from clients.tools import screening_toolset

    sentinel = ["rank", "score"]
    with screening_toolset(sentinel) as tools:
        assert tools is sentinel


def test_mcp_connections_are_signed(monkeypatch):
    """Both MCP ends take SigV4 — hr_skills_mcp has no authorizer, and the Gateway
    runs authorizer_type = AWS_IAM.

    That is what makes it safe to open these at container start-up. A bearer token
    would have to exist before the first request and none does, because AgentCore
    consumes the caller's Authorization header at its edge. SigV4 signs per
    request, so a connection opened once at boot keeps working.
    """
    import clients.tools as tools

    monkeypatch.setattr(settings, "agentcore", True)

    captured = {}

    def fake_streamable(url, headers=None, auth=None):
        captured.update(url=url, headers=headers, auth=auth)
        return None

    # Patch the function, not the module — replacing the module breaks every other
    # import inside the mcp package.
    monkeypatch.setattr(
        "mcp.client.streamable_http.streamablehttp_client", fake_streamable
    )

    captured_factory = {}
    monkeypatch.setattr(
        "strands.tools.mcp.MCPClient",
        lambda factory: captured_factory.setdefault("f", factory),
    )

    tools._mcp_client("https://example.invalid/mcp")
    captured_factory["f"]()  # MCPClient defers the transport until connect

    assert captured["url"] == "https://example.invalid/mcp"
    assert captured["auth"] is not None, "MCP hops must be SigV4-signed"


def test_deployed_toolset_without_terraform_outputs_says_which_ones(monkeypatch):
    from clients.tools import screening_toolset

    monkeypatch.setattr(settings, "agentcore", True)
    monkeypatch.setattr(settings, "skills_mcp_arn", "")
    monkeypatch.setattr(settings, "gateway_url", "")

    with pytest.raises(RuntimeError) as exc:
        with screening_toolset([]):
            pass
    assert "SKILLS_MCP_ARN" in str(exc.value)
    assert "GATEWAY_URL" in str(exc.value)


# --- discovery is its own permission ----------------------------------------


def _card_failure(status: int):
    """Drive `_resolve_card` against a resolver that fails with `status`.

    `asyncio.run` rather than pytest-asyncio: one coroutine does not justify a
    plugin, and this keeps the suite runnable with nothing but pytest.
    """
    import asyncio

    from a2a.client.errors import A2AClientHTTPError

    import clients.a2a_call as a2a

    class Boom:
        def __init__(self, *_args, **_kwargs):
            pass

        async def get_agent_card(self):
            raise A2AClientHTTPError(status, "nope")

    original = a2a.A2ACardResolver
    a2a.A2ACardResolver = Boom
    try:
        return asyncio.run(a2a._resolve_card(None, "https://example.invalid"))
    finally:
        a2a.A2ACardResolver = original


def test_a_403_on_the_card_names_the_missing_action():
    """This cost a debugging round-trip: the supervisor reported "there was an
    error fetching the agent card" and the model relayed exactly that.

    `GetAgentCard` is a separate IAM action from `InvokeAgentRuntime`. A role with
    only the latter fails at discovery, before it ever sends a message, and the
    403 names nothing.
    """
    with pytest.raises(RuntimeError) as exc:
        _card_failure(403)
    assert "GetAgentCard" in str(exc.value)
    assert "InvokeAgentRuntime" in str(exc.value)


def test_a_401_on_the_card_points_at_the_authorizer_instead():
    """Different cause, different advice. 401 is CUSTOM_JWT rejecting a caller
    that has no token; 403 is SigV4 accepted and IAM saying no."""
    with pytest.raises(RuntimeError) as exc:
        _card_failure(401)
    assert "CUSTOM_JWT" in str(exc.value)
    assert "GetAgentCard" not in str(exc.value)


def test_other_statuses_are_left_alone():
    """A 404 is a URL bug and already reads as one — rewriting it would bury the
    original message under a guess."""
    from a2a.client.errors import A2AClientHTTPError

    with pytest.raises(A2AClientHTTPError):
        _card_failure(404)
