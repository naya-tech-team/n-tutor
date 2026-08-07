"""Tests for task_tracker. Run:  pytest -q  (or: python -m pytest -q)"""

import pytest

from task_tracker import Task, TaskError, TaskList


def test_add_and_summary():
    todo = TaskList("demo")
    todo.add("a", priority=1)
    todo.add("b", priority=2)
    assert todo.summary() == "demo: 0/2 done"


def test_mark_done_updates_summary():
    todo = TaskList("demo")
    todo.add("a")
    todo.find("a").mark_done()
    assert todo.summary() == "demo: 1/1 done"


def test_pending_is_sorted_by_priority_and_skips_done():
    todo = TaskList("demo")
    todo.add("low", priority=5)
    todo.add("high", priority=1)
    todo.add("mid", priority=3)
    todo.find("mid").mark_done()
    titles = [t.title for t in todo.pending()]
    assert titles == ["high", "low"]  # sorted, "mid" excluded (done)


@pytest.mark.parametrize("title,priority", [("", 3), ("ok", 0), ("ok", 6)])
def test_invalid_input_raises(title, priority):
    todo = TaskList("demo")
    with pytest.raises(TaskError):
        todo.add(title, priority)


def test_find_missing_raises():
    todo = TaskList("demo")
    with pytest.raises(TaskError):
        todo.find("nope")


def test_task_str():
    assert str(Task("x", priority=2, done=True)) == "[x] P2 x"
