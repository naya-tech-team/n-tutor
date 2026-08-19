"""The Gateway with no agent on the calling side.

When a Gateway tool misbehaves there are four suspects: the model, the MCP
session, the Gateway's own translation, and the Lambda underneath. This removes
the first two. It opens one MCP session, lists what the Gateway publishes, and
calls one tool — no agent, no reasoning — so whatever comes back came from the
Gateway and the Lambda alone.

    uv run scripts/probe_gateway.py                      # list the tools
    uv run scripts/probe_gateway.py --call hrdata___get_requisition --args '{"job_id":"J2001"}'
    uv run scripts/probe_gateway.py --url https://...    # a gateway not in your state

**Auth is your own IAM identity.** The Gateway runs `authorizer_type = AWS_IAM`,
so this signs with whatever `aws sts get-caller-identity` returns and needs
`bedrock-agentcore:InvokeGateway` on it. There is no bearer token to fetch —
that is the whole point of the AWS_IAM switch, and it is why this script needs no
Cognito password to run.

`Session termination failed: 404` on exit is expected noise, not a failure — the MCP
client tries to DELETE the session on close and AgentCore does not implement that. It
prints before the results because it comes from the transport, not from here.

The tool names are the other half of the contract. The Gateway namespaces every
target as `{target}___{tool}` — three underscores — so what the model sees is
`hrdata___get_requisition`, not `get_requisition`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from _shared import settings  # noqa: E402
from clients.a2a_call import SigV4  # noqa: E402

GATEWAY_MODULE = ROOT / "terraform" / "03_gateway"


def resolve_url(explicit: str | None) -> str:
    """`--url`, then GATEWAY_URL from .env, then the terraform state.

    The third one exists so a fresh clone can probe without an .env — and
    because `authorizer_type` is immutable, so replacing the gateway mints a new
    URL and a stale .env is a 404 that reads like the Gateway is down.
    """
    if explicit:
        return explicit
    if settings.gateway_url:
        return settings.gateway_url

    try:
        out = subprocess.run(
            ["terraform", "output", "-raw", "gateway_url"],
            cwd=GATEWAY_MODULE,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "no gateway url. Set GATEWAY_URL in agent-core/.env, pass --url, or "
            f"run `terraform apply` in {GATEWAY_MODULE.relative_to(ROOT)}.\n{exc}"
        ) from exc
    return out.stdout.strip()


def explain(exc: Exception) -> str:
    """Turn the three failures you will actually hit into the next thing to do."""
    text = str(exc)
    if "403" in text or "AccessDenied" in text:
        return (
            "403 — signed fine, not allowed. Your identity needs\n"
            "  bedrock-agentcore:InvokeGateway  on the gateway arn.\n"
            "05_runtimes grants it to the screening runtime's execution role; your own\n"
            "user is a separate identity and needs it too."
        )
    if "404" in text:
        return (
            "404 — the URL is wrong, not the auth. `authorizer_type` is immutable, so any\n"
            "apply that changed it replaced the gateway and minted a new url. Re-read it:\n"
            "  cd terraform/03_gateway && terraform output -raw gateway_url"
        )
    if "credential" in text.lower() or "NoCredentials" in text:
        return "no AWS credentials. `aws sts get-caller-identity` should print your arn."
    return ""


async def probe(url: str, call: str | None, args: dict) -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    print(f"gateway  : {url}")
    print(f"auth     : SigV4 (AWS_IAM), region {settings.aws_region}\n")

    # `auth=` rather than a header: httpx re-signs per request, and SigV4 covers
    # a hash of the body, so a hand-built header would be wrong on every call
    # after the first.
    async with streamablehttp_client(url, auth=SigV4()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            print(f"tools    : {len(listed.tools)}")
            for tool in listed.tools:
                print(f"  {tool.name}")
            if not listed.tools:
                print("  (none — the target exists but published no tools; check its schema)")

            if not call:
                return 0

            print(f"\ncalling  : {call}({json.dumps(args)})")
            result = await session.call_tool(call, args)
            for block in result.content:
                print(f"\nreply    : {getattr(block, 'text', block)}")
            return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="gateway url (default: .env, then terraform output)")
    parser.add_argument("--call", help="a tool to invoke, e.g. hrdata___get_requisition")
    parser.add_argument("--args", default="{}", help="that tool's arguments, as JSON")
    opts = parser.parse_args()

    try:
        return asyncio.run(probe(resolve_url(opts.url), opts.call, json.loads(opts.args)))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — the advice matters more than the traceback
        print(f"\n✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        advice = explain(exc)
        if advice:
            print(f"\n{advice}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
