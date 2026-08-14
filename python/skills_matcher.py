"""Skills Matcher — a tiny runnable app that shows off core Python.

The job: a requisition is open, a few people are on the bench, and something has
to decide who to interview. That is the whole domain — and it is the same one
the FastAPI, FastMCP and Strands tracks use, so what you learn here carries over.

Concepts demonstrated (matches the Python: Quick Start page):
  - classes & objects        -> Employee, Requisition, Bench
  - encapsulation/properties -> Employee.level(), Requisition.is_mandatory()
  - generators               -> Bench.candidates_for()
  - decorators               -> @timed, @log_calls
  - error handling           -> add(), find()

Run it:
    python skills_matcher.py

No third-party packages required. Python 3.10+ (3.13 recommended).
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from typing import Iterator


# --------------------------------------------------------------------------
# Decorators — wrap a function to add behaviour without changing its body.
# --------------------------------------------------------------------------
def log_calls(func):
    """Print each call and its result. The docstring/name is preserved by wraps."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        # args[0] is `self`; skip it in the printout for readability.
        shown = args[1:] if args else ()
        print(f"  · {func.__name__}{shown} -> {result!r}")
        return result

    return wrapper


def timed(func):
    """Report how long a function took (in milliseconds)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        ms = (time.perf_counter() - start) * 1000
        print(f"  ⏱  {func.__name__} took {ms:.2f} ms")
        return result

    return wrapper


# --------------------------------------------------------------------------
# A small custom exception — clearer than a bare ValueError to callers.
# --------------------------------------------------------------------------
class BenchError(Exception):
    """Raised when a bench or matching operation is invalid."""


# --------------------------------------------------------------------------
# Classes & objects.
# --------------------------------------------------------------------------
@dataclass
class Employee:
    """One person, with each skill rated 1 (aware) to 5 (expert)."""

    employee_id: str
    name: str
    location: str
    skills: dict[str, int] = field(default_factory=dict)

    def level(self, skill: str) -> int:
        """Proficiency in one skill, or 0 if they have never used it.

        Returning 0 rather than raising is deliberate: "no Spark on record" is a
        normal answer to a screening question, not an error.
        """
        return self.skills.get(skill, 0)

    def __str__(self) -> str:
        rated = ", ".join(f"{s} L{lvl}" for s, lvl in sorted(self.skills.items()))
        return f"{self.employee_id} {self.name} ({self.location}) — {rated}"


@dataclass
class Requisition:
    """An open role: which skills it needs, at what level, and which are non-negotiable."""

    job_id: str
    title: str
    location: str
    required: dict[str, int] = field(default_factory=dict)  # skill -> minimum level
    mandatory: set[str] = field(default_factory=set)

    def is_mandatory(self, skill: str) -> bool:
        return skill in self.mandatory

    def __str__(self) -> str:
        return f"{self.job_id} {self.title} ({self.location})"


@dataclass
class Match:
    """The result of scoring one person against one role."""

    employee: Employee
    requisition: Requisition
    score: int
    blockers: list[str]

    @property
    def verdict(self) -> str:
        """A property: computed on access, used like an attribute."""
        if self.blockers:
            return "blocked"
        if self.score >= 80:
            return "strong"
        if self.score >= 55:
            return "possible"
        return "weak"

    def __str__(self) -> str:
        missing = ", ".join(self.blockers) if self.blockers else "none"
        return f"{self.employee.name:<18} {self.score:>3}%  {self.verdict:<8} blockers: {missing}"


def score(employee: Employee, requisition: Requisition) -> Match:
    """Score one employee against one requisition. Deliberately boring arithmetic.

    Each required skill is worth one point when the person is at or above the bar,
    and a pro-rata share when they are below it — someone at level 3 against a
    level-4 bar is a coaching problem, someone at 0 is a hiring problem.

    A *mandatory* skill below its bar is a blocker: the score still reports, but
    the verdict does not, because "82% but cannot do the mandatory thing" is not
    a shortlist.
    """
    if not requisition.required:
        raise BenchError(f"{requisition.job_id} lists no required skills")

    earned = 0.0
    blockers: list[str] = []

    for skill, min_level in requisition.required.items():
        have = employee.level(skill)
        if have >= min_level:
            earned += 1
        else:
            earned += have / min_level
            if requisition.is_mandatory(skill):
                blockers.append(skill)

    percent = round(100 * earned / len(requisition.required))
    return Match(employee=employee, requisition=requisition, score=percent, blockers=blockers)


@dataclass
class Bench:
    """The people currently available, and the operations a recruiter runs on them."""

    name: str
    _employees: list[Employee] = field(default_factory=list)

    @log_calls
    def add(self, employee_id: str, name: str, location: str, skills: dict[str, int]) -> Employee:
        """Add someone to the bench. Validates input and raises BenchError on bad data."""
        if not employee_id.strip():
            raise BenchError("employee_id cannot be empty")
        if any(e.employee_id == employee_id for e in self._employees):
            raise BenchError(f"{employee_id} is already on the bench")
        for skill, level in skills.items():
            if not 1 <= level <= 5:
                raise BenchError(f"{skill} level must be 1..5, got {level}")

        employee = Employee(employee_id=employee_id.strip(), name=name.strip(),
                            location=location.strip(), skills=dict(skills))
        self._employees.append(employee)
        return employee

    def find(self, employee_id: str) -> Employee:
        """Return the matching employee, or raise if nobody has that id."""
        for employee in self._employees:
            if employee.employee_id == employee_id:
                return employee
        raise BenchError(f"no employee with id {employee_id!r}")

    def candidates_for(self, requisition: Requisition) -> Iterator[Match]:
        """A GENERATOR: yields viable matches lazily, best score first.

        Nothing is computed until you iterate, and blocked candidates never get
        yielded at all — so `next(bench.candidates_for(req))` scores only as many
        people as it takes to find the first real one.
        """
        scored = sorted(
            (score(e, requisition) for e in self._employees),
            key=lambda m: m.score,
            reverse=True,
        )
        for match in scored:
            if not match.blockers:
                yield match

    @timed
    def summary(self, requisition: Requisition) -> str:
        viable = sum(1 for _ in self.candidates_for(requisition))
        return f"{self.name}: {viable}/{len(self._employees)} viable for {requisition.job_id}"


# --------------------------------------------------------------------------
# The program.
# --------------------------------------------------------------------------
def main() -> None:
    j2001 = Requisition(
        job_id="J2001",
        title="Senior Data Engineer",
        location="Bengaluru",
        required={"Python": 4, "Apache Spark": 4, "SQL": 4, "Apache Airflow": 3},
        mandatory={"Python", "Apache Spark", "SQL"},
    )

    bench = Bench("Data & Analytics bench")
    print(f"Filling {j2001}\n")
    print("Adding people to the bench:")
    bench.add("E1002", "Priya Raman", "Bengaluru",
              {"Python": 4, "Apache Spark": 5, "SQL": 5, "Apache Airflow": 4})
    bench.add("E1003", "Rahul Menon", "Hyderabad",
              {"Python": 4, "Apache Spark": 3, "SQL": 4})
    bench.add("E1005", "Vikram Iyer", "Chennai",
              {"Python": 3, "Apache Spark": 4, "Apache Kafka": 4})
    bench.add("E1006", "Sneha Kapoor", "Pune",
              {"Python": 3, "SQL": 4, "dbt": 4})

    # Error handling in action.
    print("\nTrying invalid input:")
    for bad in (
        lambda: bench.add("E1002", "Duplicate Person", "Pune", {"SQL": 4}),
        lambda: bench.add("E9999", "Impossible Person", "Kochi", {"SQL": 9}),
    ):
        try:
            bad()
        except BenchError as exc:
            print(f"  ✗ rejected: {exc}")

    # Everyone, scored — including the ones who cannot be shortlisted.
    print("\nEveryone, scored against J2001:")
    for employee in (bench.find(eid) for eid in ("E1002", "E1003", "E1005", "E1006")):
        print(f"  {score(employee, j2001)}")

    # Consume the generator — blocked candidates never appear.
    print("\nViable candidates (best first, blockers excluded):")
    for match in bench.candidates_for(j2001):
        print(f"  {match}")

    # A generator is lazy: this scores only until the first viable person.
    best = next(bench.candidates_for(j2001))
    print(f"\nFirst pick: {best.employee.name} at {best.score}%")

    print()
    print(bench.summary(j2001))


if __name__ == "__main__":
    main()
