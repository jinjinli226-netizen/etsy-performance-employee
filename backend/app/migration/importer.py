from __future__ import annotations

import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Attachment, AuditEvent, CompetitorEvidence, Conversation, ExcelJob, FeedbackEvent,
    ImportedEvidenceFingerprint, KnowledgeCandidate, KnowledgePattern, Message,
    MigrationExport, MigrationImport, RuleVersion,
)
from app.migration.contracts import ManifestRecord, RECORD_MODELS
from app.migration.exporter import APP_VERSION, ASSET_ALLOWLIST, PROFILE_ID, SCHEMA_VERSION, _scan, _sha
from app.migration.secrets import SensitiveDataError, scan_for_secrets

MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 128
MAX_RATIO = 200


class ImportValidationError(ValueError): pass
class ImportConflict(RuntimeError): pass


@dataclass(frozen=True)
class ImportReport:
    package_id: str
    dry_run: bool
    imported: bool
    credential_status: str
    record_counts: dict[str, int]
    fts_rebuild: str
    conflicts: list[str]


@dataclass(frozen=True)
class ValidatedGraph:
    manifest: dict[str, Any]
    records: dict[str, list[Any]]


def _read_jsonl(body: bytes) -> list[dict[str, Any]]:
    try:
        text_value = body.decode("utf-8")
        if "\r" in text_value:
            raise ImportValidationError("JSONL must use LF line endings")
        return [json.loads(line) for line in text_value.splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImportValidationError("invalid UTF-8 JSONL payload") from error


def _safe_name(name: str) -> str:
    pure = PurePosixPath(name)
    if not name or "\0" in name or "\\" in name or name.startswith("/") or pure.is_absolute() or ".." in pure.parts or "." in pure.parts or ":" in pure.parts[0] or pure.as_posix() != name:
        raise ImportValidationError("unsafe ZIP member path")
    return name


class MigrationImporter:
    def __init__(self, session_factory: sessionmaker[Session], *, workspace: Path, repository_assets: Path | None = None, **_legacy: Any):
        self.session_factory = session_factory
        self.workspace = workspace.resolve()
        self.repository_assets = repository_assets.resolve() if repository_assets else None

    def import_package(self, package: Path, *, dry_run: bool = False) -> ImportReport:
        package = package.resolve()
        if package.suffix.casefold() != ".zip" or not package.is_file() or package.stat().st_size > MAX_PACKAGE_BYTES:
            raise ImportValidationError("migration package must be a bounded .zip file")
        with package.open("rb") as stream:
            if stream.read(4) != b"PK\x03\x04":
                raise ImportValidationError("invalid ZIP magic")
        manifest, payloads = self._validate_archive(package)
        self._validate_trusted_assets(payloads)
        graph = self._build_graph(manifest, payloads)
        with self.session_factory() as session:
            existing = session.scalar(select(MigrationImport).where(MigrationImport.package_id == manifest["package_id"]))
            if existing is not None:
                if existing.content_sha256 != manifest["content_sha256"]:
                    raise ImportConflict("package identity collision")
                return ImportReport(manifest["package_id"], dry_run, not dry_run, existing.credential_status, manifest["record_counts"], "rebuilt", [])
        conflicts = self._conflicts()
        report = ImportReport(manifest["package_id"], dry_run, False, "pending", manifest["record_counts"], "pending" if conflicts else "ready", conflicts)
        if conflicts:
            if dry_run:
                return report
            raise ImportConflict("target data is not empty")
        if dry_run:
            return report
        self._commit_graph(graph)
        return ImportReport(manifest["package_id"], False, True, "pending", manifest["record_counts"], "rebuilt", [])

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
                    if name.casefold() in {seen.casefold() for seen in names}:
                        raise ImportValidationError("duplicate or case-colliding ZIP member")
                    names.append(name)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or info.is_dir() or info.flag_bits & 1:
                        raise ImportValidationError("unsupported ZIP member type")
                    total += info.file_size
                    if total > MAX_UNCOMPRESSED_BYTES or (info.file_size and not info.compress_size) or (info.compress_size and info.file_size / info.compress_size > MAX_RATIO):
                        raise ImportValidationError("ZIP expansion limits exceeded")
                if "manifest.json" not in names:
                    raise ImportValidationError("manifest is missing")
                manifest = json.loads(archive.read("manifest.json"))
                try:
                    ManifestRecord.model_validate(manifest)
                except ValidationError as error:
                    raise ImportValidationError("invalid migration manifest schema") from error
                manifest_keys = {"schema_version","profile_id","app_version","package_id","created_at","content_sha256","credential_status","raw_competitor_evidence_included","attachments_included","guard_threshold","record_counts","files"}
                if set(manifest) != manifest_keys or manifest["schema_version"] != SCHEMA_VERSION or manifest["profile_id"] != PROFILE_ID or manifest["app_version"] != APP_VERSION:
                    raise ImportValidationError("package compatibility check failed")
                if manifest["credential_status"] != "pending" or manifest["raw_competitor_evidence_included"] is not False or manifest["attachments_included"] is not False or not isinstance(manifest["guard_threshold"], (float, int)) or not .1 <= manifest["guard_threshold"] <= 1:
                    raise ImportValidationError("package privacy or guard policy invalid")
                files = manifest["files"]
                if not isinstance(files, list) or any(set(row) != {"path","sha256","size","mode"} for row in files):
                    raise ImportValidationError("invalid manifest file table")
                declared = [row["path"] for row in files]
                required = {f"assets/{name}" for name in ASSET_ALLOWLIST} | {f"data/{name}.jsonl" for name in RECORD_MODELS}
                if len(set(declared)) != len(declared) or set(declared) != required or set(names) != required | {"manifest.json"}:
                    raise ImportValidationError("package allowlist mismatch")
                payloads = {row["path"]: archive.read(row["path"]) for row in files}
                for row in files:
                    body = payloads[row["path"]]
                    if row["mode"] != "0644" or row["size"] != len(body) or row["sha256"] != _sha(body):
                        raise ImportValidationError("file checksum or size mismatch")
                content = _sha(b"".join(name.encode() + b"\0" + _sha(body).encode() for name, body in sorted(payloads.items())))
                if manifest["content_sha256"] != content or manifest["package_id"] != "pkg-" + content[:32]:
                    raise ImportValidationError("package content identity mismatch")
                for name, body in payloads.items():
                    if name.endswith(".jsonl"):
                        continue
                    try: _scan(body.decode("utf-8"))
                    except UnicodeDecodeError as error: raise ImportValidationError("asset must be UTF-8") from error
                return manifest, payloads
        except (zipfile.BadZipFile, json.JSONDecodeError) as error:
            raise ImportValidationError("invalid ZIP package") from error

    def _validate_trusted_assets(self, payloads: dict[str, bytes]) -> None:
        if self.repository_assets is None:
            raise ImportValidationError("trusted repository asset root is required")
        for relative in ASSET_ALLOWLIST:
            local = self.repository_assets / PurePosixPath(relative)
            if local.is_symlink() or not local.is_file() or local.stat().st_nlink != 1 or _sha(local.read_bytes()) != _sha(payloads[f"assets/{relative}"]):
                raise ImportValidationError("employee asset does not match this application version")

    def _build_graph(self, manifest: dict[str, Any], payloads: dict[str, bytes]) -> ValidatedGraph:
        records: dict[str, list[Any]] = {}
        try:
            for name, model in RECORD_MODELS.items():
                raw = _read_jsonl(payloads[f"data/{name}.jsonl"])
                if manifest["record_counts"].get(name) != len(raw):
                    raise ImportValidationError("record count mismatch")
                scan_for_secrets(raw)
                parsed = [model.model_validate_json(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) for item in raw]
                ids = [item.id for item in parsed]
                if len(ids) != len(set(ids)):
                    raise ImportValidationError(f"duplicate IDs in {name}")
                records[name] = parsed
        except (ValidationError, SensitiveDataError, ValueError) as error:
            if isinstance(error, ImportValidationError): raise
            raise ImportValidationError("record schema or sensitive data validation failed") from error
        conversations = {x.id for x in records["conversations"]}
        messages = {x.id: x for x in records["messages"]}
        candidates = {x.id: x for x in records["knowledge_candidates"]}
        patterns = {x.id: x for x in records["knowledge_patterns"]}
        if any(x.conversation_id not in conversations for x in records["messages"] + records["attachments"]):
            raise ImportValidationError("conversation relationship is invalid")
        if any(x.source_candidate_id and x.source_candidate_id not in candidates for x in records["knowledge_patterns"]):
            raise ImportValidationError("knowledge relationship is invalid")
        if any(x.pattern_id not in patterns or (x.candidate_id and x.candidate_id not in candidates) for x in records["rule_versions"]):
            raise ImportValidationError("rule relationship is invalid")
        for item in candidates.values():
            if item.conversation_id and item.conversation_id not in conversations or item.message_id and item.message_id not in messages:
                raise ImportValidationError("candidate relationship is invalid")
            if set(item.evidence_ids) != set(item.source_timestamps):
                raise ImportValidationError("candidate evidence provenance is invalid")
        guard_ids = {x.id for x in records["evidence_guard"]}
        if any(not set(item.evidence_ids).issubset(guard_ids) for item in candidates.values()):
            raise ImportValidationError("candidate evidence guard relationship is invalid")
        active_by_kind: dict[str, int] = {}
        for pattern in patterns.values():
            if pattern.status.value == "active": active_by_kind[pattern.kind or pattern.name] = active_by_kind.get(pattern.kind or pattern.name, 0) + 1
        if any(count > 1 for count in active_by_kind.values()):
            raise ImportValidationError("multiple active patterns for one kind")
        versions: set[str] = set()
        rules_by_pattern: dict[str, list[Any]] = {}
        for rule in records["rule_versions"]:
            if rule.version in versions: raise ImportValidationError("duplicate rule version")
            versions.add(rule.version)
            rules_by_pattern.setdefault(rule.pattern_id, []).append(rule)
        for pattern_id, pattern in patterns.items():
            rules = rules_by_pattern.get(pattern_id, [])
            sequences = sorted(rule.sequence for rule in rules)
            if len(sequences) != len(set(sequences)) or (sequences and sequences != list(range(sequences[0], sequences[-1] + 1))):
                raise ImportValidationError("rule sequence is not unique and contiguous")
            active_rules = [rule for rule in rules if rule.status.value == "active"]
            if pattern.status.value == "active" and (pattern.source_candidate_id is None or len(active_rules) != 1):
                raise ImportValidationError("active pattern lineage is invalid")
            if pattern.status.value == "active" and candidates[pattern.source_candidate_id].status.value != "active":
                raise ImportValidationError("active pattern source candidate is not active")
            if pattern.status.value != "active" and active_rules:
                raise ImportValidationError("active rule under non-active pattern")
            if active_rules and active_rules[0].candidate_id != pattern.source_candidate_id:
                raise ImportValidationError("active rule candidate lineage is invalid")
        rule_ids = {rule.id for rule in records["rule_versions"]}
        for candidate in candidates.values():
            if candidate.base_active_rule_public_id and candidate.base_active_rule_public_id not in rule_ids:
                raise ImportValidationError("candidate base rule token is invalid")
            if candidate.base_pattern_revision is not None:
                matching = patterns.get(next((pattern.id for pattern in patterns.values() if (pattern.kind or pattern.name) == candidate.kind), ""))
                if matching is None or candidate.base_pattern_revision > matching.revision:
                    raise ImportValidationError("candidate base revision is invalid")
        for feedback in records["feedback_events"]:
            if feedback.candidate_id and feedback.candidate_id not in candidates or feedback.conversation_id and feedback.conversation_id not in conversations:
                raise ImportValidationError("feedback relationship is invalid")
            if feedback.excel_job_id and "excel_job_external_reference" not in feedback.unresolved_relationships:
                raise ImportValidationError("external Excel job reference is unresolved")
        typed_refs = {
            "candidate": set(candidates), "pattern": set(patterns), "rule": rule_ids,
            "conversation": conversations, "message": set(messages), "evidence": guard_ids,
        }
        for audit in records["audit_events"]:
            known = typed_refs.get(audit.entity_type)
            if known is not None and audit.entity_public_id not in known and not audit.unresolved_reason:
                raise ImportValidationError("audit entity relationship is invalid")
            if audit.entity_public_id is None and audit.unresolved_reason is None:
                raise ImportValidationError("audit unresolved relationship needs a reason")
        return ValidatedGraph(manifest, records)

    def _conflicts(self, session: Session | None = None) -> list[str]:
        models = (Conversation, Message, Attachment, ExcelJob, CompetitorEvidence, KnowledgeCandidate, KnowledgePattern, RuleVersion, FeedbackEvent, AuditEvent, ImportedEvidenceFingerprint, MigrationImport, MigrationExport)
        owned = session is None
        session = session or self.session_factory()
        try: return [model.__tablename__ for model in models if session.scalar(select(func.count()).select_from(model))]
        finally:
            if owned: session.close()

    def _commit_graph(self, graph: ValidatedGraph) -> None:
        with self.session_factory.begin() as session:
            if self._conflicts(session): raise ImportConflict("target data changed during import")
            conversation_map: dict[str, Conversation] = {}
            message_map: dict[str, Message] = {}
            for item in graph.records["conversations"]:
                row = Conversation(title=item.title, employee_session_id=None, created_at=item.created_at, updated_at=item.updated_at); session.add(row); session.flush(); conversation_map[item.id] = row
            for item in graph.records["messages"]:
                row = Message(conversation_id=conversation_map[item.conversation_id].id, role=item.role, content=item.content, evidence_bound=item.evidence_bound, contains_evidence_control=item.contains_evidence_control, evidence_ids=item.evidence_ids, created_at=item.created_at); session.add(row); session.flush(); message_map[item.id] = row
            for item in graph.records["attachments"]:
                session.add(Attachment(conversation_id=conversation_map[item.conversation_id].id, filename=Path(item.filename).name, path=f"migration/attachments/not-included/{item.id}", media_type=item.media_type, created_at=item.created_at))
            candidate_map: dict[str, KnowledgeCandidate] = {}
            for item in graph.records["knowledge_candidates"]:
                row = KnowledgeCandidate(public_id=item.id, title=item.title, proposal=item.proposal, kind=item.kind, abstract_summary=item.abstract_summary, confidence=item.confidence, evidence_ids=item.evidence_ids, source_timestamps=item.source_timestamps, conversation_id=conversation_map[item.conversation_id].id if item.conversation_id else None, message_id=message_map[item.message_id].id if item.message_id else None, trace_id=item.trace_id, base_active_rule_public_id=item.base_active_rule_public_id, base_pattern_revision=item.base_pattern_revision, revision=item.revision, status=item.status, created_at=item.created_at, updated_at=item.updated_at); session.add(row); session.flush(); candidate_map[item.id] = row
            pattern_map: dict[str, KnowledgePattern] = {}
            for item in graph.records["knowledge_patterns"]:
                row = KnowledgePattern(public_id=item.id, source_candidate=candidate_map.get(item.source_candidate_id), name=item.name, kind=item.kind, abstract_summary=item.abstract_summary, revision=item.revision, pattern=item.pattern, status=item.status, created_at=item.created_at, updated_at=item.updated_at); session.add(row); session.flush(); pattern_map[item.id] = row
            for item in graph.records["rule_versions"]: session.add(RuleVersion(public_id=item.id, pattern=pattern_map[item.pattern_id], candidate=candidate_map.get(item.candidate_id), version=item.version, sequence=item.sequence, rules=item.rules, status=item.status, created_at=item.created_at))
            for item in graph.records["feedback_events"]: session.add(FeedbackEvent(public_id=item.id, knowledge_candidate_id=candidate_map[item.candidate_id].id if item.candidate_id else None, conversation_id=conversation_map[item.conversation_id].id if item.conversation_id else None, excel_job_ref=item.excel_job_id, feedback_id=item.feedback_id, row_id=item.row_id, accepted=item.accepted, event_type=item.event_type, payload=item.payload, created_at=item.created_at))
            for item in graph.records["audit_events"]: session.add(AuditEvent(public_id=item.id, actor=item.actor, action=item.action, entity_type=item.entity_type, entity_id=item.entity_public_id or "unresolved", details={**item.details, **({"unresolved_reason": item.unresolved_reason} if item.unresolved_reason else {})}, created_at=item.created_at))
            for item in graph.records["evidence_guard"]: session.add(ImportedEvidenceFingerprint(public_id=item.id, shingles=item.shingles, source_timestamp=item.source_timestamp, threshold=item.threshold, content_hash=item.content_hash, snapshot_hash=item.snapshot_hash))
            session.add(MigrationImport(package_id=graph.manifest["package_id"], content_sha256=graph.manifest["content_sha256"], profile_id=PROFILE_ID, credential_status="pending", record_counts=graph.manifest["record_counts"]))
            session.flush()
            self._rebuild_fts(session)

    @staticmethod
    def _rebuild_fts(session: Session) -> None:
        try:
            session.execute(text("DELETE FROM conversation_messages_fts")); session.execute(text("INSERT INTO conversation_messages_fts(content,message_id) SELECT content,id FROM messages"))
            session.execute(text("DELETE FROM knowledge_patterns_fts")); session.execute(text("INSERT INTO knowledge_patterns_fts(abstract_summary,pattern_id) SELECT coalesce(abstract_summary,''),id FROM knowledge_patterns"))
        except Exception as error: raise ImportValidationError("FTS rebuild failed") from error
