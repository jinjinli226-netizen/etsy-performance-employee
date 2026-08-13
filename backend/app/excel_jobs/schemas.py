from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GeneratedListingFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    filename: str
    sha256: str
    size_bytes: int
    created_at: datetime


class JobErrorRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ExcelJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_filename: str
    source_sha256: str
    source_size_bytes: int
    status: JobStatus
    progress_percent: int = Field(ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)
    error: JobErrorRead | None = None
    created_at: datetime
    updated_at: datetime
    artifact: ArtifactRead | None = None


class ExcelJobPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExcelJobRead]
    total: int
    limit: int
    offset: int


class JobEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    type: str
    payload: dict
    created_at: datetime
