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


def _task7_controlled_learning(connection: Connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS competitor_evidence ("
            "id INTEGER PRIMARY KEY, public_id VARCHAR(35) NOT NULL UNIQUE, "
            "canonical_url VARCHAR(2048) NOT NULL, source_key VARCHAR(255) NOT NULL, "
            "title VARCHAR(500) NOT NULL, snapshot TEXT NOT NULL, tags JSON NOT NULL, "
            "source_timestamp DATETIME NOT NULL, content_hash VARCHAR(64) NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "CONSTRAINT ck_evidence_public_id CHECK (public_id GLOB 'ev-[0-9a-f][0-9a-f]*' AND length(public_id)=35), "
            "CONSTRAINT ck_evidence_url CHECK (canonical_url LIKE 'https://www.etsy.com/listing/%'), "
            "CONSTRAINT ck_evidence_hash CHECK (length(content_hash)=64 AND content_hash NOT GLOB '*[^0-9a-f]*'))"
        )
    )
    connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_competitor_evidence_public_id ON competitor_evidence(public_id)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_competitor_evidence_source_key ON competitor_evidence(source_key)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_competitor_evidence_content_hash ON competitor_evidence(content_hash)"))
    additions: dict[str, dict[str, str]] = {
        "knowledge_candidates": {
            "public_id": "VARCHAR(35)", "kind": "VARCHAR(127)", "abstract_summary": "TEXT",
            "confidence": "FLOAT", "evidence_ids": "JSON", "source_timestamps": "JSON",
            "conversation_id": "INTEGER", "message_id": "INTEGER", "trace_id": "VARCHAR(127)",
            "revision": "INTEGER NOT NULL DEFAULT 0",
        },
        "knowledge_patterns": {"public_id": "VARCHAR(36)", "kind": "VARCHAR(127)", "abstract_summary": "TEXT", "revision": "INTEGER NOT NULL DEFAULT 0"},
        "rule_versions": {"public_id": "VARCHAR(36)", "sequence": "INTEGER NOT NULL DEFAULT 0"},
        "feedback_events": {"public_id": "VARCHAR(36)", "knowledge_candidate_id": "INTEGER", "feedback_id": "VARCHAR(128)", "row_id": "VARCHAR(128)", "accepted": "BOOLEAN"},
    }
    for table, columns in additions.items():
        existing = _columns(connection, table)
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))

    # Pre-v5 rows did not have public lineage ids or per-pattern sequence numbers.  Fill
    # those values before unique indexes are installed, preserving every row while
    # quarantining legacy active patterns from generation until they are re-approved.
    for row in connection.execute(
        text("SELECT id, public_id FROM knowledge_candidates ORDER BY id")
    ).mappings():
        if not (
            isinstance(row["public_id"], str)
            and re.fullmatch(r"kc-[0-9a-f]{32}", row["public_id"])
        ):
            public_id = "kc-" + uuid5(
                NAMESPACE_URL,
                f"etsy-performance-employee:legacy-knowledge-candidate:{row['id']}",
            ).hex
            connection.execute(
                text("UPDATE knowledge_candidates SET public_id=:public_id WHERE id=:id"),
                {"public_id": public_id, "id": row["id"]},
            )
    for row in connection.execute(
        text("SELECT id, public_id, pattern FROM knowledge_patterns ORDER BY id")
    ).mappings():
        if _valid_uuid(row["public_id"]):
            continue
        public_id = str(
            uuid5(
                NAMESPACE_URL,
                f"etsy-performance-employee:legacy-knowledge-pattern:{row['id']}",
            )
        )
        abstract: str | None = None
        try:
            legacy_payload = json.loads(row["pattern"]) if isinstance(row["pattern"], str) else row["pattern"]
            value = legacy_payload.get("abstract") if isinstance(legacy_payload, dict) else None
            if isinstance(value, str) and value.strip():
                abstract = value.strip()[:2000]
        except (TypeError, ValueError):
            pass
        connection.execute(
            text(
                "UPDATE knowledge_patterns SET public_id=:public_id, "
                "abstract_summary=COALESCE(abstract_summary,:abstract), status='testing' "
                "WHERE id=:id"
            ),
            {"public_id": public_id, "abstract": abstract, "id": row["id"]},
        )
    for pattern_id in connection.execute(
        text("SELECT DISTINCT pattern_id FROM rule_versions ORDER BY pattern_id")
    ).scalars():
        rows = list(
            connection.execute(
                text(
                    "SELECT id, public_id, status FROM rule_versions "
                    "WHERE pattern_id=:pattern_id ORDER BY created_at, id"
                ),
                {"pattern_id": pattern_id},
            ).mappings()
        )
        active_ids = [row["id"] for row in rows if row["status"] == "active"]
        active_id = active_ids[-1] if active_ids else None
        for sequence, row in enumerate(rows, start=1):
            public_id = row["public_id"]
            if not _valid_uuid(public_id):
                public_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"etsy-performance-employee:legacy-rule-version:{row['id']}",
                    )
                )
            status = "rolled_back" if row["id"] in active_ids and row["id"] != active_id else row["status"]
            connection.execute(
                text(
                    "UPDATE rule_versions SET public_id=:public_id, sequence=:sequence, "
                    "status=:status WHERE id=:id"
                ),
                {"public_id": public_id, "sequence": sequence, "status": status, "id": row["id"]},
            )
    for row in connection.execute(
        text("SELECT id, public_id FROM feedback_events ORDER BY id")
    ).mappings():
        if _valid_uuid(row["public_id"]):
            continue
        connection.execute(
            text("UPDATE feedback_events SET public_id=:public_id WHERE id=:id"),
            {
                "public_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        f"etsy-performance-employee:legacy-feedback-event:{row['id']}",
                    )
                ),
                "id": row["id"],
            },
        )
    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_candidates_public_id ON knowledge_candidates(public_id)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_candidates_kind ON knowledge_candidates(kind)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_candidates_trace_id ON knowledge_candidates(trace_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_patterns_public_id ON knowledge_patterns(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_patterns_kind ON knowledge_patterns(kind) WHERE kind IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_rule_versions_public_id ON rule_versions(public_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_rule_versions_pattern_sequence ON rule_versions(pattern_id, sequence)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_rule_versions_one_active ON rule_versions(pattern_id) WHERE status='active'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_feedback_events_public_id ON feedback_events(public_id)",
        "CREATE INDEX IF NOT EXISTS ix_feedback_events_knowledge_candidate_id ON feedback_events(knowledge_candidate_id)",
    ):
        connection.execute(text(statement))
    condition = "(NEW.public_id IS NOT NULL AND NOT (NEW.public_id GLOB 'kc-[0-9a-f][0-9a-f]*' AND length(NEW.public_id)=35)) OR (NEW.confidence IS NOT NULL AND (NEW.confidence < 0 OR NEW.confidence > 1)) OR NEW.revision < 0"
    for operation in ("INSERT", "UPDATE"):
        name = f"trg_knowledge_candidates_v5_{operation.casefold()}"
        connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        connection.execute(text(f"CREATE TRIGGER {name} BEFORE {operation} ON knowledge_candidates WHEN {condition} BEGIN SELECT RAISE(ABORT, 'knowledge candidate v5 contract violation'); END"))
    connection.execute(text("DROP TRIGGER IF EXISTS trg_rule_versions_v5_immutable"))
    connection.execute(
        text(
            "CREATE TRIGGER trg_rule_versions_v5_immutable BEFORE UPDATE ON rule_versions "
            "WHEN NEW.public_id IS NOT OLD.public_id OR NEW.pattern_id != OLD.pattern_id "
            "OR (NEW.knowledge_candidate_id IS NOT OLD.knowledge_candidate_id AND NOT "
            "(NEW.knowledge_candidate_id IS NULL AND OLD.knowledge_candidate_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM knowledge_candidates WHERE id=OLD.knowledge_candidate_id))) "
            "OR NEW.version != OLD.version OR NEW.sequence != OLD.sequence OR NEW.rules != OLD.rules "
            "BEGIN SELECT RAISE(ABORT, 'rule version snapshots are immutable'); END"
        )
    )
    reference_checks = {
        "knowledge_candidates": (
            "(NEW.conversation_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM conversations WHERE id=NEW.conversation_id)) OR "
            "(NEW.message_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM messages WHERE id=NEW.message_id))"
        ),
        "knowledge_patterns": (
            "NEW.source_candidate_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM knowledge_candidates WHERE id=NEW.source_candidate_id)"
        ),
        "rule_versions": (
            "NEW.knowledge_candidate_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM knowledge_candidates WHERE id=NEW.knowledge_candidate_id)"
        ),
        "feedback_events": (
            "NEW.knowledge_candidate_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM knowledge_candidates WHERE id=NEW.knowledge_candidate_id)"
        ),
    }
    for table, check in reference_checks.items():
        for operation in ("INSERT", "UPDATE"):
            name = f"trg_{table}_v5_refs_{operation.casefold()}"
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
            connection.execute(
                text(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                    f"WHEN {check} BEGIN "
                    "SELECT RAISE(ABORT, 'controlled learning reference violation'); END"
                )
            )
    for name, statement in {
        "trg_conversations_v5_candidate_null": (
            "CREATE TRIGGER trg_conversations_v5_candidate_null AFTER DELETE ON conversations "
            "BEGIN UPDATE knowledge_candidates SET conversation_id=NULL WHERE conversation_id=OLD.id; END"
        ),
        "trg_messages_v5_candidate_null": (
            "CREATE TRIGGER trg_messages_v5_candidate_null AFTER DELETE ON messages "
            "BEGIN UPDATE knowledge_candidates SET message_id=NULL WHERE message_id=OLD.id; END"
        ),
        "trg_candidates_v5_refs_cleanup": (
            "CREATE TRIGGER trg_candidates_v5_refs_cleanup AFTER DELETE ON knowledge_candidates "
            "BEGIN UPDATE knowledge_patterns SET source_candidate_id=NULL WHERE source_candidate_id=OLD.id; "
            "UPDATE rule_versions SET knowledge_candidate_id=NULL WHERE knowledge_candidate_id=OLD.id; "
            "DELETE FROM feedback_events WHERE knowledge_candidate_id=OLD.id; END"
        ),
    }.items():
        connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        connection.execute(text(statement))


MIGRATIONS: tuple[tuple[int, Migration], ...] = (
    (1, _task4_chat_columns),
    (2, _task6_excel_job_columns),
    (3, _task6_legacy_policy_and_constraints),
    # Refresh the v3 contract for databases that applied it before canonical UUID checks.
    (4, _task6_legacy_policy_and_constraints),
    (5, _task7_controlled_learning),
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
