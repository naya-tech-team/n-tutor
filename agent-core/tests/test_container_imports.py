"""What has to import on a container that has no agent framework.

`package.py` builds six zips and only five of them are agents. `hr_data_fn` is a
plain Lambda behind the Gateway — it dispatches tool calls to dictionary lookups,
so it vendors no strands, and does not pay ~70 MB and a cold start for a library
it never calls.

That makes the import list at the top of `_shared/__init__.py` a contract rather
than a convenience: **every name it imports eagerly must import with only
pydantic-settings present.** Nothing else in this suite can notice when that
breaks, because strands is installed in the dev environment, so the eager import
succeeds on every machine except the one that runs it. The evidence turns up in
CloudWatch instead, naming a module nobody deliberately imported:

    Runtime.ImportModuleError: Unable to import module 'main':
    No module named 'strands'

So this file reproduces the container by refusing the import.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import package  # noqa: E402

# Derived, not listed. `package.py` decides which artifacts vendor strands, so
# asking it is what makes this test cover the next strands-free artifact somebody
# adds — rather than the two that exist today.
STRANDS_FREE = sorted(
    name
    for name, spec in package.ARTIFACTS.items()
    if not any("strands" in dep for dep in spec["deps"])
)

# The entrypoint module for each, as an import path under `app/`.
ENTRYPOINTS = {
    name: ".".join(
        package.ARTIFACTS[name]["entry"]
        .relative_to(package.APP)
        .with_suffix("")
        .parts
    )
    for name in STRANDS_FREE
}

# `_shared` is the thing under test; `strands` so a real import is attempted
# rather than a cached hit; the rest so each entrypoint imports fresh.
ROOTS = {"strands", "_shared", "lambda_fn", "runtimes"}


class NoStrands:
    """A meta-path finder that refuses strands, the way `hr_data_fn.zip` does."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "strands" or fullname.startswith("strands."):
            raise ImportError(f"No module named {fullname!r}")
        return None


@pytest.fixture
def no_strands():
    loaded = lambda: [m for m in sys.modules if m.split(".")[0] in ROOTS]  # noqa: E731

    saved = {name: sys.modules[name] for name in loaded()}
    for name in saved:
        del sys.modules[name]

    blocker = NoStrands()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        # Put the originals back. Other test modules hold references to the
        # `settings` object created by the first import; leaving a second copy in
        # sys.modules would make their monkeypatching land on the wrong one.
        sys.meta_path.remove(blocker)
        for name in loaded():
            del sys.modules[name]
        sys.modules.update(saved)


def test_the_blocker_really_blocks(no_strands):
    """Guard on the guard.

    Without this, a typo in `NoStrands` makes every test below pass for the wrong
    reason and keeps passing after the bug comes back.
    """
    with pytest.raises(ImportError):
        importlib.import_module("strands.hooks")


def test_both_strands_free_artifacts_are_covered():
    """Names the blast radius, so a passing suite is not mistaken for a narrow one.

    Two artifacts vendor no strands and both import `_shared` at module scope:
    `hr_data_fn` is the one that reported it, `hr_skills_mcp` had the same defect
    and would have failed its 30s init the same way.
    """
    assert STRANDS_FREE == ["hr_data_fn", "hr_skills_mcp"]


@pytest.mark.parametrize("artifact", STRANDS_FREE)
def test_the_entrypoint_imports(artifact, no_strands):
    """The exact module the service resolves — `main.py` at the zip root.

    `install()` runs at module scope in both, and is a no-op in local mode, so
    this imports the real entrypoint rather than an approximation of it.
    """
    module = importlib.import_module(ENTRYPOINTS[artifact])
    assert module.settings is not None


def test_importing_shared_does_not_pull_the_agent_framework(no_strands):
    """Stricter than "it imported": nothing may arrive as a side effect either."""
    importlib.import_module("_shared")
    assert [m for m in sys.modules if m.split(".")[0] == "strands"] == []


def test_the_lambda_still_answers_and_does_not_score(no_strands):
    """The tools work, not merely import. A lazy re-export that resolved to the
    wrong object would satisfy every test above."""
    handler = importlib.import_module(ENTRYPOINTS["hr_data_fn"])
    out = handler.find_by_skill(skill="pyspark", min_level=4)
    assert [e["employee_id"] for e in out["employees"]] == ["E1002", "E1005"]
    assert set(handler.TOOLS) == {
        "find_by_skill",
        "get_requisition",
        "list_bench",
        "record_shortlist",
        "get_shortlist",
    }


# --- and the other direction: lazy must not mean gone ------------------------


def test_tool_budget_still_resolves_when_strands_is_there():
    """Three runtimes do `from _shared import ToolBudget`. Deferring the import
    must not change what the name means."""
    from _shared import ToolBudget
    from _shared.tool_budget import ToolBudget as direct

    assert ToolBudget is direct
    assert ToolBudget(max_calls=2).max_calls == 2


def test_an_unknown_name_is_an_attribute_error_not_an_import_error():
    """`from _shared import a2a_serve` works only because `__getattr__` raises
    AttributeError — that is what makes the import system fall through and look
    for a submodule. Raise ImportError instead and three runtimes stop importing.
    """
    import _shared

    with pytest.raises(AttributeError):
        _shared.no_such_name

    from _shared import a2a_serve

    assert a2a_serve.__name__ == "_shared.a2a_serve"
