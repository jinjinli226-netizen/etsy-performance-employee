from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.chat.schemas import MessageRole
from app.knowledge.schemas import KnowledgeStatus


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("created_at", "updated_at", check_fields=False, mode="before")
    @classmethod
    def canonical_zulu(cls, value: Any) -> Any:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("canonical Zulu timestamp required")
        return datetime.fromisoformat(value[:-1] + "+00:00")

    @field_validator("created_at", "updated_at", check_fields=False)
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("Zulu UTC timestamp required")
        return value


def _portable_id(value: str) -> str:
    import re
    from uuid import UUID
    if value.startswith(("legacy-", "kc-", "ev-")):
        if not re.fullmatch(r"(?:legacy-[a-z]+-[0-9a-f]{24}|k[cv]-[0-9a-f]{32}|ev-[0-9a-f]{32})", value):
            raise ValueError("non-canonical portable ID")
        return value
    if str(UUID(value)) != value:
        raise ValueError("non-canonical UUID")
    return value


class ConversationRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    created_at: datetime
    updated_at: datetime
    _canonical_id = field_validator("id")(_portable_id)


class MessageRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    conversation_id: str = Field(min_length=8, max_length=64)
    role: MessageRole
    content: str = Field(max_length=1_000_000)
    created_at: datetime
    evidence_bound: bool
    contains_evidence_control: bool
    evidence_ids: list[str] = Field(max_length=500)
    _canonical_ids = field_validator("id", "conversation_id")(_portable_id)
    _canonical_evidence = field_validator("evidence_ids")(
        lambda values: [_portable_id(value) for value in values]
    )


class AttachmentRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    conversation_id: str = Field(min_length=8, max_length=64)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=127)
    content_included: Literal[False]
    created_at: datetime
    _canonical_ids = field_validator("id", "conversation_id")(_portable_id)


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
    _canonical_id = field_validator("id")(_portable_id)
    _canonical_refs = field_validator("conversation_id", "message_id", "base_active_rule_public_id")(
        lambda value: _portable_id(value) if value is not None else value
    )
    _canonical_evidence = field_validator("evidence_ids")(
        lambda values: [_portable_id(value) for value in values]
    )


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
    _canonical_id = field_validator("id")(_portable_id)
    _canonical_source = field_validator("source_candidate_id")(
        lambda value: _portable_id(value) if value is not None else value
    )


class RuleRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    pattern_id: str
    candidate_id: str | None = None
    version: str = Field(min_length=1, max_length=127)
    sequence: int = Field(ge=0)
    rules: dict[str, Any]
    status: KnowledgeStatus
    created_at: datetime
    _canonical_ids = field_validator("id", "pattern_id")(_portable_id)
    _canonical_candidate = field_validator("candidate_id")(
        lambda value: _portable_id(value) if value is not None else value
    )


class FeedbackRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    candidate_id: str | None = None
    conversation_id: str | None = None
    excel_job_id: str | None = None
    unresolved_relationships: list[str] = Field(default_factory=list, max_length=16)
    feedback_id: str | None = Field(default=None, max_length=128)
    row_id: str | None = Field(default=None, max_length=128)
    accepted: bool | None
    event_type: str = Field(min_length=1, max_length=127)
    payload: dict[str, Any]
    created_at: datetime
    _canonical_id = field_validator("id")(_portable_id)
    _canonical_refs = field_validator("candidate_id", "conversation_id", "excel_job_id")(
        lambda value: _portable_id(value) if value is not None else value
    )


class AuditRecord(StrictRecord):
    id: str = Field(min_length=8, max_length=64)
    actor: str = Field(min_length=1, max_length=127)
    action: str = Field(min_length=1, max_length=127)
    entity_type: Literal["candidate", "pattern", "rule", "conversation", "message", "excel_job", "evidence", "learning", "config"]
    entity_public_id: str | None = Field(default=None, max_length=127)
    unresolved_reason: str | None = Field(default=None, max_length=127)
    details: dict[str, Any]
    created_at: datetime
    _canonical_id = field_validator("id")(_portable_id)
    _canonical_entity = field_validator("entity_public_id")(
        lambda value: _portable_id(value) if value is not None else value
    )


class GuardRecord(StrictRecord):
    id: str = Field(pattern=r"^ev-[0-9a-f]{32}$")
    source_timestamp: datetime | None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shingles: list[str] = Field(max_length=30_000)
    threshold: float = Field(ge=0, le=1)


class ManifestFileRecord(StrictRecord):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=256 * 1024 * 1024)
    mode: Literal["0644"]


class ManifestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    profile_id: Literal["etsy-performance-us"]
    app_version: Literal["0.1.0"]
    package_id: str = Field(pattern=r"^pkg-[0-9a-f]{32}$")
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_status: Literal["pending"]
    raw_competitor_evidence_included: Literal[False]
    attachments_included: Literal[False]
    guard_threshold: float = Field(ge=0, le=1)
    record_counts: dict[str, int]
    files: list[ManifestFileRecord] = Field(min_length=1, max_length=128)


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
