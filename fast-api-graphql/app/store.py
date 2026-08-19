"""A tiny in-memory data store, injected into every GraphQL resolver.

In a real app this would be an HRMS. Nothing in here knows about GraphQL — the
same class backed the REST version of this project, and the scoring engine below
is the reason: a protocol is a way to *ask*, not a place to keep business rules.

It arrives at resolvers through the GraphQL context, which is itself built by a
FastAPI dependency, so a test can swap in a fresh store per test.
"""

from __future__ import annotations

from itertools import count

from app.models import (
    Availability,
    Employee,
    EmployeeCreate,
    Gap,
    MatchResult,
    Requisition,
    ShortlistEntry,
    SkillRating,
    SkillRequirement,
    Verdict,
)


def _seed_employees() -> list[Employee]:
    """Four people from the course's directory, enough to show every verdict."""
    return [
        Employee(
            employee_id="E1002", name="Priya Raman", location="Bengaluru",
            availability=Availability.bench, experience_years=8.5,
            skills=[SkillRating(skill="Python", level=4), SkillRating(skill="Apache Spark", level=5),
                    SkillRating(skill="SQL", level=5), SkillRating(skill="Apache Airflow", level=4)],
        ),
        Employee(
            employee_id="E1003", name="Rahul Menon", location="Hyderabad",
            availability=Availability.bench, experience_years=5.0,
            skills=[SkillRating(skill="Python", level=4), SkillRating(skill="Apache Spark", level=3),
                    SkillRating(skill="SQL", level=4)],
        ),
        Employee(
            employee_id="E1005", name="Vikram Iyer", location="Chennai",
            availability=Availability.bench, experience_years=7.5,
            skills=[SkillRating(skill="Python", level=3), SkillRating(skill="Apache Spark", level=4),
                    SkillRating(skill="Apache Kafka", level=4)],
        ),
        Employee(
            employee_id="E1007", name="Arjun Nair", location="Bengaluru",
            availability=Availability.allocated, experience_years=6.0,
            skills=[SkillRating(skill="Python", level=5), SkillRating(skill="SQL", level=4),
                    SkillRating(skill="Apache Spark", level=4)],
        ),
    ]


def _seed_requisitions() -> list[Requisition]:
    return [
        Requisition(
            job_id="J2001", title="Senior Data Engineer", location="Bengaluru",
            min_experience_years=6,
            required_skills=[
                SkillRequirement(skill="Python", min_level=4, mandatory=True, weight=2),
                SkillRequirement(skill="Apache Spark", min_level=4, mandatory=True, weight=2),
                SkillRequirement(skill="SQL", min_level=4, mandatory=True, weight=2),
                SkillRequirement(skill="Apache Airflow", min_level=3),
            ],
        ),
        Requisition(
            job_id="J2002", title="Streaming Platform Engineer", location="Chennai",
            min_experience_years=7,
            required_skills=[
                SkillRequirement(skill="Apache Kafka", min_level=4, mandatory=True, weight=2),
                SkillRequirement(skill="Apache Spark", min_level=4, mandatory=True, weight=2),
                SkillRequirement(skill="Python", min_level=3),
            ],
        ),
    ]


class HRStore:
    def __init__(self) -> None:
        self._employees: dict[str, Employee] = {e.employee_id: e for e in _seed_employees()}
        self._requisitions: dict[str, Requisition] = {r.job_id: r for r in _seed_requisitions()}
        self._shortlists: dict[str, list[ShortlistEntry]] = {}
        self._ids = count(1013)     # next generated id is E1013
        self.batch_calls = 0        # counts get_employees() calls, so a test can prove batching

    # -- employees ---------------------------------------------------------
    def list_employees(self, available_only: bool = False) -> list[Employee]:
        people = list(self._employees.values())
        if available_only:
            people = [e for e in people if e.availability is Availability.bench]
        return people

    def get_employee(self, employee_id: str) -> Employee | None:
        return self._employees.get(employee_id.upper())

    def get_employees(self, employee_ids: list[str]) -> list[Employee | None]:
        """Fetch many people in one call — what a GraphQL DataLoader batches into.

        The result is positional: one slot per requested id, `None` where there is
        no such person. A loader hands back results by position, so a store that
        silently dropped the misses would misalign every id after the first gap.
        """
        self.batch_calls += 1
        return [self._employees.get(eid.upper()) for eid in employee_ids]

    def create_employee(self, data: EmployeeCreate) -> Employee:
        employee = Employee(employee_id=f"E{next(self._ids)}", **data.model_dump())
        self._employees[employee.employee_id] = employee
        return employee

    # -- requisitions ------------------------------------------------------
    def list_requisitions(self) -> list[Requisition]:
        return list(self._requisitions.values())

    def get_requisition(self, job_id: str) -> Requisition | None:
        return self._requisitions.get(job_id.upper())

    # -- matching ----------------------------------------------------------
    def score(self, employee: Employee, requisition: Requisition) -> MatchResult:
        """Deliberately boring arithmetic, not a model call.

        A match score you cannot reproduce by hand is a score nobody will defend
        in a hiring review — so this is the one place the number is decided.
        """
        levels = {s.skill: s.level for s in employee.skills}
        earned = 0.0
        total = 0.0
        gaps: list[Gap] = []
        blockers: list[str] = []

        for req in requisition.required_skills:
            total += req.weight
            have = levels.get(req.skill, 0)
            if have >= req.min_level:
                earned += req.weight
                continue
            # Partial credit: level 3 against a level-4 bar is a coaching problem,
            # level 0 is a hiring problem. The score should know the difference.
            earned += req.weight * (have / req.min_level)
            gaps.append(Gap(skill=req.skill, required=req.min_level, actual=have,
                            mandatory=req.mandatory))
            if req.mandatory:
                blockers.append(req.skill)

        score = round(100 * earned / total) if total else 0
        meets_experience = employee.experience_years >= requisition.min_experience_years

        if blockers:
            verdict = Verdict.blocked
        elif score >= 80 and meets_experience:
            verdict = Verdict.strong
        elif score >= 55:
            verdict = Verdict.possible
        else:
            verdict = Verdict.weak

        return MatchResult(
            employee_id=employee.employee_id, name=employee.name, job_id=requisition.job_id,
            score=score, verdict=verdict, gaps=gaps, blockers=blockers,
            meets_experience=meets_experience,
        )

    def rank(self, requisition: Requisition, limit: int = 3) -> list[MatchResult]:
        ranked = sorted(
            (self.score(e, requisition) for e in self.list_employees(available_only=True)),
            key=lambda m: m.score,
            reverse=True,
        )
        return ranked[:limit]

    # -- shortlists --------------------------------------------------------
    def get_shortlist(self, job_id: str) -> list[ShortlistEntry]:
        return self._shortlists.get(job_id.upper(), [])

    def add_to_shortlist(self, job_id: str, match: MatchResult) -> ShortlistEntry:
        entries = self._shortlists.setdefault(job_id.upper(), [])
        for existing in entries:
            if existing.employee_id == match.employee_id:
                return existing          # idempotent: POSTing twice is not an error
        entry = ShortlistEntry(employee_id=match.employee_id, name=match.name,
                               score=match.score, verdict=match.verdict)
        entries.append(entry)
        return entry


# One shared instance for the app. Tests can build their own.
_store = HRStore()


def get_store() -> HRStore:
    """Dependency provider — FastAPI calls this to inject the store."""
    return _store
