"""A tiny in-memory data store, injected via FastAPI's Depends().

In a real app this would be a database. Keeping it in one class (no globals)
makes it easy to swap out and easy to test.
"""

from __future__ import annotations

from itertools import count

from app.models import Task, TaskCreate


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._ids = count(1)

    def list(self) -> list[Task]:
        return list(self._tasks.values())

    def get(self, task_id: int) -> Task | None:
        return self._tasks.get(task_id)

    def create(self, data: TaskCreate) -> Task:
        task = Task(id=next(self._ids), **data.model_dump())
        self._tasks[task.id] = task
        return task

    def set_done(self, task_id: int, done: bool) -> Task | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.done = done
        return task


# One shared instance for the app. Tests can build their own.
_store = TaskStore()


def get_store() -> TaskStore:
    """Dependency provider — FastAPI calls this to inject the store."""
    return _store
