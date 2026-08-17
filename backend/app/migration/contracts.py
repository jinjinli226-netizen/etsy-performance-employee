from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.chat.schemas import MessageRole
from app.knowledge.schemas import KnowledgeStatus
from app.migration.guard import GuardValidationError, validate_shingles


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @field_validator(
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "reviewed_at",
        check_fields=False,
        mode="before",
    )
    @classmethod
    def canonical_zulu(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("canonical Zulu timestamp required")
        return datetime.fromisoformat(value[:-1] + "+00:00")

    @field_validator(
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "reviewed_at",
        check_fields=False,
    )
    @classmethod
    def utc_only(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
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


def _canonical_source_timestamps(values: dict[str, str]) -> dict[str, str]:
    import re

    pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
    checked: dict[str, str] = {}
    for key, value in values.items():
        _portable_id(key)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError("canonical evidence timestamp required")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as error:
            raise ValueError("valid canonical evidence timestamp required") from error
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("UTC evidence timestamp required")
        canonical = parsed.isoformat(timespec="microseconds" if parsed.microsecond else "seconds").replace("+00:00", "Z")
        if canonical != value:
            raise ValueError("canonical evidence timestamp required")
        checked[key] = value
    return checked


_LOCAL_PATH_OR_URL = re.compile(
    r"(?:file://|data:image/|https?://(?:www\.)?etsy\.com/listing/|(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|(?:^|[\s\"'])/(?:home|Users|var|tmp)/)",
    re.IGNORECASE,
)
_NON_PORTABLE_KEY = re.compile(r"(?:^|_)(?:path|url|uri|raw_image|image_data|image_bytes|image_base64)$", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BASE64_BLOB = re.compile(r"^[A-Za-z0-9+/]{1024,}={0,2}$")


def _portable_json(value: Any) -> Any:
    nodes = 0
    characters = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes, characters
        nodes += 1
        if depth > 20 or nodes > 10_000:
            raise ValueError("portable fact payload exceeds structure limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or _NON_PORTABLE_KEY.search(key):
                    raise ValueError("portable fact payload contains a non-portable field")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str):
            characters += len(item)
            if characters > 1_000_000 or _LOCAL_PATH_OR_URL.search(item) or _BASE64_BLOB.fullmatch(item):
                raise ValueError("portable fact payload contains local or raw image data")

    visit(value, 0)
    return value


def _portable_text(value: str | None) -> str | None:
    if value is not None and (_LOCAL_PATH_OR_URL.search(value) or _CONTROL.search(value)):
        raise ValueError("portable text contains local or unsafe data")
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
    _canonical_source_times = field_validator("source_timestamps")(
        lambda values: _canonical_source_timestamps(values)
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
    shingles: list[str] = Field(min_length=1, max_length=30_000)
    threshold: float = Field(ge=.1, le=1)

    @field_validator("source_timestamp", mode="before")
    @classmethod
    def canonical_source_timestamp(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str) or not __import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
            raise ValueError("canonical evidence timestamp required")
        return datetime.fromisoformat(value[:-1] + "+00:00")

    @field_validator("shingles")
    @classmethod
    def canonical_shingles(cls, value: list[str]) -> list[str]:
        try:
            return list(validate_shingles(value))
        except GuardValidationError as error:
            raise ValueError(str(error)) from error


class TrainingRunRecord(StrictRecord):
    id: str = Field(min_length=36, max_length=36)
    source_workbook_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_workbook_name: str = Field(min_length=1, max_length=255)
    requested_limit: int | None = Field(default=None, ge=1)
    status: Literal["running", "completed", "failed"]
    counts: dict[str, int]
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    _canonical_id = field_validator("id")(_portable_id)

    @field_validator("source_workbook_name")
    @classmethod
    def portable_filename(cls, value: str) -> str:
        if value.strip() != value or "/" in value or "\\" in value or any(ord(char) < 32 for char in value) or _portable_text(value) != value:
            raise ValueError("portable workbook filename required")
        return value

    @field_validator("counts")
    @classmethod
    def safe_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if len(value) > 16 or any(not re.fullmatch(r"[a-z_]{1,31}", key) or count < 0 for key, count in value.items()):
            raise ValueError("invalid training counts")
        return value


class TrainingSampleRecord(StrictRecord):
    id: str = Field(min_length=36, max_length=36)
    training_run_id: str = Field(min_length=36, max_length=36)
    listing_id: str = Field(pattern=r"^[0-9]{1,32}$")
    source_timestamp: datetime | None
    listing_snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    main_image_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    visual_facts: dict[str, Any] | None
    merged_facts: dict[str, Any] | None
    conflicts: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    schema_version: Literal[1]
    status: Literal["claimed", "fetching", "image_ready", "facts_ready", "candidates_ready", "reviewing", "activating", "completed", "skipped", "failed"]
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,62}$")
    image_included: Literal[False]
    image_path_included: Literal[False]
    created_at: datetime
    updated_at: datetime
    _canonical_ids = field_validator("id", "training_run_id")(_portable_id)
    _portable_facts = field_validator("visual_facts", "merged_facts", "conflicts")(_portable_json)

    @field_validator("source_timestamp", mode="before")
    @classmethod
    def canonical_source_timestamp(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("canonical Zulu timestamp required")
        return datetime.fromisoformat(value[:-1] + "+00:00")

    @field_validator("source_timestamp")
    @classmethod
    def utc_source_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0):
            raise ValueError("Zulu UTC timestamp required")
        return value


class TrainingReviewRecord(StrictRecord):
    id: str = Field(min_length=36, max_length=36)
    training_sample_id: str = Field(min_length=36, max_length=36)
    candidate_id: str | None = Field(default=None, min_length=8, max_length=64)
    kind: str = Field(min_length=1, max_length=127, pattern=r"^[a-z][a-z0-9_]{0,126}$")
    reviewer_version: str = Field(min_length=1, max_length=127)
    prompt_schema_version: Literal[1]
    decision: Literal["approve", "reject"]
    reason_code: str = Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_]{0,62}$")
    reason: str = Field(min_length=1, max_length=500)
    risk_flags: list[str] = Field(max_length=32)
    confidence: float = Field(ge=0, le=1)
    active_rule_public_id: str | None = Field(default=None, min_length=8, max_length=64)
    active_pattern_revision: int | None = Field(default=None, ge=0)
    activated_rule_version: str | None = Field(default=None, max_length=127)
    not_activated_reason: str | None = Field(default=None, max_length=63, pattern=r"^[a-z][a-z0-9_]{0,62}$")
    reviewed_at: datetime
    _canonical_ids = field_validator("id", "training_sample_id")(_portable_id)
    _canonical_refs = field_validator("candidate_id", "active_rule_public_id")(
        lambda value: _portable_id(value) if value is not None else value
    )
    _portable_text_fields = field_validator("reviewer_version", "reason", "activated_rule_version")(_portable_text)

    @field_validator("risk_flags")
    @classmethod
    def portable_risk_flags(cls, value: list[str]) -> list[str]:
        if any(not flag or len(flag) > 127 or _portable_text(flag) != flag for flag in value):
            raise ValueError("invalid risk flag")
        return value


class ManifestFileRecord(StrictRecord):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=256 * 1024 * 1024)
    mode: Literal["0644"]


class ManifestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal[1]
    profile_id: Literal["etsy-performance-us"]
    app_version: Literal["0.1.0"]
    package_id: str = Field(pattern=r"^pkg-[0-9a-f]{32}$")
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_status: Literal["pending"]
    raw_competitor_evidence_included: Literal[False]
    attachments_included: Literal[False]
    guard_threshold: float = Field(ge=.1, le=1)
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
    "training_runs": TrainingRunRecord,
    "training_samples": TrainingSampleRecord,
    "training_reviews": TrainingReviewRecord,
}
