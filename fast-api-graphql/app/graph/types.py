"""The GraphQL types — the contract, kept deliberately separate from the domain.

`models.py` is what the store keeps; this file is what clients may ask for. They
mirror each other today, but they are allowed to diverge: renaming a Pydantic
field is a refactor, renaming a GraphQL field breaks every client in production.

Three things here have no REST equivalent and are worth reading closely:

* **Nested resolvers** — `Requisition.candidates` and `Employee.match` are fields
  that run code *and take arguments*. A REST client needs a second round trip
  (`/requisitions/J2001/candidates`); a GraphQL client just asks for the field.
* **`strawberry.Private`** — carries the domain object alongside the exposed
  fields without publishing it in the schema.
* **Error types** — a GraphQL mutation answers 200 whatever happens, so "blocked"
  and "not found" are *types in the schema*, not status codes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import strawberry
from graphql import GraphQLError

from app import models
from app.graph.context import Context

# Reuse the domain enums rather than redeclaring them: one list of verdicts,
# published to GraphQL. Add a verdict in models.py and the schema follows.
Availability = strawberry.enum(models.Availability, description="Bench or on a project.")
Verdict = strawberry.enum(models.Verdict, description="The scoring engine's answer.")

Info = strawberry.Info[Context, None]

MAX_CANDIDATES = 20


@strawberry.type(description="One rated skill. Level is 1 aware · 3 independent · 5 expert.")
class SkillRating:
    skill: str
    level: int

    @classmethod
    def from_model(cls, rating: models.SkillRating) -> SkillRating:
        return cls(skill=rating.skill, level=rating.level)


@strawberry.type(description="A skill a requisition needs, and how badly.")
class SkillRequirement:
    skill: str
    min_level: int
    mandatory: bool = strawberry.field(
        description="True only if a candidate without it cannot do the job"
    )
    weight: int = strawberry.field(description="How much of the score this skill is worth")

    @classmethod
    def from_model(cls, requirement: models.SkillRequirement) -> SkillRequirement:
        return cls(
            skill=requirement.skill,
            min_level=requirement.min_level,
            mandatory=requirement.mandatory,
            weight=requirement.weight,
        )


@strawberry.type(description="Where a candidate falls short of one required skill.")
class Gap:
    skill: str
    required: int
    actual: int
    mandatory: bool

    @classmethod
    def from_model(cls, gap: models.Gap) -> Gap:
        return cls(
            skill=gap.skill, required=gap.required, actual=gap.actual, mandatory=gap.mandatory
        )


@strawberry.type(description="One employee scored against one requisition.")
class MatchResult:
    employee_id: strawberry.ID
    name: str
    job_id: strawberry.ID
    score: int
    verdict: Verdict
    gaps: list[Gap]
    blockers: list[str] = strawberry.field(description="Mandatory skills below the bar")
    meets_experience: bool

    @classmethod
    def from_model(cls, match: models.MatchResult) -> MatchResult:
        return cls(
            employee_id=strawberry.ID(match.employee_id),
            name=match.name,
            job_id=strawberry.ID(match.job_id),
            score=match.score,
            verdict=match.verdict,
            gaps=[Gap.from_model(g) for g in match.gaps],
            blockers=list(match.blockers),
            meets_experience=match.meets_experience,
        )

    @strawberry.field(description="The full record behind this score, batched by a DataLoader.")
    async def employee(self, info: Info) -> Employee | None:
        person = await info.context.employees.load(self.employee_id)
        return Employee.from_model(person) if person else None


@strawberry.type(description="Someone the recruiter has committed to.")
class ShortlistEntry:
    employee_id: strawberry.ID
    name: str
    score: int
    verdict: Verdict
    added_at: datetime

    @classmethod
    def from_model(cls, entry: models.ShortlistEntry) -> ShortlistEntry:
        return cls(
            employee_id=strawberry.ID(entry.employee_id),
            name=entry.name,
            score=entry.score,
            verdict=entry.verdict,
            added_at=entry.added_at,
        )

    @strawberry.field(description="The full record behind this entry, batched by a DataLoader.")
    async def employee(self, info: Info) -> Employee | None:
        person = await info.context.employees.load(self.employee_id)
        return Employee.from_model(person) if person else None


@strawberry.type(description="Someone in the directory.")
class Employee:
    employee_id: strawberry.ID
    name: str
    location: str
    availability: Availability
    experience_years: float
    skills: list[SkillRating]
    created_at: datetime

    # Private: available to the resolvers below, absent from the published schema.
    model: strawberry.Private[models.Employee]

    @classmethod
    def from_model(cls, employee: models.Employee) -> Employee:
        return cls(
            employee_id=strawberry.ID(employee.employee_id),
            name=employee.name,
            location=employee.location,
            availability=employee.availability,
            experience_years=employee.experience_years,
            skills=[SkillRating.from_model(s) for s in employee.skills],
            created_at=employee.created_at,
            model=employee,
        )

    @strawberry.field(description="Score this person against one open role. Null if no such role.")
    def match(self, info: Info, job_id: strawberry.ID) -> MatchResult | None:
        requisition = info.context.store.get_requisition(job_id)
        if requisition is None:
            return None
        return MatchResult.from_model(info.context.store.score(self.model, requisition))


@strawberry.type(description="An open role and the skills it needs.")
class Requisition:
    job_id: strawberry.ID
    title: str
    location: str
    min_experience_years: float
    required_skills: list[SkillRequirement]

    model: strawberry.Private[models.Requisition]

    @classmethod
    def from_model(cls, requisition: models.Requisition) -> Requisition:
        return cls(
            job_id=strawberry.ID(requisition.job_id),
            title=requisition.title,
            location=requisition.location,
            min_experience_years=requisition.min_experience_years,
            required_skills=[SkillRequirement.from_model(s) for s in requisition.required_skills],
            model=requisition,
        )

    @strawberry.field(description="Everyone on the bench, scored and ranked, best first.")
    def candidates(self, info: Info, limit: int = 3) -> list[MatchResult]:
        # GraphQL's type system checks that `limit` is an Int. It has no opinion on
        # whether 0 or 10_000 is sensible — REST got that free from Query(ge=1, le=20),
        # here the resolver owns it. Bounds are a schema promise; enforce them.
        if not 1 <= limit <= MAX_CANDIDATES:
            raise GraphQLError(f"limit must be between 1 and {MAX_CANDIDATES}, got {limit}")
        return [MatchResult.from_model(m) for m in info.context.store.rank(self.model, limit=limit)]

    @strawberry.field(description="Who has been shortlisted for this role so far.")
    def shortlist(self, info: Info) -> list[ShortlistEntry]:
        entries = info.context.store.get_shortlist(self.model.job_id)
        return [ShortlistEntry.from_model(e) for e in entries]


# --------------------------------------------------------------------------
# Inputs — a mutation's arguments are their own types, never object types
# --------------------------------------------------------------------------
@strawberry.input(description="One rated skill on a new employee.")
class SkillRatingInput:
    skill: str
    level: int


@strawberry.input(description="A new person for the directory.")
class EmployeeInput:
    name: str
    location: str
    experience_years: float
    availability: Availability = models.Availability.bench
    skills: list[SkillRatingInput] = strawberry.field(default_factory=list)


# --------------------------------------------------------------------------
# Errors as data — the GraphQL answer to 404, 409 and 422
# --------------------------------------------------------------------------
@strawberry.type(description="A field the server refused, and why.")
class InvalidField:
    field: str
    message: str


@strawberry.type(description="The input was well-formed GraphQL but broke a domain rule.")
class ValidationFailed:
    message: str
    invalid_fields: list[InvalidField]


@strawberry.type(description="No such employee or requisition.")
class NotFound:
    message: str
    kind: str = strawberry.field(description='"employee" or "requisition"')
    id: strawberry.ID


@strawberry.type(description="The candidate misses a mandatory skill — the REST 409.")
class CandidateBlocked:
    message: str
    blockers: list[str]
    score: int


AddEmployeeResult = Annotated[
    Employee | ValidationFailed,
    strawberry.union(
        "AddEmployeeResult",
        description="Either the created employee, or why the server would not create one.",
    ),
]

ShortlistResult = Annotated[
    ShortlistEntry | NotFound | CandidateBlocked,
    strawberry.union(
        "ShortlistResult",
        description="Every way shortlisting can end, spelled out in the schema.",
    ),
]
