from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    AuditEvent,
    Attachment,
    CompetitorEvidence,
    Conversation,
    FeedbackEvent,
    ExcelJob,
    ImportedEvidenceFingerprint,
    KnowledgeCandidate,
    KnowledgePattern,
    Message,
    RuleVersion,
)
from app.knowledge.originality import OriginalityGuard
from app.knowledge.schemas import KnowledgeStatus
from app.migration.secrets import SensitiveDataError, scan_for_secrets
from app.migration.guard import GuardValidationError, PortableGuard, merge_guards, validate_portable_guard_size

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
MAX_SCAN_DEPTH = 20
MAX_SCAN_NODES = 10_000
MAX_SCAN_CHARS = 1_000_000


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
_ABSOLUTE = re.compile(
    r"(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|(?:^|[\s\"'])/(?:home|Users|var|tmp)/)",
    re.I,
)
_ETSY_LISTING = re.compile(r"https?://(?:www\.)?etsy\.com/listing/[^\s\"'<>]+", re.I)
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\r\n\"'<>]+")
_UNIX_PATH = re.compile(r"(?<![\w:])/(?:home|Users|var|tmp)/[^\r\n\"'<>]+", re.I)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def _evidence_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"[^\W_]+", normalized, re.UNICODE)


def _bounded_strings(value: Any) -> list[str]:
    output: list[str] = []
    nodes = 0
    characters = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes, characters
        nodes += 1
        if depth > MAX_SCAN_DEPTH or nodes > MAX_SCAN_NODES:
            raise ExportError("migration record exceeds scan structure limits")
        if isinstance(item, str):
            characters += len(item)
            if characters > MAX_SCAN_CHARS:
                raise ExportError("migration record exceeds scan text limits")
            output.append(item)
        elif isinstance(item, dict):
            for key in sorted(item):
                visit(item[key], depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)
    return output


