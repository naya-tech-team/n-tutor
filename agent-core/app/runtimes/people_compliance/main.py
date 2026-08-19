"""People Compliance Reviewer — A2A. The one that answers "who checked this?"

It reviews notes; it never writes them. Its one tool re-derives the match from
the record, so a note claiming a score the HR data does not support gets rejected
even when every agent upstream believed it.

This is the fourth runtime and the cheapest insurance in the system: the failure
it catches — a warm note to someone who cannot do the job — is the one failure
this domain actually cares about.

    uv run app/runtimes/people_compliance/main.py     # http://127.0.0.1:9007
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from a2a.types import AgentSkill
from strands import Agent, tool
from strands.hooks import BeforeInvocationEvent, HookProvider, HookRegistry

from _shared import ToolBudget, a2a_serve, get_employee, get_job, install, make_model, match
from _shared import model_banner, settings

install()

LOCAL_PORT = 9007

ID_PATTERN = re.compile(r"\b(E\d{4})\b.*?\b(J\d{4})\b", re.S)


@tool
def verify_match_claim(employee_id: str, job_id: str) -> str:
    """Check what the HR record actually says about a candidate-to-role match.

    Use this whenever an outreach note states a score, a skill level, or claims
    someone is a strong fit — before approving it.

    Args:
        employee_id: e.g. "E1002"
        job_id: e.g. "J2001"
    """
    print(f"  [compliance] verify_match_claim({employee_id!r}, {job_id!r})", flush=True)
    employee, job = get_employee(employee_id), get_job(job_id)
    if employee is None or job is None:
        return f"NOT ON RECORD: no employee {employee_id!r} or requisition {job_id!r}."
    result = match(employee, job)
    return (
        f"{result['name']} vs {result['title']}: score {result['score']}%, "
        f"verdict {result['verdict']}, blockers {result['blockers'] or 'none'}, "
        f"meets_experience {result['meets_experience']}."
    )


class GroundTruth(HookProvider):
    """Attach the HR record to the request before the model ever sees it.

    The original version of this agent asked the model to call
    `verify_match_claim` first. It did not. Worse, it then *rejected* a note for
    claims "not verified by verify_match_claim" — asserting a check it had never
    run, against facts that were all true.

    **A reviewer that rubber-stamps rejections is as broken as one that
    rubber-stamps approvals.** So the verification stops being something the
    model chooses to do. The ids are extracted with a regex, `match()` computes
    the truth, and the answer is appended to the request. The tool stays for the
    cases this misses — a note naming a second person — but the common path no
    longer depends on the model's judgement about whether to check.

    This is the same rule as everywhere else in the domain: `match()` is
    arithmetic, and arithmetic should not be optional.
    """

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(BeforeInvocationEvent, self._attach)

    def _attach(self, event: BeforeInvocationEvent) -> None:
        messages = getattr(event, "messages", None) or getattr(event.agent, "messages", [])
        if not messages:
            return
        blocks = messages[-1].get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))

        found = ID_PATTERN.search(text)
        if not found:
            return
        employee_id, job_id = found.groups()
        employee, job = get_employee(employee_id), get_job(job_id)
        if employee is None or job is None:
            truth = f"HR RECORD: no employee {employee_id} or requisition {job_id}."
        else:
            result = match(employee, job)
            truth = (
                f"HR RECORD for {employee_id} vs {job_id}: {result['name']}, "
                f"{result['title']} in {job['location']}. Score {result['score']}%, "
                f"verdict {result['verdict']}, blockers {result['blockers'] or 'none'}. "
                "Skills on record: "
                + ", ".join(f"{s['skill']} L{s['level']}" for s in employee["skills"])
                + "."
            )
        print(f"  [compliance] ground truth attached for {employee_id}/{job_id}", flush=True)
        blocks.append({"text": f"\n\n{truth}"})


SYSTEM_PROMPT = (
    "You are the company's compliance review for internal recruiting outreach. "
    "You review notes; you never write them.\n\n"
    "Every request ends with a line beginning 'HR RECORD:'. That is the company's "
    "own data, already looked up for you — it is the truth. Judge the note against "
    "it. Do not claim anything is unverified: you have the record in front of you.\n\n"
    "Reject a note only if it:\n"
    "- references age, gender, family status, health, or nationality;\n"
    "- promises pay, promotion, a specific project, or an offer;\n"
    "- states a score, skill level, role title or location that CONTRADICTS the HR "
    "RECORD line;\n"
    "- is addressed to a candidate whose verdict is 'blocked'.\n\n"
    "A skill level the HR RECORD confirms is correct — approve it. Naming the role "
    "and location from the record is correct — approve it.\n\n"
    "Reply with exactly 'APPROVED' or 'REJECTED:' followed by the specific edits "
    "required. Two sentences maximum."
)

DESCRIPTION = (
    "Reviews internal recruiting outreach for bias, over-promising and "
    "claims the HR record does not support."
)

REVIEW_SKILL = AgentSkill(
    id="outreach_compliance_review",
    name="Recruiting outreach compliance review",
    description=(
        "Review an internal recruiting outreach note for discriminatory language, "
        "over-promising, and unverifiable claims about a candidate's match. Returns "
        "APPROVED or the required edits."
    ),
    tags=["recruiting", "compliance", "fairness", "review"],
    examples=[
        "Review this note to E1002 about J2001: 'Hi Priya, you're a perfect 100% fit...'",
        "Is this outreach note compliant?",
    ],
)


def build_reviewer(context_id: str) -> Agent:
    """Build a fresh reviewer per A2A conversation."""
    return Agent(
        name="People Compliance Reviewer",
        description=DESCRIPTION,
        model=make_model(),
        # The tool remains for the cases the hook cannot see — a note naming a
        # second person, or a follow-up question. It is no longer the only path
        # to the truth, which is the point.
        tools=[verify_match_claim],
        # GroundTruth runs first and makes the record unconditional. ToolBudget is
        # here because there is no call site to pass limits to, and a looping
        # reviewer blocks the pipeline it exists to unblock.
        hooks=[GroundTruth(), ToolBudget(max_calls=3)],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )


if __name__ == "__main__":
    print(f"data_source={settings.data_source} {model_banner()}")
    a2a_serve.serve(
        build_reviewer,
        name="People Compliance Reviewer",
        description=DESCRIPTION,
        skills=[REVIEW_SKILL],
        local_port=LOCAL_PORT,
    )
