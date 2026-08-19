"""FastAPI app entry point: middleware, CORS, and the HR routers.

An HTTP face on the same domain the rest of the course uses: employees have
rated skills, requisitions require skills, and /candidates decides who fits.

Run it either way — both work:
    uvicorn app.main:app --reload      # from the fast-api/ folder
    python app/main.py                 # directly

Then open http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Running this file directly puts `app/` on sys.path — NOT the project root — so
# the `from app.models import ...` lines inside the routers would raise
# `ModuleNotFoundError: No module named 'app'`. Adding the parent makes the same
# absolute imports resolve under `python app/main.py`, `uvicorn app.main:app`,
# and pytest alike, so there is only one import style in the codebase.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers import candidates  # noqa: E402 — must follow the sys.path line above

app = FastAPI(
    title="HR Skills API",
    version="1.0.0",
    description="Employees, open requisitions, and a scoring engine that decides who fits.",
)

# CORS — allow a browser front-end to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to real origins in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    """Custom middleware: time every request and report it in a header."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time-ms"] = f"{elapsed_ms:.2f}"
    return response


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(candidates.employees_router)
app.include_router(candidates.requisitions_router)

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8100"))
    uvicorn.run(app, host=host, port=port)