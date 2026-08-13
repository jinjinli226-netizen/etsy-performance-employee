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


def _task7_trust_hardening(connection: Connection) -> None:
    evidence_columns = _columns(connection, "competitor_evidence")
    if "snapshot_hash" not in evidence_columns:
        connection.execute(text("ALTER TABLE competitor_evidence ADD COLUMN snapshot_hash VARCHAR(64)"))
    rows = connection.execute(
        text("SELECT id, title, snapshot, tags, snapshot_hash FROM competitor_evidence ORDER BY id")
    ).mappings()
    import hashlib
    import unicodedata

    for row in rows:
        digest = row["snapshot_hash"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            def normalized(value: object) -> object:
                if isinstance(value, str):
                    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
                if isinstance(value, list):
                    return [normalized(item) for item in value]
                return value
            tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"]
            payload = {"title": normalized(row["title"]), "snapshot": normalized(row["snapshot"]), "tags": normalized(tags)}
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
            connection.execute(
                text("UPDATE competitor_evidence SET snapshot_hash=:digest WHERE id=:id"),
                {"digest": digest, "id": row["id"]},
            )
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_competitor_evidence_snapshot_hash ON competitor_evidence(snapshot_hash)"))
    condition = "NEW.snapshot_hash IS NULL OR length(NEW.snapshot_hash)!=64 OR NEW.snapshot_hash GLOB '*[^0-9a-f]*'"
    for operation in ("INSERT", "UPDATE"):
        name = f"trg_competitor_evidence_v6_{operation.casefold()}"
        connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        connection.execute(text(
            f"CREATE TRIGGER {name} BEFORE {operation} ON competitor_evidence WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'evidence snapshot hash violation'); END"
        ))
    for table in ("knowledge_candidates", "knowledge_patterns", "rule_versions"):
        condition = "NEW.status NOT IN ('proposed','testing','active','rejected','rolled_back')"
        for operation in ("INSERT", "UPDATE"):
            name = f"trg_{table}_v6_status_{operation.casefold()}"
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
            connection.execute(text(
                f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT, 'controlled learning status violation'); END"
            ))
    connection.execute(text("DROP TRIGGER IF EXISTS trg_patterns_v6_active_lineage_insert"))
    connection.execute(text("DROP TRIGGER IF EXISTS trg_patterns_v6_active_lineage_update"))
    for operation in ("INSERT", "UPDATE"):
        name = f"trg_patterns_v6_active_lineage_{operation.casefold()}"
        connection.execute(text(
            f"CREATE TRIGGER {name} BEFORE {operation} ON knowledge_patterns "
            "WHEN NEW.status='active' AND NEW.source_candidate_id IS NULL "
            "BEGIN SELECT RAISE(ABORT, 'active pattern lineage violation'); END"
        ))
    connection.execute(text("DROP TRIGGER IF EXISTS trg_rule_versions_v6_active_lineage_insert"))
    connection.execute(text(
        "CREATE TRIGGER trg_rule_versions_v6_active_lineage_insert BEFORE INSERT ON rule_versions "
        "WHEN NEW.status='active' AND (NEW.knowledge_candidate_id IS NULL OR NOT EXISTS "
        "(SELECT 1 FROM knowledge_patterns WHERE id=NEW.pattern_id AND status='active' "
        "AND source_candidate_id=NEW.knowledge_candidate_id)) "
        "BEGIN SELECT RAISE(ABORT, 'active rule lineage violation'); END"
    ))
    # Legacy patterns were quarantined in v5; their legacy rule rows cannot remain active.
    connection.execute(text(
        "UPDATE rule_versions SET status='rolled_back' WHERE status='active' AND pattern_id IN "
        "(SELECT id FROM knowledge_patterns WHERE status!='active')"
    ))


