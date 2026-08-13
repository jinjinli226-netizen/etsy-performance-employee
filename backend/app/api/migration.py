from __future__ import annotations

import hmac
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.migration.exporter import ExportError
from app.migration.importer import ImportConflict, ImportValidationError

router = APIRouter(prefix="/api/migration", tags=["migration"])


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
    destination = request.app.state.settings.data_dir / "migration-packages" / "etsy-performance-us.zip"
    if destination.exists():
        destination = destination.with_name(f"etsy-performance-us-{os.urandom(8).hex()}.zip")
    try:
        result = request.app.state.migration_exporter.export(destination)
    except ExportError as error:
        raise HTTPException(422, str(error)) from error
    return {"package_id": result.package_id, "filename": result.path.name, "size_bytes": result.size_bytes, "credential_status": "pending"}


@router.get("/exports")
def list_exports(request: Request, x_migration_capability: str | None = Header(default=None)):
    _authorize(request, x_migration_capability)
    root = request.app.state.settings.data_dir / "migration-packages"
    return [{"filename": path.name, "size_bytes": path.stat().st_size} for path in sorted(root.glob("*.zip"), key=lambda item: item.stat().st_mtime_ns, reverse=True)]


@router.get("/exports/{filename}")
def download_export(filename: str, request: Request, x_migration_capability: str | None = Header(default=None)):
    _authorize(request, x_migration_capability)
    if Path(filename).name != filename or not filename.casefold().endswith(".zip"):
        raise HTTPException(404, "Migration package not found")
    path = request.app.state.settings.data_dir / "migration-packages" / filename
    if not path.is_file():
        raise HTTPException(404, "Migration package not found")
    return FileResponse(path, filename=filename, media_type="application/zip")


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
        }
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
