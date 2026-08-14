"""Tests for skills_matcher. Run:  pytest -q  (or: python -m pytest -q)"""

import pytest

from skills_matcher import Bench, BenchError, Employee, Requisition, score


@pytest.fixture
def j2001() -> Requisition:
    return Requisition(
        job_id="J2001",
        title="Senior Data Engineer",
        location="Bengaluru",
        required={"Python": 4, "Apache Spark": 4, "SQL": 4, "Apache Airflow": 3},
        mandatory={"Python", "Apache Spark", "SQL"},
    )


@pytest.fixture
def bench() -> Bench:
    b = Bench("demo")
    b.add("E1002", "Priya Raman", "Bengaluru",
          {"Python": 4, "Apache Spark": 5, "SQL": 5, "Apache Airflow": 4})
    b.add("E1003", "Rahul Menon", "Hyderabad",
          {"Python": 4, "Apache Spark": 3, "SQL": 4})
    b.add("E1005", "Vikram Iyer", "Chennai",
          {"Python": 3, "Apache Spark": 4, "Apache Kafka": 4})
    return b


def test_perfect_match_scores_100(bench, j2001):
    match = score(bench.find("E1002"), j2001)
    assert match.score == 100
    assert match.blockers == []
    assert match.verdict == "strong"


def test_missing_mandatory_skill_is_a_blocker(bench, j2001):
    """Vikram has no SQL on record — mandatory, so he is blocked regardless of score."""
    match = score(bench.find("E1005"), j2001)
    assert "SQL" in match.blockers
    assert match.verdict == "blocked"


def test_below_bar_on_a_mandatory_skill_still_blocks(bench, j2001):
    """Rahul has Spark 3 against a bar of 4 — partial credit, but still blocked."""
    match = score(bench.find("E1003"), j2001)
    assert match.blockers == ["Apache Spark"]
    assert 0 < match.score < 100          # partial credit, not zero
    assert match.verdict == "blocked"


def test_level_returns_zero_for_an_unrated_skill(bench):
    assert bench.find("E1005").level("SQL") == 0
    assert bench.find("E1005").level("Apache Kafka") == 4


def test_candidates_for_is_sorted_and_skips_blocked(bench, j2001):
    names = [m.employee.employee_id for m in bench.candidates_for(j2001)]
    assert names == ["E1002"]             # E1003 and E1005 are both blocked


def test_candidates_for_is_lazy(bench, j2001):
    """A generator yields on demand — nothing runs until you iterate."""
    gen = bench.candidates_for(j2001)
    assert next(gen).employee.name == "Priya Raman"


def test_summary(bench, j2001):
    assert bench.summary(j2001) == "demo: 1/3 viable for J2001"


@pytest.mark.parametrize(
    "employee_id,skills",
    [("", {"SQL": 4}), ("E1002", {"SQL": 4}), ("E7777", {"SQL": 0}), ("E7777", {"SQL": 6})],
)
def test_invalid_input_raises(bench, employee_id, skills):
    with pytest.raises(BenchError):
        bench.add(employee_id, "Someone", "Pune", skills)


def test_find_missing_raises(bench):
    with pytest.raises(BenchError):
        bench.find("E0000")


def test_requisition_with_no_skills_raises(bench):
    empty = Requisition(job_id="J9999", title="Ghost Role", location="Remote")
    with pytest.raises(BenchError):
        score(bench.find("E1002"), empty)


def test_employee_str():
    employee = Employee("E1002", "Priya Raman", "Bengaluru", {"SQL": 5, "Python": 4})
    assert str(employee) == "E1002 Priya Raman (Bengaluru) — Python L4, SQL L5"
