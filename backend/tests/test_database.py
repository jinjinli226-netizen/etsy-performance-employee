from __future__ import annotations

from datetime import UTC
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.db.base import Base
from app.db.init_db import init_db
from app.db.models import (
    Artifact,
    Conversation,
    ExcelJob,
    JobEvent,
    KnowledgeCandidate,
    KnowledgePattern,
    Message,
    RuleVersion,
)
from app.db.session import create_engine_for_url, create_session_factory, session_scope
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
            public_id="00000000-0000-4000-8000-000000000010",
            source_filename="listings.xlsx",
            source_sha256="a" * 64,
            source_size_bytes=1,
            status=JobStatus.NEEDS_REVIEW,
        )
        job.artifacts.append(Artifact(kind="review_workbook", path="artifacts/review.xlsx"))

        candidate = KnowledgeCandidate(
            title="Prefer material facts",
            proposal={"field": "specification", "rule": "retain material"},
            status=KnowledgeStatus.ACTIVE,
        )
        pattern = KnowledgePattern(
            name="material_facts",
            pattern={"field": "specification"},
            status=KnowledgeStatus.ACTIVE,
            source_candidate=candidate,
        )
        pattern.rule_versions.extend(
            [
                RuleVersion(
                    version="rules-2026-08-12.1",
                    sequence=1,
                    rules={"retain_material": True},
                    candidate=candidate,
                    status=KnowledgeStatus.ACTIVE,
                ),
                RuleVersion(
                    version="rules-2026-08-12.2",
                    sequence=2,
                    rules={"retain_material": True, "warn_missing_material": True},
                    candidate=candidate,
                    status=KnowledgeStatus.TESTING,
                ),
            ]
        )
        session.add_all([conversation, job, candidate, pattern])
        session.commit()
        conversation_id = conversation.id
        job_id = job.id
        candidate_id = candidate.id
        pattern_id = pattern.id

    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        job = session.get(ExcelJob, job_id)
        candidate = session.get(KnowledgeCandidate, candidate_id)
        pattern = session.get(KnowledgePattern, pattern_id)

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
        assert candidate.status is KnowledgeStatus.ACTIVE
        assert stored_candidate_status == "active"
        assert pattern is not None
        assert pattern.source_candidate is candidate
        assert [version.version for version in pattern.rule_versions] == [
            "rules-2026-08-12.1",
            "rules-2026-08-12.2",
        ]
        assert all(version.pattern is pattern for version in pattern.rule_versions)
        assert all(version.candidate is candidate for version in pattern.rule_versions)
        assert pattern.rule_versions[0].status is KnowledgeStatus.ACTIVE


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
            "job_events",
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
            "job_events",
            "competitor_evidence",
            "imported_evidence_fingerprints",
            "migration_imports",
            "migration_exports",
        }
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("model"),
    [
        ExcelJob(
            public_id="00000000-0000-4000-8000-000000000011",
            source_filename="invalid.xlsx",
            source_sha256="b" * 64,
            source_size_bytes=1,
            status="not_a_status",
        ),
        KnowledgeCandidate(
            title="Invalid",
            proposal={},
            status="not_a_status",
        ),
    ],
)
def test_orm_rejects_invalid_enum_strings(session_factory, model) -> None:
    with session_factory() as session:
        session.add(model)
        with pytest.raises(StatementError):
            session.commit()


def test_database_enum_constraints_reject_invalid_raw_values(session_factory) -> None:
    engine = session_factory.kw["bind"]
    constraint_names = {
        constraint["name"]
        for table in (
            "messages",
            "excel_jobs",
            "knowledge_candidates",
            "knowledge_patterns",
            "rule_versions",
        )
        for constraint in inspect(engine).get_check_constraints(table)
    }
    assert {
        "ck_messages_role",
        "ck_excel_jobs_status",
        "ck_knowledge_candidates_status",
        "ck_knowledge_patterns_status",
        "ck_rule_versions_status",
    } <= constraint_names

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO excel_jobs "
                    "(source_filename, status, created_at, updated_at) "
                    "VALUES ('invalid.xlsx', 'not_a_status', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )


