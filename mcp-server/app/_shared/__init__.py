"""Shared helpers every numbered example imports.

Each lesson folder is self-contained *except* for three boring things every one
of them needs: settings, a model, and the HR dataset the whole course runs on.
Duplicating those 16 times would bury the one idea each lesson is teaching, so
they live here.
"""

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

__all__ = [
    "settings",
    "make_model",
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
]
