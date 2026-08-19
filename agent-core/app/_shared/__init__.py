"""Shared helpers every runtime in this project imports.

Five entrypoints need the same four boring things: settings, a model, the HR
domain, and a way to know where the records live. They live here, and this
directory is vendored into every deployment zip so `from _shared import ...`
resolves identically on your laptop and at `/var/task`.

**Six** zips, though, and one of them is not an agent. `hr_data_fn` is a plain
Lambda behind the Gateway; it has no agent loop, so `package.py` deliberately
does not vendor strands into it. That makes this file's import list a public
contract: anything eagerly imported here must import on a container that has
only pydantic-settings. See `ToolBudget` at the bottom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import settings
from .hr_data import (
    EMPLOYEES,
    JOBS,
    SKILLS,
    canonical_skill,
    employees_with_skill,
    find_employee_by_name,
    get_employee,
    get_job,
    match,
    rank_candidates,
    rank_jobs_for_employee,
    skill_level,
    summarize_match,
)
from .llm import make_model, model_banner
from .store import append_shortlist, install, read_shortlist

if TYPE_CHECKING:  # for editors and type checkers only — never at runtime.
    from .tool_budget import ToolBudget

__all__ = [
    "settings",
    "make_model",
    "model_banner",
    # data
    "EMPLOYEES",
    "JOBS",
    "SKILLS",
    # lookups
    "get_employee",
    "find_employee_by_name",
    "get_job",
    "canonical_skill",
    "skill_level",
    "employees_with_skill",
    # matching
    "match",
    "rank_candidates",
    "rank_jobs_for_employee",
    "summarize_match",
    # where the records come from
    "install",
    "read_shortlist",
    "append_shortlist",
    # guardrail
    "ToolBudget",
]


def __getattr__(name: str):
    """Resolve `ToolBudget` on demand rather than at import.

    `tool_budget.py` subclasses `strands.hooks.HookProvider`, so importing it
    imports strands — and importing it *here* meant every `from _shared import
    get_job` did too. In `hr_data_fn`, which vendors no strands, that was:

        Runtime.ImportModuleError: Unable to import module 'main':
        No module named 'strands'

    A Lambda with no agent loop paying for the agent framework, at import time,
    to satisfy a name it never uses. The four runtimes that *do* use it are
    unaffected: `from _shared import ToolBudget` asks for the name immediately,
    so it is imported immediately.

    The dev environment cannot catch this — strands is installed, so the eager
    import succeeded everywhere except the one place that mattered.
    `tests/test_container_imports.py` blocks strands to reproduce the container.
    """
    if name == "ToolBudget":
        from .tool_budget import ToolBudget

        return ToolBudget
    # AttributeError, not ImportError: `from _shared import a2a_serve` relies on
    # this failing so the import system falls through to the submodule.
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
