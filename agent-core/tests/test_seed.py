"""The seed objects must round-trip the dataset without losing a field.

JSON has no tuples, no sets and no Python objects. If a record ever grows one,
this test fails here rather than in a shortlist three weeks later.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import seed_s3  # noqa: E402

from _shared import hr_data  # noqa: E402


def test_the_three_keys_are_the_ones_the_readers_expect():
    from _shared import store

    assert set(seed_s3.SEED) == {
        store.EMPLOYEES_KEY,
        store.REQUISITIONS_KEY,
        store.SKILLS_KEY,
    }


def test_every_record_survives_json(tmp_path):
    seed_s3.write_local(tmp_path)
    for key, original in seed_s3.SEED.items():
        loaded = json.loads((tmp_path / key).read_text())
        assert loaded == original, f"{key} did not round-trip"


def test_the_counts_are_the_documented_ones(tmp_path):
    seed_s3.write_local(tmp_path)
    from _shared import store

    assert len(json.loads((tmp_path / store.EMPLOYEES_KEY).read_text())) == 12
    assert len(json.loads((tmp_path / store.REQUISITIONS_KEY).read_text())) == 6
    assert len(json.loads((tmp_path / store.SKILLS_KEY).read_text())) == 24


def test_the_alias_table_is_in_the_payload(tmp_path):
    """Aliases are data. If they stay behind in Python, S3 mode loses `pyspark`."""
    seed_s3.write_local(tmp_path)
    from _shared import store

    skills = json.loads((tmp_path / store.SKILLS_KEY).read_text())
    spark = next(s for s in skills if s["skill"] == "Apache Spark")
    assert "pyspark" in spark["aliases"]


def test_mandatory_flags_survive(tmp_path):
    """`mandatory` is a bool — the one field whose loss would silently unblock people."""
    seed_s3.write_local(tmp_path)
    from _shared import store

    jobs = json.loads((tmp_path / store.REQUISITIONS_KEY).read_text())
    j2001 = next(j for j in jobs if j["job_id"] == "J2001")
    assert sum(1 for s in j2001["required_skills"] if s["mandatory"]) == 3
    assert all(isinstance(s["mandatory"], bool) for s in j2001["required_skills"])


def test_seed_matches_the_live_module():
    assert seed_s3.SEED[__import__("_shared.store", fromlist=["x"]).EMPLOYEES_KEY] is hr_data.EMPLOYEES
