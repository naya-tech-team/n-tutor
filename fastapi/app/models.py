"""Pydantic v2 models — the shapes of data going in and out of the API."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Priority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class TaskCreate(BaseModel):
    """What a client sends to create a task."""

    title: str = Field(min_length=1, max_length=120, examples=["Write docs"])
    priority: Priority = Priority.medium


class Task(TaskCreate):
    """What the API stores and returns (adds server-set fields)."""

    id: int
    done: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
