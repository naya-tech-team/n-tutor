"""A runnable MCP server built with FastMCP.

It exposes three kinds of capability an AI client can use:
  - tools     : actions the AI can take        (add, convert_currency, search_notes,
                                                digest_notes, summarize_note)
  - resources : read-only data the AI can load (notes://all)
  - prompts   : reusable prompt templates       (summarize)

The last two tools take a `ctx: Context` parameter. That is how a tool talks back
to the client *while it runs* — logging, progress, reading other resources, and
borrowing the client's own LLM. See "Context" near the bottom of this file.

Run it (stdio transport — how Claude Desktop and most clients connect):
    pip install fastmcp
    python server.py

Or run over HTTP for a browser/other clients:
    fastmcp run server.py --transport http --port 8000

Test it without any AI using the bundled client:
    python client.py
"""

from __future__ import annotations

from fastmcp import Context, FastMCP

mcp = FastMCP("Notes & Utilities")

# A tiny in-memory "database" of notes.
_NOTES: dict[str, str] = {
    "welcome": "MCP gives an AI real tools and data.",
    "tip": "Docstrings tell the model when to use a tool.",
}

# Fixed demo exchange rates (so the example is deterministic/offline).
_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 83.0}


@mcp.tool
def add(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b


@mcp.tool
def convert_currency(amount: float, from_ccy: str, to_ccy: str) -> float:
    """Convert an amount between USD, EUR, GBP, and INR (demo rates)."""
    f, t = from_ccy.upper(), to_ccy.upper()
    if f not in _RATES or t not in _RATES:
        raise ValueError(f"unknown currency; known: {', '.join(_RATES)}")
    usd = amount / _RATES[f]
    return round(usd * _RATES[t], 2)


@mcp.tool
def search_notes(query: str) -> list[str]:
    """Return note titles whose text contains the query (case-insensitive)."""
    q = query.lower()
    return [title for title, body in _NOTES.items() if q in body.lower()]


@mcp.resource("notes://all")
def all_notes() -> str:
    """All notes as readable text (a resource the AI can load for context)."""
    return "\n".join(f"- {title}: {body}" for title, body in _NOTES.items())


@mcp.prompt
def summarize(text: str) -> str:
    """A reusable prompt template for summarizing text in one sentence."""
    return f"Summarize the following in one sentence:\n\n{text}"


# --------------------------------------------------------------------------
# Context — talking back to the client while the tool runs
#
# Add a `ctx: Context` parameter and FastMCP injects it automatically: the
# caller never passes it and it does not appear in the tool's public schema.
# The parameter can be named anything; the *type hint* is what FastMCP matches.
# Context only exists during a request, and its methods are async.
# --------------------------------------------------------------------------


@mcp.tool
async def digest_notes(ctx: Context) -> str:
    """Summarise every stored note as 'title: N words', for a quick overview.

    Use when someone wants to know what notes exist and how substantial they
    are, rather than the note text itself.
    """
    await ctx.info("Reading notes://all")  # log to the client

    # Read another resource this server exposes, from inside a tool. Reusing
    # the resource keeps one source of truth instead of touching _NOTES again.
    result = await ctx.read_resource("notes://all")
    lines = [ln for ln in result.contents[0].content.splitlines() if ln.strip()]

    digest: list[str] = []
    for i, line in enumerate(lines, start=1):
        title, _, body = line.removeprefix("- ").partition(": ")
        digest.append(f"{title}: {len(body.split())} words")
        await ctx.report_progress(progress=i, total=len(lines))  # move the bar

    await ctx.info(f"Digested {len(lines)} notes")
    return " | ".join(digest)


@mcp.tool
async def summarize_note(title: str, ctx: Context) -> str:
    """Summarise one stored note in a single sentence, by note title.

    Known titles come from `search_notes` or the `notes://all` resource.
    """
    if title not in _NOTES:
        raise ValueError(f"unknown note; known: {', '.join(_NOTES)}")

    # The clever one: ask the *client's* LLM to do a sub-task. The server
    # borrows the model from whoever is using it — no API key needed here.
    # Clients that don't support sampling will error, so keep it to tools
    # where that trade is worth it.
    reply = await ctx.sample(f"Summarize in one sentence:\n\n{_NOTES[title]}")
    return reply.text or "(the model returned nothing)"


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8080)  # stdio transport by default
