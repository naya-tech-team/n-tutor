"""`hr-data-fn`, exercised the way the Gateway actually calls it.

No AWS: the Gateway's contract is an `event` dict plus a `context` carrying
`client_context.custom`, both of which are trivial to fake. That is the whole
reason build steps 1-6 need no account.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lambda_fn import handler


def ctx(tool: str):
    """A context shaped like the one AgentCore Gateway passes."""
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={
                "bedrockAgentCoreToolName": tool,
                "bedrockAgentCoreMessageVersion": "1.0",
                "bedrockAgentCoreGatewayId": "gw-test",
                "bedrockAgentCoreTargetId": "tgt-test",
            }
        )
    )


# --- the prefix -------------------------------------------------------------


def test_strips_the_three_underscore_prefix():
    assert handler.tool_name(ctx("hrdata___find_by_skill")) == "find_by_skill"


def test_a_bare_name_still_works():
    """Local probing and the MCP Inspector send no prefix."""
    assert handler.tool_name(ctx("find_by_skill")) == "find_by_skill"


def test_two_underscores_is_not_the_delimiter():
    """`__` would split `hrdata__find_by_skill` — and this is why it must not."""
    assert handler.tool_name(ctx("hrdata___record_shortlist")) == "record_shortlist"


# --- dispatch ---------------------------------------------------------------


def test_unknown_tool_is_returned_not_raised():
    out = handler.lambda_handler({}, ctx("hrdata___nope"))
    assert "error" in out
    assert "find_by_skill" in out["available"]


def test_wrong_arguments_come_back_as_data():
    out = handler.lambda_handler({"nonsense": 1}, ctx("hrdata___get_requisition"))
    assert "error" in out


# --- the tools --------------------------------------------------------------


def test_find_by_skill_resolves_an_alias():
    out = handler.lambda_handler(
        {"skill": "pyspark", "min_level": 4}, ctx("hrdata___find_by_skill")
    )
    assert [e["employee_id"] for e in out["employees"]] == ["E1002", "E1005"]


def test_find_by_skill_without_a_skill_says_what_to_do():
    """A required parameter answered with a bare validation error is a loop generator."""
    out = handler.lambda_handler({}, ctx("hrdata___find_by_skill"))
    assert "find_by_skill" in out["error"]


def test_unknown_skill_returns_a_next_step_not_an_empty_list():
    out = handler.lambda_handler({"skill": "cobol"}, ctx("hrdata___find_by_skill"))
    assert out["employees"] == []
    assert "resolve_skill" in out["note"]


def test_get_requisition_shape():
    out = handler.lambda_handler({"job_id": "J2001"}, ctx("hrdata___get_requisition"))
    assert out["title"] == "Senior Data Engineer"
    assert len(out["required_skills"]) == 6
    assert sum(1 for s in out["required_skills"] if s["mandatory"]) == 3


def test_get_requisition_unknown_id():
    out = handler.lambda_handler({"job_id": "J9999"}, ctx("hrdata___get_requisition"))
    assert "J2001" in out["error"]


def test_list_bench_counts_only_the_unallocated():
    out = handler.lambda_handler({}, ctx("hrdata___list_bench"))
    assert out["count"] == 8
    assert all(e["employee_id"].startswith("E") for e in out["employees"])


def test_list_bench_filters_by_location():
    out = handler.lambda_handler({"location": "Bengaluru"}, ctx("hrdata___list_bench"))
    assert out["count"] >= 1
    assert {e["location"] for e in out["employees"]} == {"Bengaluru"}


# --- the write path ---------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_shortlists():
    from _shared import store

    store._LOCAL_SHORTLISTS.clear()
    yield
    store._LOCAL_SHORTLISTS.clear()


def test_shortlisting_a_strong_candidate_writes():
    out = handler.lambda_handler(
        {"job_id": "J2001", "employee_id": "E1002", "score": 100, "verdict": "strong"},
        ctx("hrdata___record_shortlist"),
    )
    assert out["shortlisted"] is True
    assert [e["employee_id"] for e in out["shortlist"]] == ["E1002"]


def test_shortlisting_is_idempotent():
    event = {"job_id": "J2001", "employee_id": "E1002", "score": 100, "verdict": "strong"}
    handler.lambda_handler(event, ctx("hrdata___record_shortlist"))
    out = handler.lambda_handler(event, ctx("hrdata___record_shortlist"))
    assert len(out["shortlist"]) == 1


def test_a_blocked_candidate_is_refused_at_the_last_hop():
    """Rahul is 61% and blocked on Apache Spark. Persistence is the final gate."""
    out = handler.lambda_handler(
        {"job_id": "J2001", "employee_id": "E1003", "score": 61, "verdict": "blocked"},
        ctx("hrdata___record_shortlist"),
    )
    assert out["shortlisted"] is False
    assert "blocked" in out["error"]
    assert handler.lambda_handler({"job_id": "J2001"}, ctx("hrdata___get_shortlist"))["shortlist"] == []
