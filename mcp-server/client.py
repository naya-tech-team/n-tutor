"""A plain MCP client that talks to server.py — no AI model needed.

It launches the server as a subprocess over stdio, lists what the server
offers, then calls each capability and prints the result. This is the best
way to see exactly what an AI client would receive.

Run:
    uv run client.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Client


# --- Handlers: the client side of Context -------------------------------
# A tool's ctx.info() / ctx.report_progress() / ctx.sample() calls arrive
# here. Supply a handler and you see them; omit one and it is discarded.


async def on_log(message: Any) -> None:
    """Receives ctx.debug/info/warning/error from the server."""
    data = message.data
    text = data.get("msg", data) if isinstance(data, dict) else data
    print(f"   [server:{message.level}] {text}")


async def on_progress(progress: float, total: float | None, message: str | None) -> None:
    """Receives ctx.report_progress — drive a progress bar from this."""
    print(f"   [progress] {progress:g}/{total:g}" if total else f"   [progress] {progress:g}")


async def on_sample(messages: Any, params: Any, context: Any) -> str:
    """Stands in for the client's LLM when the server calls ctx.sample().

    A real client would forward this to its model. Returning a canned string
    keeps the demo offline while showing where the model plugs in.
    """
    return "(pretend summary from the client's model)"


# Point the client at the server script; FastMCP starts it over stdio.
client = Client(
    "server.py",
    log_handler=on_log,
    progress_handler=on_progress,
    sampling_handler=on_sample,
)


async def main() -> None:
    async with client:
        # Discovery — what does this server expose?
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        print("Tools:    ", [t.name for t in tools])
        print("Resources:", [str(r.uri) for r in resources])
        print("Prompts:  ", [p.name for p in prompts])

        # Call a tool.
        result = await client.call_tool("add", {"a": 2, "b": 3})
        print("\nadd(2, 3)               ->", result.data)

        result = await client.call_tool(
            "convert_currency", {"amount": 100, "from_ccy": "USD", "to_ccy": "EUR"}
        )
        print("convert 100 USD -> EUR  ->", result.data)

        result = await client.call_tool("search_notes", {"query": "tool"})
        print("search_notes('tool')    ->", result.data)

        # Read a resource.
        notes = await client.read_resource("notes://all")
        print("\nnotes://all:\n" + notes[0].text)

        # Tools that use Context. Note the ctx parameter is never passed —
        # the server injects it — and the handlers above print what it emits.
        print("\ndigest_notes()")
        result = await client.call_tool("digest_notes", {})
        print("  ->", result.data)

        print("\nsummarize_note('welcome')   # server calls back into our model")
        result = await client.call_tool("summarize_note", {"title": "welcome"})
        print("  ->", result.data)


if __name__ == "__main__":
    asyncio.run(main())