def test_excel_job_contract_rejects_unreachable_public_ids_and_accepts_canonical_uuid(
    session_factory,
) -> None:
    engine = session_factory.kw["bind"]
    canonical = "12345678-1234-4abc-8def-1234567890ab"
    invalid_ids = (
        "x" * 36,
        "1234567-81234-4abc-8def-1234567890ab",
        "g2345678-1234-4abc-8def-1234567890ab",
        canonical.upper(),
    )
    insert = text(
        "INSERT INTO excel_jobs "
        "(public_id, source_filename, source_sha256, source_size_bytes, status, "
        "progress_percent, created_at, updated_at) VALUES "
        "(:public_id, 'raw.xlsx', :sha, 1, 'failed', 100, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    with engine.begin() as connection:
        connection.execute(insert, {"public_id": canonical, "sha": "a" * 64})
        assert connection.execute(
            text("SELECT public_id FROM excel_jobs WHERE public_id=:public_id"),
            {"public_id": canonical},
        ).scalar_one() == canonical

        for public_id in invalid_ids:
            with pytest.raises(IntegrityError):
                connection.execute(insert, {"public_id": public_id, "sha": "b" * 64})
            with pytest.raises(IntegrityError):
                connection.execute(
                    text("UPDATE excel_jobs SET public_id=:public_id WHERE public_id=:canonical"),
                    {"public_id": public_id, "canonical": canonical},
                )


def test_session_scope_commits_successful_transaction(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Conversation(title="Committed"))

    with session_factory() as session:
        assert session.query(Conversation).filter_by(title="Committed").one()


def test_session_scope_rolls_back_failed_transaction(session_factory) -> None:
    with pytest.raises(RuntimeError, match="stop"):
        with session_scope(session_factory) as session:
            session.add(Conversation(title="Rolled back"))
            session.flush()
            raise RuntimeError("stop")

    with session_factory() as session:
        assert session.query(Conversation).filter_by(title="Rolled back").count() == 0


def test_init_db_migrates_task2_sqlite_schema_and_preserves_data(tmp_path) -> None:
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'task2.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE conversations ("
                    "id INTEGER PRIMARY KEY, title VARCHAR(255) NOT NULL, "
                    "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE messages ("
                    "id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, "
                    "role VARCHAR(9) NOT NULL, content TEXT NOT NULL, created_at DATETIME NOT NULL, "
                    "FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversations VALUES "
                    "(1, 'Old conversation', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO messages VALUES "
                    "(1, 1, 'user', 'Preserve me', CURRENT_TIMESTAMP)"
                )
            )

        init_db(engine)
        init_db(engine)
        factory = create_session_factory(engine)
        with factory() as session:
            conversation = session.get(Conversation, 1)
            message = session.get(Message, 1)
            assert conversation is not None
            assert conversation.title == "Old conversation"
            assert conversation.employee_session_id is None
            assert message is not None
            assert message.content == "Preserve me"
            assert message.operation_id is None
            assert message.operation_status is None

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version FROM schema_migrations ORDER BY version")
                ).scalars().all() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
            index_names = {
                row[1] for row in connection.execute(text("PRAGMA index_list('messages')"))
            }
            assert "ix_messages_operation_id" in index_names
    finally:
        engine.dispose()


