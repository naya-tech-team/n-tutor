# FastAPI + GraphQL Quick Start — runnable code

The same **HR Skills API** as the [FastAPI project](../fast-api/README.md), converted from REST to
**GraphQL** with [Strawberry](https://strawberry.rocks/): a typed schema, nested
resolvers, unions instead of status codes, a DataLoader that kills N+1, and a full
**pytest** suite. FastAPI still runs the process — GraphQL is one route on it.

The domain is the one the whole course uses: **employees have skills rated 1–5,
requisitions require skills at a minimum level, and some of those are mandatory.**
A candidate below a mandatory bar is *blocked* — no score saves them.

## Table of contents

- [What changed from REST](#what-changed-from-rest)
- [Layout](#layout)
- [The schema](#the-schema)
- [Install & run](#install--run)
- [Try it](#try-it)
- [Run the tests](#run-the-tests)
- [Poke at it with Bruno](#poke-at-it-with-bruno)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## What changed from REST

`models.py` and `store.py` are **untouched domain code** — the scoring engine does not
know which protocol is asking. Everything else moved:

| REST version | GraphQL version | Why |
|---|---|---|
| 8 URLs under `/employees` and `/requisitions` | one URL: `POST /graphql` | the client picks fields; the server stops guessing what a screen needs |
| `GET /requisitions/J2001/candidates` | `Requisition.candidates(limit:)` — a *field* | ranked candidates arrive inside the requisition query, no second round trip |
| `response_model=Employee` | `@strawberry.type class Employee` | the schema is the contract, published as SDL and introspectable |
| `404` requisition not found | `NotFound` union member | a mutation returns 200; outcomes live in the schema instead |
| `409` candidate blocked | `CandidateBlocked` union member | a client cannot forget the case — it has to name the type to read it |
| `422` level must be 1–5 | `ValidationFailed` union member | `Int!` types a field, it cannot bound it (see below) |
| `Query(3, ge=1, le=20)` | a check inside the resolver | same reason |
| `Depends(get_store)` per route | `Depends(get_store)` once, in the context | one route, so dependencies arrive through `info.context` |
| N calls to fetch N employees | `DataLoader` batches them into one | GraphQL's own failure mode, and its own fix |

`GET /health` stayed plain HTTP on purpose: a load balancer should not have to speak
GraphQL to check liveness.

## Layout

```
fast-api-graphql/
├── app/
│   ├── main.py               # FastAPI app: middleware, CORS, GraphQLRouter mount
│   ├── models.py             # Pydantic v2 domain models — unchanged from the REST version
│   ├── store.py              # in-memory store + the scoring engine — unchanged, plus a batch read
│   └── graph/
│       ├── context.py        # per-request context: the store + the DataLoader
│       ├── types.py          # object types, inputs, error types, unions, nested resolvers
│       └── schema.py         # Query, Mutation, strawberry.Schema
├── tests/test_graphql.py     # 31 pytest tests
├── bruno/                    # Bruno collection: 24 requests, 80 tests, runnable in CI
├── pyproject.toml            # dependencies + pytest config
└── uv.lock                   # pinned, reproducible versions (commit this)
```

> **Why `graph/` and not `graphql/`?** A local package named `graphql` sits on
> `sys.path` ahead of the installed **graphql-core** library when you run
> `python app/main.py`, and Strawberry's own `from graphql import ...` then finds
> your folder instead of the library. Cheap to avoid, confusing to debug.

## The schema

Everything a client may do, in one place — dump it with
`uv run strawberry export-schema app.graph.schema:schema`:

```graphql
type Query {
  health: String!
  employees(availableOnly: Boolean! = false): [Employee!]!
  employee(employeeId: ID!): Employee              # null, not an error, when missing
  requisitions: [Requisition!]!
  requisition(jobId: ID!): Requisition
}

type Mutation {
  addEmployee(employee: EmployeeInput!): AddEmployeeResult!
  shortlistCandidate(jobId: ID!, employeeId: ID!): ShortlistResult!
}

type Employee {
  employeeId: ID!  name: String!  location: String!
  availability: Availability!  experienceYears: Float!
  skills: [SkillRating!]!  createdAt: DateTime!
  match(jobId: ID!): MatchResult                   # a field that runs the scoring engine
}

type Requisition {
  jobId: ID!  title: String!  location: String!
  minExperienceYears: Float!  requiredSkills: [SkillRequirement!]!
  candidates(limit: Int! = 3): [MatchResult!]!     # ranked, best first
  shortlist: [ShortlistEntry!]!
}

union AddEmployeeResult = Employee | ValidationFailed
union ShortlistResult   = ShortlistEntry | NotFound | CandidateBlocked
```

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
cd fast-api-graphql
uv run uvicorn app.main:app --reload

# or
uv run app/main.py
```

That's the whole setup: `uv run` creates the virtualenv and installs the locked
dependencies on first use, then runs the command inside it. No `pip install`, no
manual `activate`. To provision the environment without starting the server, use
`uv sync`.

Open **http://127.0.0.1:8000/graphql** for **GraphiQL** — the in-browser explorer.
It reads the schema by introspection, so it autocompletes every field and shows the
docstrings above as documentation. There is no `/docs` here: the schema *is* the docs.

## Try it

Reads go over `GET` with `--data-urlencode`, so the query can stay readable across
several lines. (A raw newline is illegal inside a JSON string, so a `POST` body has
to keep the query on one line — or send it from a file, as the mutations below do.)

```bash
# ask for exactly two fields — you get exactly two fields
curl -sG http://127.0.0.1:8000/graphql --data-urlencode 'query=
{ employees(availableOnly: true) { name location } }'
# -> {"data":{"employees":[{"name":"Priya Raman","location":"Bengaluru"}, ...]}}

# one round trip: the role, its ranked candidates, their gaps and their records
curl -sG http://127.0.0.1:8000/graphql --data-urlencode 'query=
{ requisition(jobId: "J2001") {
    title
    candidates(limit: 3) {
      name score verdict blockers
      gaps { skill required actual }
      employee { location experienceYears }
    }
} }'
# -> Priya 100 strong · Rahul 79 blocked (Apache Spark) · Vikram 50 blocked (Python, SQL)

# scoring is a field on a person too — same engine, asked the other way round
curl -sG http://127.0.0.1:8000/graphql --data-urlencode 'query=
{ employee(employeeId: "e1002") { name match(jobId: "J2001") { score verdict } } }'

# a field that does not exist never reaches Python at all
curl -sG http://127.0.0.1:8000/graphql --data-urlencode 'query={ employees { salary } }'
# -> {"data":null,"errors":[{"message":"Cannot query field 'salary' on type 'Employee'..."}]}
```

Mutations must be `POST`. Sending the body on stdin keeps the quoting sane and shows
the shape real clients use — an operation plus `variables`:

```bash
# shortlist the strong one. The fragments are not optional: a union makes the
# client name every outcome it wants to read.
curl -s http://127.0.0.1:8000/graphql -H "Content-Type: application/json" --data-binary @- <<'JSON'
{"query": "mutation S($job: ID!, $emp: ID!) { shortlistCandidate(jobId: $job, employeeId: $emp) { __typename ... on ShortlistEntry { name score verdict } ... on CandidateBlocked { message blockers } ... on NotFound { message kind } } }",
 "variables": {"job": "J2001", "emp": "E1002"}}
JSON
# -> {"data":{"shortlistCandidate":{"__typename":"ShortlistEntry","name":"Priya Raman",...}}}

# now someone with no SQL on record — a mandatory skill. Same query, "emp": "E1005".
# -> {"__typename":"CandidateBlocked","message":"Vikram Iyer is missing mandatory Python, SQL"}
# HTTP 200, and no "errors" key: the server did exactly what it should.

# rejected by Pydantic, reported as data: levels are 1-5
curl -s http://127.0.0.1:8000/graphql -H "Content-Type: application/json" --data-binary @- <<'JSON'
{"query": "mutation A($e: EmployeeInput!) { addEmployee(employee: $e) { __typename ... on Employee { employeeId } ... on ValidationFailed { message invalidFields { field message } } } }",
 "variables": {"e": {"name": "X", "location": "Pune", "experienceYears": 1, "skills": [{"skill": "SQL", "level": 9}]}}}
JSON
# -> ValidationFailed · skills.0.level · "Input should be less than or equal to 5"

# every response still carries the middleware header
# (-i is needed: HTTP/1.1 sends header names lowercased on the wire)
curl -i -s http://127.0.0.1:8000/health | grep -i x-process-time-ms
```

**Two error channels, and they mean different things.** A malformed or invalid
*query* fails before any resolver runs and comes back under `errors` with `data:
null` — that is a bug in the client. A *domain* outcome (blocked, not found,
rejected input) comes back under `data` as a union member — that is the server
working correctly. Squashing both into `errors` is the most common way to make a
GraphQL API unpleasant to consume.

## Run the tests

```bash
uv run pytest -q
```

Expected: `31 passed`.

Each test gets its own store, exactly as in the REST version — the store reaches
resolvers through the GraphQL context, but the context is built by a FastAPI
dependency, so the override still works:

```python
store = HRStore()                                    # ONCE per test…
app.dependency_overrides[get_store] = lambda: store  # …handed to every request
```

Writing `lambda: HRStore()` there looks equivalent and is not: the provider runs
*per request*, so every call would get a brand-new store and nothing would persist
between two requests in the same test.

One test earns its keep more than the others:

```python
def test_dataloader_batches_employee_lookups(client, store):
    ...
    assert store.batch_calls == 1            # N+1 would make this 2
```

`HRStore.batch_calls` counts trips to the "database". Without the DataLoader in
`app/graph/context.py`, a shortlist of 30 people would make 30 of them.

## Poke at it with Bruno

`bruno/` is a [Bruno](https://www.usebruno.com/) collection — 24 requests grouped the way
this README is: **Meta · Queries · Mutations · Errors · Schema**. Every request carries its
own tests and a `docs` tab explaining what it demonstrates, so the collection doubles as a
guided tour of the schema.

### Open it in the Bruno app

A Bruno collection *is* a folder of `.bru` files, so there is nothing to import — **Import
Collection** is for Postman/Insomnia/OpenAPI exports and will not work here.

1. **Collection → Open Collection** (or the button on the welcome screen).
2. Pick the `fast-api-graphql/bruno` folder **itself**, not a file inside it. Bruno
   recognises it by the `bruno.json` at its root.
3. Allow the macOS folder-access prompt if one appears, or the collection opens empty.

Bruno remembers it, so that is a one-time step. Two things to do before running anything:

- **Start the API** (`uv run uvicorn app.main:app --reload`) — the collection points at
  `localhost`, so otherwise every request is a connection error.
- **Switch the environment** from *No Environment* to **Local**, top right. Skip it and
  every request fails on an unresolved `{{baseUrl}}`.

Then run one request with the **→** arrow (⌘↵), or right-click the collection → **Run** to
run all of them. Folders execute in the `seq` order recorded in each `folder.bru`, which
matters: *Mutations* shortlists someone before *Read the shortlist* checks the result.

Because the collection lives inside the repo, anything you change in the GUI is written
straight back to the `.bru` files and shows up in `git status`.

### Or run it headlessly

With the server already running:

```bash
cd fast-api-graphql/bruno
npx @usebruno/cli run --env Local -r
```

```
Requests      24 (24 Passed)
Tests         80/80
Assertions    14/14
```

That is the same command a CI job would use — `-r` recurses into the folders, and a failing
test exits non-zero.

### What to look at first

| Request | Shows |
|---|---|
| *Mutations › Shortlist a blocked candidate* | HTTP **200**, no `errors` key, and a `CandidateBlocked` object in `data` — the REST 409, moved into the schema |
| *Errors › Wrong argument type* vs *Mutations › Add an employee with an impossible level* | the same field rejected by two different layers: `Int!` catches the wrong **type**, Pydantic catches the wrong **value** |
| *Schema › Every way a mutation can end* | introspection listing all three arms of `ShortlistResult` — what lets a client handle every outcome without reading the source |

The collection is **re-runnable against a long-lived server**: shortlisting is idempotent,
the added employee is deliberately weak enough never to displace the ranking, and the
membership assertions use `include` rather than exact counts. A `tests` block in
`collection.bru` runs against *every* response, checking the 200 and the middleware's
`X-Process-Time-ms` header.

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
| `ModuleNotFoundError: No module named 'app'` | `app/main.py` puts the project root on `sys.path` before importing the schema, so both run commands above work from the `fast-api-graphql/` folder. Tests are immune either way — `pyproject.toml` sets `pythonpath = ["."]`. |
| `ImportError: cannot import name 'GraphQLError' from 'graphql'` | Something on `sys.path` is shadowing **graphql-core** — usually a folder or file of your own named `graphql`. That is why the package here is `app/graph/`. |
| `Cannot query field 'x' on type 'Y'` | The query asked for a field the schema does not have. Open `/graphql` and let GraphiQL autocomplete it — it reads the live schema. |
| Mutation returns `{}` or only `__typename` | You selected on a union without fragments. Add `... on ShortlistEntry { ... }` for each member you care about. |
| Port 8000 in use | `uv run uvicorn app.main:app --reload --port 8001`. |
| `RuntimeError: install httpx` in tests | `uv sync` — the test client dependency lives in the `dev` group. |
| Stale `.venv` copied from another folder | `rm -rf .venv && uv sync` — console scripts hard-code the interpreter path they were created with. |
| `invalid peer certificate: UnknownIssuer` | You're behind a TLS-inspecting proxy. Add `--system-certs` (e.g. `uv sync --system-certs`) or export `UV_SYSTEM_CERTS=1`. |

## Next Steps

- Read the [GraphQL: Quick Start](../docs/quickstart/graphql.html) page — it walks this code end to end.
- Compare with the [REST version](../fast-api/README.md) of the same API, and the [FastAPI: Quick Start](../docs/quickstart/fastapi.html) page.
- Then the [MCP server](../mcp-server/README.md) — the same domain, exposed to an AI instead of the web.
- Then the [Strands course](../strands-ai/README.md) — an agent that decides *when* to call it.
