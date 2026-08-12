from __future__ import annotations

from collections.abc import Callable

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


MIGRATIONS: tuple[tuple[int, Migration], ...] = (
    (1, _task4_chat_columns),
    (2, _task6_excel_job_columns),
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