def test_init_db_migrates_legacy_excel_jobs_and_preserves_rows(tmp_path) -> None:
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'legacy-excel.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE conversations (id INTEGER PRIMARY KEY, title VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
            connection.execute(text("CREATE TABLE excel_jobs (id INTEGER PRIMARY KEY, conversation_id INTEGER, source_filename VARCHAR(255) NOT NULL, status VARCHAR(12) NOT NULL, error TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
            connection.execute(text("CREATE TABLE artifacts (id INTEGER PRIMARY KEY, excel_job_id INTEGER NOT NULL, kind VARCHAR(63) NOT NULL, path TEXT NOT NULL, created_at DATETIME NOT NULL)"))
            connection.execute(text("INSERT INTO excel_jobs VALUES (7, NULL, 'legacy.xlsx', 'failed', 'old error', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
            connection.execute(text("INSERT INTO artifacts VALUES (8, 7, 'legacy', 'legacy/output.xlsx', CURRENT_TIMESTAMP)"))

        init_db(engine)
        init_db(engine)
        with engine.connect() as connection:
            expected_public_id = str(uuid5(NAMESPACE_URL, "etsy-performance-employee:legacy-excel-job:7"))
            assert connection.execute(
                text(
                    "SELECT public_id, source_filename, source_sha256, source_size_bytes, "
                    "status, error_code, progress_percent FROM excel_jobs WHERE id=7"
                )
            ).one() == (
                expected_public_id,
                "legacy.xlsx",
                "legacy-unavailable",
                0,
                "failed",
                "legacy_migrated",
                100,
            )
            assert connection.execute(text("SELECT kind, path FROM artifacts WHERE id=8")).one() == ("legacy", "legacy/output.xlsx")
            assert connection.execute(text("SELECT version FROM schema_migrations ORDER BY version")).scalars().all() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
            assert {row[1] for row in connection.execute(text("PRAGMA table_info('excel_jobs')"))} >= {"public_id", "source_sha256", "source_size_bytes", "error_code", "error_message", "progress_percent"}
            assert {row[1] for row in connection.execute(text("PRAGMA table_info('artifacts')"))} >= {"filename", "sha256", "size_bytes"}
            assert "job_events" in {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            assert connection.execute(
                text("SELECT event_type FROM job_events WHERE excel_job_id=7")
            ).scalar_one() == "failed"
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO excel_jobs "
                        "(public_id, source_filename, source_sha256, source_size_bytes, status, progress_percent, created_at, updated_at) "
                        "VALUES ('bad', '', 'fake', -1, 'not_a_status', 101, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
            for clause in (
                "public_id=NULL",
                "source_filename=''",
                "source_sha256='fake'",
                "source_size_bytes=-1",
                "status='bogus'",
                "progress_percent=101",
            ):
                with pytest.raises(IntegrityError):
                    connection.execute(text(f"UPDATE excel_jobs SET {clause} WHERE id=7"))
    finally:
        engine.dispose()


def test_uuid_contract_upgrade_archives_ids_accepted_by_older_v3(tmp_path) -> None:
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'uuid-upgrade.db'}")
    try:
        init_db(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_excel_jobs_v2_insert"))
            connection.execute(text("DROP TRIGGER trg_excel_jobs_v2_update"))
            connection.execute(text("DELETE FROM schema_migrations WHERE version=4"))
            connection.execute(
                text(
                    "INSERT INTO excel_jobs "
                    "(public_id, source_filename, source_sha256, source_size_bytes, status, "
                    "progress_percent, created_at, updated_at) VALUES "
                    "(:public_id, 'v3.xlsx', :sha, 1, 'completed', 100, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"public_id": "x" * 36, "sha": "c" * 64},
            )
            legacy_id = connection.execute(text("SELECT max(id) FROM excel_jobs")).scalar_one()

        init_db(engine)
        init_db(engine)
        expected = str(uuid5(NAMESPACE_URL, f"etsy-performance-employee:legacy-excel-job:{legacy_id}"))
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT public_id, status, error_code FROM excel_jobs WHERE id=:id"),
                {"id": legacy_id},
            ).one() == (expected, "failed", "legacy_migrated")
            assert connection.execute(
                text("SELECT version FROM schema_migrations ORDER BY version")
                ).scalars().all() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    finally:
        engine.dispose()
