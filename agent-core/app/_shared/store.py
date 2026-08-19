"""Where the records come from — the only module that knows.

`hr_data.py` is the domain: `match()`, the verdict thresholds, the alias table.
It is arithmetic and it is not allowed to care whether a record arrived from a
Python list or an S3 object. This module is the seam that keeps that true.

The lookups in `hr_data` close over module-level lists (`EMPLOYEES`, `JOBS`,
`SKILLS`). Rather than fork every one of them into an S3 variant, `install()`
replaces the *contents* of those lists once at start-up. Every lookup and
`match()` itself then run unchanged against whichever source you configured —
which is exactly the property `tests/test_store_parity.py` asserts by scoring the
same three people both ways and demanding identical output.

**A score that changes when you move the data is a score nobody will defend.**
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from . import hr_data
from .config import settings

EMPLOYEES_KEY = "employees/employees.json"
REQUISITIONS_KEY = "requisitions/requisitions.json"
SKILLS_KEY = "skills/skills.json"


def shortlist_key(job_id: str) -> str:
    """Where one requisition's shortlist decisions live."""
    return f"shortlists/{job_id.strip().upper()}.json"


# ---------------------------------------------------------------------------
# The S3 edge. Two functions, so a test can replace them without moto or a
# network, and so the boto3 import never happens on a local run.
# ---------------------------------------------------------------------------


def _client():
    import boto3

    return boto3.client("s3", region_name=settings.aws_region)


def get_object(key: str) -> bytes:
    """Read one object. Monkeypatch this in tests."""
    if not settings.s3_bucket:
        raise RuntimeError("DATA_SOURCE=s3 but S3_BUCKET is empty.")
    return _client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def put_object(key: str, body: bytes) -> None:
    """Write one object. Monkeypatch this in tests."""
    if not settings.s3_bucket:
        raise RuntimeError("DATA_SOURCE=s3 but S3_BUCKET is empty.")
    _client().put_object(Bucket=settings.s3_bucket, Key=key, Body=body)


def _load(key: str, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if settings.data_source == "local":
        return fallback
    return json.loads(get_object(key))


# lru_cache because a warm Runtime container serves many sessions, and re-reading
# the whole employee directory per tool call is a cost you pay in latency on the
# hottest path in the system. clear_cache() exists for tests and for seed_s3.py.


@lru_cache(maxsize=1)
def load_employees() -> list[dict[str, Any]]:
    return _load(EMPLOYEES_KEY, hr_data.EMPLOYEES)


@lru_cache(maxsize=1)
def load_jobs() -> list[dict[str, Any]]:
    return _load(REQUISITIONS_KEY, hr_data.JOBS)


@lru_cache(maxsize=1)
def load_skills() -> list[dict[str, Any]]:
    return _load(SKILLS_KEY, hr_data.SKILLS)


def clear_cache() -> None:
    load_employees.cache_clear()
    load_jobs.cache_clear()
    load_skills.cache_clear()


# ---------------------------------------------------------------------------
# Binding the loaded records back into the domain module.
# ---------------------------------------------------------------------------


def install() -> None:
    """Point `hr_data`'s lookups at whatever `data_source` says.

    A no-op in local mode, so calling it unconditionally at the top of every
    entrypoint is safe and means no runtime has to know which mode it is in.
    """
    if settings.data_source == "local":
        return

    # Slice-assign rather than rebind: `from _shared import EMPLOYEES` elsewhere
    # would otherwise keep pointing at the old list object.
    hr_data.SKILLS[:] = load_skills()
    hr_data.EMPLOYEES[:] = load_employees()
    hr_data.JOBS[:] = load_jobs()
    _rebuild_alias_index()


def _rebuild_alias_index() -> None:
    """Re-derive the alias table after SKILLS changes.

    This is what keeps `find_by_skill("pyspark")` returning people whose records
    say "Apache Spark" once the catalog is an S3 object instead of a literal.
    """
    index = hr_data._ALIAS_INDEX  # noqa: SLF001 — this module is hr_data's other half
    index.clear()
    for entry in hr_data.SKILLS:
        index[entry["skill"].lower()] = entry["skill"]
        for alias in entry.get("aliases", []):
            index[alias.lower()] = entry["skill"]


# ---------------------------------------------------------------------------
# Shortlists — the one thing this system writes.
# ---------------------------------------------------------------------------


def read_shortlist(job_id: str) -> list[dict[str, Any]]:
    """Everyone shortlisted for a requisition so far. Empty if nobody is."""
    if settings.data_source == "local":
        return _LOCAL_SHORTLISTS.get(job_id.strip().upper(), [])
    try:
        return json.loads(get_object(shortlist_key(job_id)))
    except Exception:  # noqa: BLE001 — no object yet is the common case, not an error
        return []


def append_shortlist(job_id: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Add one candidate to a requisition's shortlist. Idempotent on employee_id."""
    job_id = job_id.strip().upper()
    current = read_shortlist(job_id)
    if any(e["employee_id"] == entry["employee_id"] for e in current):
        return current
    current = [*current, entry]
    if settings.data_source == "local":
        _LOCAL_SHORTLISTS[job_id] = current
    else:
        put_object(shortlist_key(job_id), json.dumps(current, indent=2).encode())
    return current


# In local mode there is no bucket to write to, so shortlists live for the life
# of the process. That is a real difference from deployed behaviour, and it is
# the reason the local pipeline cannot demonstrate "already shortlisted".
_LOCAL_SHORTLISTS: dict[str, list[dict[str, Any]]] = {}
