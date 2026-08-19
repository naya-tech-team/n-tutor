"""The A2A protocol with no AI on the calling side.

When a delegation misbehaves there are two suspects: the protocol and the model.
This removes one of them. It reads a card and sends one message — no agent, no
tools, no reasoning — so whatever comes back came from the remote agent alone.

Run it against whichever specialist you are doubting:

    uv run app/clients/raw_client.py                    # the screener
    uv run app/clients/raw_client.py 9002               # the writer
    uv run app/clients/raw_client.py 9007               # the reviewer
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.a2a_call import call_agent, describe_agent  # noqa: E402

QUESTIONS = {
    9001: "Who are the top 2 available candidates for J2001?",
    9002: (
        "Draft an outreach note using only these screening facts:\n"
        "E1002 Priya Raman — 100% strong, strengths: Python L4, Apache Spark L5, "
        "SQL L5, blockers: none"
    ),
    9007: (
        "Review this note. E1002 / J2001.\n"
        "Hi Priya, you are a perfect fit and we can offer you a promotion."
    ),
}


async def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    url = f"http://127.0.0.1:{port}"

    try:
        name, skills = await describe_agent(url)
    except Exception as exc:  # noqa: BLE001 — any failure here is the same advice
        print(f"✗ nothing at {url} ({type(exc).__name__}). Start that agent first.")
        return

    print(f"card     : {name}")
    print(f"skills   : {skills}")

    question = QUESTIONS.get(port, "What can you do?")
    print(f"\nasking   : {question.splitlines()[0]}")

    reply = await call_agent(url, question)
    print(f"\nreply    : {reply}")


if __name__ == "__main__":
    asyncio.run(main())
