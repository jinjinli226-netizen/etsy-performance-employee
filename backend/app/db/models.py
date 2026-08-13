from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum as PythonEnum
from typing import Any, TypeVar

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.chat.schemas import MessageRole
from app.db.base import Base
from app.excel_jobs.schemas import JobStatus
from app.knowledge.schemas import KnowledgeStatus

EnumType = TypeVar("EnumType", bound=PythonEnum)


def enum_values(enum_class: type[EnumType]) -> list[str]:
    return [member.value for member in enum_class]


def strict_enum(enum_class: type[EnumType], constraint_name: str) -> Enum:
    return Enum(
        enum_class,
        values_callable=enum_values,
        native_enum=False,
        validate_strings=True,
        create_constraint=True,
        name=constraint_name,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC without an offset in SQLite and restore aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    employee_session_id: Mapped[str | None] = mapped_column(String(255))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    excel_jobs: Mapped[list[ExcelJob]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        strict_enum(MessageRole, "ck_messages_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    operation_status: Mapped[str | None] = mapped_column(String(31))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="attachments")


class ExcelJob(TimestampMixin, Base):
    __tablename__ = "excel_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        strict_enum(JobStatus, "ck_excel_jobs_status"),
        default=JobStatus.QUEUED,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(63))
    error_message: Mapped[str | None] = mapped_column(String(255))
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    conversation: Mapped[Conversation | None] = relationship(back_populates="excel_jobs")
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="excel_job", cascade="all, delete-orphan", order_by="Artifact.id"
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="excel_job", cascade="all, delete-orphan", order_by="JobEvent.id"
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    excel_job_id: Mapped[int] = mapped_column(
        ForeignKey("excel_jobs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(63), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    excel_job: Mapped[ExcelJob] = relationship(back_populates="artifacts")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    excel_job_id: Mapped[int] = mapped_column(
        ForeignKey("excel_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(63), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    excel_job: Mapped[ExcelJob] = relationship(back_populates="events")


class CompetitorEvidence(Base):
    __tablename__ = "competitor_evidence"
    __table_args__ = (
        CheckConstraint("public_id GLOB 'ev-[0-9a-f][0-9a-f]*' AND length(public_id)=35", name="ck_evidence_public_id"),
        CheckConstraint("canonical_url LIKE 'https://www.etsy.com/listing/%'", name="ck_evidence_url"),
        CheckConstraint("length(content_hash)=64 AND content_hash NOT GLOB '*[^0-9a-f]*'", name="ck_evidence_hash"),
        CheckConstraint("length(snapshot_hash)=64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*'", name="ck_evidence_snapshot_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(35), unique=True, index=True, nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class KnowledgeCandidate(TimestampMixin, Base):
    __tablename__ = "knowledge_candidates"
    __table_args__ = (
        CheckConstraint("public_id IS NULL OR (public_id GLOB 'kc-[0-9a-f][0-9a-f]*' AND length(public_id)=35)", name="ck_candidate_public_id"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_candidate_confidence"),
        CheckConstraint("revision >= 0", name="ck_candidate_revision"),
        CheckConstraint("base_pattern_revision IS NULL OR base_pattern_revision >= 0", name="ck_candidate_base_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str | None] = mapped_column(String(35), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    kind: Mapped[str | None] = mapped_column(String(127), index=True)
    abstract_summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    evidence_ids: Mapped[list[str] | None] = mapped_column(JSON)
    source_timestamps: Mapped[dict[str, str] | None] = mapped_column(JSON)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    trace_id: Mapped[str | None] = mapped_column(String(127), index=True)
    base_active_rule_public_id: Mapped[str | None] = mapped_column(String(36))
    base_pattern_revision: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        strict_enum(KnowledgeStatus, "ck_knowledge_candidates_status"),
        default=KnowledgeStatus.PROPOSED,
        nullable=False,
    )

    rule_versions: Mapped[list[RuleVersion]] = relationship(
        back_populates="candidate", order_by="RuleVersion.id"
    )
    sourced_patterns: Mapped[list[KnowledgePattern]] = relationship(
        back_populates="source_candidate", order_by="KnowledgePattern.id"
    )


class KnowledgePattern(TimestampMixin, Base):
    __tablename__ = "knowledge_patterns"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_candidates.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    kind: Mapped[str | None] = mapped_column(String(127), unique=True, index=True)
    abstract_summary: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pattern: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        strict_enum(KnowledgeStatus, "ck_knowledge_patterns_status"),
        default=KnowledgeStatus.PROPOSED,
        nullable=False,
    )

    source_candidate: Mapped[KnowledgeCandidate | None] = relationship(
        back_populates="sourced_patterns"
    )
    rule_versions: Mapped[list[RuleVersion]] = relationship(
        back_populates="pattern", cascade="all, delete-orphan", order_by="RuleVersion.id"
    )


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    pattern_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_patterns.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_candidates.id", ondelete="SET NULL")
    )
    version: Mapped[str] = mapped_column(String(127), nullable=False, unique=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        strict_enum(KnowledgeStatus, "ck_rule_versions_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    pattern: Mapped[KnowledgePattern] = relationship(back_populates="rule_versions")
    candidate: Mapped[KnowledgeCandidate | None] = relationship(back_populates="rule_versions")


class FeedbackEvent(Base):
    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    knowledge_candidate_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_candidates.id", ondelete="CASCADE"), index=True)
    feedback_id: Mapped[str | None] = mapped_column(String(128), index=True)
    row_id: Mapped[str | None] = mapped_column(String(128), index=True)
    accepted: Mapped[bool | None] = mapped_column()
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    excel_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("excel_jobs.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(127), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(127), nullable=False)
    action: Mapped[str] = mapped_column(String(127), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(127), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(127), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
