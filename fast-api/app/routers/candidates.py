"""Employee, requisition and shortlist routes, grouped in APIRouters.

Two routers in one module because they share a store and read as one API:
  /employees      the directory
  /requisitions   open roles, their ranked candidates, and their shortlists
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models import (
    Employee,
    EmployeeCreate,
    MatchResult,
    Requisition,
    ShortlistEntry,
    ShortlistRequest,
)
from app.store import HRStore, get_store

employees_router = APIRouter(prefix="/employees", tags=["employees"])
requisitions_router = APIRouter(prefix="/requisitions", tags=["requisitions"])


# --------------------------------------------------------------------------
# Employees
# --------------------------------------------------------------------------
@employees_router.get("", response_model=list[Employee])
async def list_employees(
    available_only: bool = Query(False, description="Only people currently on the bench"),
    store: HRStore = Depends(get_store),
) -> list[Employee]:
    return store.list_employees(available_only=available_only)


@employees_router.post("", response_model=Employee, status_code=status.HTTP_201_CREATED)
async def create_employee(
    data: EmployeeCreate, store: HRStore = Depends(get_store)
) -> Employee:
    return store.create_employee(data)


@employees_router.get("/{employee_id}", response_model=Employee)
async def get_employee(employee_id: str, store: HRStore = Depends(get_store)) -> Employee:
    employee = store.get_employee(employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="employee not found")
    return employee


# --------------------------------------------------------------------------
# Requisitions
# --------------------------------------------------------------------------
@requisitions_router.get("", response_model=list[Requisition])
async def list_requisitions(store: HRStore = Depends(get_store)) -> list[Requisition]:
    return store.list_requisitions()


@requisitions_router.get("/{job_id}", response_model=Requisition)
async def get_requisition(job_id: str, store: HRStore = Depends(get_store)) -> Requisition:
    requisition = store.get_requisition(job_id)
    if requisition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="requisition not found")
    return requisition


@requisitions_router.get("/{job_id}/candidates", response_model=list[MatchResult])
async def rank_candidates(
    job_id: str,
    limit: int = Query(3, ge=1, le=20),
    store: HRStore = Depends(get_store),
) -> list[MatchResult]:
    """Score every available employee against this requisition, best first."""
    requisition = store.get_requisition(job_id)
    if requisition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="requisition not found")
    return store.rank(requisition, limit=limit)


@requisitions_router.get("/{job_id}/candidates/{employee_id}", response_model=MatchResult)
async def score_candidate(
    job_id: str, employee_id: str, store: HRStore = Depends(get_store)
) -> MatchResult:
    requisition = store.get_requisition(job_id)
    if requisition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="requisition not found")
    employee = store.get_employee(employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="employee not found")
    return store.score(employee, requisition)


@requisitions_router.get("/{job_id}/shortlist", response_model=list[ShortlistEntry])
async def get_shortlist(job_id: str, store: HRStore = Depends(get_store)) -> list[ShortlistEntry]:
    if store.get_requisition(job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="requisition not found")
    return store.get_shortlist(job_id)


@requisitions_router.post(
    "/{job_id}/shortlist", response_model=ShortlistEntry, status_code=status.HTTP_201_CREATED
)
async def add_to_shortlist(
    job_id: str, data: ShortlistRequest, store: HRStore = Depends(get_store)
) -> ShortlistEntry:
    """Shortlist a candidate — refused if they miss a mandatory skill.

    409 rather than 400: the request is well-formed, it conflicts with the state
    of the requisition. That distinction is what lets a client tell "you sent me
    nonsense" apart from "this person genuinely cannot do the job".
    """
    requisition = store.get_requisition(job_id)
    if requisition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="requisition not found")
    employee = store.get_employee(data.employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="employee not found")

    match = store.score(employee, requisition)
    if match.blockers:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"{match.name} is missing mandatory {', '.join(match.blockers)}",
        )
    return store.add_to_shortlist(job_id, match)
