from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.chat.schemas import MessageRole
from app.db.models import (
    AuditEvent,
    Attachment,
    Conversation,
    FeedbackEvent,
    KnowledgeCandidate,
    KnowledgePattern,
    Message,
    RuleVersion,
)
from app.knowledge.schemas import KnowledgeStatus
from app.migration.exporter import (
    APP_VERSION,
    ASSET_ALLOWLIST,
    PROFILE_ID,
    SCHEMA_VERSION,
    _scan,
    _sha,
)
from app.knowledge.service import _canonical_hash

MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 128
MAX_RATIO = 200


class ImportValidationError(ValueError):
    pass


class ImportConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportReport:
    package_id: str
    dry_run: bool
    imported: bool
    credential_status: str
    record_counts: dict[str, int]
    fts_rebuild: str


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ImportValidationError("package contains a non-UTC timestamp")
    return parsed.astimezone(UTC)


def _read_jsonl(body: bytes) -> list[dict[str, Any]]:
    try:
        text = body.decode("utf-8")
        if "\r" in text:
            raise ImportValidationError("JSONL must use LF line endings")
        return [json.loads(line) for line in text.splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImportValidationError("invalid UTF-8 JSONL payload") from error


def _safe_name(name: str) -> str:
    if not name or "\0" in name or "\\" in name or name.startswith("/"):
        raise ImportValidationError("unsafe ZIP member path")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts or ":" in pure.parts[0]:
        raise ImportValidationError("unsafe ZIP member path")
    canonical = pure.as_posix()
    if canonical != name:
        raise ImportValidationError("non-canonical ZIP member path")
    return canonical


class MigrationImporter:
    def __init__(self, session_factory: sessionmaker[Session], *, employee_assets: Path, workspace: Path, repository_assets: Path | None = None, guard_path: Path | None = None):
        self.session_factory = session_factory
        self.employee_assets = employee_assets.resolve()
        self.workspace = workspace.resolve()
        self.repository_assets = repository_assets.resolve() if repository_assets else None
        self.guard_path = guard_path.resolve() if guard_path else self.employee_assets / "migration" / "evidence-guard.json"

    def import_package(self, package: Path, *, dry_run: bool = False) -> ImportReport:
        package = package.resolve()
        if package.suffix.casefold() != ".zip" or not package.is_file():
            raise ImportValidationError("migration package must be a .zip file")
        if package.stat().st_size > MAX_PACKAGE_BYTES or package.read_bytes()[:4] != b"PK\x03\x04":
            raise ImportValidationError("invalid or oversized ZIP package")
        manifest, payloads = self._validate_archive(package)
        self._validate_trusted_assets(payloads)
        self._ensure_empty()
        report = ImportReport(
            package_id=manifest["package_id"], dry_run=dry_run, imported=not dry_run,
            credential_status="pending", record_counts=manifest["record_counts"],
            fts_rebuild="not-configured-no-op",
        )
        if dry_run:
            return report

        self.workspace.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="import-", dir=self.workspace))
        published = False
        try:
            os.chmod(stage, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            staged_assets = stage / "employee"
            for relative in ASSET_ALLOWLIST:
                destination = staged_assets / PurePosixPath(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payloads[f"assets/{relative}"])
            guard_records = _read_jsonl(payloads["data/evidence_guard.jsonl"])
            staged_guard = stage / "evidence-guard.json"
            usable_records = []
            for record in guard_records:
                compact = {"id": record["id"], "shingles": record["shingles"]}
                usable_records.append({**compact, "content_sha256": _canonical_hash(compact)})
            export_id = "eg-" + _canonical_hash(usable_records)[:32]
            guard_payload = {"schema_version": 1, "export_id": export_id, "issuer": "local-evidence-guard-v1", "threshold": 0.72, "records": usable_records}
            guard = {**guard_payload, "content_sha256": _canonical_hash(guard_payload)}
            staged_guard.write_text(json.dumps(guard, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8", newline="\n")

            with self.session_factory.begin() as session:
                self._import_records(session, payloads)
                self._import_rules(session, payloads)
                if self.employee_assets.exists():
                    raise ImportConflict("target employee assets already exist")
                self.employee_assets.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_assets, self.employee_assets)
                self.guard_path.parent.mkdir(parents=True, exist_ok=True)
                if self.guard_path.exists():
                    raise ImportConflict("target evidence guard already exists")
                os.replace(staged_guard, self.guard_path)
                published = True
            marker = self.workspace / f"{manifest['package_id']}.imported.json"
            marker.write_text(json.dumps({"package_id": manifest["package_id"], "credential_status": "pending", "fts_rebuild": "not-configured-no-op"}, sort_keys=True), encoding="utf-8")
            return report
        except Exception:
            if published and self.employee_assets.exists():
                shutil.rmtree(self.employee_assets, ignore_errors=True)
            if published:
                self.guard_path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _ensure_empty(self) -> None:
        tables = (Conversation, KnowledgeCandidate, KnowledgePattern, RuleVersion, FeedbackEvent, AuditEvent)
        with self.session_factory() as session:
            if any(session.scalar(select(func.count()).select_from(model)) for model in tables):
                raise ImportConflict("target data is not empty")
        if self.employee_assets.exists():
            raise ImportConflict("target employee assets already exist")
        if self.guard_path.exists():
            raise ImportConflict("target evidence guard already exists")

    def _validate_archive(self, package: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
        try:
            with zipfile.ZipFile(package) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_MEMBERS:
                    raise ImportValidationError("invalid ZIP member count")
                names: list[str] = []
                total = 0
                for info in infos:
                    name = _safe_name(info.filename)
                    if name in names or name.casefold() in {item.casefold() for item in names}:
                        raise ImportValidationError("duplicate or case-colliding ZIP member")
                    names.append(name)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or info.is_dir() or info.flag_bits & 0x1:
                        raise ImportValidationError("unsupported ZIP member type")
                    if name.startswith("assets/") and Path(name).suffix.casefold() not in {".md", ".py"}:
                        raise ImportValidationError("unexpected executable or asset type")
                    total += info.file_size
                    if total > MAX_UNCOMPRESSED_BYTES or (info.compress_size == 0 and info.file_size) or (info.compress_size and info.file_size / info.compress_size > MAX_RATIO):
                        raise ImportValidationError("ZIP expansion limits exceeded")
                if "manifest.json" not in names:
                    raise ImportValidationError("manifest is missing")
                try:
                    manifest = json.loads(archive.read("manifest.json"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ImportValidationError("manifest is invalid") from error
                expected_manifest_keys = {"schema_version", "profile_id", "app_version", "package_id", "created_at", "content_sha256", "credential_status", "raw_competitor_evidence_included", "attachments_included", "fts_rebuild", "record_counts", "files"}
                if set(manifest) != expected_manifest_keys:
                    raise ImportValidationError("manifest fields are incompatible")
                if manifest["schema_version"] != SCHEMA_VERSION or manifest["profile_id"] != PROFILE_ID or manifest["app_version"] != APP_VERSION:
                    raise ImportValidationError("package compatibility check failed")
                if manifest["credential_status"] != "pending" or manifest["raw_competitor_evidence_included"] is not False or manifest["attachments_included"] is not False:
                    raise ImportValidationError("package violates migration privacy policy")
                _parse_time(manifest["created_at"])
                file_entries = manifest["files"]
                if not isinstance(file_entries, list) or any(set(item) != {"path", "sha256", "size", "mode"} for item in file_entries):
                    raise ImportValidationError("invalid manifest file table")
                declared = {item["path"] for item in file_entries}
                if len(declared) != len(file_entries) or declared | {"manifest.json"} != set(names):
                    raise ImportValidationError("manifest/member mismatch")
                required = {f"assets/{name}" for name in ASSET_ALLOWLIST} | {f"data/{name}.jsonl" for name in ("conversations", "messages", "attachments", "knowledge_candidates", "knowledge_patterns", "rule_versions", "feedback_events", "audit_events", "evidence_guard")}
                if declared != required:
                    raise ImportValidationError("package allowlist mismatch")
                payloads = {item["path"]: archive.read(item["path"]) for item in file_entries}
                for item in file_entries:
                    body = payloads[item["path"]]
                    if item["mode"] != "0644" or item["size"] != len(body) or item["sha256"] != _sha(body):
                        raise ImportValidationError("file checksum or size mismatch")
                content_hash = _sha(b"".join(name.encode() + b"\0" + _sha(body).encode() for name, body in sorted(payloads.items())))
                if manifest["content_sha256"] != content_hash or manifest["package_id"] != "pkg-" + content_hash[:32]:
                    raise ImportValidationError("package content identity mismatch")
                all_records: list[Any] = []
                for name, body in payloads.items():
                    if name.endswith(".jsonl"):
                        records = _read_jsonl(body)
                        expected_count = manifest["record_counts"].get(Path(name).stem)
                        if expected_count != len(records):
                            raise ImportValidationError("record count mismatch")
                        all_records.extend(records)
                    else:
                        try:
                            _scan(body.decode("utf-8"))
                        except UnicodeDecodeError as error:
                            raise ImportValidationError("asset must be UTF-8 text") from error
                try:
                    _scan(all_records)
                except ValueError as error:
                    raise ImportValidationError("package contains sensitive or machine-local data") from error
                return manifest, payloads
        except zipfile.BadZipFile as error:
            raise ImportValidationError("invalid ZIP package") from error

    def _import_records(self, session: Session, payloads: dict[str, bytes]) -> None:
        conversations: dict[str, Conversation] = {}
        for record in _read_jsonl(payloads["data/conversations.jsonl"]):
            conversation = Conversation(title=record["title"], employee_session_id=None, created_at=_parse_time(record["created_at"]), updated_at=_parse_time(record["updated_at"]))
            session.add(conversation)
            session.flush()
            conversations[record["id"]] = conversation
        for record in _read_jsonl(payloads["data/messages.jsonl"]):
            parent = conversations.get(record["conversation_id"])
            if parent is None:
                raise ImportValidationError("message relationship is invalid")
            session.add(Message(conversation_id=parent.id, role=MessageRole(record["role"]), content=record["content"], created_at=_parse_time(record["created_at"])))
        for record in _read_jsonl(payloads["data/attachments.jsonl"]):
            parent = conversations.get(record["conversation_id"])
            if parent is None or record.get("content_included") is not False:
                raise ImportValidationError("attachment relationship or policy is invalid")
            session.add(Attachment(conversation_id=parent.id, filename=Path(record["filename"]).name, path=f"migration/attachments/not-included/{record['id']}", media_type=record["media_type"], created_at=_parse_time(record["created_at"])))

        candidates: dict[str, KnowledgeCandidate] = {}
        for record in _read_jsonl(payloads["data/knowledge_candidates.jsonl"]):
            candidate = KnowledgeCandidate(public_id=record["id"], title=record["title"], proposal=record["proposal"], kind=record["kind"], abstract_summary=record["abstract_summary"], confidence=record["confidence"], evidence_ids=record["evidence_ids"], source_timestamps=record["source_timestamps"], revision=record["revision"], status=KnowledgeStatus(record["status"]), created_at=_parse_time(record["created_at"]), updated_at=_parse_time(record["updated_at"]))
            session.add(candidate)
            session.flush()
            candidates[record["id"]] = candidate
        patterns: dict[str, KnowledgePattern] = {}
        for record in _read_jsonl(payloads["data/knowledge_patterns.jsonl"]):
            candidate = candidates.get(record["source_candidate_id"]) if record["source_candidate_id"] else None
            if record["source_candidate_id"] and candidate is None:
                raise ImportValidationError("knowledge lineage is invalid")
            pattern = KnowledgePattern(public_id=record["id"], source_candidate=candidate, name=record["name"], kind=record["kind"], abstract_summary=record["abstract_summary"], revision=record["revision"], pattern=record["pattern"], status=KnowledgeStatus(record["status"]), created_at=_parse_time(record["created_at"]), updated_at=_parse_time(record["updated_at"]))
            session.add(pattern)
            session.flush()
            patterns[record["id"]] = pattern
        self._candidate_map = candidates
        self._pattern_map = patterns

    def _validate_trusted_assets(self, payloads: dict[str, bytes]) -> None:
        if self.repository_assets is None:
            return
        for relative in ASSET_ALLOWLIST:
            local = self.repository_assets / PurePosixPath(relative)
            if local.is_symlink() or not local.is_file() or local.stat().st_nlink != 1:
                raise ImportValidationError("trusted repository asset is missing or unsafe")
            if _sha(local.read_bytes()) != _sha(payloads[f"assets/{relative}"]):
                raise ImportValidationError("employee asset does not match this application version")

    def _import_rules(self, session: Session, payloads: dict[str, bytes]) -> None:
        candidates: dict[str, KnowledgeCandidate] = self._candidate_map
        patterns: dict[str, KnowledgePattern] = self._pattern_map
        for record in _read_jsonl(payloads["data/rule_versions.jsonl"]):
            pattern = patterns.get(record["pattern_id"])
            candidate = candidates.get(record["candidate_id"]) if record["candidate_id"] else None
            if pattern is None or (record["candidate_id"] and candidate is None):
                raise ImportValidationError("rule lineage is invalid")
            session.add(RuleVersion(public_id=record["id"], pattern=pattern, candidate=candidate, version=record["version"], sequence=record["sequence"], rules=record["rules"], status=KnowledgeStatus(record["status"]), created_at=_parse_time(record["created_at"])))
        for record in _read_jsonl(payloads["data/feedback_events.jsonl"]):
            candidate = candidates.get(record["candidate_id"]) if record["candidate_id"] else None
            if record["candidate_id"] and candidate is None:
                raise ImportValidationError("feedback lineage is invalid")
            session.add(FeedbackEvent(public_id=record["id"], knowledge_candidate_id=candidate.id if candidate else None, feedback_id=record["feedback_id"], row_id=record["row_id"], accepted=record["accepted"], event_type=record["event_type"], payload=record["payload"], created_at=_parse_time(record["created_at"])))
        for record in _read_jsonl(payloads["data/audit_events.jsonl"]):
            session.add(AuditEvent(actor=record["actor"], action=record["action"], entity_type=record["entity_type"], entity_id=record["entity_id"], details=record["details"], created_at=_parse_time(record["created_at"])))
