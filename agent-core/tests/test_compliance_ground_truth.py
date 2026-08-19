"""The compliance reviewer must not be able to skip the record.

The first version of this agent asked the model to call `verify_match_claim`
first. On qwen2.5:7b it did not — and then rejected a note for claims "not
verified by verify_match_claim", asserting a check it had never run against facts
that were all true.

`GroundTruth` removes the choice. These tests pin the two properties that matter:
the record is attached without any model involvement, and it says what the HR
data says.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MAIN = Path(__file__).resolve().parents[1] / "app/runtimes/people_compliance/main.py"


@pytest.fixture(scope="module")
def compliance():
    spec = importlib.util.spec_from_file_location("people_compliance_main", MAIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def invoke(module, text: str) -> str:
    """Run the hook over one user message and return what it appended."""
    blocks = [{"text": text}]
    event = SimpleNamespace(
        messages=[{"role": "user", "content": blocks}],
        agent=SimpleNamespace(messages=[]),
    )
    module.GroundTruth()._attach(event)
    return "".join(b["text"] for b in blocks[1:])


def test_the_record_is_attached_with_no_tool_call(compliance):
    added = invoke(compliance, "Review this note. E1002 / J2001.\nHi Priya, ...")
    assert "HR RECORD" in added
    assert "Priya Raman" in added
    assert "Senior Data Engineer" in added


def test_it_carries_the_score_and_verdict(compliance):
    added = invoke(compliance, "Review this note. E1002 / J2001.\nHi Priya")
    assert "100%" in added
    assert "strong" in added
    assert "blockers none" in added


def test_a_blocked_candidate_arrives_labelled_blocked(compliance):
    """The reviewer's last line of defence: Rahul is 61% and blocked."""
    added = invoke(compliance, "Review this note. E1003 / J2001.\nHi Rahul")
    assert "blocked" in added
    assert "Apache Spark" in added


def test_skill_levels_are_included_so_a_claim_can_be_checked(compliance):
    added = invoke(compliance, "Review this note. E1002 / J2001.")
    assert "Apache Spark L5" in added
    assert "Python L4" in added


def test_unknown_ids_say_so_rather_than_inventing(compliance):
    added = invoke(compliance, "Review this note. E9999 / J2001.")
    assert "no employee E9999" in added


def test_a_note_with_no_ids_is_left_alone(compliance):
    """Nothing to look up — the model still gets the note, just no record line."""
    assert invoke(compliance, "Review this note. Hi Priya, great to meet you.") == ""


def test_the_ids_are_found_across_newlines(compliance):
    """The supervisor sends 'E1002 / J2001' on the first line, the note below."""
    added = invoke(compliance, "Review this note.\nE1002\n\nsome text\n\nJ2001\nHi Priya")
    assert "Priya Raman" in added
