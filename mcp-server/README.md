# FastMCP Quick Start — runnable code

A working **MCP server** that gives an AI real tools and real data: the HR team's
skills-matching engine, published once and callable from a Strands agent, Claude
Desktop, an IDE, or a plain script.

The domain is the one the whole course uses: **employees have skills rated 1–5,
requisitions require skills, and some of those are mandatory.** A candidate below a
mandatory bar is *blocked* — no score saves them.

> **The point of this project, in one line from the source:**
> *"This file has no idea Strands exists."* The team that owns employee data
> publishes tools once; every consumer speaks the protocol, not their library.

## Table of contents

- [Layout](#layout)
- [What it exposes](#what-it-exposes)
- [Install & run](#install--run)
- [Expected output](#expected-output)
- [Poke at it with Bruno](#poke-at-it-with-bruno)
- [Use it from Claude Desktop](#use-it-from-claude-desktop)
- [Use it from a Strands agent](#use-it-from-a-strands-agent)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## Layout

```
mcp-server/
├── app/
│   ├── main.py           # the MCP server: 4 tools
│   └── _shared/
│       ├── hr_data.py    # 12 employees, 6 requisitions, and match()
│       ├── config.py     # settings
│       └── llm.py        # unused here — the server needs no model of its own
├── bruno/                # Bruno collection: 17 requests, 61 tests, runnable in CI
├── pyproject.toml        # dependencies (fastmcp) + required Python version
└── uv.lock               # pinned, reproducible versions (commit this)
```

That the server needs **no model** is worth noticing. An MCP server is not an AI —
it is the hands. The intelligence lives in whatever client connects.

## What it exposes

The server is named `hr-skills` and exposes four tools:

| Tool | Does |
|------|------|
| `find_by_skill(skill, min_level=3, available_only=True)` | Everyone at or above a level in a skill. Accepts aliases — `"pyspark"` resolves to `"Apache Spark"`. |
| `get_requisition(job_id)` | One open role and the skills it requires, with `min_level`, `mandatory` and `weight`. |
| `score_match(employee_id, job_id)` | Score one person against one role: percentage, verdict, matched skills, gaps and **blockers**. |
| `shortlist(job_id, limit=3)` | Rank the best available candidates for a role, best first. |

No resources, no prompts — just tools. (The richer HR server in
[`strands-ai/app/00_tutorial/hr_mcp_server.py`](../strands-ai/app/00_tutorial/hr_mcp_server.py)
adds `hr://` resources and authored prompts if you want to see those.)

### The scoring is arithmetic, never a model call

`score_match` returns what `match()` computed — a weighted sum with partial credit
below the bar, and any mandatory skill under its minimum recorded as a blocker.
**A match score you cannot reproduce by hand is a score nobody will defend in a
hiring review**, so the model's job is to decide *who* to score and explain the
result, never to invent the number.

### Errors are returned, not raised

```python
return job or {"error": f"no requisition {job_id}"}
```

An MCP tool's return value is the next thing the model reads. A returned `{"error": …}`
is something it can act on; an unhandled exception is just a failed call. Better still,
say what to do next — *"unknown employee E1148; call find_by_skill and use an id it
returned"* — or a weak model will try E1149, then E1150, until something stops it.

## Install & run

Managed with [uv](https://docs.astral.sh/uv/). Python 3.12+ — uv installs a suitable
interpreter for you, so you don't need one preinstalled.

```bash
cd mcp-server

# Option A — run the server over HTTP (what main.py does today):
uv run app/main.py
# -> http://127.0.0.1:8000/mcp/

# Option B — poke at it in the MCP Inspector (browser UI):
uv run fastmcp dev app/main.py

# Option C — serve it on a different port, to avoid the FastAPI clash:
uv run fastmcp run app/main.py --transport http --port 8010
```

`uv run` creates the virtualenv and installs the locked dependencies on first use —
there is no separate install step.

### stdio vs. HTTP

The last line of `app/main.py` picks the transport:

```python
mcp.run(transport="http", show_banner=False, port=8000)   # serves /mcp/
```

| Transport | The client… | Use when |
|---|---|---|
| `http` | connects to a URL | the server runs somewhere and several clients share it |
| `stdio` | launches this file as a subprocess and talks over stdin/stdout | a desktop app owns the lifecycle (Claude Desktop does this) |

Same tools, same code, different plumbing. Swap the string and nothing else changes.

## Expected output

There is no `client.py` in this folder — twenty lines of `fastmcp.Client` is enough
to see everything. With the server running:

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://127.0.0.1:8000/mcp/") as c:
        print("tools     :", [t.name for t in await c.list_tools()])
        r = await c.call_tool("shortlist", {"job_id": "J2001", "limit": 3})
        print("shortlist :", [(m["name"], m["score"]) for m in r.data])

asyncio.run(main())
```

A real run:

```text
tools     : ['find_by_skill', 'get_requisition', 'score_match', 'shortlist']
resources : []
prompts   : []

find_by_skill(pyspark, 4) -> ['E1002', 'E1005']
score_match(E1005, J2001) -> 50 blocked ['Python', 'SQL']
shortlist(J2001)          -> [('Priya Raman', 100), ('Rahul Menon', 61), ('Vikram Iyer', 50)]
get_requisition(J2001)    -> Senior Data Engineer | 6 skills
```

Read the second and third lines together. **Vikram Iyer scores 50% and is `blocked`** —
J2001 marks Python and SQL mandatory and he has neither on record — yet he still
appears third in the shortlist. The ranking is by score; the *decision* is the
verdict. A client that shows the percentage and drops the blocker has told the
truth and caused the wrong outreach.

Note also that `find_by_skill("pyspark", 4)` finds people whose records say
*"Apache Spark"*. The alias table does that, not the model.

## Poke at it with Bruno

`bruno/` is a [Bruno](https://www.usebruno.com/) collection that speaks MCP over plain
HTTP — 17 requests grouped **Handshake · Discovery · Tools · Errors**, each with its own
tests and a `docs` tab. It is the fastest way to see the protocol *without* a client
library doing the interesting parts for you.

### Open it in the Bruno app

A Bruno collection *is* a folder of `.bru` files, so there is nothing to import.

1. **Collection → Open Collection**, and pick the `mcp-server/bruno` folder **itself** —
   Bruno recognises it by the `bruno.json` at its root.
2. Start the server (`uv run app/main.py`) and switch the environment to **Local**, top
   right.
3. Right-click the collection → **Run**. Or run *Handshake › Initialize* first if you want
   to step through requests one at a time — see below.

### Or run it headlessly

```bash
cd mcp-server/bruno
npx @usebruno/cli run --env Local -r
```

```
Requests      17 (17 Passed)
Tests         61/61
Assertions    17/17
```

If port 8000 is taken by [`fast-api/`](../fast-api/README.md), start this server with
`--port 8010` and change `baseUrl` in `environments/Local.bru`.

### Three things it makes visible

Every request is a `POST` to the **same URL** with the method name in the body, so nothing
you know about REST collections transfers. Three consequences, each with a request proving
it:

| | Request | What it shows |
|---|---|---|
| **Responses are Server-Sent Events** | *Handshake › Initialize* | `content-type: text/event-stream`, even for a plain request/response. A four-line script in `collection.bru` unwraps the `data:` lines so the tests can read `res.getBody().result` normally. |
| **The session is stateful** | *Errors › Calling without a session* | `initialize` mints an id in a **header**; every later request echoes it or gets a `400`/`404`. This is why folder order is load-bearing here and not in the REST collection. |
| **The status code stops meaning anything** | *Errors › Calling a tool that does not exist* | a failed tool is `200` with `isError: true`. `4xx` is reserved for the *transport* being misused — as in *Errors › Forgetting the SSE Accept header*, the single most common way a hand-rolled client fails. |

Also worth opening: *Discovery › List tools*, which is the whole argument for MCP in one
response — a client that has never seen this server learns four tool names, their
descriptions and a JSON Schema per argument, all derived from the Python signatures in
`app/main.py`.

Nothing here mutates server state, so the collection is re-runnable as often as you like.
Stepping through single requests works too, as long as *Handshake › Initialize* has run at
least once in the session — and re-running it is also the fix after the server restarts,
since sessions live in memory.

### Compare it with the REST collection

[`fast-api/bruno`](../fast-api/README.md) publishes this same HR domain as eight REST
endpoints. Run both and the contrast is sharp: a candidate blocked on a mandatory skill is
a **409** there, and a `200` carrying `{"error": …}` here — because the reader on the other
end is a model that has to decide what to do next, not a client that has to branch on a
status code.

## Use it from Claude Desktop

Let the CLI write the config entry for you:

```bash
uv run fastmcp install claude-desktop app/main.py
```

Then restart Claude Desktop and ask it: *"Who could fill requisition J2001?"* — it
will call `shortlist` on its own. `fastmcp install` also supports `claude-code`,
`cursor`, `gemini-cli` and `goose`; use `mcp-json` to print the JSON and wire it up
by hand.

Claude Desktop launches servers over **stdio**, so switch the transport in
`app/main.py` before installing.

## Use it from a Strands agent

The same four tools, consumed by an agent that chooses when to call them:

```python
from strands import Agent
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

hr = MCPClient(lambda: streamablehttp_client("http://127.0.0.1:8000/mcp/"))

with hr:                      # the block is load-bearing — see below
    agent = Agent(tools=hr.list_tools_sync(), model=make_model())
    print(agent("Who are the top 2 available candidates for J2001?"))
```

**Leaving the `with` block kills the connection**, and a dead MCP server looks like
an agent that quietly starts improvising — it still has the tool *names* in its
history, so it makes plausible answers up. Lesson 02 and step 11 of the tutorial in
[`strands-ai/`](../strands-ai/README.md) do exactly this.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `[Errno 48] address already in use` | Something else is on port 8000 — most likely [`fast-api/`](../fast-api/README.md), which defaults to the same port. Run one, or start this on `--port 8010`. |
| `ModuleNotFoundError: fastmcp` | Use `uv run …` rather than a bare `python …` — it resolves the environment for you. |
| `ModuleNotFoundError: _shared` | Run it as a script (`uv run app/main.py`) so Python puts `app/` on the path. |
| Client connects but lists no tools | Check the URL ends in `/mcp/` — the bare origin returns a 307 redirect. |
| Claude Desktop doesn't see it | Switch to `transport="stdio"`, re-run the install command, then fully quit and reopen the app (not just close the window). |
| Tools vanish mid-conversation | Your client left the `with` block. The agent will improvise rather than error. |
| Nothing on stdout from a **stdio** server | Correct — a stdio server talks over stdout. Never `print()` in one; log via `ctx` instead. (Under HTTP, printing is harmless.) |
| A score looks wrong | It came from `match()`, not a model. Check `hr_data.py`: `weight`, `min_level` and `mandatory` decide everything. |

## Next Steps

- Read the [FastMCP: Quick Start](../docs/quickstart/fastmcp.html) page.
- Or follow the [FastMCP session transcript](../docs/transcript/fastmcp.html), which builds a server from an empty file.
- Then connect it to an agent — the [Strands course](../strands-ai/README.md) does this in lesson 02, and [`a2a-strands/`](../a2a-strands/README.md) shows the other protocol: delegation *between* agents rather than tools *for* one.
