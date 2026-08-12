from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GeneratedListingFields(BaseModel):
    head_titles: str
    tags: list[str]
    specification: str
    category: str
    instructions_for_buyers: str
    confidence: float = Field(ge=0, le=1)
    fact_warnings: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    rule_version: str


class ExcelJobCreate(BaseModel):
    conversation_id: int | None = None
    source_filename: str


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    path: str
    created_at: datetime


class ExcelJobRead(ExcelJobCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    artifacts: list[ArtifactRead]
