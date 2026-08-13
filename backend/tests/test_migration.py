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
)
from app.db.session import create_engine_for_url, create_session_factory
from app.knowledge.schemas import KnowledgeStatus
from app.main import create_app
from app.migration.exporter import ExportError, MigrationExporter
from app.migration.importer import ImportConflict, ImportValidationError, MigrationImporter
from app.core.config import Settings


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


def _export(tmp_path: Path) -> tuple[Path, object]:
    engine, factory = _database(tmp_path / "source.db")
    _seed(factory)
    exporter = MigrationExporter(factory, employee_assets=_assets(tmp_path / "repo"), workspace=tmp_path / "migration")
    result = exporter.export(tmp_path / "employee.zip", created_at=datetime(2026, 8, 13, tzinfo=UTC))
    engine.dispose()
    return result.path, result


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
    importer = MigrationImporter(target_factory, employee_assets=tmp_path / "target-assets", repository_assets=tmp_path / "repo" / "employee", guard_path=tmp_path / "trust" / "evidence-guard.json", workspace=tmp_path / "imports")
    report = importer.import_package(package, dry_run=True)
    assert report.package_id == result.package_id
    assert report.credential_status == "pending"
    with target_factory() as session:
        assert session.query(Conversation).count() == 0

    report = importer.import_package(package)
    assert report.imported is True
    with target_factory() as session:
        conversation = session.query(Conversation).one()
        assert "Prefer concise titles" in conversation.messages[0].content
        assert "etsy.com/listing" not in conversation.messages[0].content
        assert conversation.employee_session_id is None
        assert conversation.attachments[0].path.startswith("migration/attachments/not-included/")
        assert session.query(KnowledgePattern).one().source_candidate is not None
        assert session.query(RuleVersion).one().pattern is not None
    guard = json.loads((tmp_path / "trust" / "evidence-guard.json").read_text(encoding="utf-8"))
    assert guard["issuer"] == "local-evidence-guard-v1"
    assert guard["content_sha256"]
    assert guard["records"][0]["id"] == "ev-" + "2" * 32
    assert "url" not in guard["records"][0]
    with pytest.raises(ImportConflict):
        importer.import_package(package)
    engine.dispose()


def test_import_rejects_package_assets_that_do_not_match_local_repository(tmp_path: Path) -> None:
    package, _ = _export(tmp_path)
    (tmp_path / "repo" / "employee" / "SOUL.md").write_text("tampered", encoding="utf-8")
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, employee_assets=tmp_path / "assets", repository_assets=tmp_path / "repo" / "employee", workspace=tmp_path / "imports")
    with pytest.raises(ImportValidationError, match="application version"):
        importer.import_package(package, dry_run=True)


@pytest.mark.parametrize("member", ["../escape.json", "/absolute.json", "C:/drive.json", "DATA/conversations.jsonl"])
def test_import_rejects_unsafe_or_case_colliding_members(tmp_path: Path, member: str) -> None:
    package, _ = _export(tmp_path)
    attacked = tmp_path / "attacked.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(attacked, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info.filename))
        target.writestr(member, b"{}\n")
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, employee_assets=tmp_path / "assets", workspace=tmp_path / "imports")
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
        MigrationImporter(factory, employee_assets=tmp_path / "assets", workspace=tmp_path / "imports").import_package(corrupted, dry_run=True)


def test_import_rolls_back_database_and_assets_on_mid_import_failure(tmp_path: Path, monkeypatch) -> None:
    package, _ = _export(tmp_path)
    _, factory = _database(tmp_path / "target.db")
    importer = MigrationImporter(factory, employee_assets=tmp_path / "assets", workspace=tmp_path / "imports")
    monkeypatch.setattr(importer, "_import_rules", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        importer.import_package(package)
    with factory() as session:
        assert session.query(Conversation).count() == 0
        assert session.query(KnowledgeCandidate).count() == 0
    assert not (tmp_path / "assets").exists()


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
