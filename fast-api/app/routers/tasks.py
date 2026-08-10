"""Task routes, grouped in an APIRouter (mounted under /tasks in main.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.models import Task, TaskCreate
from app.store import TaskStore, get_store

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[Task])
async def list_tasks(store: TaskStore = Depends(get_store)) -> list[Task]:
    return store.list()


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate, store: TaskStore = Depends(get_store)
) -> Task:
    return store.create(data)


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: int, store: TaskStore = Depends(get_store)) -> Task:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
    return task


@router.post("/{task_id}/done", response_model=Task)
async def complete_task(
    task_id: int, store: TaskStore = Depends(get_store)
) -> Task:
    task = store.set_done(task_id, True)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
    return task
