# FastMCP Quick Start — runnable code

A working **MCP server** that gives an AI real tools and data, plus a **plain client** so you can see it work without any AI model.

## Table of contents

- [Layout](#layout)
- [Install & run](#install--run)
- [Expected output](#expected-output)
- [Use it from Claude Desktop](#use-it-from-claude-desktop)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## Layout

```
map-server/
├── server.py         # MCP server: 5 tools, 1 resource, 1 prompt
├── client.py         # launches the server over stdio and calls everything
├── pyproject.toml    # dependencies (fastmcp) + required Python version
└── uv.lock           # pinned, reproducible versions (commit this)
```

The server is named `Notes & Utilities` and exposes:

| Kind | Name | Does |
|------|------|------|
| tool | `add(a, b)` | Adds two numbers. |
| tool | `convert_currency(amount, from_ccy, to_ccy)` | Converts between USD, EUR, GBP, INR (fixed demo rates, so it works offline). |
| tool | `search_notes(query)` | Returns note titles whose **body** contains the query. |
| tool | `digest_notes()` | Every note as `title: N words`. Uses **Context** — logs, reads `notes://all`, reports progress. |
| tool | `summarize_note(title)` | One-sentence summary of a note, produced by the **client's** model via `ctx.sample()`. |
| resource | `notes://all` | All notes as readable text. |
| prompt | `summarize(text)` | A reusable one-sentence-summary template. |

### Context — talking back while a tool runs

Most tools take inputs and return a value. The last two take a `ctx: Context`
parameter instead, which is how a tool communicates *during* the call:

```python
@mcp.tool
async def digest_notes(ctx: Context) -> str:
    """Summarise every stored note as 'title: N words'."""
    await ctx.info("Reading notes://all")                    # log to the client
    result = await ctx.read_resource("notes://all")          # read another resource
    ...
    await ctx.report_progress(progress=i, total=len(lines))  # move the bar
```

Three things worth knowing:

- **You never pass `ctx`.** FastMCP spots the `Context` type hint and injects it, and
  it stays out of the tool's public schema. The parameter can be named anything — the
  *type* is what matters.
- **Context only exists during a request**, and its methods are `async` — so these are
  `async def` tools with `await`.
- **`ctx.sample()` inverts the usual direction**: instead of the AI calling your tool,
  your tool asks the *client's* model to do a sub-task. Your server gets model access
  without owning an API key. Clients that don't support sampling will error on it.

`client.py` supplies a `log_handler`, `progress_handler`, and `sampling_handler`, so
running it shows you exactly what each of those calls delivers. Its sampling handler
returns a canned string in place of a real model, which keeps the demo offline.

## Install & run

Managed with [uv](https://docs.astral.sh/uv/). Python 3.12+ — uv installs a suitable
interpreter for you, so you don't need one preinstalled.

```bash
cd map-server

# Option A — see it work end to end (recommended first):
uv run client.py

# Option B — just run the server (stdio), for an AI client to connect:
uv run server.py

# Option C — run it over HTTP instead of stdio:
uv run fastmcp run server.py --transport http --port 8000

# Option D — poke at it in the MCP Inspector (browser UI):
uv run fastmcp dev inspector server.py
```

`uv run` creates the virtualenv and installs the locked dependencies on first use —
there is no separate install step.

## Expected output

Running `uv run client.py`:

```text
Tools:     ['add', 'convert_currency', 'search_notes', 'digest_notes', 'summarize_note']
Resources: ['notes://all']
Prompts:   ['summarize']

add(2, 3)               -> 5.0
convert 100 USD -> EUR  -> 92.0
search_notes('tool')    -> ['welcome', 'tip']

notes://all:
- welcome: MCP gives an AI real tools and data.
- tip: Docstrings tell the model when to use a tool.

digest_notes()
   [server:info] Reading notes://all
   [progress] 1/2
   [progress] 2/2
   [server:info] Digested 2 notes
  -> welcome: 8 words | tip: 9 words

summarize_note('welcome')   # server calls back into our model
  -> (pretend summary from the client's model)
```

The indented `[server:…]` and `[progress]` lines are the Context calls arriving at
the client's handlers — they are printed by `client.py`, not by the tool's return value.

FastMCP prints a startup banner and a `Starting MCP server … with transport 'stdio'`
log line above this. That's the server booting as a subprocess, not an error.

Both notes match `search_notes('tool')` because the search looks at note **bodies**,
and both bodies contain the word "tool" — `welcome` has "real tools and data".

## Use it from Claude Desktop

There is no config file to copy in this folder — let the CLI write the entry for you:

```bash
uv run fastmcp install claude-desktop server.py
```

Then restart Claude Desktop and ask it: *"Convert 50 GBP to INR"* — it will call your tool.

To wire it up by hand instead (or to install into another client), generate the JSON:

```bash
uv run fastmcp install mcp-json server.py
```

`fastmcp install` also supports `claude-code`, `cursor`, `gemini-cli`, and `goose`.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: fastmcp` | Use `uv run …` rather than a bare `python …` — it resolves the environment for you. |
| Client hangs | Run `client.py` from this folder. It points at `server.py` by relative path. |
| Claude Desktop doesn't see it | Re-run the install command and fully quit and reopen the app (not just close the window). |
| `unknown currency` | Supported: USD, EUR, GBP, INR. |
| `unknown note` | Titles come from `search_notes` or `notes://all` — `welcome` and `tip`. |
| `summarize_note` fails in another client | That client doesn't support sampling. `ctx.sample()` needs the client to run a model; the other tools don't. |
| No `[server:…]` / `[progress]` lines | The client didn't register a `log_handler` / `progress_handler`. Without one, those notifications are simply dropped. |
| Nothing appears on stdout from the server | Correct — a stdio server talks over stdout. Never `print()` in one; log via `ctx` instead. |

## Next Steps

- Read the [FastMCP: Quick Start](../docs/quickstart/fastmcp.html) page.
- Or follow the [FastMCP session transcript](../docs/transcript/fastmcp.html), which builds this exact server from an empty file.
- Then connect it to an agent — see the [Strands session transcript](../docs/transcript/strands.html) for pointing an agent at this server, and the [A2A session transcript](../docs/transcript/a2a.html) for agent-to-agent delegation.