def _task7_candidate_cas_and_capacity(connection: Connection) -> None:
    columns = _columns(connection, "knowledge_candidates")
    if "base_active_rule_public_id" not in columns:
        connection.execute(text("ALTER TABLE knowledge_candidates ADD COLUMN base_active_rule_public_id VARCHAR(36)"))
    if "base_pattern_revision" not in columns:
        connection.execute(text("ALTER TABLE knowledge_candidates ADD COLUMN base_pattern_revision INTEGER"))
    connection.execute(text(
        "UPDATE knowledge_candidates SET "
        "base_active_rule_public_id=(SELECT rv.public_id FROM knowledge_patterns kp "
        "JOIN rule_versions rv ON rv.pattern_id=kp.id AND rv.status='active' "
        "WHERE kp.kind=knowledge_candidates.kind), "
        "base_pattern_revision=(SELECT kp.revision FROM knowledge_patterns kp "
        "WHERE kp.kind=knowledge_candidates.kind) "
        "WHERE status='proposed' AND base_pattern_revision IS NULL"
    ))
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS knowledge_capacity_state ("
        "id INTEGER PRIMARY KEY CHECK (id=1), status VARCHAR(31) NOT NULL, "
        "evidence_count INTEGER NOT NULL, record_limit INTEGER NOT NULL, "
        "oversized_count INTEGER NOT NULL, updated_at DATETIME NOT NULL)"
    ))
    evidence = list(connection.execute(text(
        "SELECT public_id,title,snapshot,tags FROM competitor_evidence ORDER BY public_id"
    )).mappings())
    count = len(evidence)
    oversized = sum(
        len(str(row["snapshot"]).encode("utf-8")) > 20_000
        or len(str(row["title"]).encode("utf-8")) > 500
        for row in evidence
    )
    status = "exceeded" if count > 500 or oversized or _legacy_guard_too_large(evidence) else "ready"
    connection.execute(text(
        "INSERT INTO knowledge_capacity_state "
        "(id,status,evidence_count,record_limit,oversized_count,updated_at) "
        "VALUES (1,:status,:count,500,:oversized,CURRENT_TIMESTAMP) "
        "ON CONFLICT(id) DO UPDATE SET status=excluded.status, evidence_count=excluded.evidence_count, "
        "record_limit=excluded.record_limit, oversized_count=excluded.oversized_count, updated_at=CURRENT_TIMESTAMP"
    ), {"status": status, "count": count, "oversized": oversized})
    condition = (
        "(NEW.base_pattern_revision IS NOT NULL AND NEW.base_pattern_revision < 0) OR "
        "(NEW.base_active_rule_public_id IS NOT NULL AND "
        "(length(NEW.base_active_rule_public_id)!=36 OR NEW.base_active_rule_public_id != lower(NEW.base_active_rule_public_id)))"
    )
    for operation in ("INSERT", "UPDATE"):
        name = f"trg_knowledge_candidates_v7_base_{operation.casefold()}"
        connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        connection.execute(text(
            f"CREATE TRIGGER {name} BEFORE {operation} ON knowledge_candidates WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'knowledge candidate base token violation'); END"
        ))


def _legacy_guard_too_large(evidence: list[dict]) -> bool:
    if not evidence:
        return False
    import hashlib
    from app.knowledge.originality import OriginalityGuard

    def digest(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    guard = OriginalityGuard()
    records = []
    for row in evidence:
        try:
            tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"]
        except (TypeError, ValueError):
            tags = []
        tags = tags if isinstance(tags, list) else []
        shingles = guard.fingerprint_texts([row["title"], row["snapshot"], *tags])
        if len(shingles) > 30_000:
            return True
        record = {"id": row["public_id"], "shingles": shingles}
        records.append({**record, "content_sha256": digest(record)})
    export_id = "eg-" + digest(records)[:32]
    payload = {
        "schema_version": 1, "export_id": export_id, "issuer": "local-evidence-guard-v1",
        "threshold": 0.72, "records": records,
    }
    envelope = {**payload, "content_sha256": digest(payload)}
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return len(encoded) > 8 * 1024 * 1024


def _task8_migration_and_fts(connection: Connection) -> None:
    message_columns = _columns(connection, "messages")
    if "evidence_bound" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN evidence_bound BOOLEAN NOT NULL DEFAULT 0"))
    if "contains_evidence_control" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN contains_evidence_control BOOLEAN NOT NULL DEFAULT 0"))
    if "evidence_ids" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN evidence_ids JSON NOT NULL DEFAULT '[]'"))
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS imported_evidence_fingerprints ("
        "id INTEGER PRIMARY KEY, public_id VARCHAR(35) NOT NULL UNIQUE, shingles JSON NOT NULL, "
        "source_timestamp DATETIME, threshold FLOAT NOT NULL, content_hash VARCHAR(64), "
        "snapshot_hash VARCHAR(64), created_at DATETIME NOT NULL)"
    ))
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS migration_imports ("
        "id INTEGER PRIMARY KEY, package_id VARCHAR(36) NOT NULL UNIQUE, content_sha256 VARCHAR(64) NOT NULL UNIQUE, "
        "profile_id VARCHAR(63) NOT NULL, credential_status VARCHAR(31) NOT NULL, record_counts JSON NOT NULL, created_at DATETIME NOT NULL)"
    ))
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS migration_exports ("
        "id INTEGER PRIMARY KEY, package_id VARCHAR(36) NOT NULL UNIQUE, content_sha256 VARCHAR(64) NOT NULL, "
        "filename VARCHAR(255) NOT NULL UNIQUE, file_sha256 VARCHAR(64) NOT NULL, size_bytes BIGINT NOT NULL, created_at DATETIME NOT NULL)"
    ))
    try:
        connection.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS conversation_messages_fts USING fts5(content, message_id UNINDEXED)"))
        connection.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_patterns_fts USING fts5(abstract_summary, pattern_id UNINDEXED)"))
    except Exception as error:
        raise RuntimeError("SQLite FTS5 support is required") from error


