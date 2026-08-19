"""Pydantic v2 models — the shapes of data going in and out of the API.

The domain is the one the whole course runs on: employees have rated skills,
requisitions require skills, and something has to decide who fits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Availability(str, Enum):
    """A str Enum shows up in OpenAPI as a fixed set of values, so /docs offers a dropdown."""

    bench = "bench"
    allocated = "allocated"


class Verdict(str, Enum):
    strong = "strong"
    possible = "possible"
    weak = "weak"
    blocked = "blocked"


class SkillRating(BaseModel):
    """One rated skill. `level` is validated at the edge, so no route has to check it."""

    skill: str = Field(min_length=1, max_length=60, examples=["Apache Spark"])
    level: int = Field(ge=1, le=5, description="1 aware · 3 works independently · 5 expert")


class EmployeeCreate(BaseModel):
    """What a client sends to add someone to the directory."""

    name: str = Field(min_length=1, max_length=120, examples=["Priya Raman"])
    location: str = Field(min_length=1, max_length=60, examples=["Bengaluru"])
    availability: Availability = Availability.bench
    experience_years: float = Field(ge=0, le=50, examples=[8.5])
    skills: list[SkillRating] = Field(default_factory=list)


class Employee(EmployeeCreate):
    """What the API stores and returns (adds server-set fields)."""

    employee_id: str = Field(examples=["E1002"])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SkillRequirement(BaseModel):
    skill: str = Field(min_length=1, max_length=60, examples=["Apache Spark"])
    min_level: int = Field(ge=1, le=5)
    mandatory: bool = Field(
        default=False,
        description="True only if a candidate without it cannot do the job",
    )
    weight: int = Field(default=1, ge=1, le=5, description="How much of the score this skill is worth")


class Requisition(BaseModel):
    job_id: str = Field(examples=["J2001"])
    title: str = Field(examples=["Senior Data Engineer"])
    location: str = Field(examples=["Bengaluru"])
    min_experience_years: float = Field(ge=0, le=50, examples=[6])
    required_skills: list[SkillRequirement]


class Gap(BaseModel):
    skill: str
    required: int
    actual: int
    mandatory: bool


class MatchResult(BaseModel):
    """The scoring engine's answer — computed, never guessed."""

    employee_id: str
    name: str
    job_id: str
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    gaps: list[Gap]
    blockers: list[str] = Field(description="Mandatory skills below the bar")
    meets_experience: bool


class ShortlistRequest(BaseModel):
    employee_id: str = Field(examples=["E1002"])


class ShortlistEntry(BaseModel):
    employee_id: str
    name: str
    score: int
    verdict: Verdict
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
