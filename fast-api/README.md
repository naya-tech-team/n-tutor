# FastAPI Quick Start — runnable code

A small **HR Skills API** showing the pieces from the FastAPI: Quick Start page working together: Pydantic v2 models, two `APIRouter`s, custom **middleware**, **CORS**, **dependency injection** (`Depends`), and a full **pytest** suite.

The domain is the one the whole course uses: **employees have skills rated 1–5, requisitions require skills at a minimum level, and some of those are mandatory.** A candidate below a mandatory bar is *blocked* — no score saves them.

## Table of contents

- [Layout](#layout)
- [Install & run](#install--run)
- [Try it](#try-it)
- [Run the tests](#run-the-tests)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## Layout

```
fast-api/
├── app/
│   ├── main.py                 # FastAPI app: middleware, CORS, router mounts
│   ├── models.py               # Pydantic v2 models (Employee, Requisition, MatchResult…)
│   ├── store.py                # in-memory store + the scoring engine, injected via Depends()
│   └── routers/candidates.py   # /employees and /requisitions routes in two APIRouters
├── tests/test_api.py           # 19 pytest tests
├── pyproject.toml              # dependencies + pytest config
└── uv.lock                     # pinned, reproducible versions (commit this)
```

## The endpoints

| Endpoint | Does |
|---|---|
| `GET /employees?available_only=true` | the directory, optionally just the bench |
| `POST /employees` | add someone (levels validated 1–5 at the edge) |
| `GET /employees/{employee_id}` | one person |
| `GET /requisitions` · `GET /requisitions/{job_id}` | open roles and their required skills |
| `GET /requisitions/{job_id}/candidates?limit=3` | everyone scored and ranked, best first |
| `GET /requisitions/{job_id}/candidates/{employee_id}` | one score, with gaps and blockers |
| `GET /requisitions/{job_id}/shortlist` | who has been shortlisted so far |
| `POST /requisitions/{job_id}/shortlist` | shortlist someone — **409** if they are blocked |

**`store.score()` is arithmetic, never a guess.** A match score you cannot
reproduce by hand is a score nobody will defend in a hiring review.

> **On the numbers.** This project carries a trimmed dataset — four people and two
> requisitions, enough to show every verdict. The *algorithm* is the course's
> (weighted skills, partial credit below the bar, mandatory skills as blockers),
> but J2001 lists four required skills here versus six in the
> [Strands](../strands-ai/README.md) and [MCP](../mcp-server/README.md) tracks. So
> Priya is 100% everywhere, while Rahul is 79% here and 61% there. Same rules,
> smaller world.

## Install & run

Managed with [uv](https://docs.astral.sh/uv/). Python 3.10+ — uv installs a suitable
interpreter for you, so you don't need one preinstalled.

```bash
cd fast-api
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
# who is on the bench?
curl http://127.0.0.1:8000/employees?available_only=true

# rank everyone against the open Senior Data Engineer role
curl http://127.0.0.1:8000/requisitions/J2001/candidates
# -> [{"employee_id":"E1002","name":"Priya Raman","score":100,"verdict":"strong","blockers":[]},
#     {"employee_id":"E1003","name":"Rahul Menon","score":79,"verdict":"blocked",
#      "blockers":["Apache Spark"]}, ...]

# shortlist the strong one
curl -X POST http://127.0.0.1:8000/requisitions/J2001/shortlist \
  -H "Content-Type: application/json" -d '{"employee_id": "E1002"}'
# -> 201 {"employee_id":"E1002","name":"Priya Raman","score":100,"verdict":"strong",...}

# now try someone with no SQL on record — a mandatory skill
curl -i -X POST http://127.0.0.1:8000/requisitions/J2001/shortlist \
  -H "Content-Type: application/json" -d '{"employee_id": "E1005"}'
# -> 409 {"detail":"Vikram Iyer is missing mandatory Python, SQL"}

# rejected before it reaches any route: levels are 1-5
curl -i -X POST http://127.0.0.1:8000/employees \
  -H "Content-Type: application/json" \
  -d '{"name":"X","location":"Pune","experience_years":1,"skills":[{"skill":"SQL","level":9}]}'
# -> 422, naming the field and the rule

# every response carries the middleware header
# (-i is needed: HTTP/1.1 sends header names lowercased on the wire)
curl -i http://127.0.0.1:8000/health | grep -i x-process-time-ms
```

Note the status codes: **422** "your JSON is wrong" · **404** "no such thing" ·
**409** "understood, but it conflicts with the current state". That distinction is
what lets a client tell a bug apart from a business rule.

## Run the tests

```bash
uv run pytest -q
```

Expected: `19 passed`.

Each test gets its own store via `app.dependency_overrides`, so no test can see
another test's shortlist:

```python
store = HRStore()                                    # ONCE per test…
app.dependency_overrides[get_store] = lambda: store  # …handed to every request
```

Writing `lambda: HRStore()` there looks equivalent and is not: the provider runs
*per request*, so every call would get a brand-new store and nothing would persist
between two requests in the same test. (Yes, that bug was written first.)

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
| `ModuleNotFoundError: No module named 'app'` | `app/main.py` puts the project root on `sys.path` before importing the routers, so both run commands above work from the `fast-api/` folder. If you moved or copied a module out of `app/`, that bootstrap is what you lost. Tests are immune either way — `pyproject.toml` sets `pythonpath = ["."]`. |
| Port 8000 in use | `uv run uvicorn app.main:app --reload --port 8001`. |
| `RuntimeError: install httpx` in tests | `uv sync` — the test client dependency lives in the `dev` group. |
| `invalid peer certificate: UnknownIssuer` | You're behind a TLS-inspecting proxy. Add `--system-certs` (e.g. `uv sync --system-certs`) or export `UV_SYSTEM_CERTS=1`. |

## Next Steps

- Read the [FastAPI: Quick Start](../docs/quickstart/fastapi.html) page.
- Then the [MCP server](../mcp-server/README.md) — the same domain, exposed to an AI instead of the web.
- Then the [Strands course](../strands-ai/README.md) — an agent that decides *when* to call it.
