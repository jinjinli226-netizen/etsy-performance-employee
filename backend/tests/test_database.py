from __future__ import annotations

from datetime import UTC

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.init_db import init_db
from app.db.models import (
    Artifact,
    Conversation,
    ExcelJob,
    KnowledgeCandidate,
    Message,
    RuleVersion,
)
from app.db.session import create_engine_for_url, create_session_factory
from app.excel_jobs.schemas import JobStatus
from app.knowledge.schemas import KnowledgeStatus


@pytest.fixture
def session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine_for_url(database_url)
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def test_persists_and_reloads_core_records_with_relationships(session_factory) -> None:
    with session_factory() as session:
        conversation = Conversation(title="Listing review")
        conversation.messages.append(Message(role="user", content="Review this listing"))

        job = ExcelJob(
            conversation=conversation,
            source_filename="listings.xlsx",
            status=JobStatus.NEEDS_REVIEW,
        )
        job.artifacts.append(Artifact(kind="review_workbook", path="artifacts/review.xlsx"))

        candidate = KnowledgeCandidate(
            title="Prefer material facts",
            proposal={"field": "specification", "rule": "retain material"},
            status=KnowledgeStatus.TESTING,
        )
        candidate.rule_versions.append(
            RuleVersion(
                version="rules-2026-08-12.1",
                rules={"retain_material": True},
                status=KnowledgeStatus.ACTIVE,
            )
        )
        session.add_all([conversation, job, candidate])
        session.commit()
        conversation_id = conversation.id
        job_id = job.id
        candidate_id = candidate.id

    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        job = session.get(ExcelJob, job_id)
        candidate = session.get(KnowledgeCandidate, candidate_id)

        stored_job_status = session.execute(
            text("SELECT status FROM excel_jobs WHERE id = :id"), {"id": job_id}
        ).scalar_one()
        stored_candidate_status = session.execute(
            text("SELECT status FROM knowledge_candidates WHERE id = :id"),
            {"id": candidate_id},
        ).scalar_one()

        assert conversation is not None
        assert [(message.role, message.content) for message in conversation.messages] == [
            ("user", "Review this listing")
        ]
        assert conversation.created_at.tzinfo == UTC
        assert conversation.created_at.isoformat().endswith("+00:00")

        assert job is not None
        assert job.status is JobStatus.NEEDS_REVIEW
        assert stored_job_status == "needs_review"
        assert job.conversation is conversation
        assert [(artifact.kind, artifact.path) for artifact in job.artifacts] == [
            ("review_workbook", "artifacts/review.xlsx")
        ]
        assert job.updated_at.tzinfo == UTC

        assert candidate is not None
        assert candidate.status is KnowledgeStatus.TESTING
        assert stored_candidate_status == "testing"
        assert candidate.rule_versions[0].version == "rules-2026-08-12.1"
        assert candidate.rule_versions[0].status is KnowledgeStatus.ACTIVE


def test_sqlite_foreign_keys_are_enabled(session_factory) -> None:
    with session_factory() as session:
        enabled = session.execute(text("PRAGMA foreign_keys")).scalar_one()
        assert enabled == 1

        session.add(Message(conversation_id=999, role="user", content="orphan"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_init_db_creates_all_required_tables(tmp_path) -> None:
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'schema.db'}")
    try:
        init_db(engine)
        with engine.connect() as connection:
            table_names = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
            }
        assert {
            "conversations",
            "messages",
            "attachments",
            "excel_jobs",
            "artifacts",
            "knowledge_candidates",
            "knowledge_patterns",
            "rule_versions",
            "feedback_events",
            "audit_events",
        } <= table_names
        assert set(Base.metadata.tables) == {
            "conversations",
            "messages",
            "attachments",
            "excel_jobs",
            "artifacts",
            "knowledge_candidates",
            "knowledge_patterns",
            "rule_versions",
            "feedback_events",
            "audit_events",
        }
    finally:
        engine.dispose()
