from __future__ import annotations

from datetime import datetime

from app.schemas.common import ApiModel, ORMModel


class MemoryFileOut(ORMModel):
    """One of the memory tool's files (task 5.2)."""

    path: str
    content: str
    updated_at: datetime


class MemoryCleared(ApiModel):
    deleted: int


__all__ = ["MemoryCleared", "MemoryFileOut"]
