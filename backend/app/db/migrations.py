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


MIGRATIONS: tuple[tuple[int, Migration], ...] = ((1, _task4_chat_columns),)


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
