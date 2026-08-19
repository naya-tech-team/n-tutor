"""One A2A round-trip, as a function — local or across AgentCore Runtime.

Discover → connect → send → collect. The collecting is the subtle part and it is
identical in both worlds, so it is worth understanding before you write your own.

**What comes back is not one response.** `send_message` is an async *iterator*: a
compliant server streams a sequence of events, and their shape depends on where
the remote agent is in the task lifecycle:

    (Task, TaskArtifactUpdateEvent)   the result, growing token by token
    (Task, TaskStatusUpdateEvent)     working → completed
    Message                           some servers reply with a plain message

The `Task` in those tuples is cumulative — each event carries the whole task so
far, with the answer accumulating in `task.artifacts[*].parts[*].text`. Keep the
**last** task you saw and read its artifacts at the end.

Reading `event.parts` alone — the obvious first guess — silently returns nothing
against a streaming server, because a tuple has no `.parts`.

What *does* change on AgentCore is the address and the headers:

    local       http://127.0.0.1:9001
    AgentCore   https://bedrock-agentcore.{region}.amazonaws.com
                  /runtimes/{url-encoded arn}/invocations

plus a **SigV4 signature** and `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`.

SigV4, not a bearer token: AgentCore consumes the caller's Authorization header
at its edge and never passes it to the container, so there is nothing to forward
and no way to mint one without shipping a password. Machines authenticate as
machines, with the execution role. The supervisor keeps CUSTOM_JWT because the
thing on the other side of its door is a person.

That session id is the requisition id, and carrying it here is what makes five
runtimes produce one trace instead of five.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientHTTPError
from a2a.types import Message, Part, Role, TextPart

from _shared import settings

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


def runtime_url(arn: str) -> str:
    """The invocations URL for a deployed runtime. The ARN must be URL-encoded —
    colons and slashes both, which is why `safe=''` is not optional."""
    return (
        f"https://bedrock-agentcore.{settings.aws_region}.amazonaws.com"
        f"/runtimes/{quote(arn, safe='')}/invocations"
    )


def agent_url(local_url: str, arn: str) -> str:
    """Whichever address this agent lives at right now."""
    return runtime_url(arn) if settings.agentcore and arn else local_url


SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{32,255}$")


def safe_session_id(candidate: str) -> str:
    """A session id AgentCore will accept, derived from the one you meant.

    Both `InvokeAgentRuntime` and `GetAgentCard` require **33 characters minimum**.
    The obvious value to send is the requisition — and `J2001` is five, so a CLI
    run that works locally is rejected the moment it crosses a runtime boundary.

    Padded deterministically rather than replaced with a UUID: the session id is
    what stitches five runtimes into one trace, so the same input has to produce
    the same id on every hop. `J2001` becomes `J2001-00...0`, which is still
    legible in a trace listing.

    Mirrors `safeSessionId` in ui/proxy/index.mjs. Two implementations of one
    rule, which is why both name the 33.
    """
    candidate = (candidate or "").strip()
    if SESSION_RE.match(candidate):
        return candidate
    # Keep whatever was meaningful, drop what the pattern forbids, then pad.
    base = re.sub(r"[^a-zA-Z0-9_-]", "-", candidate) or "session"
    if not base[0].isalnum():
        base = f"s{base}"
    return f"{base}-{'0' * 33}"[:64]


class SigV4(httpx.Auth):
    """Sign every request with this container's execution role.

    The four inner runtimes use SigV4 inbound auth, because there is no way for a
    container to hold a Cognito token: AgentCore consumes the caller's
    `Authorization` header at its edge and never forwards it, and a workload
    access token is documented as usable only against first-party AgentCore
    identity services — not for invoking another runtime.

    So machines authenticate as machines. The execution role already carries
    `bedrock-agentcore:InvokeAgentRuntime`; nothing here holds a secret and there
    is no token to refresh.

    `requires_request_body` because SigV4 signs a hash of the body, and httpx
    otherwise hands the hook a request whose content has not been read yet — the
    signature would cover an empty body and every call would be a 403.
    """

    requires_request_body = True

    def __init__(self, service: str = "bedrock-agentcore") -> None:
        import boto3

        self._service = service
        self._credentials = boto3.Session().get_credentials()
        if self._credentials is None:
            raise RuntimeError(
                "No AWS credentials to sign with. Inside a runtime these come from "
                "the execution role; locally, export them or set AGENTCORE=false."
            )

    def auth_flow(self, request):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        signable = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            # Only the headers we actually send — signing a header we then drop
            # invalidates the signature just as surely as changing one.
            headers=dict(request.headers),
        )
        SigV4Auth(
            self._credentials.get_frozen_credentials(), self._service, settings.aws_region
        ).add_auth(signable)

        request.headers.update(dict(signable.headers))
        yield request


def signer() -> httpx.Auth | None:
    """The auth to attach to an A2A call, or None locally where there is none."""
    return SigV4() if settings.agentcore else None


def auth_headers(session_id: str | None = None) -> dict[str, str]:
    """The session header, and a bearer token if one happens to be set.

    **Nothing between services needs a token any more.** The four inner runtimes
    are SigV4-signed by `signer()`, and so is the Gateway — it runs
    `authorizer_type = "AWS_IAM"`, which a Gateway supports just as a runtime
    does. `BEARER_TOKEN` is still honoured here, so setting one is harmless and
    a CUSTOM_JWT gateway keeps working, but no code path requires it.

    The one place a bearer token is still the answer is the **supervisor**, which
    is CUSTOM_JWT because a person is on the other side of it. That token comes
    from the browser via `07_api`, not from this environment variable; see
    `03_gateway`'s `bearer_token_command` output for the curl-it-by-hand version.

    Empty locally — an A2AServer on 127.0.0.1 authenticates nobody, which is
    exactly why you would not expose one.
    """
    if not settings.agentcore:
        return {}

    headers = {}
    token = os.environ.get("BEARER_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        # Padded to AgentCore's 33-character minimum. Sending the raw requisition
        # id is a ValidationException on InvokeAgentRuntime and GetAgentCard alike.
        headers[SESSION_HEADER] = safe_session_id(session_id)
    return headers


def _artifact_text(task: object) -> str:
    """Join every text part of every artifact on a task. Parts stream one token
    at a time, so they concatenate with no separator."""
    chunks: list[str] = []
    for artifact in getattr(task, "artifacts", None) or []:
        for part in getattr(artifact, "parts", None) or []:
            root = getattr(part, "root", part)
            text = getattr(root, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks)


async def _resolve_card(http: httpx.AsyncClient, base_url: str):
    """Fetch an agent card, and make a 403 say what it actually means.

    Discovery is the first thing every delegation does, so it is the first thing
    that fails — and it fails as "cannot fetch agent card", which reads like the
    remote agent is down. It is almost never the agent.

    `GetAgentCard` is its OWN IAM action. A role with
    `bedrock-agentcore:InvokeAgentRuntime` and nothing else can send messages it
    can never get far enough to send, because the card fetch in front of them is
    denied. That combination is silent in every log except the one line below.

    Under CUSTOM_JWT the same call fails 401 instead, and note that **401 is not
    in GetAgentCard's documented error list** — it comes from the auth frontend,
    before the API is reached.
    """
    try:
        return await A2ACardResolver(http, base_url).get_agent_card()
    except A2AClientHTTPError as exc:
        if exc.status_code == 403:
            raise RuntimeError(
                f"403 fetching the agent card at {base_url}. The signature was "
                "accepted; the permission was not. A2A discovery needs "
                "`bedrock-agentcore:GetAgentCard`, which is a SEPARATE action from "
                "`bedrock-agentcore:InvokeAgentRuntime` — 05_runtimes grants both on "
                "the edges in `local.callees`. Confirm with:\n"
                "  aws iam simulate-principal-policy --policy-source-arn <this role> \\\n"
                "    --action-names bedrock-agentcore:GetAgentCard --resource-arns <callee arn>"
            ) from exc
        if exc.status_code == 401:
            raise RuntimeError(
                f"401 fetching the agent card at {base_url}. The runtime is behind "
                "CUSTOM_JWT and this caller sent no usable token. Inner runtimes are "
                "meant to be SigV4 — check `authorizer_configuration` on that runtime."
            ) from exc
        raise


async def call_agent(
    base_url: str,
    text: str,
    *,
    timeout: float = 180,
    session_id: str | None = None,
) -> str:
    """Send one message to an A2A agent and return its final answer as text."""
    async with httpx.AsyncClient(
        timeout=timeout, headers=auth_headers(session_id), auth=signer()
    ) as http:
        # 1. Discovery: the card lives at a well-known URL, so a caller needs
        #    nothing but the base address to learn what this agent can do.
        card = await _resolve_card(http, base_url)

        # 2. A client bound to that card.
        client = ClientFactory(ClientConfig(httpx_client=http)).create(card)

        # 3. Send, then drain the event stream.
        message = Message(
            role=Role.user,
            message_id=uuid4().hex,
            parts=[Part(root=TextPart(text=text))],
        )
        last_task = None
        plain: list[str] = []
        async for event in client.send_message(message):
            if isinstance(event, tuple):
                last_task = event[0]          # cumulative — keep the newest
            else:
                for part in getattr(event, "parts", None) or []:
                    root = getattr(part, "root", part)
                    if getattr(root, "text", None):
                        plain.append(root.text)

        answer = _artifact_text(last_task) if last_task is not None else ""
        return (answer or "".join(plain)).strip() or "(the remote agent returned nothing)"


async def describe_agent(
    base_url: str,
    *,
    timeout: float = 15,
    session_id: str | None = None,
) -> tuple[str, list[str]]:
    """Read a card and return (name, skill ids) — discovery with no message sent."""
    async with httpx.AsyncClient(
        timeout=timeout, headers=auth_headers(session_id), auth=signer()
    ) as http:
        card = await _resolve_card(http, base_url)
        return card.name, [skill.id for skill in card.skills]
