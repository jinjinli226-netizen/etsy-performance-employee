from __future__ import annotations

import json
import re
from collections.abc import Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Connection, Engine, text

Migration = Callable[[Connection], None]


def _columns(connection: Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(text(f"PRAGMA table_info('{table}')"))}


def _task4_chat_columns(connection: Connection) -> None:
    conversation_columns = _columns(connection, "conversations")
    if "employee_session_id" not in conversation_columns:
        connection.execute(
            text("ALTER TABLE conversations ADD COLUMN employee_session_id VARCHAR(255)")
        )

    message_columns = _columns(connection, "messages")
    if "operation_id" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN operation_id VARCHAR(36)"))
    if "operation_status" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN operation_status VARCHAR(31)"))
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_messages_operation_id ON messages (operation_id)")
    )


def _task6_excel_job_columns(connection: Connection) -> None:
    job_columns = _columns(connection, "excel_jobs")
    additions = {
        "public_id": "VARCHAR(36)",
        "source_sha256": "VARCHAR(64)",
        "source_size_bytes": "BIGINT",
        "error_code": "VARCHAR(63)",
        "error_message": "VARCHAR(255)",
        "progress_percent": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in job_columns:
            connection.execute(text(f"ALTER TABLE excel_jobs ADD COLUMN {name} {definition}"))
    connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_excel_jobs_public_id ON excel_jobs (public_id)"))

    artifact_columns = _columns(connection, "artifacts")
    for name, definition in {
        "filename": "VARCHAR(255)",
        "sha256": "VARCHAR(64)",
        "size_bytes": "BIGINT",
    }.items():
        if name not in artifact_columns:
            connection.execute(text(f"ALTER TABLE artifacts ADD COLUMN {name} {definition}"))

    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS job_events ("
            "id INTEGER PRIMARY KEY, excel_job_id INTEGER NOT NULL, "
            "event_type VARCHAR(63) NOT NULL, payload JSON NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "FOREIGN KEY(excel_job_id) REFERENCES excel_jobs(id) ON DELETE CASCADE)"
        )
    )
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_job_events_excel_job_id ON job_events (excel_job_id)"))


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JOB_STATUSES = "'queued','running','completed','failed','cancelled','needs_review'"


def _valid_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _task6_legacy_policy_and_constraints(connection: Connection) -> None:
    """Archive unverifiable pre-Task6 jobs and enforce the v2 job contract.

    SQLite cannot add NOT NULL/CHECK constraints in place without rebuilding a referenced
    parent table.  These BEFORE triggers provide equivalent insert/update enforcement while
    retaining legacy ids and every artifacts/events/feedback foreign-key relationship.
    """
    rows = connection.execute(
        text(
            "SELECT id, public_id, source_filename, source_sha256, source_size_bytes, "
            "status, progress_percent FROM excel_jobs ORDER BY id"
        )
    ).mappings()
    for row in rows:
        public_id = row["public_id"]
        digest = row["source_sha256"]
        source_size = row["source_size_bytes"]
        source_filename = row["source_filename"]
        progress = row["progress_percent"]
        unverifiable = not (
            _valid_uuid(public_id)
            and isinstance(digest, str)
            and _SHA256.fullmatch(digest)
            and isinstance(source_size, int)
            and source_size >= 0
            and isinstance(source_filename, str)
            and bool(source_filename.strip())
            and isinstance(progress, int)
            and 0 <= progress <= 100
        )
        if not unverifiable:
            continue
        migrated_public_id = (
            public_id
            if _valid_uuid(public_id)
            else str(uuid5(NAMESPACE_URL, f"etsy-performance-employee:legacy-excel-job:{row['id']}"))
        )
        filename = source_filename.strip() if isinstance(source_filename, str) and source_filename.strip() else f"legacy-{row['id']}.xlsx"
        message = "This legacy job was archived because its source integrity cannot be verified."
        connection.execute(
            text(
                "UPDATE excel_jobs SET public_id=:public_id, source_filename=:filename, "
                "source_sha256='legacy-unavailable', source_size_bytes=0, status='failed', "
                "error=:message, error_code='legacy_migrated', error_message=:message, "
                "progress_percent=100 WHERE id=:id"
            ),
            {"public_id": migrated_public_id, "filename": filename[:255], "message": message, "id": row["id"]},
        )
        already_has_event = connection.execute(
            text(
                "SELECT 1 FROM job_events WHERE excel_job_id=:id AND event_type='failed' "
                "AND json_extract(payload, '$.error.code')='legacy_migrated' LIMIT 1"
            ),
            {"id": row["id"]},
        ).first()
        if already_has_event is None:
            connection.execute(
                text(
                    "INSERT INTO job_events (excel_job_id, event_type, payload, created_at) "
                    "VALUES (:id, 'failed', :payload, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": row["id"],
                    "payload": json.dumps(
                        {"status": "failed", "error": {"code": "legacy_migrated", "message": message}},
                        separators=(",", ":"),
                    ),
                },
            )

    connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_excel_jobs_public_id ON excel_jobs (public_id)"))
    condition = (
        "NEW.public_id IS NULL OR length(NEW.public_id) != 36 "
        "OR NEW.public_id != lower(NEW.public_id) "
        "OR substr(NEW.public_id, 9, 1) != '-' "
        "OR substr(NEW.public_id, 14, 1) != '-' "
        "OR substr(NEW.public_id, 19, 1) != '-' "
        "OR substr(NEW.public_id, 24, 1) != '-' "
        "OR length(replace(NEW.public_id, '-', '')) != 32 "
        "OR replace(NEW.public_id, '-', '') GLOB '*[^0-9a-f]*' "
        "OR NEW.source_filename IS NULL OR length(trim(NEW.source_filename)) = 0 "
        "OR NEW.source_sha256 IS NULL OR NOT ("
        "NEW.source_sha256 = 'legacy-unavailable' OR (length(NEW.source_sha256) = 64 "
        "AND NEW.source_sha256 NOT GLOB '*[^0-9a-f]*')) "
        "OR NEW.source_size_bytes IS NULL OR NEW.source_size_bytes < 0 "
        f"OR NEW.status NOT IN ({_JOB_STATUSES}) "
        "OR NEW.progress_percent IS NULL OR NEW.progress_percent < 0 OR NEW.progress_percent > 100 "
        "OR (NEW.source_sha256 = 'legacy-unavailable' AND "
        "(NEW.status != 'failed' OR NEW.error_code != 'legacy_migrated'))"
    )
    for operation in ("INSERT", "UPDATE"):
        trigger = f"trg_excel_jobs_v2_{operation.casefold()}"
        connection.execute(text(f"DROP TRIGGER IF EXISTS {trigger}"))
        connection.execute(
            text(
                f"CREATE TRIGGER {trigger} BEFORE {operation} ON excel_jobs "
                f"WHEN {condition} BEGIN "
                "SELECT RAISE(ABORT, 'excel_jobs v2 contract violation'); END"
            )
        )


MIGRATIONS: tuple[tuple[int, Migration], ...] = (
    (1, _task4_chat_columns),
    (2, _task6_excel_job_columns),
    (3, _task6_legacy_policy_and_constraints),
    # Refresh the v3 contract for databases that applied it before canonical UUID checks.
    (4, _task6_legacy_policy_and_constraints),
)


def run_sqlite_migrations(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        applied = set(
            connection.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
        for version, migration in MIGRATIONS:
            if version in applied:
                continue
            migration(connection)
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
