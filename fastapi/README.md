# FastAPI Quick Start — runnable code

A small **Task API** showing the pieces from the FastAPI: Quick Start page working together: Pydantic v2 models, an `APIRouter`, custom **middleware**, **CORS**, **dependency injection** (`Depends`), and a full **pytest** suite.

## Table of contents

- [Layout](#layout)
- [Install & run](#install--run)
- [Try it](#try-it)
- [Run the tests](#run-the-tests)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## Layout

```
fastapi/
├── app/
│   ├── main.py            # FastAPI app: middleware, CORS, router mount
│   ├── models.py          # Pydantic v2 models (TaskCreate, Task, Priority)
│   ├── store.py           # in-memory store, injected via Depends()
│   └── routers/tasks.py   # /tasks routes in an APIRouter
├── pyproject.toml         # dependencies + pytest config
└── uv.lock                # pinned, reproducible versions (commit this)
```

## Install & run

Managed with [uv](https://docs.astral.sh/uv/). Python 3.10+ — uv installs a suitable
interpreter for you, so you don't need one preinstalled.

```bash
cd fastapi
uv run uvicorn app.main:app --reload

#or 
uv run app/main.py
```

That's the whole setup: `uv run` creates the virtualenv and installs the locked
dependencies on first use, then runs the command inside it. No `pip install`, no
manual `activate`. To provision the environment without starting the server, use
`uv sync`.

Open the auto-generated docs at **http://127.0.0.1:8000/docs** — you can try every endpoint from there.

## Try it

```bash
# create a task
curl -X POST http://127.0.0.1:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Write docs", "priority": "high"}'
# -> {"title":"Write docs","priority":"high","id":1,"done":false,"created_at":"..."}

# list tasks
curl http://127.0.0.1:8000/tasks

# mark done
curl -X POST http://127.0.0.1:8000/tasks/1/done

# every response carries the middleware header
# (-i is needed: HTTP/1.1 sends header names lowercased on the wire)
curl -i http://127.0.0.1:8000/health | grep -i x-process-time-ms
```

## Run the tests

```bash
uv run pytest -q
```

Expected: `5 passed`.

## Managing dependencies

```bash
uv add <package>              # add a runtime dependency
uv add --dev <package>        # add a test-only dependency
uv sync                       # install exactly what uv.lock pins
uv lock --upgrade             # refresh the lock to newer versions
```

`uv add` edits `pyproject.toml` and `uv.lock` together, so the two never drift apart.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: app` | Run from the `fastapi/` folder (not `app/`). Tests are immune — `pyproject.toml` sets `pythonpath = ["."]`. |
| Port 8000 in use | `uv run uvicorn app.main:app --reload --port 8001`. |
| `RuntimeError: install httpx` in tests | `uv sync` — the test client dependency lives in the `dev` group. |
| `invalid peer certificate: UnknownIssuer` | You're behind a TLS-inspecting proxy. Add `--system-certs` (e.g. `uv sync --system-certs`) or export `UV_SYSTEM_CERTS=1`. |

## Next Steps

- Read the [FastAPI: Quick Start](../../fastapi.html) page.
- Then [FastMCP code](../fastmcp/README.md) to expose tools to an AI instead of the web.
