"""Recruiting Outreach Agent — A2A, and the deliberate opposite of the screener.

**No tools at all.** It cannot look anything up. It is pure writing skill working
entirely from facts the caller hands it, and that is what makes it the sharpest
test of the pipeline: if the screening facts were summarised on the way here, the
note is wrong and nothing downstream can tell.

From the outside the two agents are indistinguishable — same card format, same
protocol, same task lifecycle — even though one owns a scoring engine and the
other owns a house style. A protocol is what lets you not care.

    uv run app/runtimes/recruiting_outreach/main.py     # http://127.0.0.1:9002
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from a2a.types import AgentSkill
from strands import Agent

from _shared import ToolBudget, a2a_serve, install, make_model, model_banner, settings

install()

LOCAL_PORT = 9002

SYSTEM_PROMPT = (
    "You write short internal recruiting outreach notes to employees about an "
    "open role.\n\n"
    "You have no access to HR data. Use only the facts in the message you were "
    "given — never invent a score, a skill level, a salary, a band, or a start "
    "date. If you were given no named strength, do not name one.\n\n"
    "CHOOSING WHO TO WRITE TO: if several candidates are listed, write to the "
    "highest-scoring one that has NO blockers. Never write to a candidate with "
    "blockers — a blocker means they are missing a skill the role requires, and "
    "approaching them would be misleading. If every candidate is blocked, write "
    "no note and reply exactly: 'No candidate is clear to approach.'\n\n"
    "Write at most four sentences: greet them by first name, say which role and "
    "why the screening flagged them, name one concrete strength you were given, "
    "and ask whether they would like a conversation. Never promise an offer, a "
    "promotion or pay. Never mention age, gender, family status, health or "
    "nationality.\n\n"
    "Output only the note itself."
)

DESCRIPTION = (
    "Drafts short, factual internal outreach notes to candidates about an "
    "open role, using only the screening facts it is given."
)

OUTREACH_SKILL = AgentSkill(
    id="candidate_outreach_note",
    name="Candidate outreach note",
    description=(
        "Draft a short, factual internal outreach note to a candidate about an open "
        "role. Requires the screening facts — this agent cannot look up candidates, "
        "scores or requisitions itself."
    ),
    tags=["recruiting", "outreach", "writing"],
    examples=[
        "Write to Priya Raman (E1002) about J2001 — she scored 100%, strong, Spark 5.",
    ],
)


def build_writer(context_id: str) -> Agent:
    """Build a fresh writer per A2A conversation — one per caller's context."""
    return Agent(
        name="Recruiting Outreach Agent",
        description=DESCRIPTION,
        model=make_model(temperature=0.4),  # a little warmer: this one writes prose
        # No tools, so the budget can never fire — carried anyway so that adding
        # a tool later cannot quietly reintroduce an unbounded loop.
        hooks=[ToolBudget(max_calls=2)],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )


if __name__ == "__main__":
    print(model_banner())
    a2a_serve.serve(
        build_writer,
        name="Recruiting Outreach Agent",
        description=DESCRIPTION,
        skills=[OUTREACH_SKILL],
        local_port=LOCAL_PORT,
    )
