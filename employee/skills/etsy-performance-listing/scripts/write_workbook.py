from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter


HEADER_TO_KEY = {
    "head titles": "head_titles",
    "13 tags": "tags",
    "SPECIFICATION": "specification",
    "Category": "category",
    "Instructions for buyers": "instructions_for_buyers",
}


class WorkbookWriteError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self), "details": self.details}}


def _load_sibling(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"etsy_excel_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_excel_string(value: Any) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        raise WorkbookWriteError("unsafe_output_value", "An output value begins with an Excel formula prefix.")
    return text


def _validate_manifest_mapping(workbook, manifest: dict[str, Any]) -> tuple[Any, dict[str, int]]:
    inspect = _load_sibling("inspect_workbook")
    sheet = manifest.get("sheet")
    if not isinstance(sheet, str) or sheet not in workbook.sheetnames:
        raise WorkbookWriteError("manifest_mismatch", "The manifest sheet is not present in the copied workbook.")
    ws = workbook[sheet]
    header_row = manifest.get("header_row")
    mapping = manifest.get("output_columns")
    if not isinstance(header_row, int) or not isinstance(mapping, dict) or set(mapping) != set(HEADER_TO_KEY):
        raise WorkbookWriteError("manifest_mismatch", "The manifest output mapping is invalid.")
    columns: dict[str, int] = {}
    for header, letter in mapping.items():
        try:
            column = column_index_from_string(letter)
        except (TypeError, ValueError) as exc:
            raise WorkbookWriteError("manifest_mismatch", "The manifest contains an invalid output column.") from exc
        if inspect.normalize_header(ws.cell(header_row, column).value) != header:
            raise WorkbookWriteError("manifest_mismatch", "The copied workbook no longer matches the manifest output mapping.")
        if inspect._merged_range_for(ws, header_row, column):
            raise WorkbookWriteError("manifest_mismatch", "An output header became merged.")
        columns[header] = column
    return ws, columns


def write_workbook(
    source_path: str | Path,
    output_dir: str | Path,
    manifest: dict[str, Any],
    row_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source = Path(source_path).resolve(strict=True)
    destination_dir = Path(output_dir).resolve()
    if source.suffix.casefold() != ".xlsx":
        raise WorkbookWriteError("unsupported_workbook_type", "Only .xlsx workbooks are supported.")
    if _sha256(source) != manifest.get("source_sha256"):
        raise WorkbookWriteError("source_hash_mismatch", "The source workbook changed after inspection.")
    destination_dir.mkdir(parents=True, exist_ok=True)
    final = destination_dir / f"{source.stem}-generated-{manifest['source_sha256'][:12]}.xlsx"
    if final.exists():
        raise WorkbookWriteError("output_exists", "The generated batch workbook already exists.")
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise WorkbookWriteError("manifest_mismatch", "The manifest row list is invalid.")
    manifest_rows = {item.get("row_id"): item for item in rows if isinstance(item, dict)}
    if not set(row_results).issubset(manifest_rows):
        raise WorkbookWriteError("unknown_row", "A generated result does not belong to this manifest.")
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".listing-", suffix=".xlsx", dir=destination_dir)
        os.close(fd)
        temp_path = Path(temp_name)
        shutil.copyfile(source, temp_path)
        workbook = load_workbook(temp_path, data_only=False, keep_links=True, read_only=False)
        ws, columns = _validate_manifest_mapping(workbook, manifest)
        changed: list[str] = []
        for row_id in sorted(row_results):
            row_number = manifest_rows[row_id].get("row_number")
            if not isinstance(row_number, int) or row_number <= manifest["header_row"]:
                raise WorkbookWriteError("manifest_mismatch", "The manifest contains an invalid product row.")
            result = row_results[row_id]
            for header, key in HEADER_TO_KEY.items():
                if key not in result:
                    raise WorkbookWriteError("invalid_result", f"The generated result is missing {key}.")
                cell = ws.cell(row_number, columns[header])
                cell.value = _safe_excel_string(result[key])
                cell.data_type = "s"
                changed.append(f"{ws.title}!{get_column_letter(columns[header])}{row_number}")
        workbook.save(temp_path)
        reopened = load_workbook(temp_path, data_only=False, keep_links=True, read_only=False)
        _validate_manifest_mapping(reopened, manifest)
        if _sha256(source) != manifest.get("source_sha256"):
            raise WorkbookWriteError("source_hash_mismatch", "The source workbook changed during writing.")
        os.replace(temp_path, final)
        temp_path = None
        return {"output_path": str(final), "output_sha256": _sha256(final), "changed_cells": sorted(changed)}
    except WorkbookWriteError:
        raise
    except Exception as exc:
        raise WorkbookWriteError("workbook_write_failed", "The output workbook could not be written safely.") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output_dir")
    parser.add_argument("manifest")
    parser.add_argument("results")
    args = parser.parse_args()
    try:
        report = write_workbook(
            args.source,
            args.output_dir,
            json.loads(Path(args.manifest).read_text(encoding="utf-8")),
            json.loads(Path(args.results).read_text(encoding="utf-8")),
        )
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (WorkbookWriteError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        error = exc.as_dict() if isinstance(exc, WorkbookWriteError) else WorkbookWriteError("invalid_input", "The writer input could not be parsed.").as_dict()
        print(json.dumps(error, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
