from __future__ import annotations

import json
import stat
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.init_db import init_db
from app.db.models import (
    AuditEvent,
    Attachment,
    Conversation,
    FeedbackEvent,
    KnowledgeCandidate,
    KnowledgePattern,
    Message,
    RuleVersion,
    ImportedEvidenceFingerprint,
    MigrationImport,
    CompetitorEvidence,
)
from app.db.session import create_engine_for_url, create_session_factory
from app.knowledge.schemas import KnowledgeStatus
from app.knowledge.service import KnowledgeService, KnowledgeValidationError
from app.main import create_app
from app.migration.exporter import ExportError, MigrationExporter
from app.migration.importer import ImportConflict, ImportValidationError, MigrationImporter
from app.migration.secrets import SensitiveDataError, scan_for_secrets
from app.core.config import Settings
from app.migration.capability import _validate_windows_acl_snapshot, create_capability_file, remove_owned_capability_file
from app.migration.contracts import GuardRecord, ManifestRecord, MessageRecord
from pydantic import ValidationError


def _assets(root: Path) -> Path:
    files = {
        "SOUL.md": "Etsy performance employee\n",
        "skills/etsy-performance-listing/SKILL.md": "---\nname: etsy-performance-listing\n---\n",
        "skills/etsy-performance-listing/references/output-contract.md": "five fields\n",
        "skills/etsy-performance-listing/scripts/inspect_workbook.py": "print('inspect')\n",
        "skills/etsy-performance-listing/scripts/originality_guard.py": "print('guard')\n",
        "skills/etsy-performance-listing/scripts/run_task.py": "print('run')\n",
        "skills/etsy-performance-listing/scripts/validate_output.py": "print('validate')\n",
        "skills/etsy-performance-listing/scripts/write_workbook.py": "print('write')\n",
    }
    employee = root / "employee"
    for name, content in files.items():
        path = employee / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return employee


def _database(path: Path):
    engine = create_engine_for_url(f"sqlite:///{path.as_posix()}")
    init_db(engine)
    return engine, create_session_factory(engine)


def _seed(factory) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    with factory.begin() as session:
        conversation = Conversation(title="Training notes", employee_session_id="native-secret-session")
        session.add(conversation)
        session.flush()
        session.add(Message(conversation_id=conversation.id, role="user", content="Prefer concise titles; source https://www.etsy.com/listing/123/example from C:\\Users\\owner\\photo.jpg", created_at=now))
        session.add(Attachment(conversation_id=conversation.id, filename="reference.jpg", path="C:/Users/owner/private/reference.jpg", media_type="image/jpeg", created_at=now))
        candidate = KnowledgeCandidate(
            public_id="kc-" + "1" * 32,
            title="Concise title structure",
            proposal={"pattern": "intent then style"},
            kind="title_structure",
            abstract_summary="Lead with buyer intent, then the visual style.",
            confidence=.91,
            evidence_ids=["ev-" + "2" * 32],
            source_timestamps={"ev-" + "2" * 32: now.isoformat()},
            revision=1,
            status=KnowledgeStatus.ACTIVE,
        )
        pattern = KnowledgePattern(
            public_id=str(uuid4()),
            source_candidate=candidate,
            name="title_structure",
            kind="title_structure",
            abstract_summary=candidate.abstract_summary,
            revision=1,
            pattern={"template": "intent-style"},
            status=KnowledgeStatus.ACTIVE,
        )
        version = RuleVersion(
            public_id=str(uuid4()), pattern=pattern, candidate=candidate,
            version="knowledge-title-v1", sequence=1,
            rules={"title_order": ["intent", "style"]}, status=KnowledgeStatus.ACTIVE,
            created_at=now,
        )
        session.add_all([candidate, pattern, version])
        session.flush()
        session.add(FeedbackEvent(public_id=str(uuid4()), knowledge_candidate_id=candidate.id, feedback_id="edit-1", row_id="row-1", accepted=True, event_type="accepted_edit", payload={"field": "title"}, created_at=now))
        session.add(AuditEvent(actor="owner", action="candidate_activated", entity_type="candidate", entity_id=candidate.public_id, details={"trace_id": "trace-safe"}, created_at=now))
        session.add(CompetitorEvidence(public_id="ev-" + "2" * 32, canonical_url="https://www.etsy.com/listing/123", source_key="etsy-listing:123", title="Crystal dance costume", snapshot="Crystal dance costume with fringe stage sparkle", tags=["dance costume"], source_timestamp=now, content_hash="c" * 64, snapshot_hash="d" * 64, created_at=now))