def _task8_portable_lineage(connection: Connection) -> None:
    feedback_columns = _columns(connection, "feedback_events")
    if "excel_job_ref" not in feedback_columns:
        connection.execute(text("ALTER TABLE feedback_events ADD COLUMN excel_job_ref VARCHAR(36)"))
    audit_columns = _columns(connection, "audit_events")
    if "public_id" not in audit_columns:
        connection.execute(text("ALTER TABLE audit_events ADD COLUMN public_id VARCHAR(36)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_audit_events_public_id ON audit_events(public_id)"))
    # Databases which applied the original v8 still need message provenance columns.
    message_columns = _columns(connection, "messages")
    if "evidence_bound" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN evidence_bound BOOLEAN NOT NULL DEFAULT 0"))
    if "contains_evidence_control" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN contains_evidence_control BOOLEAN NOT NULL DEFAULT 0"))
    if "evidence_ids" not in message_columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN evidence_ids JSON NOT NULL DEFAULT '[]'"))


def _task8_guard_threshold_constraints(connection: Connection) -> None:
    condition = "NEW.threshold IS NULL OR NEW.threshold < 0.1 OR NEW.threshold > 1"
    for operation in ("INSERT", "UPDATE"):
        name = f"trg_imported_evidence_threshold_{operation.casefold()}"
        connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        connection.execute(text(
            f"CREATE TRIGGER {name} BEFORE {operation} ON imported_evidence_fingerprints WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'imported evidence threshold violation'); END"
        ))


def _task10_chat_recovery(connection: Connection) -> None:
    columns = _columns(connection, "messages")
    if "attachment_ids" not in columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN attachment_ids JSON NOT NULL DEFAULT '[]'"))
    if "learning_mode" not in columns:
        connection.execute(text("ALTER TABLE messages ADD COLUMN learning_mode BOOLEAN NOT NULL DEFAULT 0"))


def _task10_atomic_attachments(connection: Connection) -> None:
    columns = _columns(connection, "attachments")
    if "claimed_by_message_id" not in columns:
        connection.execute(text("ALTER TABLE attachments ADD COLUMN claimed_by_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_attachments_claimed_by_message_id ON attachments(claimed_by_message_id)"))


_EXCEL_WARNING_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_EXCEL_WARNING_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


def _migration_warning_list(value: object) -> list[str]:
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    total = 0
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 500
            or _EXCEL_WARNING_CONTROL.search(item)
            or _EXCEL_WARNING_URL.search(item)
        ):
            continue
        cleaned = item.strip()
        if cleaned in seen or len(result) >= 40 or total + len(cleaned) > 5_000:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        total += len(cleaned)
    return result


def _task11_excel_warning_aggregate(connection: Connection) -> None:
    if "warning_messages" not in _columns(connection, "excel_jobs"):
        connection.execute(
            text("ALTER TABLE excel_jobs ADD COLUMN warning_messages JSON NOT NULL DEFAULT '[]'")
        )

    rows = connection.execute(
        text(
            "SELECT jobs.id, jobs.warning_messages, events.payload "
            "FROM excel_jobs AS jobs LEFT JOIN job_events AS events "
            "ON events.excel_job_id = jobs.id AND events.event_type = 'worker_row_completed' "
            "ORDER BY jobs.id, events.id"
        ),
        execution_options={"stream_results": True},
    )
    current_id: int | None = None
    merged: list[str] = []

    def persist() -> None:
        if current_id is not None:
            connection.execute(
                text("UPDATE excel_jobs SET warning_messages=:warnings WHERE id=:id"),
                {"id": current_id, "warnings": json.dumps(merged, ensure_ascii=False)},
            )

    for job_id, existing, payload in rows:
        if job_id != current_id:
            persist()
            current_id = job_id
            merged = _migration_warning_list(existing)
        parsed = payload
        if isinstance(parsed, (str, bytes)):
            try:
                parsed = json.loads(parsed)
            except (json.JSONDecodeError, UnicodeDecodeError):
                parsed = None
        incoming = parsed.get("warnings", []) if isinstance(parsed, dict) else []
        merged = _migration_warning_list([*merged, *_migration_warning_list(incoming)])
    persist()


MIGRATIONS: tuple[tuple[int, Migration], ...] = (
    (1, _task4_chat_columns),
    (2, _task6_excel_job_columns),
    (3, _task6_legacy_policy_and_constraints),
    # Refresh the v3 contract for databases that applied it before canonical UUID checks.
    (4, _task6_legacy_policy_and_constraints),
    (5, _task7_controlled_learning),
    (6, _task7_trust_hardening),
    (7, _task7_candidate_cas_and_capacity),
    (8, _task8_migration_and_fts),
    (9, _task8_portable_lineage),
    (10, _task8_guard_threshold_constraints),
    (11, _task10_chat_recovery),
    (12, _task10_atomic_attachments),
    (13, _task11_excel_warning_aggregate),
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
