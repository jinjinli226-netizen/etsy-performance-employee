from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.chat.schemas import MessageRole
from app.knowledge.schemas import KnowledgeStatus


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)

    @field_validator("created_at", "updated_at", check_fields=False)
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC timestamp required")
        return value


class ConversationRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    created_at: datetime
    updated_at: datetime


class MessageRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    conversation_id: str = Field(min_length=8, max_length=64)
    role: MessageRole
    content: str = Field(max_length=1_000_000)
    created_at: datetime


class AttachmentRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    conversation_id: str = Field(min_length=8, max_length=64)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=127)
    content_included: Literal[False]
    created_at: datetime


class CandidateRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    kind: str | None = Field(default=None, max_length=127)
    abstract_summary: str | None = Field(default=None, max_length=100_000)
    proposal: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_ids: list[str]
    source_timestamps: dict[str, str]
    conversation_id: str | None = None
    message_id: str | None = None
    trace_id: str | None = Field(default=None, max_length=127)
    base_active_rule_public_id: str | None = Field(default=None, max_length=64)
    base_pattern_revision: int | None = Field(default=None, ge=0)
    revision: int = Field(ge=0)
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime


class PatternRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    source_candidate_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    kind: str | None = Field(default=None, max_length=127)
    abstract_summary: str | None = Field(default=None, max_length=100_000)
    revision: int = Field(ge=0)
    pattern: dict[str, Any]
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime


class RuleRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    pattern_id: str
    candidate_id: str | None = None
    version: str = Field(min_length=1, max_length=127)
    sequence: int = Field(ge=0)
    rules: dict[str, Any]
    status: KnowledgeStatus
    created_at: datetime


class FeedbackRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    candidate_id: str | None = None
    conversation_id: str | None = None
    excel_job_id: str | None = None
    unresolved_relationships: list[str] = []
    feedback_id: str | None = Field(default=None, max_length=128)
    row_id: str | None = Field(default=None, max_length=128)
    accepted: bool | None
    event_type: str = Field(min_length=1, max_length=127)
    payload: dict[str, Any]
    created_at: datetime


class AuditRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    actor: str = Field(min_length=1, max_length=127)
    action: str = Field(min_length=1, max_length=127)
    entity_type: str = Field(min_length=1, max_length=127)
    entity_id: str = Field(min_length=1, max_length=127)
    details: dict[str, Any]
    created_at: datetime


class GuardRecord(StrictRecord):
    id: str = Field(pattern=r"^ev-[0-9a-f]{32}$")
    source_timestamp: datetime | None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shingles: list[str] = Field(max_length=30_000)
    threshold: float = Field(ge=0.1, le=1)


RECORD_MODELS = {
    "conversations": ConversationRecord,
    "messages": MessageRecord,
    "attachments": AttachmentRecord,
    "knowledge_candidates": CandidateRecord,
    "knowledge_patterns": PatternRecord,
    "rule_versions": RuleRecord,
    "feedback_events": FeedbackRecord,
    "audit_events": AuditRecord,
    "evidence_guard": GuardRecord,
}
