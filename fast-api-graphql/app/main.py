"""FastAPI app entry point: middleware, CORS, and one GraphQL endpoint.

The same HR domain the rest of the course uses — employees have rated skills,
requisitions require skills, and the scoring engine decides who fits — behind
GraphQL instead of REST. One URL, `/graphql`, and the client says what it wants.

Run it either way — both work:
    uvicorn app.main:app --reload      # from the fast-api-graphql/ folder
    python app/main.py                 # directly

Then open http://127.0.0.1:8000/graphql for the GraphiQL explorer.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Running this file directly puts `app/` on sys.path — NOT the project root — so
# the `from app.models import ...` lines inside the graph package would raise
# `ModuleNotFoundError: No module named 'app'`. Adding the parent makes the same
# absolute imports resolve under `python app/main.py`, `uvicorn app.main:app`,
# and pytest alike, so there is only one import style in the codebase.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strawberry.fastapi import GraphQLRouter  # noqa: E402 — must follow the sys.path line

from app.graph.context import get_context  # noqa: E402
from app.graph.schema import schema  # noqa: E402

app = FastAPI(
    title="HR Skills GraphQL API",
    version="1.0.0",
    description="Employees, open requisitions, and a scoring engine — behind one GraphQL endpoint.",
)

# CORS — allow a browser front-end to call this API. GraphQL needs it as much as
# REST does: it is still an HTTP POST from a page on another origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to real origins in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    """Custom middleware: time every request and report it in a header.

    Middleware sees one POST /graphql, not the fields inside it. That is the
    trade: HTTP-level tooling (timing, rate limits, caching) can no longer tell
    a cheap query from an expensive one — per-field timing belongs in a schema
    extension instead.
    """
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time-ms"] = f"{elapsed_ms:.2f}"
    return response


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Plain HTTP, on purpose — a load balancer should not have to speak GraphQL."""
    return {"status": "ok"}


# The whole API. `context_getter` is an ordinary FastAPI dependency, which is why
# `app.dependency_overrides[get_store]` still works in the tests.
graphql_router = GraphQLRouter(
    schema,
    context_getter=get_context,
    graphql_ide="graphiql",       # the in-browser explorer at GET /graphql
)
app.include_router(graphql_router, prefix="/graphql")

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8200"))
    uvicorn.run(app, host=host, port=port)
