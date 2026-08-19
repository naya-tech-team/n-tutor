# FastAPI Quick Start — runnable code

A small **HR Skills API** showing the pieces from the FastAPI: Quick Start page working together: Pydantic v2 models, two `APIRouter`s, custom **middleware**, **CORS**, **dependency injection** (`Depends`), and a full **pytest** suite.

The domain is the one the whole course uses: **employees have skills rated 1–5, requisitions require skills at a minimum level, and some of those are mandatory.** A candidate below a mandatory bar is *blocked* — no score saves them.

## Table of contents

- [Layout](#layout)
- [Install & run](#install--run)
- [Try it](#try-it)
- [Run the tests](#run-the-tests)
- [Poke at it with Bruno](#poke-at-it-with-bruno)
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
├── bruno/                      # Bruno collection: 24 requests, 79 tests, runnable in CI
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

## Poke at it with Bruno

`bruno/` is a [Bruno](https://www.usebruno.com/) collection — 24 requests grouped **Meta ·
Employees · Requisitions · Matching · Shortlist · Errors**. Every request carries its own
tests and a `docs` tab explaining what it demonstrates, so the collection doubles as a
guided tour of the API.

### Open it in the Bruno app

A Bruno collection *is* a folder of `.bru` files, so there is nothing to import — **Import
Collection** is for Postman/Insomnia/OpenAPI exports and will not work here.

1. **Collection → Open Collection** (or the button on the welcome screen).
2. Pick the `fast-api/bruno` folder **itself**, not a file inside it. Bruno recognises it by
   the `bruno.json` at its root.
3. Allow the macOS folder-access prompt if one appears, or the collection opens empty.

Two things to do before running anything:

- **Start the API** — the collection points at `localhost`, so otherwise every request is a
  connection error.
- **Switch the environment** from *No Environment* to **Local**, top right. Skip it and
  every request fails on an unresolved `{{baseUrl}}`.

> **On the port.** `Local` expects **8100**, which is what `uv run app/main.py` serves and
> what this repo's VS Code launch config starts. Running `uv run uvicorn app.main:app
> --reload` instead serves **8000** — change `baseUrl` in `environments/Local.bru` if you
> use that command.

Then run one request with the **→** arrow (⌘↵), or right-click the collection → **Run** to
run all of them. Folders execute in the `seq` recorded in each `folder.bru`, which matters:
*Employees* creates a person *Matching* then has to tolerate in the rankings, and
*Shortlist* adds an entry before *Read the shortlist* checks it.

Because the collection lives inside the repo, anything you change in the GUI is written
straight back to the `.bru` files and shows up in `git status`.

### Or run it headlessly

With the server already running:

```bash
cd fast-api/bruno
npx @usebruno/cli run --env Local -r
```

```
Requests      24 (24 Passed)
Tests         79/79
Assertions    24/24
```

That is the same command a CI job would use — `-r` recurses into the folders, and a failing
test exits non-zero.

### What to look at first

| Request | Shows |
|---|---|
| *Shortlist › Shortlist a blocked candidate* | **409**, not 400 — the request was fine, the *state* refused it |
| *Matching › An allocated employee never ranks* | Arjun scores 86 and `strong`, and still never appears in a candidate list |
| *Matching › The same people, a different role* | the ranking inverts on J2002 — there is no good candidate, only a good match |
| *Errors › An impossible skill level* | a `422` whose `loc` names the request part, field, array index and attribute |

The collection is **re-runnable against a long-lived server**: shortlisting is idempotent,
the employee it creates is too weak to displace anyone in a top-3, and membership
assertions use `include` rather than exact counts. A `tests` block in `collection.bru` runs
against *every* response, checking the middleware's `X-Process-Time-ms` header — including
on the 404s and 422s, which is where middleware most often turns out not to run.

### Compare it with the GraphQL collection

[`fast-api-graphql/bruno`](../fast-api-graphql/README.md) is the same domain over GraphQL,
and the two are worth opening side by side. There, *every* response is **200** and the
outcome lives in the body; here the status line carries it. Same refusal, two designs.

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
