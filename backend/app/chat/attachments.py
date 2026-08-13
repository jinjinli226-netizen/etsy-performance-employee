from __future__ import annotations

import json
import os
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from PIL import Image

from app.excel_jobs.storage import StorageError, _validate_xlsx_package


CHUNK_BYTES = 64 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_TYPES = {
    ".png": ("image/png", "PNG"),
    ".jpg": ("image/jpeg", "JPEG"),
    ".jpeg": ("image/jpeg", "JPEG"),
    ".webp": ("image/webp", "WEBP"),
}
TEXT_TYPES = {
    ".txt": {"text/plain"},
    ".csv": {"text/csv", "application/csv"},
    ".json": {"application/json"},
}
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class AttachmentValidationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class StagedAttachment:
    filename: str
    media_type: str
    path: Path
    suffix: str


def _contract(filename: str, media_type: str) -> tuple[str, str | None]:
    suffix = Path(filename).suffix.casefold()
    if suffix in IMAGE_TYPES:
        expected_type, image_format = IMAGE_TYPES[suffix]
        if media_type != expected_type:
            raise AttachmentValidationError("attachment_content_mismatch", "Attachment type does not match its filename.")
        return "image", image_format
    if suffix == ".xlsx":
        if media_type != XLSX_TYPE:
            raise AttachmentValidationError("attachment_content_mismatch", "Attachment type does not match its filename.")
        return "xlsx", None
    if suffix == ".pdf":
        if media_type != "application/pdf":
            raise AttachmentValidationError("attachment_content_mismatch", "Attachment type does not match its filename.")
        return "pdf", None
    if suffix in TEXT_TYPES:
        if media_type not in TEXT_TYPES[suffix]:
            raise AttachmentValidationError("attachment_content_mismatch", "Attachment type does not match its filename.")
        return "text", None
    raise AttachmentValidationError("attachment_unsupported", "This attachment type is not supported.")


def _validate_image(path: Path, expected_format: str) -> None:
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != expected_format or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise AttachmentValidationError("attachment_content_mismatch", "Image content does not match its declared type.")
                image.verify()
        content = path.read_bytes()
        complete = (
            expected_format == "PNG" and content.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
            or expected_format == "JPEG" and content.endswith(b"\xff\xd9")
            or expected_format == "WEBP" and content.startswith(b"RIFF")
            and len(content) >= 12 and int.from_bytes(content[4:8], "little") + 8 == len(content)
        )
        if not complete:
            raise AttachmentValidationError("attachment_content_mismatch", "Image contains trailing or incomplete content.")
    except AttachmentValidationError:
        raise
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AttachmentValidationError("attachment_content_mismatch", "Image content is invalid.") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def _validate_content(path: Path, kind: str, expected_format: str | None) -> None:
    if kind == "image":
        _validate_image(path, expected_format or "")
    elif kind == "xlsx":
        with path.open("rb") as stream:
            signature = stream.read(4)
        if signature != b"PK\x03\x04":
            raise AttachmentValidationError("attachment_content_mismatch", "Workbook content is invalid.")
        try:
            _validate_xlsx_package(path)
        except StorageError as exc:
            raise AttachmentValidationError("attachment_content_mismatch", "Workbook content is invalid.") from exc
    elif kind == "pdf":
        content = path.read_bytes()
        if not content.startswith(b"%PDF-") or not content.rstrip().endswith(b"%%EOF"):
            raise AttachmentValidationError("attachment_content_mismatch", "PDF content is invalid.")
    else:
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentValidationError("attachment_content_mismatch", "Text attachments must be UTF-8.") from exc
        if "\x00" in value or any(ord(char) < 32 and char not in "\t\r\n" for char in value):
            raise AttachmentValidationError("attachment_content_mismatch", "Text attachment contains unsafe control characters.")
        if path.suffix.casefold() == ".json":
            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                raise AttachmentValidationError("attachment_content_mismatch", "JSON attachment is invalid.") from exc


async def stage_upload(upload: UploadFile, *, staging_root: Path, filename: str, max_bytes: int) -> StagedAttachment:
    media_type = (upload.content_type or "application/octet-stream").casefold()
    kind, expected_format = _contract(filename, media_type)
    staging_root.mkdir(parents=True, exist_ok=True)
    path = staging_root / f"{uuid4().hex}{Path(filename).suffix.casefold()}"
    size = 0
    try:
        with path.open("xb") as stream:
            while chunk := await upload.read(CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise AttachmentValidationError("attachment_too_large", "Attachment exceeds the upload limit.")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if not size:
            raise AttachmentValidationError("attachment_content_mismatch", "Attachment is empty.")
        _validate_content(path, kind, expected_format)
        return StagedAttachment(filename, media_type, path, path.suffix)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def cleanup_staging(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
