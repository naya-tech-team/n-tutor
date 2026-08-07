"""Task Tracker — a tiny runnable app that shows off core Python.

Concepts demonstrated (matches the Python: Quick Start page):
  - classes & objects        -> Task, TaskList
  - encapsulation/properties -> Task.done, Task.mark_done()
  - generators               -> TaskList.pending()
  - decorators               -> @timed, @log_calls
  - error handling           -> add(), find()

Run it:
    python task_tracker.py

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
class TaskError(Exception):
    """Raised when a task operation is invalid."""


# --------------------------------------------------------------------------
# Classes & objects.
# --------------------------------------------------------------------------
@dataclass
class Task:
    title: str
    priority: int = 3          # 1 = high, 5 = low
    done: bool = False

    def mark_done(self) -> None:
        self.done = True

    def __str__(self) -> str:
        box = "x" if self.done else " "
        return f"[{box}] P{self.priority} {self.title}"


@dataclass
class TaskList:
    name: str
    _tasks: list[Task] = field(default_factory=list)

    @log_calls
    def add(self, title: str, priority: int = 3) -> Task:
        """Add a task. Validates input and raises TaskError on bad data."""
        if not title.strip():
            raise TaskError("title cannot be empty")
        if not 1 <= priority <= 5:
            raise TaskError(f"priority must be 1..5, got {priority}")
        task = Task(title=title.strip(), priority=priority)
        self._tasks.append(task)
        return task

    def find(self, title: str) -> Task:
        """Return the first matching task, or raise if none is found."""
        for task in self._tasks:
            if task.title == title:
                return task
        raise TaskError(f"no task titled {title!r}")

    def pending(self) -> Iterator[Task]:
        """A GENERATOR: yields tasks lazily, highest priority first.

        Nothing is computed until you iterate — memory-friendly for big lists.
        """
        for task in sorted(self._tasks, key=lambda t: t.priority):
            if not task.done:
                yield task

    @timed
    def summary(self) -> str:
        done = sum(1 for t in self._tasks if t.done)
        return f"{self.name}: {done}/{len(self._tasks)} done"


# --------------------------------------------------------------------------
# The program.
# --------------------------------------------------------------------------
def main() -> None:
    todo = TaskList("Launch AI agent")
    print("Adding tasks:")
    todo.add("Write Terraform config", priority=1)
    todo.add("Build MCP server", priority=2)
    todo.add("Wire up Strands agent", priority=2)
    todo.add("Write docs", priority=4)

    # Error handling in action.
    print("\nTrying an invalid task:")
    try:
        todo.add("", priority=9)
    except TaskError as exc:
        print(f"  ✗ rejected: {exc}")

    # Mark one done via find().
    todo.find("Build MCP server").mark_done()

    # Consume the generator.
    print("\nStill pending (high priority first):")
    for task in todo.pending():
        print(f"  {task}")

    print()
    print(todo.summary())


if __name__ == "__main__":
    main()
