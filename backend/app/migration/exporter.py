from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    AuditEvent,
    Attachment,
    CompetitorEvidence,
    Conversation,
    FeedbackEvent,
    KnowledgeCandidate,
    KnowledgePattern,
    Message,
    RuleVersion,
)
from app.knowledge.originality import OriginalityGuard
from app.knowledge.schemas import KnowledgeStatus
from app.migration.secrets import SensitiveDataError, scan_for_secrets

SCHEMA_VERSION = 1
PROFILE_ID = "etsy-performance-us"
APP_VERSION = "0.1.0"
ASSET_ALLOWLIST = (
    "SOUL.md",
    "skills/etsy-performance-listing/SKILL.md",
    "skills/etsy-performance-listing/references/output-contract.md",
    "skills/etsy-performance-listing/scripts/inspect_workbook.py",
    "skills/etsy-performance-listing/scripts/originality_guard.py",
    "skills/etsy-performance-listing/scripts/run_task.py",
    "skills/etsy-performance-listing/scripts/validate_output.py",
    "skills/etsy-performance-listing/scripts/write_workbook.py",
)
MAX_TEXT_FILE = 16 * 1024 * 1024


class ExportError(ValueError):
    pass


@dataclass(frozen=True)
class ExportResult:
    path: Path
    package_id: str
    content_sha256: str
    size_bytes: int
    record_counts: dict[str, int]
    file_sha256: str


