from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum as PythonEnum
from typing import Any, TypeVar

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.chat.schemas import MessageRole
from app.db.base import Base
from app.excel_jobs.schemas import JobStatus
from app.knowledge.schemas import KnowledgeStatus

EnumType = TypeVar("EnumType", bound=PythonEnum)


def enum_values(enum_class: type[EnumType]) -> list[str]:
    return [member.value for member in enum_class]


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
        Enum(MessageRole, values_callable=enum_values), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
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
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, values_callable=enum_values), default=JobStatus.QUEUED, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)

    conversation: Mapped[Conversation | None] = relationship(back_populates="excel_jobs")
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="excel_job", cascade="all, delete-orphan", order_by="Artifact.id"
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    excel_job_id: Mapped[int] = mapped_column(
        ForeignKey("excel_jobs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(63), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    excel_job: Mapped[ExcelJob] = relationship(back_populates="artifacts")


class KnowledgeCandidate(TimestampMixin, Base):
    __tablename__ = "knowledge_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, values_callable=enum_values),
        default=KnowledgeStatus.PROPOSED,
        nullable=False,
    )

    rule_versions: Mapped[list[RuleVersion]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="RuleVersion.id"
    )


class KnowledgePattern(TimestampMixin, Base):
    __tablename__ = "knowledge_patterns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    pattern: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, values_callable=enum_values),
        default=KnowledgeStatus.PROPOSED,
        nullable=False,
    )


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_candidates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(127), nullable=False, unique=True)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, values_callable=enum_values), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    candidate: Mapped[KnowledgeCandidate] = relationship(back_populates="rule_versions")


class FeedbackEvent(Base):
    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(primary_key=True)
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