def _scan_logical_record(value: Any, *, fingerprints: list[tuple[str, list[str]]] | None = None, threshold: float = .72) -> None:
    _scan(value)
    strings = _bounded_strings(value)
    raw_views = [*strings, "".join(strings), " ".join(strings)]
    for view in raw_views:
        if view:
            _scan(view)
    aggregate = " ".join(" ".join(_evidence_words(item)) for item in strings)
    if fingerprints:
        originality = OriginalityGuard(threshold=threshold)
        for text_value in [*raw_views, aggregate]:
            if text_value and not originality.check_fingerprints([text_value], fingerprints).passed:
                raise ExportError("raw competitor evidence detected in portable payload")


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
                for index, record in enumerate(records):
                    _scan_logical_record(record)
                    if index:
                        _scan_logical_record([records[index - 1], record])
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
                "guard_threshold": min((item["threshold"] for item in payloads["evidence_guard"]), default=.72),
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
            try:
                os.link(publish, destination, follow_symlinks=False)
            except FileExistsError:
                raise ExportError("destination already exists")
            publish.unlink()
            return ExportResult(destination, package_id, content_hash, destination.stat().st_size, counts, _sha_file(destination))
        finally:
            if 'publish' in locals():
                publish.unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)

    def _snapshot(self) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
        engine = self.session_factory.kw.get("bind")
        if engine is not None and engine.dialect.name == "sqlite":
            descriptor, snapshot_name = tempfile.mkstemp(prefix="db-snapshot-", suffix=".sqlite", dir=self.workspace)
            os.close(descriptor)
            snapshot_path = Path(snapshot_name)
            snapshot_engine = None
            try:
                source = engine.raw_connection()
                target = sqlite3.connect(snapshot_path)
                try:
                    source.driver_connection.backup(target)
                finally:
                    target.close()
                    source.close()
                snapshot_engine = create_engine(f"sqlite:///{snapshot_path.as_posix()}")
                return self._snapshot_from_factory(sessionmaker(bind=snapshot_engine, expire_on_commit=False))
            finally:
                if snapshot_engine is not None:
                    snapshot_engine.dispose()
                snapshot_path.unlink(missing_ok=True)
        return self._snapshot_from_factory(self.session_factory)

    def _snapshot_from_factory(self, factory: sessionmaker[Session]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
        with factory() as session:
            conversations = list(session.scalars(select(Conversation).order_by(Conversation.id)))
            conversation_ids = {item.id: _public("conversation", None, f"conversation:{item.id}:{item.created_at.isoformat()}") for item in conversations}
            messages = list(session.scalars(select(Message).order_by(Message.conversation_id, Message.id)))
            message_ids = {item.id: _public("message", None, f"message:{item.id}:{item.created_at.isoformat()}") for item in messages}
            attachments = list(session.scalars(select(Attachment).order_by(Attachment.conversation_id, Attachment.id)))
            jobs = list(session.scalars(select(ExcelJob).order_by(ExcelJob.id)))
            job_ids = {item.id: item.public_id for item in jobs}
            candidates = list(session.scalars(select(KnowledgeCandidate).order_by(KnowledgeCandidate.public_id, KnowledgeCandidate.id)))
            candidate_ids = {item.id: _public("candidate", item.public_id, str(item.id)) for item in candidates}
            patterns = list(session.scalars(select(KnowledgePattern).where(KnowledgePattern.status == KnowledgeStatus.ACTIVE).order_by(KnowledgePattern.public_id, KnowledgePattern.id)))
            pattern_ids = {item.id: _public("pattern", item.public_id, str(item.id)) for item in patterns}
            rules = list(session.scalars(select(RuleVersion).where(RuleVersion.pattern_id.in_(pattern_ids)).order_by(RuleVersion.pattern_id, RuleVersion.sequence, RuleVersion.id))) if pattern_ids else []
            rule_ids = {item.id: _public("rule", item.public_id, str(item.id)) for item in rules}
            feedback = list(session.scalars(select(FeedbackEvent).order_by(FeedbackEvent.public_id, FeedbackEvent.id)))
            audits = list(session.scalars(select(AuditEvent).order_by(AuditEvent.created_at, AuditEvent.id)))
            evidence = list(session.scalars(select(CompetitorEvidence).order_by(CompetitorEvidence.public_id)))
            imported_evidence = list(session.scalars(select(ImportedEvidenceFingerprint).order_by(ImportedEvidenceFingerprint.public_id)))
            evidence_by_url = {item.canonical_url: item.public_id for item in evidence}
            # Scan source values before redaction so credentials cannot be hidden by a later transform.
            try:
                scan_for_secrets([x.content for x in messages])
                scan_for_secrets([x.details for x in audits])
            except SensitiveDataError as error:
                raise ExportError("sensitive data detected in migration export") from error
            raw_texts = [part for item in evidence for part in [item.title, item.snapshot, *(item.tags or [])] if isinstance(part, str) and len(part.strip()) >= 8]
            normalized_portable = " ".join(_evidence_words(json.dumps([x.content for x in messages] + [x.details for x in audits], ensure_ascii=False)))
            for raw in raw_texts:
                normalized = " ".join(_evidence_words(raw))
                if normalized and normalized in normalized_portable:
                    raise ExportError("raw competitor evidence detected in portable payload")

            records: dict[str, list[dict[str, Any]]] = {
                "conversations": [{"id": conversation_ids[x.id], "title": x.title, "created_at": _timestamp(x.created_at), "updated_at": _timestamp(x.updated_at)} for x in conversations],
                "messages": [],
                "attachments": [{"id": _public("attachment", None, f"attachment:{x.id}:{x.created_at.isoformat()}"), "conversation_id": conversation_ids[x.conversation_id], "filename": Path(x.filename).name, "media_type": x.media_type, "content_included": False, "created_at": _timestamp(x.created_at)} for x in attachments],
                "knowledge_candidates": [{"id": candidate_ids[x.id], "title": x.title, "kind": x.kind, "abstract_summary": x.abstract_summary, "proposal": x.proposal, "confidence": x.confidence, "evidence_ids": sorted(x.evidence_ids or []), "source_timestamps": {key: _timestamp(datetime.fromisoformat(value.replace("Z", "+00:00"))) for key, value in (x.source_timestamps or {}).items()}, "conversation_id": conversation_ids.get(x.conversation_id), "message_id": message_ids.get(x.message_id), "trace_id": x.trace_id, "base_active_rule_public_id": x.base_active_rule_public_id, "base_pattern_revision": x.base_pattern_revision, "revision": x.revision, "status": x.status.value, "created_at": _timestamp(x.created_at), "updated_at": _timestamp(x.updated_at)} for x in candidates],
                "knowledge_patterns": [{"id": pattern_ids[x.id], "source_candidate_id": candidate_ids.get(x.source_candidate_id), "name": x.name, "kind": x.kind, "abstract_summary": x.abstract_summary, "revision": x.revision, "pattern": x.pattern, "status": x.status.value, "created_at": _timestamp(x.created_at), "updated_at": _timestamp(x.updated_at)} for x in patterns],
                "rule_versions": [{"id": rule_ids[x.id], "pattern_id": pattern_ids[x.pattern_id], "candidate_id": candidate_ids.get(x.knowledge_candidate_id), "version": x.version, "sequence": x.sequence, "rules": x.rules, "status": x.status.value, "created_at": _timestamp(x.created_at)} for x in rules],
                "feedback_events": [{"id": _public("feedback", x.public_id, str(x.id)), "candidate_id": candidate_ids.get(x.knowledge_candidate_id), "conversation_id": conversation_ids.get(x.conversation_id), "excel_job_id": x.excel_job_ref or job_ids.get(x.excel_job_id), "unresolved_relationships": (["excel_job_external_reference"] if (x.excel_job_ref or x.excel_job_id) else []), "feedback_id": x.feedback_id, "row_id": x.row_id, "accepted": x.accepted, "event_type": x.event_type, "payload": x.payload, "created_at": _timestamp(x.created_at)} for x in feedback],
                "audit_events": [],
                "evidence_guard": [],
            }
            for item in messages:
                bound_ids = set(item.evidence_ids or [])
                if item.evidence_bound:
                    for url in _ETSY_LISTING.findall(item.content):
                        canonical = re.sub(r"(/listing/[0-9]+).*", r"\1", url)
                        if canonical in evidence_by_url:
                            bound_ids.add(evidence_by_url[canonical])
                protected = bool(item.evidence_bound or item.contains_evidence_control)
                if protected and not bound_ids:
                    raise ExportError("evidence-bound message is missing portable provenance")
                records["messages"].append({"id": message_ids[item.id], "conversation_id": conversation_ids[item.conversation_id], "role": item.role.value if hasattr(item.role, "value") else str(item.role), "content": "[evidence content omitted]" if protected else _redact_portable_text(item.content), "evidence_bound": bool(item.evidence_bound), "contains_evidence_control": bool(item.contains_evidence_control), "evidence_ids": sorted(bound_ids), "created_at": _timestamp(item.created_at)})
            typed_maps = {"candidate": candidate_ids, "pattern": pattern_ids, "rule": rule_ids, "conversation": conversation_ids, "message": message_ids, "excel_job": job_ids}
            public_values = {kind: set(mapping.values()) for kind, mapping in typed_maps.items()}
            evidence_values = {item.public_id for item in evidence}
            for item in audits:
                entity_type = item.entity_type if item.entity_type in {"candidate", "pattern", "rule", "conversation", "message", "excel_job", "evidence", "learning", "config"} else "config"
                entity_public_id = str(item.entity_id)
                if entity_type in typed_maps and entity_public_id not in public_values[entity_type]:
                    entity_public_id = typed_maps[entity_type].get(int(entity_public_id)) if entity_public_id.isdigit() else None
                elif entity_type == "evidence" and entity_public_id not in evidence_values:
                    entity_public_id = None
                elif entity_type in {"learning", "config"}:
                    entity_public_id = None
                unresolved = None if entity_public_id else "unresolved_legacy_reference"
                records["audit_events"].append({"id": _public("audit", item.public_id, f"audit:{item.id}:{item.created_at.isoformat()}"), "actor": item.actor, "action": item.action, "entity_type": entity_type, "entity_public_id": entity_public_id, "unresolved_reason": unresolved, "details": item.details, "created_at": _timestamp(item.created_at)})
            guard = OriginalityGuard()
            portable_guards: list[PortableGuard] = []
            for item in evidence:
                portable_guards.append(PortableGuard(item.public_id, tuple(guard.fingerprint_texts([item.title, item.snapshot, *item.tags])), item.source_timestamp, guard.threshold, item.content_hash, item.snapshot_hash))
            portable_guards.extend(PortableGuard(item.public_id, tuple(item.shingles), item.source_timestamp, item.threshold, item.content_hash, item.snapshot_hash) for item in imported_evidence)
            try:
                merged_guards, guard_threshold = merge_guards(portable_guards)
            except GuardValidationError as error:
                raise ExportError(str(error)) from error
            known = {item.public_id for item in merged_guards}
            for candidate in candidates:
                for evidence_id in sorted(candidate.evidence_ids or []):
                    if evidence_id not in known:
                        raise ExportError("referenced evidence guard fingerprint is missing")
            records["evidence_guard"] = [{"id": item.public_id, "source_timestamp": _timestamp(item.source_timestamp) if item.source_timestamp else None, "content_hash": item.content_hash, "snapshot_hash": item.snapshot_hash, "shingles": list(item.shingles), "threshold": guard_threshold} for item in merged_guards]
            try:
                validate_portable_guard_size(records["evidence_guard"], guard_threshold)
            except GuardValidationError as error:
                raise ExportError(str(error)) from error
            evidence_fingerprints = [(item.public_id, list(item.shingles)) for item in merged_guards]
            for name, rows in records.items():
                if name == "evidence_guard":
                    continue
                for index, row in enumerate(rows):
                    _scan_logical_record(row, fingerprints=evidence_fingerprints, threshold=guard_threshold)
                    if index:
                        _scan_logical_record([rows[index - 1], row], fingerprints=evidence_fingerprints, threshold=guard_threshold)
            counts = {name: len(items) for name, items in records.items()}
            return records, counts
