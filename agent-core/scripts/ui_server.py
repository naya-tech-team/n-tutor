"""The chat proxy's local twin, so `ui/` runs without deploying anything.

Deployed, the browser talks to a Node Lambda behind CloudFront. That Lambda cannot
run here — response streaming is a Lambda runtime feature, not a library — so this
serves the same two routes over the same paths against the local supervisor.

The contract it has to match is the wire, not the code: `/api/login` returns a
token-shaped object, `/api/chat` returns `text/event-stream` whose frames are the
supervisor's own event dicts. Everything above that in `ui/src` is then identical
in both worlds, which is the point — you debug the React against Ollama and the
four local servers, and deploy without touching it.

**There is no authentication here.** Anything is accepted as a login and no token
is checked, exactly as `auth_headers()` sends nothing to an A2A server on
127.0.0.1. It is why you would not expose this.

    make ui-api          this
    make ui-dev          vite, in another terminal
    uv run scripts/ui_server.py --port 8123

8123 rather than the obvious 8080/8081: both are common defaults for corporate
TLS-inspecting proxies, and when one holds the port the bind fails while `lsof`
shows nothing — the listener belongs to root and you cannot see it. That is a
confusing ten minutes for a port number nobody cares about. `--port` if 8123 is
taken too.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

SUPERVISOR = ROOT / "app/runtimes/hiring_supervisor/main.py"


def load_supervisor():
    """Import the runtime as a module rather than shelling out to it.

    `app/runtimes/*` are scripts, not a package — importing by path is how the
    tests do it too, and it keeps one definition of the pipeline instead of a
    local copy that drifts.
    """
    spec = importlib.util.spec_from_file_location("hiring_supervisor_main", SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


supervisor = load_supervisor()


async def login(request: Request) -> JSONResponse:
    """Accepts anyone. The deployed proxy calls Cognito here; this returns the
    same shape so the React never learns which world it is in.

    `token` rather than `accessToken`: deployed it is a Cognito **ID** token,
    because that is what an API Gateway Cognito authorizer with no scopes
    accepts. Nothing here checks it, so the value is a label, not a credential.
    """
    return JSONResponse({"token": "local-development-token", "expiresIn": 86400})


async def chat(request: Request) -> StreamingResponse:
    body = await request.json()

    async def frames():
        # Byte-for-byte the framing bedrock_agentcore produces in the container:
        # one JSON object per `data:` line, blank line terminated.
        session_id = body.get("sessionId") or "local-chat-session"
        yield f"data: {json.dumps({'type': 'session', 'sessionId': session_id})}\n\n"

        events = supervisor.stream_pipeline(
            body.get("jobId") or supervisor.DEFAULT_JOB,
            session_id=session_id,
            prompt=body.get("prompt") or "",
        )
        async for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        # Without this, a proxy in front of uvicorn may buffer the whole response
        # and deliver it at the end — which looks exactly like no streaming at all.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app = Starlette(
    routes=[
        Route("/api/login", login, methods=["POST"]),
        Route("/api/chat", chat, methods=["POST"]),
    ]
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    print(f"  chat proxy (local)  http://127.0.0.1:{args.port}/api/chat")
    print("  no auth, no Cognito, no AWS. Start the four agent servers first: make up")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