def _export(tmp_path: Path) -> tuple[Path, object]:
    engine, factory = _database(tmp_path / "source.db")
    _seed(factory)
    exporter = MigrationExporter(factory, employee_assets=_assets(tmp_path / "repo"), workspace=tmp_path / "migration")
    result = exporter.export(tmp_path / "employee.zip", created_at=datetime(2026, 8, 13, tzinfo=UTC))
    engine.dispose()
    return result.path, result


def _rewrite_jsonl(package: Path, destination: Path, member: str, transform) -> Path:
    with zipfile.ZipFile(package) as source:
        bodies = {info.filename: source.read(info.filename) for info in source.infolist()}
    records = [transform(json.loads(line)) for line in bodies[member].decode().splitlines() if line]
    bodies[member] = b"".join((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode() for record in records)
    manifest = json.loads(bodies["manifest.json"])
    for item in manifest["files"]:
        body = bodies[item["path"]]
        item["size"] = len(body)
        item["sha256"] = __import__("hashlib").sha256(body).hexdigest()
    content = __import__("hashlib").sha256(b"".join(name.encode() + b"\0" + __import__("hashlib").sha256(body).hexdigest().encode() for name, body in sorted(bodies.items()) if name != "manifest.json")).hexdigest()
    manifest["content_sha256"] = content
    manifest["package_id"] = "pkg-" + content[:32]
    bodies["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as target:
        for name, body in bodies.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, body)
    return destination


def test_export_is_portable_deterministic_and_excludes_sensitive_evidence(tmp_path: Path) -> None:
    package, first = _export(tmp_path)
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert "assets/SOUL.md" in names
        assert "assets/skills/etsy-performance-listing/scripts/run_task.py" in names
        assert "data/conversations.jsonl" in names
        assert "data/knowledge_patterns.jsonl" in names
        assert "data/evidence_guard.jsonl" in names
        assert not any(".env" in name or "state.db" in name or "session" in name.casefold() for name in names)
        all_text = b"\n".join(archive.read(name) for name in names).decode("utf-8")
        assert "etsy.com/listing" not in all_text
        assert "native-secret-session" not in all_text
        assert "C:/Users" not in all_text and "C:\\Users" not in all_text
        attachments = [json.loads(line) for line in archive.read("data/attachments.jsonl").decode().splitlines()]
        assert attachments[0]["filename"] == "reference.jpg"
        assert attachments[0]["content_included"] is False
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 1
        assert manifest["profile_id"] == "etsy-performance-us"
        assert manifest["credential_status"] == "pending"
        assert manifest["record_counts"]["conversations"] == 1
        assert all(len(item["sha256"]) == 64 for item in manifest["files"])

    second = MigrationExporter(
        create_session_factory(create_engine_for_url(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")),
        employee_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "migration2",
    ).export(tmp_path / "employee-2.zip", created_at=datetime(2026, 8, 13, tzinfo=UTC))
    assert first.content_sha256 == second.content_sha256


def test_export_refuses_secret_or_tampered_repository_asset(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path / "source.db")
    assets = _assets(tmp_path / "repo")
    with factory.begin() as session:
        session.add(AuditEvent(actor="owner", action="saved", entity_type="config", entity_id="1", details={"api_key": "sk-" + "x" * 40}))
    exporter = MigrationExporter(factory, employee_assets=assets, workspace=tmp_path / "migration")
    with pytest.raises(ExportError, match="sensitive"):
        exporter.export(tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()
    assert list((tmp_path / "migration").glob("stage-*")) == []
    engine.dispose()


def test_import_validates_then_restores_relationships_and_guard_only(tmp_path: Path) -> None:
    package, result = _export(tmp_path)
    engine, target_factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(target_factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    report = importer.import_package(package, dry_run=True)
    assert report.package_id == result.package_id
    assert report.credential_status == "pending"
    with target_factory() as session:
        assert session.query(Conversation).count() == 0

    report = importer.import_package(package)
    assert report.imported is True
    with target_factory() as session:
        conversation = session.query(Conversation).one()
        assert conversation.messages[0].content == "[evidence content omitted]"
        assert "etsy.com/listing" not in conversation.messages[0].content
        assert conversation.employee_session_id is None
        assert conversation.attachments[0].path.startswith("migration/attachments/not-included/")
        assert session.query(KnowledgePattern).one().source_candidate is not None
        assert session.query(RuleVersion).one().pattern is not None
        assert session.query(ImportedEvidenceFingerprint).one().public_id == "ev-" + "2" * 32
        assert session.query(MigrationImport).one().credential_status == "pending"
        assert session.execute(__import__("sqlalchemy").text("SELECT count(*) FROM conversation_messages_fts WHERE conversation_messages_fts MATCH 'evidence'")).scalar_one() == 1
        assert session.execute(__import__("sqlalchemy").text("SELECT count(*) FROM knowledge_patterns_fts WHERE knowledge_patterns_fts MATCH 'buyer'")).scalar_one() == 1
    assert not (tmp_path / "target-assets").exists()
    repeated = importer.import_package(package)
    assert repeated.imported is True
    with target_factory() as session:
        assert session.query(MigrationImport).count() == 1
        assert session.query(Conversation).count() == 1
    engine.dispose()


def test_import_rejects_package_assets_that_do_not_match_local_repository(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    (tmp_path / "repo" / "employee" / "SOUL.md").write_text("tampered", encoding="utf-8")
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    with pytest.raises(ImportValidationError, match="application version"):
        importer.import_package(package, dry_run=True)


def test_import_requires_explicit_trusted_repository_root(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    _, factory = _database(tmp_path / "target.db")
    with pytest.raises(ImportValidationError, match="trusted repository"):
        MigrationImporter(factory, workspace=tmp_path / "imports").import_package(package, dry_run=True)


def test_secret_scanner_catches_multiple_credentials_without_hash_false_positives() -> None:
    for value in ("github_pat_" + "a" * 40, "xoxb-123456789-abcdefghijklmnop", "AKIA" + "A" * 16, "AIza" + "A" * 35, "Authorization: Bearer abc.def.ghi", "postgresql://owner:secret@example.test/db", "apikey is " + "Q" * 32, "cookievalue " + "Z" * 30, "session " + "R" * 40):
        with pytest.raises(SensitiveDataError):
            scan_for_secrets({"note": value})
    scan_for_secrets({"sha256": "a" * 64, "id": str(uuid4()), "abstract": "sequence and color balance"})


def test_contracts_reject_json_type_coercion_and_non_zulu_time() -> None:
    valid = {"id": "legacy-message-" + "1" * 24, "conversation_id": "legacy-conversation-" + "2" * 24, "role": "user", "content": "hello", "created_at": "2026-08-13T00:00:00Z", "evidence_bound": False, "contains_evidence_control": False, "evidence_ids": []}
    assert MessageRecord.model_validate_json(json.dumps(valid)).content == "hello"
    for patch in ({"content": 123}, {"created_at": "2026-08-13T08:00:00+08:00"}, {"evidence_bound": 1}):
        with pytest.raises(ValidationError):
            MessageRecord.model_validate_json(json.dumps({**valid, **patch}))


def test_guard_thresholds_are_finite_bounded_and_match_manifest(tmp_path: Path) -> None:
    base_guard = {"id": "ev-" + "1" * 32, "source_timestamp": "2026-08-13T00:00:00Z", "content_hash": None, "snapshot_hash": None, "shingles": [], "threshold": .72}
    for invalid in (0, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            GuardRecord.model_validate({**base_guard, "threshold": invalid})
    manifest = {"schema_version": 1, "profile_id": "etsy-performance-us", "app_version": "0.1.0", "package_id": "pkg-" + "a" * 32, "created_at": "2026-08-13T00:00:00Z", "content_sha256": "a" * 64, "credential_status": "pending", "raw_competitor_evidence_included": False, "attachments_included": False, "guard_threshold": float("nan"), "record_counts": {}, "files": [{"path": "x", "sha256": "a" * 64, "size": 0, "mode": "0644"}]}
    with pytest.raises(ValidationError):
        ManifestRecord.model_validate(manifest)
    package, _ = _export(tmp_path)
    attacked = _rewrite_jsonl(package, tmp_path / "threshold.zip", "data/evidence_guard.jsonl", lambda row: {**row, "threshold": .71})
    _, factory = _database(tmp_path / "threshold-target.db")
    with pytest.raises(ImportValidationError, match="threshold"):
        MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports").import_package(attacked, dry_run=True)


def test_message_evidence_provenance_is_fail_closed(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    attacked = _rewrite_jsonl(package, tmp_path / "message.zip", "data/messages.jsonl", lambda row: {**row, "evidence_bound": True, "content": "raw teaching text", "evidence_ids": []})
    _, factory = _database(tmp_path / "message-target.db")
    with pytest.raises(ImportValidationError, match="provenance"):
        MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports").import_package(attacked, dry_run=True)


def test_imported_fingerprints_feed_runtime_originality_guard(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    importer.import_package(package)
    service = KnowledgeService(factory, export_dir=tmp_path / "trust", originality_threshold=.72)
    trust = service.export_evidence_guard(tmp_path / "guard.json")
    envelope = json.loads(trust.path.read_text(encoding="utf-8"))
    assert envelope["records"][0]["id"] == "ev-" + "2" * 32
    assert envelope["records"][0]["shingles"]


def test_existing_evidence_or_excel_job_is_reported_as_dry_run_conflict(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    _, factory = _database(tmp_path / "target.db")
    with factory.begin() as session:
        from app.db.models import ExcelJob
        from app.excel_jobs.schemas import JobStatus
        session.add(ExcelJob(public_id=str(uuid4()), source_filename="existing.xlsx", source_sha256="a" * 64, source_size_bytes=1, status=JobStatus.QUEUED))
    importer = MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    report = importer.import_package(package, dry_run=True)
    assert report.conflicts == ["excel_jobs"]
    with pytest.raises(ImportConflict):
        importer.import_package(package)


def test_export_keeps_rule_history_and_ancestor_candidate_lineage(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path / "source.db")
    _seed(factory)
    with factory.begin() as session:
        active_pattern = session.query(KnowledgePattern).one()
        ancestor = KnowledgeCandidate(public_id="kc-" + "3" * 32, title="Previous version", proposal={}, kind="title_structure", abstract_summary="Earlier safe abstraction", confidence=.9, evidence_ids=[], source_timestamps={}, revision=0, status=KnowledgeStatus.ROLLED_BACK)
        session.add(ancestor); session.flush()
        session.add(RuleVersion(public_id=str(uuid4()), pattern=active_pattern, candidate=ancestor, version="knowledge-title-v0", sequence=0, rules={"order": "style-first"}, status=KnowledgeStatus.ROLLED_BACK, created_at=datetime.now(UTC)))
    exporter = MigrationExporter(factory, employee_assets=_assets(tmp_path / "repo"), workspace=tmp_path / "migration")
    package = exporter.export(tmp_path / "history.zip").path
    with zipfile.ZipFile(package) as archive:
        candidates = [json.loads(line) for line in archive.read("data/knowledge_candidates.jsonl").decode().splitlines()]
        rules = [json.loads(line) for line in archive.read("data/rule_versions.jsonl").decode().splitlines()]
    assert {item["status"] for item in candidates} == {"active", "rolled_back"}
    assert {item["sequence"] for item in rules} == {0, 1}
    engine.dispose()


def test_export_rejects_raw_competitor_substring_hidden_in_audit(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path / "source.db")
    _seed(factory)
    with factory.begin() as session:
        session.add(AuditEvent(actor="owner", action="note", entity_type="candidate", entity_id="safe", details={"note": "crystal dance costume with fringe stage sparkle"}))
    exporter = MigrationExporter(factory, employee_assets=_assets(tmp_path / "repo"), workspace=tmp_path / "migration")
    with pytest.raises(ExportError, match="raw competitor"):
        exporter.export(tmp_path / "raw.zip")
    engine.dispose()


def test_export_rejects_unicode_punctuation_variant_of_raw_evidence(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path / "source.db")
    _seed(factory)
    with factory.begin() as session:
        session.add(AuditEvent(actor="owner", action="note", entity_type="candidate", entity_id="safe", details={"note": "Ｃrystal\u00a0dance—costume\twith…fringe, stage sparkle"}))
    with pytest.raises(ExportError, match="raw competitor"):
        MigrationExporter(factory, employee_assets=_assets(tmp_path / "repo"), workspace=tmp_path / "migration").export(tmp_path / "raw.zip")
    engine.dispose()


def test_imported_guard_uses_strictest_threshold(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    _, factory = _database(tmp_path / "target.db")
    MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports").import_package(package)
    service = KnowledgeService(factory, export_dir=tmp_path / "trust", originality_threshold=.99)
    envelope = json.loads(service.export_evidence_guard(tmp_path / "guard.json").path.read_text(encoding="utf-8"))
    assert envelope["threshold"] == .72


def test_imported_fingerprint_blocks_similar_workbook_but_allows_original(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    _, factory = _database(tmp_path / "fingerprint-target.db")
    MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports").import_package(package)
    service = KnowledgeService(factory, export_dir=tmp_path / "trust", originality_threshold=.99)
    for name, title, should_fail in (("similar.xlsx", "Crystal dance costume with fringe stage sparkle", True), ("original.xlsx", "Handmade sapphire blue lyrical performance outfit", False)):
        workbook = __import__("openpyxl").Workbook()
        sheet = workbook.active
        sheet.append(["head titles", "SPECIFICATION", "Instructions for buyers"])
        sheet.append([title, "Made to order sizing", "Confirm measurements before purchase"])
        path = tmp_path / name
        workbook.save(path)
        workbook.close()
        if should_fail:
            with pytest.raises(KnowledgeValidationError, match="originality_failed"):
                service.validate_generated_workbook(path)
        else:
            service.validate_generated_workbook(path)


def test_dry_run_rejects_broken_relationship_before_database_write(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    attacked = _rewrite_jsonl(package, tmp_path / "broken.zip", "data/messages.jsonl", lambda row: {**row, "conversation_id": "missing-conversation"})
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    with pytest.raises(ImportValidationError, match="schema|relationship"):
        importer.import_package(attacked, dry_run=True)
    with factory() as session:
        assert session.query(Conversation).count() == 0


@pytest.mark.parametrize("member", ["../escape.json", "/absolute.json", "C:/drive.json", "DATA/conversations.jsonl"])
def test_import_rejects_unsafe_or_case_colliding_members(tmp_path: Path, member: str) -> None:
    package, _ = _export(tmp_path)
    attacked = tmp_path / "attacked.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(attacked, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info.filename))
        target.writestr(member, b"{}\n")
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    with pytest.raises(ImportValidationError):
        importer.import_package(attacked, dry_run=True)
    assert not (tmp_path / "escape.json").exists()


def test_import_rejects_checksum_corruption_and_symlink_entries(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    corrupted = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(corrupted, "w") as target:
        for info in source.infolist():
            body = source.read(info.filename)
            if info.filename == "data/messages.jsonl":
                body += b"{}\n"
            target.writestr(info, body)
        link = zipfile.ZipInfo("assets/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(link, "target")
    _, factory = _database(tmp_path / "target.db")
    with pytest.raises(ImportValidationError):
        MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports").import_package(corrupted, dry_run=True)


def test_import_rolls_back_database_and_assets_on_mid_import_failure(tmp_path: Path, monkeypatch) -> None:
    package, _ = _export(tmp_path)
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    monkeypatch.setattr(importer, "_commit_graph", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        importer.import_package(package)
    with factory() as session:
        assert session.query(Conversation).count() == 0
        assert session.query(KnowledgeCandidate).count() == 0
        assert session.query(MigrationImport).count() == 0


def test_migration_api_requires_capability_and_supports_dry_run(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    settings = Settings(data_dir=tmp_path / "runtime", database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        app.state.migration_importer.repository_assets = tmp_path / "repo" / "employee"
        denied = client.post("/api/migration/imports?dry_run=true", files={"file": ("employee.zip", package.read_bytes(), "application/zip")})
        assert denied.status_code == 403
        token = app.state.migration_capability
        accepted = client.post(
            "/api/migration/imports?dry_run=true",
            headers={"X-Migration-Capability": token},
            files={"file": ("employee.zip", package.read_bytes(), "application/zip")},
        )
        assert accepted.status_code == 200
        assert accepted.json()["dry_run"] is True


def test_package_script_does_not_accept_secrets_on_argv() -> None:
    script = (Path(__file__).parents[2] / "scripts" / "package-employee.ps1").read_text(encoding="utf-8")
    lowered = script.casefold()
    assert "api_key" not in lowered and "password" not in lowered
    assert "invoke-expression" not in lowered and "start-process" not in lowered
    assert "x-migration-capability" in lowered
    assert "test-path" in lowered
    assert "getenvironmentvariable" not in lowered
    assert "migration-capability" in lowered
    assert "move-item" in lowered


def test_capability_file_is_random_private_and_owner_cleanup_is_bounded(tmp_path: Path) -> None:
    first = create_capability_file(tmp_path)
    token = first.path.read_text(encoding="ascii")
    assert token == first.token and len(token) >= 32
    assert first.path.name == "migration-capability"
    first.path.write_text("replaced", encoding="ascii")
    remove_owned_capability_file(first)
    assert first.path.exists()
    first.path.write_text(token, encoding="ascii")
    remove_owned_capability_file(first)
    assert not first.path.exists()


def test_windows_capability_acl_rejects_unexpected_allow_rules() -> None:
    sid = "S-1-5-21-123"
    _validate_windows_acl_snapshot({"protected": True, "allows": [sid, "S-1-5-18"]}, sid)
    with pytest.raises(RuntimeError, match="ACL"):
        _validate_windows_acl_snapshot({"protected": True, "allows": [sid, "S-1-1-0"]}, sid)
    script = (Path(__file__).parents[2] / "scripts" / "package-employee.ps1").read_text(encoding="utf-8")
    assert "unexpectedAllows" in script and "AreAccessRulesProtected" in script


def test_export_api_persists_manifest_hash_and_refuses_symlink_download(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "runtime", database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        app.state.migration_exporter.employee_assets = _assets(tmp_path / "repo")
        token = app.state.migration_capability
        created = client.post("/api/migration/exports", headers={"X-Migration-Capability": token})
        assert created.status_code == 200
        payload = created.json()
        assert len(payload["file_sha256"]) == 64
        downloaded = client.get(f"/api/migration/exports/{payload['filename']}", headers={"X-Migration-Capability": token})
        assert downloaded.status_code == 200
        assert downloaded.headers["X-Content-SHA256"] == payload["file_sha256"]
        exported = settings.data_dir / "migration-packages" / payload["filename"]
        exported.unlink()
        try:
            exported.symlink_to(Path(__file__))
        except OSError:
            pytest.skip("symlinks unavailable")
        assert client.get(f"/api/migration/exports/{payload['filename']}", headers={"X-Migration-Capability": token}).status_code == 404


def test_export_api_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "runtime", database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        app.state.migration_exporter.employee_assets = _assets(tmp_path / "repo")
        headers = {"X-Migration-Capability": app.state.migration_capability}
        first = client.post("/api/migration/exports", headers=headers)
        second = client.post("/api/migration/exports", headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.json()["package_id"] == second.json()["package_id"]
        assert first.json()["filename"] == second.json()["filename"]
        with app.state.session_factory() as session:
            from app.db.models import MigrationExport
            assert session.query(MigrationExport).count() == 1
