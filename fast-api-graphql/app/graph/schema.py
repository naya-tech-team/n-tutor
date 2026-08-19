"""Query, Mutation, and the Schema object itself.

REST spreads its surface over many URLs; GraphQL puts the whole surface in these
two types. `Query` is everything a client may read, `Mutation` everything it may
change — and if a field is not here (or reachable from something here), it does
not exist as far as any client is concerned.
"""

from __future__ import annotations

import strawberry
from pydantic import ValidationError as PydanticValidationError

from app import models
from app.graph.types import (
    AddEmployeeResult,
    CandidateBlocked,
    Employee,
    EmployeeInput,
    Info,
    InvalidField,
    NotFound,
    Requisition,
    ShortlistEntry,
    ShortlistResult,
    ValidationFailed,
)


@strawberry.type
class Query:
    @strawberry.field(description="Liveness check that exercises the whole GraphQL path.")
    def health(self) -> str:
        return "ok"

    @strawberry.field(description="The directory, optionally just the people on the bench.")
    def employees(self, info: Info, available_only: bool = False) -> list[Employee]:
        people = info.context.store.list_employees(available_only=available_only)
        return [Employee.from_model(p) for p in people]

    @strawberry.field(description="One person by id. Null if there is no such person.")
    def employee(self, info: Info, employee_id: strawberry.ID) -> Employee | None:
        # Null, not an error: "no such row" is an ordinary answer to a lookup, and
        # a nullable field lets the rest of a multi-field query still succeed.
        person = info.context.store.get_employee(employee_id)
        return Employee.from_model(person) if person else None

    @strawberry.field(description="Every open role.")
    def requisitions(self, info: Info) -> list[Requisition]:
        return [Requisition.from_model(r) for r in info.context.store.list_requisitions()]

    @strawberry.field(description="One open role by id. Null if there is no such role.")
    def requisition(self, info: Info, job_id: strawberry.ID) -> Requisition | None:
        requisition = info.context.store.get_requisition(job_id)
        return Requisition.from_model(requisition) if requisition else None


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Add someone to the directory. Skill levels must be 1–5.")
    def add_employee(self, info: Info, employee: EmployeeInput) -> AddEmployeeResult:
        """GraphQL checked the *types*; Pydantic still has to check the *values*.

        `level: Int!` says nothing about 1–5, so the same models that guarded the
        REST edge are re-used one layer in. Doing it here rather than in the store
        keeps the rule in one place and still lets the schema report it as data.
        """
        try:
            data = models.EmployeeCreate(**strawberry.asdict(employee))
        except PydanticValidationError as exc:
            return ValidationFailed(
                message=f"{exc.error_count()} field(s) rejected",
                invalid_fields=[
                    InvalidField(field=".".join(str(p) for p in err["loc"]), message=err["msg"])
                    for err in exc.errors()
                ],
            )
        return Employee.from_model(info.context.store.create_employee(data))

    @strawberry.mutation(description="Shortlist a candidate — refused if they are blocked.")
    def shortlist_candidate(
        self, info: Info, job_id: strawberry.ID, employee_id: strawberry.ID
    ) -> ShortlistResult:
        """Three outcomes, all of them ordinary, all of them in the schema.

        HTTP had 404 and 409 to say "no such role" and "well-formed but refused".
        A GraphQL mutation returns 200 either way, so those become union members
        the client selects on — and *cannot* forget to handle, because reading a
        field off `ShortlistEntry` requires naming the type in the query.
        """
        store = info.context.store
        requisition = store.get_requisition(job_id)
        if requisition is None:
            return NotFound(message="requisition not found", kind="requisition", id=job_id)

        employee = store.get_employee(employee_id)
        if employee is None:
            return NotFound(message="employee not found", kind="employee", id=employee_id)

        match = store.score(employee, requisition)
        if match.blockers:
            return CandidateBlocked(
                message=f"{match.name} is missing mandatory {', '.join(match.blockers)}",
                blockers=match.blockers,
                score=match.score,
            )
        return ShortlistEntry.from_model(store.add_to_shortlist(job_id, match))


schema = strawberry.Schema(query=Query, mutation=Mutation)
