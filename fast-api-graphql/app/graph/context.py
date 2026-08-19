"""The GraphQL context: what every resolver is handed, and how it gets built.

REST hands a route its dependencies through `Depends()`. GraphQL has one route,
so dependencies arrive a level down — through the *context*, a per-request object
Strawberry passes to every resolver as `info.context`.

Building it with a FastAPI dependency means the two systems stay connected:
`get_context` still `Depends(get_store)`, so `app.dependency_overrides` works in
tests exactly as it did for the REST version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends
from strawberry.dataloader import DataLoader
from strawberry.fastapi import BaseContext

from app.store import HRStore, get_store

if TYPE_CHECKING:
    from app.models import Employee


class Context(BaseContext):
    """Per-request state. One instance per HTTP request, not per resolver call.

    `BaseContext` gives the `request` / `response` / `background_tasks` attributes,
    so a resolver can read a header or set a cookie if it ever needs to.
    """

    def __init__(self, store: HRStore) -> None:
        super().__init__()
        self.store = store
        # Fresh per request — a DataLoader's cache must never outlive the request
        # that filled it, or one client's read would serve another client's stale data.
        self.employees: DataLoader[str, Employee | None] = DataLoader(
            load_fn=self._load_employees
        )

    async def _load_employees(self, employee_ids: list[str]) -> list[Employee | None]:
        """Batch function: N `.load()` calls in one tick arrive here as one list.

        This is the answer to N+1. Ask for a shortlist of 30 people and each entry
        wants its employee record; without this, that is 30 trips to the HRMS. With
        it, Strawberry collects the ids and calls this once.
        """
        return self.store.get_employees(employee_ids)


async def get_context(store: HRStore = Depends(get_store)) -> Context:
    """Context factory, wired into FastAPI's dependency system by GraphQLRouter."""
    return Context(store=store)
