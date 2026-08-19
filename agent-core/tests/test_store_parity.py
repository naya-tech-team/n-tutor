"""Moving the records to S3 must not move a single number.

This is the test the whole data seam exists for. It scores the same three people
against J2001 twice — once from the Python dataset, once from JSON served by a
stubbed S3 — and demands byte-identical `match()` output.

If this ever fails, `store.install()` has started changing the domain instead of
just relocating it, and every score in the system is suspect.
"""

from __future__ import annotations

import json

import pytest

from _shared import hr_data, store
from _shared.config import settings

CANDIDATES = ["E1002", "E1003", "E1005"]
JOB = "J2001"


@pytest.fixture
def s3_backed(monkeypatch):
    """Serve the in-process dataset back through the S3 code path."""
    objects = {
        store.EMPLOYEES_KEY: json.dumps(hr_data.EMPLOYEES).encode(),
        store.REQUISITIONS_KEY: json.dumps(hr_data.JOBS).encode(),
        store.SKILLS_KEY: json.dumps(hr_data.SKILLS).encode(),
    }
    monkeypatch.setattr(store, "get_object", lambda key: objects[key])
    monkeypatch.setattr(settings, "data_source", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "test-bucket")
    store.clear_cache()
    yield
    store.clear_cache()


def _score_all() -> list[dict]:
    job = hr_data.get_job(JOB)
    return [hr_data.match(hr_data.get_employee(e), job) for e in CANDIDATES]


def test_local_and_s3_produce_identical_matches(s3_backed):
    # `s3_backed` is active, but install() has not run yet, so the lists still
    # hold the literals. Capture that first.
    from_local = _score_all()

    store.install()
    from_s3 = _score_all()

    assert from_s3 == from_local


def test_the_verdicts_are_the_documented_ones(s3_backed):
    store.install()
    scored = {m["employee_id"]: m for m in _score_all()}

    assert scored["E1002"]["score"] == 100
    assert scored["E1002"]["verdict"] == "strong"
    assert scored["E1002"]["blockers"] == []

    assert scored["E1003"]["score"] == 61
    assert scored["E1003"]["verdict"] == "blocked"
    assert scored["E1003"]["blockers"] == ["Apache Spark"]

    assert scored["E1005"]["score"] == 50
    assert scored["E1005"]["verdict"] == "blocked"
    assert scored["E1005"]["blockers"] == ["Python", "SQL"]


def test_the_alias_table_survives_the_move(s3_backed):
    """`pyspark` must still resolve to `Apache Spark` once SKILLS is an object."""
    store.install()
    assert hr_data.canonical_skill("pyspark") == "Apache Spark"
    found = hr_data.employees_with_skill("pyspark", min_level=4, available_only=True)
    assert [e["employee_id"] for e in found] == ["E1002", "E1005"]


def test_install_is_a_noop_in_local_mode():
    """Every entrypoint calls install() unconditionally — it must be safe."""
    before = len(hr_data.EMPLOYEES)
    store.install()
    assert len(hr_data.EMPLOYEES) == before
    assert hr_data.get_employee("E1002")["name"] == "Priya Raman"


def test_ranking_is_unchanged_by_the_move(s3_backed):
    store.install()
    ranked = hr_data.rank_candidates(JOB, available_only=True, limit=3)
    assert [(r["employee_id"], r["score"]) for r in ranked] == [
        ("E1002", 100),
        ("E1003", 61),
        ("E1005", 50),
    ]
