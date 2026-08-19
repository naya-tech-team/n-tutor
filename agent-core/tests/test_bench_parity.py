"""`list_bench` exists twice, and the two must agree.

Locally the screening agent calls an in-process `@tool`. Deployed it calls
`hrdata___list_bench` through the Gateway, which is the Lambda. Same question,
two implementations, and nothing but this file stops them drifting — the failure
mode is a bench that looks different depending on whether you are demoing or
deployed, which is the sort of thing nobody believes until they see it twice.

Written after the local one was added: the chat UI let people ask availability
questions, and the local screener had no tool to answer them with.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def screening():
    return _load("talent_screening_main", ROOT / "app/runtimes/talent_screening/main.py")


@pytest.fixture(scope="module")
def handler():
    return _load("hr_data_fn_handler", ROOT / "app/lambda_fn/handler.py")


def ids_from_text(text: str) -> set[str]:
    return set(re.findall(r"E1\d{3}", text))


def ids_from_payload(payload: dict) -> set[str]:
    return {e["employee_id"] for e in payload["employees"]}


def call_local(screening, location=""):
    """The @tool decorator wraps the function; the original is on .original."""
    fn = getattr(screening.list_bench, "original", screening.list_bench)
    return fn(location)


@pytest.mark.parametrize("location", ["", "Bengaluru", "Chennai", "Pune", "Atlantis"])
def test_both_implementations_name_the_same_people(screening, handler, location):
    local = ids_from_text(call_local(screening, location))
    remote = ids_from_payload(handler.list_bench(location))
    assert local == remote, f"{location or 'everywhere'}: local {local} vs lambda {remote}"


def test_location_matching_is_case_insensitive_in_both(screening, handler):
    """A recruiter types 'bengaluru'. Neither side may care."""
    assert ids_from_text(call_local(screening, "bengaluru")) == ids_from_text(
        call_local(screening, "Bengaluru")
    )
    assert ids_from_payload(handler.list_bench("bengaluru")) == ids_from_payload(
        handler.list_bench("Bengaluru")
    )


def test_the_bench_excludes_allocated_people(screening):
    """The whole point of the tool. `availability` is a string, not a boolean —
    truthiness testing it puts everyone on the bench, allocated included."""
    from _shared import hr_data

    on_bench = ids_from_text(call_local(screening))
    allocated = {e["employee_id"] for e in hr_data.EMPLOYEES if e["availability"] == "allocated"}
    assert allocated and not (on_bench & allocated)


def test_an_empty_bench_says_so_rather_than_returning_nothing(screening):
    """An empty string reads to a model as a broken tool, and it invents a list."""
    assert "Nobody is on the bench" in call_local(screening, "Atlantis")
