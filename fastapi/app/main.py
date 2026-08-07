"""FastAPI app entry point: middleware, CORS, and the tasks router.

Run:
    uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.routers import tasks

app = FastAPI(title="Task API", version="1.0.0")

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


app.include_router(tasks.router)

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)