_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|password|private[_-]?key|client[_-]?secret)",
    re.I,
)
_CREDENTIAL = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._~-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)", re.I)
_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|(?:^|[\s\"'])/(?:home|Users|var|tmp)/)", re.I)
_ETSY_LISTING = re.compile(r"https?://(?:www\.)?etsy\.com/listing/[^\s\"'<>]+", re.I)
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\r\n\"'<>]+")
_UNIX_PATH = re.compile(r"(?<![\w:])/(?:home|Users|var|tmp)/[^\r\n\"'<>]+", re.I)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ExportError("timestamps must be UTC-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _scan(value: Any, *, key: str = "") -> None:
    try:
        scan_for_secrets(value, key=key)
    except SensitiveDataError as error:
        raise ExportError("sensitive data detected in migration export") from error
    if key and _SENSITIVE_KEY.search(key):
        raise ExportError("sensitive data detected in migration export")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _scan(child, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            _scan(child)
    elif isinstance(value, str):
        if _CREDENTIAL.search(value) or _ABSOLUTE.search(value) or "etsy.com/listing/" in value.casefold():
            raise ExportError("sensitive data detected in migration export")


def _redact_portable_text(value: str) -> str:
    if _ETSY_LISTING.search(value) or re.search(r"\bev-[0-9a-f]{32}\b|LEARNING_MODE:|learning_batch", value, re.I):
        return "[evidence content omitted]"
    value = _ETSY_LISTING.sub("[competitor-link-redacted]", value)
    value = _WINDOWS_PATH.sub("[local-path-redacted]", value)
    return _UNIX_PATH.sub("[local-path-redacted]", value)


def _public(kind: str, value: str | None, fallback: str) -> str:
    if value:
        return value
    return f"legacy-{kind}-" + hashlib.sha256(fallback.encode()).hexdigest()[:24]


class MigrationExporter:
    def __init__(self, session_factory: sessionmaker[Session], *, employee_assets: Path, workspace: Path):
        self.session_factory = session_factory
        self.employee_assets = employee_assets.resolve()
        self.workspace = workspace.resolve()

    def export(self, destination: Path, *, created_at: datetime | None = None) -> ExportResult:
        destination = destination.resolve()
        if destination.suffix.casefold() != ".zip":
            raise ExportError("migration package must use .zip")
        if destination.exists():
            raise ExportError("destination already exists")
        self.workspace.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="stage-", dir=self.workspace))
        try:
            os.chmod(stage, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            payloads, counts = self._snapshot()
            files: dict[str, bytes] = {}
            for relative in ASSET_ALLOWLIST:
                source = self.employee_assets / PurePosixPath(relative)
                if source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1:
                    raise ExportError(f"employee asset is missing or unsafe: {relative}")
                body = source.read_bytes()
                if len(body) > MAX_TEXT_FILE:
                    raise ExportError("employee asset exceeds size limit")
                body.decode("utf-8")
                _scan(body.decode("utf-8"))
                files[f"assets/{relative}"] = body
            for name, records in payloads.items():
                body = b"".join(_json(record) for record in records)
                _scan(records)
                files[f"data/{name}.jsonl"] = body
            content_hash = _sha(b"".join(name.encode() + b"\0" + _sha(body).encode() for name, body in sorted(files.items())))
            package_id = "pkg-" + content_hash[:32]
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "profile_id": PROFILE_ID,
                "app_version": APP_VERSION,
                "package_id": package_id,
                "created_at": _timestamp(created_at or datetime.now(UTC)),
                "content_sha256": content_hash,
                "credential_status": "pending",
                "raw_competitor_evidence_included": False,
                "attachments_included": False,
                "guard_threshold": 0.72,
                "record_counts": counts,
                "files": [
                    {"path": name, "sha256": _sha(body), "size": len(body), "mode": "0644"}
                    for name, body in sorted(files.items())
                ],
            }
            _scan(manifest)
            files["manifest.json"] = _json(manifest)
            archive = stage / "package.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
                for name, body in sorted(files.items()):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    handle.writestr(info, body)
            destination.parent.mkdir(parents=True, exist_ok=True)
            publish = destination.parent / f".publish-{uuid4().hex}.tmp"
            shutil.copyfile(archive, publish)
            with publish.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(publish, destination)
            return ExportResult(destination, package_id, content_hash, destination.stat().st_size, counts, _sha(destination.read_bytes()))
        finally:
            if 'publish' in locals():
                publish.unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)

    def _snapshot(self) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
        with self.session_factory() as session:
            connection = session.connection()
            conversations = list(session.scalars(select(Conversation).order_by(Conversation.id)))
            conversation_ids = {item.id: _public("conversation", None, f"conversation:{item.id}:{item.created_at.isoformat()}") for item in conversations}
            messages = list(session.scalars(select(Message).order_by(Message.conversation_id, Message.id)))
            attachments = list(session.scalars(select(Attachment).order_by(Attachment.conversation_id, Attachment.id)))
            candidates = list(session.scalars(select(KnowledgeCandidate).order_by(KnowledgeCandidate.public_id, KnowledgeCandidate.id)))
            candidate_ids = {item.id: _public("candidate", item.public_id, str(item.id)) for item in candidates}
            patterns = list(session.scalars(select(KnowledgePattern).where(KnowledgePattern.status == KnowledgeStatus.ACTIVE).order_by(KnowledgePattern.public_id, KnowledgePattern.id)))
            pattern_ids = {item.id: _public("pattern", item.public_id, str(item.id)) for item in patterns}
            rules = list(session.scalars(select(RuleVersion).where(RuleVersion.pattern_id.in_(pattern_ids)).order_by(RuleVersion.pattern_id, RuleVersion.sequence, RuleVersion.id))) if pattern_ids else []
            feedback = list(session.scalars(select(FeedbackEvent).where(FeedbackEvent.knowledge_candidate_id.in_(candidate_ids)).order_by(FeedbackEvent.public_id, FeedbackEvent.id))) if candidate_ids else []
            audits = list(session.scalars(select(AuditEvent).order_by(AuditEvent.created_at, AuditEvent.id)))
            evidence = list(session.scalars(select(CompetitorEvidence).order_by(CompetitorEvidence.public_id)))
            # Scan source values before redaction so credentials cannot be hidden by a later transform.
            try:
                scan_for_secrets([x.content for x in messages])
                scan_for_secrets([x.details for x in audits])
            except SensitiveDataError as error:
                raise ExportError("sensitive data detected in migration export") from error
            raw_texts = [part for item in evidence for part in [item.title, item.snapshot, *(item.tags or [])] if isinstance(part, str) and len(part.strip()) >= 8]
            serialized_portable = json.dumps([x.content for x in messages] + [x.details for x in audits], ensure_ascii=False).casefold()
            normalized_portable = " ".join(serialized_portable.split())
            for raw in raw_texts:
                normalized = " ".join(raw.casefold().split())
                if normalized and normalized in normalized_portable:
                    raise ExportError("raw competitor evidence detected in portable payload")

            records: dict[str, list[dict[str, Any]]] = {
                "conversations": [{"id": conversation_ids[x.id], "title": x.title, "created_at": _timestamp(x.created_at), "updated_at": _timestamp(x.updated_at)} for x in conversations],
                "messages": [{"id": _public("message", None, f"message:{x.id}:{x.created_at.isoformat()}"), "conversation_id": conversation_ids[x.conversation_id], "role": x.role.value if hasattr(x.role, "value") else str(x.role), "content": _redact_portable_text(x.content), "created_at": _timestamp(x.created_at)} for x in messages],
                "attachments": [{"id": _public("attachment", None, f"attachment:{x.id}:{x.created_at.isoformat()}"), "conversation_id": conversation_ids[x.conversation_id], "filename": Path(x.filename).name, "media_type": x.media_type, "content_included": False, "created_at": _timestamp(x.created_at)} for x in attachments],
                "knowledge_candidates": [{"id": candidate_ids[x.id], "title": x.title, "kind": x.kind, "abstract_summary": x.abstract_summary, "proposal": x.proposal, "confidence": x.confidence, "evidence_ids": sorted(x.evidence_ids or []), "source_timestamps": x.source_timestamps or {}, "conversation_id": conversation_ids.get(x.conversation_id), "message_id": _public("message", None, f"message:{x.message_id}:{next((m.created_at.isoformat() for m in messages if m.id == x.message_id), '')}") if x.message_id else None, "trace_id": x.trace_id, "base_active_rule_public_id": x.base_active_rule_public_id, "base_pattern_revision": x.base_pattern_revision, "revision": x.revision, "status": x.status.value, "created_at": _timestamp(x.created_at), "updated_at": _timestamp(x.updated_at)} for x in candidates],
                "knowledge_patterns": [{"id": pattern_ids[x.id], "source_candidate_id": candidate_ids.get(x.source_candidate_id), "name": x.name, "kind": x.kind, "abstract_summary": x.abstract_summary, "revision": x.revision, "pattern": x.pattern, "status": x.status.value, "created_at": _timestamp(x.created_at), "updated_at": _timestamp(x.updated_at)} for x in patterns],
                "rule_versions": [{"id": _public("rule", x.public_id, str(x.id)), "pattern_id": pattern_ids[x.pattern_id], "candidate_id": candidate_ids.get(x.knowledge_candidate_id), "version": x.version, "sequence": x.sequence, "rules": x.rules, "status": x.status.value, "created_at": _timestamp(x.created_at)} for x in rules],
                "feedback_events": [{"id": _public("feedback", x.public_id, str(x.id)), "candidate_id": candidate_ids.get(x.knowledge_candidate_id), "conversation_id": conversation_ids.get(x.conversation_id), "excel_job_id": None, "unresolved_relationships": (["excel_job"] if x.excel_job_id else []), "feedback_id": x.feedback_id, "row_id": x.row_id, "accepted": x.accepted, "event_type": x.event_type, "payload": x.payload, "created_at": _timestamp(x.created_at)} for x in feedback],
                "audit_events": [{"id": _public("audit", None, f"audit:{x.id}:{x.created_at.isoformat()}"), "actor": x.actor, "action": x.action, "entity_type": x.entity_type, "entity_id": x.entity_id, "details": x.details, "created_at": _timestamp(x.created_at)} for x in audits],
                "evidence_guard": [],
            }
            guard = OriginalityGuard()
            for item in evidence:
                records["evidence_guard"].append({"id": item.public_id, "source_timestamp": _timestamp(item.source_timestamp), "content_hash": item.content_hash, "snapshot_hash": item.snapshot_hash, "shingles": guard.fingerprint_texts([item.title, item.snapshot, *item.tags]), "threshold": guard.threshold})
            known = {record["id"] for record in records["evidence_guard"]}
            for candidate in candidates:
                for evidence_id in sorted(candidate.evidence_ids or []):
                    if evidence_id not in known:
                        records["evidence_guard"].append({"id": evidence_id, "source_timestamp": (candidate.source_timestamps or {}).get(evidence_id), "content_hash": None, "snapshot_hash": None, "shingles": [], "threshold": guard.threshold})
                        known.add(evidence_id)
            records["evidence_guard"].sort(key=lambda item: item["id"])
            counts = {name: len(items) for name, items in records.items()}
            return records, counts
