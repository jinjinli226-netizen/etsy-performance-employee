from __future__ import annotations

import hashlib
import hmac
import os
import stat
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.migration.exporter import ExportError
from app.migration.importer import ImportConflict, ImportValidationError
from app.db.models import AuditEvent, MigrationExport
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/api/migration", tags=["migration"])


def _verify_persisted_export(root: Path, record: MigrationExport) -> bool:
    path = root / record.filename
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size != record.size_bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest() == record.file_sha256
    except OSError:
        return False


def require_migration_capability(
    request: Request, x_migration_capability: str | None = Header(default=None)
) -> None:
    expected = getattr(request.app.state, "migration_capability", "")
    if not x_migration_capability or not expected or not hmac.compare_digest(x_migration_capability, expected):
        raise HTTPException(403, "Local migration capability required")


def _authorize(request: Request, value: str | None) -> None:
    require_migration_capability(request, value)


@router.post("/exports")
def create_export(request: Request, x_migration_capability: str | None = Header(default=None)):
    _authorize(request, x_migration_capability)
    destination = request.app.state.settings.data_dir / "migration-packages" / f"etsy-performance-us-{os.urandom(8).hex()}.zip"
    try:
        result = request.app.state.migration_exporter.export(destination)
    except FileExistsError as error:
        raise HTTPException(409, "Migration package destination conflict") from error
    except OSError as error:
        raise HTTPException(507, "Migration package storage unavailable") from error
    except ExportError as error:
        raise HTTPException(422, str(error)) from error
    try:
        with request.app.state.session_factory.begin() as session:
            existing = session.scalar(select(MigrationExport).where(MigrationExport.package_id == result.package_id))
            if existing is None:
                existing = MigrationExport(package_id=result.package_id, content_sha256=result.content_sha256, filename=result.path.name, file_sha256=result.file_sha256, size_bytes=result.size_bytes)
                session.add(existing)
        if existing.filename != result.path.name:
            if not _verify_persisted_export(destination.parent, existing):
                result.path.unlink(missing_ok=True)
                raise HTTPException(409, "Persisted migration export changed")
            result.path.unlink(missing_ok=True)
    except IntegrityError:
        result.path.unlink(missing_ok=True)
        with request.app.state.session_factory() as session:
            existing = session.scalar(select(MigrationExport).where(MigrationExport.package_id == result.package_id))
        if existing is None:
            raise HTTPException(409, "Concurrent migration export conflict")
    return {"package_id": existing.package_id, "filename": existing.filename, "size_bytes": existing.size_bytes, "file_sha256": existing.file_sha256, "credential_status": "pending"}


@router.get("/exports")
def list_exports(request: Request, x_migration_capability: str | None = Header(default=None)):
    _authorize(request, x_migration_capability)
    with request.app.state.session_factory() as session:
        records = list(session.scalars(select(MigrationExport).order_by(MigrationExport.created_at.desc())))
    return [{"filename": row.filename, "size_bytes": row.size_bytes, "file_sha256": row.file_sha256, "package_id": row.package_id} for row in records]


@router.get("/exports/{filename}")
def download_export(filename: str, request: Request, x_migration_capability: str | None = Header(default=None)):
    _authorize(request, x_migration_capability)
    if Path(filename).name != filename or not filename.casefold().endswith(".zip"):
        raise HTTPException(404, "Migration package not found")
    root = (request.app.state.settings.data_dir / "migration-packages").resolve()
    path = root / filename
    with request.app.state.session_factory() as session:
        record = session.scalar(select(MigrationExport).where(MigrationExport.filename == filename))
    if record is None or path.parent.resolve() != root:
        raise HTTPException(404, "Migration package not found")
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size != record.size_bytes:
            with request.app.state.session_factory.begin() as session:
                session.add(AuditEvent(actor="system", action="migration_export_integrity_failed", entity_type="config", entity_id="unresolved", details={"filename_hash": hashlib.sha256(filename.encode()).hexdigest(), "reason": "metadata"}))
            raise HTTPException(410, "Migration package changed")
        digest = hashlib.sha256()
        with os.fdopen(os.dup(descriptor), "rb") as verifier:
            while chunk := verifier.read(1024 * 1024): digest.update(chunk)
        if digest.hexdigest() != record.file_sha256:
            with request.app.state.session_factory.begin() as session:
                session.add(AuditEvent(actor="system", action="migration_export_integrity_failed", entity_type="config", entity_id="unresolved", details={"filename_hash": hashlib.sha256(filename.encode()).hexdigest(), "reason": "checksum"}))
            raise HTTPException(409, "Migration package checksum changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        def chunks():
            try:
                while body := stream.read(1024 * 1024): yield body
            finally:
                stream.close()
        return StreamingResponse(chunks(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Length": str(record.size_bytes), "X-Content-SHA256": record.file_sha256})
    except FileNotFoundError as error:
        raise HTTPException(404, "Migration package not found") from error
    finally:
        if descriptor is not None: os.close(descriptor)


@router.post("/imports")
async def import_package(
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False),
    x_migration_capability: str | None = Header(default=None),
):
    _authorize(request, x_migration_capability)
    if not file.filename or Path(file.filename).suffix.casefold() != ".zip":
        raise HTTPException(415, "Only .zip migration packages are accepted")
    workspace = request.app.state.settings.data_dir / "migration-workspace"
    descriptor, name = tempfile.mkstemp(prefix="upload-", suffix=".zip", dir=workspace)
    os.close(descriptor)
    temporary = Path(name)
    total = 0
    try:
        with temporary.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > request.app.state.settings.max_migration_package_bytes:
                    raise HTTPException(413, "Migration package is too large")
                stream.write(chunk)
        try:
            report = request.app.state.migration_importer.import_package(temporary, dry_run=dry_run)
        except ImportConflict as error:
            raise HTTPException(409, str(error)) from error
        except ImportValidationError as error:
            raise HTTPException(422, str(error)) from error
        return {
            "package_id": report.package_id,
            "dry_run": report.dry_run,
            "imported": report.imported,
            "credential_status": report.credential_status,
            "record_counts": report.record_counts,
            "fts_rebuild": report.fts_rebuild,
            "conflicts": report.conflicts,
            "ready": not report.conflicts,
        }
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
