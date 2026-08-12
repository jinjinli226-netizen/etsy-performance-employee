from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import PatternFill


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "employee" / "skills" / "etsy-performance-listing" / "scripts"
FIXTURE = Path(__file__).parent / "fixtures" / "performance-listing-template.xlsx"
HEADERS = (
    "head titles",
    "13 tags",
    "SPECIFICATION",
    "Category",
    "Instructions for buyers",
)


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def excel_modules():
    return tuple(load_script(name) for name in ("inspect_workbook", "validate_output", "write_workbook", "run_task"))


def png_bytes() -> bytes:
    # 1x1 transparent PNG; synthetic and deterministic.
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082"
    )


def make_book(
    path: Path,
    *,
    headers: tuple[str, ...] = HEADERS,
    rows: tuple[tuple[object, ...], ...] = (("SKU-1", "Blue sequin dance costume", 29.5, "internal"),),
    second_candidate: bool = False,
    image_rows: tuple[int, ...] = (),
) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    columns = ("SKU", "Product notes", "Cost price", "Logistics status", *headers)
    for col, value in enumerate(columns, 1):
        ws.cell(3, col, value)
    ws.cell(4, 1, "instruction")
    ws.cell(4, 2, "Please fill one product per row")
    for row_no, values in enumerate(rows, 5):
        for col, value in enumerate(values, 1):
            ws.cell(row_no, col, value)
    ws["B5"].hyperlink = "https://example.invalid/product"
    ws["B5"].fill = PatternFill("solid", fgColor="00FF00")
    ws.column_dimensions["B"].width = 42
    ws.row_dimensions[5].height = 30
    ws["D10"] = "=1+1"
    for row_no in image_rows:
        image = Image(BytesIO(png_bytes()))
        image.width = image.height = 1
        ws.add_image(image, f"B{row_no}")
    if second_candidate:
        other = wb.create_sheet("Also Products")
        for col, value in enumerate(columns, 1):
            other.cell(1, col, value)
    wb.save(path)
    return path


def valid_result(*, title: str = "Blue Sequin Dance Costume", tags: int = 13) -> dict[str, object]:
    return {
        "head_titles": title,
        "tags": [f"dance tag {index}" for index in range(tags)],
        "specification": "Blue sequin performance costume.",
        "category": "Costumes",
        "instructions_for_buyers": "Check the supplied measurements before ordering.",
        "confidence": 0.9,
        "fact_warnings": [],
        "quality_warnings": [],
        "rule_version": "rules-v1",
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "performance-listing-template.xlsx"
    shutil.copyfile(FIXTURE, destination)
    return destination


def test_inspection_finds_moved_headers_isolates_rows_and_filters_internal_fields(tmp_path: Path, excel_modules) -> None:
    inspect, _, _, _ = excel_modules
    path = make_book(
        tmp_path / "moved.xlsx",
        headers=(" Category ", "Instructions\nfor buyers", "13 tags", "head titles", "SPECIFICATION"),
        rows=(("SKU-1", "Blue costume", 10, "air"), ("SKU-2", "Red costume", 20, "sea")),
    )

    manifest = inspect.inspect_workbook(path, tmp_path / "operation")

    assert manifest["sheet"] == "Products"
    assert manifest["header_row"] == 3
    assert manifest["source_sha256"] == sha(path)
    assert set(manifest["output_columns"]) == set(HEADERS)
    assert len(manifest["rows"]) == 2
    assert manifest["rows"][0]["row_id"] != manifest["rows"][1]["row_id"]
    assert {field["header"] for field in manifest["rows"][0]["candidate_fields"]} == {"SKU", "Product notes"}
    serialized = json.dumps(manifest)
    assert "Cost price" not in serialized and "Logistics status" not in serialized
    assert "SKU-2" not in json.dumps(manifest["rows"][0])

    wb = load_workbook(path)
    wb["Products"]["A3"] = "Costume style"
    wb.save(path)
    semantic_manifest = inspect.inspect_workbook(path, tmp_path / "operation-semantic")
    assert "Costume style" in {field["header"] for field in semantic_manifest["rows"][0]["candidate_fields"]}


def test_tracked_fixture_contains_instruction_two_products_and_two_images(tmp_path: Path, excel_modules) -> None:
    inspect, _, _, _ = excel_modules
    source = copy_fixture(tmp_path)
    manifest = inspect.inspect_workbook(source, tmp_path / "operation")

    assert FIXTURE.is_file()
    assert [row["row_number"] for row in manifest["rows"]] == [5, 6]
    assert sum(len(row["image_paths"]) for row in manifest["rows"]) == 2
    workbook = load_workbook(source, data_only=False)
    assert workbook["Products"]["D10"].value == "=1+1"
    assert workbook["Products"]["B5"].hyperlink.target == "https://example.invalid/synthetic-product"


def test_inspection_extracts_multiple_embedded_images_with_generated_names(tmp_path: Path, excel_modules) -> None:
    inspect, _, _, _ = excel_modules
    path = make_book(tmp_path / "images.xlsx", image_rows=(5, 5))

    manifest = inspect.inspect_workbook(path, tmp_path / "operation")

    images = manifest["rows"][0]["image_paths"]
    assert len(images) == 2
    assert [Path(item).name for item in images] == ["row-000005-image-001.png", "row-000005-image-002.png"]
    assert all(Path(item).is_file() and Path(item).parent == tmp_path / "operation" / "images" for item in images)


def test_inspection_filters_chinese_finance_and_raw_source_columns(tmp_path: Path, excel_modules) -> None:
    inspect, _, _, _ = excel_modules
    path = make_book(tmp_path / "semantic-filter.xlsx")
    wb = load_workbook(path)
    ws = wb["Products"]
    ws["A3"] = "中文标题"
    ws["B3"] = "淘宝链接"
    ws["C3"] = "单件拿货价"
    ws["D3"] = "日常销售价（获利30%）"
    ws["A5"] = "蓝色亮片演出服"
    ws["B5"] = "https://source.invalid/raw"
    ws["C5"] = 99
    ws["D5"] = "=C5*2"
    wb.save(path)

    manifest = inspect.inspect_workbook(path, tmp_path / "operation")

    assert [field["header"] for field in manifest["rows"][0]["candidate_fields"]] == ["中文标题"]
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "source.invalid" not in serialized and "拿货价" not in serialized and "销售价" not in serialized


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda ws: setattr(ws["I3"], "value", None), "missing_output_header"),
        (lambda ws: setattr(ws["A3"], "value", "head titles"), "duplicate_output_header"),
        (lambda ws: ws.merge_cells("I3:J3"), "merged_output_header"),
    ],
)
def test_inspection_returns_structured_header_errors(tmp_path: Path, excel_modules, mutator, code: str) -> None:
    inspect, _, _, _ = excel_modules
    path = make_book(tmp_path / f"{code}.xlsx")
    wb = load_workbook(path)
    mutator(wb["Products"])
    wb.save(path)

    with pytest.raises(inspect.WorkbookError) as raised:
        inspect.inspect_workbook(path, tmp_path / "operation")
    assert raised.value.as_dict()["error"]["code"] == code


def test_inspection_rejects_ambiguous_sheet_and_non_xlsx(tmp_path: Path, excel_modules) -> None:
    inspect, _, _, _ = excel_modules
    path = make_book(tmp_path / "ambiguous.xlsx", second_candidate=True)
    with pytest.raises(inspect.WorkbookError, match="ambiguous") as raised:
        inspect.inspect_workbook(path, tmp_path / "operation")
    assert raised.value.code == "ambiguous_header_location"
    bad = tmp_path / "macro.xlsm"
    bad.write_bytes(path.read_bytes())
    with pytest.raises(inspect.WorkbookError) as raised:
        inspect.inspect_workbook(bad, tmp_path / "operation-2")
    assert raised.value.code == "unsupported_workbook_type"


def test_validation_is_strict_configurable_and_blocks_excel_injection(excel_modules) -> None:
    _, validate, _, _ = excel_modules
    assert validate.validate_generated(valid_result(), {"tag_count": 13})["rule_version"] == "rules-v1"
    custom = valid_result(tags=2)
    custom["tags"] = ["one", "two"]
    custom["head_titles"] = "Short title"
    assert validate.validate_generated(custom, {"tag_count": 2, "tag_max_chars": 4, "title_min_words": 2})["tags"] == ["one", "two"]

    for mutation in (
        {"extra": "forbidden"},
        {"head_titles": "=HYPERLINK(\"bad\")"},
        {"tags": ["same"] * 13},
    ):
        payload = valid_result()
        payload.update(mutation)
        with pytest.raises(validate.OutputValidationError) as raised:
            validate.validate_generated(payload, {})
        assert raised.value.as_dict()["error"]["code"] == "invalid_generated_output"

    wrong_version = valid_result()
    wrong_version["rule_version"] = "rules-old"
    with pytest.raises(validate.OutputValidationError):
        validate.validate_generated(wrong_version, {"rule_version": "rules-current"})


def test_writer_preserves_workbook_and_changes_only_five_target_cells(tmp_path: Path, excel_modules) -> None:
    inspect, _, writer, _ = excel_modules
    source = copy_fixture(tmp_path)
    before_hash = sha(source)
    manifest = inspect.inspect_workbook(source, tmp_path / "operation")
    before = load_workbook(source, data_only=False)
    result = valid_result()

    report = writer.write_workbook(
        source,
        tmp_path / "out",
        manifest,
        {manifest["rows"][0]["row_id"]: result},
        rules={"rule_version": "rules-v1"},
        expected_rule_version="rules-v1",
    )

    output = Path(report["output_path"])
    assert source.is_file() and sha(source) == before_hash and output != source
    after = load_workbook(output, data_only=False)
    assert after["Products"]["D10"].value == "=1+1"
    assert after["Products"]["B5"].hyperlink.target == before["Products"]["B5"].hyperlink.target
    assert after["Products"]["B5"].fill.fgColor.rgb == before["Products"]["B5"].fill.fgColor.rgb
    assert len(after["Products"]._images) == 2
    assert len(report["changed_cells"]) == 5
    assert report["changed_cells"] == sorted(report["changed_cells"])
    assert report["output_sha256"] == sha(output)

    target_cells = set(report["changed_cells"])
    for sheet_name in before.sheetnames:
        before_sheet = before[sheet_name]
        after_sheet = after[sheet_name]
        assert before_sheet.max_row == after_sheet.max_row
        assert before_sheet.max_column == after_sheet.max_column
        for row in before_sheet.iter_rows():
            for cell in row:
                address = f"{sheet_name}!{cell.coordinate}"
                if address not in target_cells:
                    peer = after_sheet[cell.coordinate]
                    assert peer.value == cell.value, address
                    assert peer.style_id == cell.style_id, address
                    assert (peer.hyperlink.target if peer.hyperlink else None) == (cell.hyperlink.target if cell.hyperlink else None), address


def test_writer_is_atomic_on_manifest_or_existing_destination_failure(tmp_path: Path, excel_modules) -> None:
    inspect, _, writer, _ = excel_modules
    source = make_book(tmp_path / "source.xlsx")
    manifest = inspect.inspect_workbook(source, tmp_path / "operation")
    manifest["source_sha256"] = "0" * 64
    out = tmp_path / "out"
    with pytest.raises(writer.WorkbookWriteError):
        writer.write_workbook(source, out, manifest, {}, rules={"rule_version": "rules-v1"}, expected_rule_version="rules-v1")
    assert not out.exists() or not list(out.glob("*.xlsx"))


def test_writer_checks_temporary_copy_hash_before_opening(tmp_path: Path, excel_modules, monkeypatch) -> None:
    inspect, _, writer, _ = excel_modules
    source = copy_fixture(tmp_path)
    manifest = inspect.inspect_workbook(source, tmp_path / "operation")

    def corrupt_copy(source_path, destination_path):
        Path(destination_path).write_bytes(b"not the inspected workbook")

    monkeypatch.setattr(writer.shutil, "copyfile", corrupt_copy)
    with pytest.raises(writer.WorkbookWriteError) as raised:
        writer.write_workbook(source, tmp_path / "out", manifest, {}, rules={"rule_version": "rules-v1"}, expected_rule_version="rules-v1")
    assert raised.value.code == "copy_hash_mismatch"
    assert not list((tmp_path / "out").glob("*.xlsx"))


@pytest.mark.parametrize("tamper", ["row_id", "row_number", "candidate_fields", "context_hash", "duplicate"])
def test_writer_rejects_tampered_or_duplicate_manifest_rows(tmp_path: Path, excel_modules, tamper: str) -> None:
    inspect, _, writer, _ = excel_modules
    source = copy_fixture(tmp_path)
    manifest = inspect.inspect_workbook(source, tmp_path / "operation")
    row = manifest["rows"][0]
    if tamper == "row_id":
        row["row_id"] = "f" * 64
    elif tamper == "row_number":
        row["row_number"] = 6
    elif tamper == "candidate_fields":
        row["candidate_fields"][0]["value"] = "TAMPERED"
    elif tamper == "context_hash":
        row["context_hash"] = "0" * 64
    else:
        manifest["rows"].append(dict(row))

    with pytest.raises(writer.WorkbookWriteError) as raised:
        writer.write_workbook(source, tmp_path / "out", manifest, {}, rules={"rule_version": "rules-v1"}, expected_rule_version="rules-v1")
    assert raised.value.code == "manifest_mismatch"


def test_writer_strictly_validates_results_and_expected_rule_version(tmp_path: Path, excel_modules) -> None:
    inspect, _, writer, _ = excel_modules
    source = copy_fixture(tmp_path)
    manifest = inspect.inspect_workbook(source, tmp_path / "operation")
    row_id = manifest["rows"][0]["row_id"]
    invalid = valid_result()
    invalid["extra"] = "forbidden"
    with pytest.raises(writer.WorkbookWriteError) as raised:
        writer.write_workbook(source, tmp_path / "out", manifest, {row_id: invalid}, rules={"rule_version": "rules-v1"}, expected_rule_version="rules-v1")
    assert raised.value.code == "invalid_result"

    wrong_version = valid_result()
    wrong_version["rule_version"] = "rules-old"
    with pytest.raises(writer.WorkbookWriteError) as raised:
        writer.write_workbook(source, tmp_path / "out-2", manifest, {row_id: wrong_version}, rules={"rule_version": "rules-v1"}, expected_rule_version="rules-v1")
    assert raised.value.code == "invalid_result"


class FakeHermes:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, prompt))
        return subprocess.CompletedProcess(command, 0, self.outputs.pop(0), "session_id: secret")


def test_run_task_retries_malformed_json_keeps_rows_isolated_and_uses_one_image(tmp_path: Path, excel_modules) -> None:
    _, _, _, run = excel_modules
    source = make_book(
        tmp_path / "source.xlsx",
        rows=(("SKU-1", "Blue costume", 10, "air"), ("SKU-2", "Red costume", 20, "sea")),
        image_rows=(5, 5, 6),
    )
    fake = FakeHermes(["not json", json.dumps(valid_result()), json.dumps(valid_result(title="Red Dance Costume"))])
    events: list[dict[str, object]] = []
    knowledge = tmp_path / "knowledge.json"
    knowledge.write_text(
        json.dumps(
            [
                {
                    "id": "k1",
                    "status": "active",
                    "abstract": "Use occasion-specific words.",
                    "raw_competitor_text": "SECRET COMPETITOR COPY",
                    "source_url": "https://competitor.invalid/raw",
                },
                {"id": "k2", "status": "inactive", "abstract": "INACTIVE SECRET"},
            ]
        ),
        encoding="utf-8",
    )

    report = run.run_task(
        source,
        tmp_path / "job",
        knowledge_path=knowledge,
        rules={"tag_count": 13, "rule_version": "rules-v1"},
        command_runner=fake,
        emit=events.append,
    )

    assert [event["event"] for event in events] == ["started", "row_started", "row_completed", "row_started", "row_completed", "completed"]
    assert Path(report["output_path"]).is_file()
    assert len(fake.calls) == 3
    for command, prompt in fake.calls:
        assert command[:7] == ["hermes", "-p", "etsy-performance-us", "chat", "-Q", "--source", "tool"]
        assert "--max-turns" in command and "--resume" not in command and "--yolo" not in command
        assert command[-2] == "-q" and command[-1] == prompt
        assert command.count("--image") <= 1
        assert "Cost price" not in prompt and "Logistics status" not in prompt
        assert "competitor" in prompt.lower() and "raw" in prompt.lower()
        assert "SECRET COMPETITOR COPY" not in prompt
        assert "competitor.invalid" not in prompt
        assert "INACTIVE SECRET" not in prompt
        assert "Use occasion-specific words." in prompt
        assert '"type":"object"' in prompt
        assert '"additionalProperties":false' in prompt
        assert '"confidence":{"type":"number","minimum":0,"maximum":1}' in prompt
        assert '"minItems":13,"maxItems":13' in prompt
        assert '"const":"rules-v1"' in prompt
        assert '"pattern":"^(?![=+@-])"' in prompt
        assert '"maxLength":20' in prompt
    assert "SKU-2" not in fake.calls[0][1] and "SKU-1" not in fake.calls[-1][1]


def test_run_task_emits_row_failure_and_leaves_no_partial_output(tmp_path: Path, excel_modules) -> None:
    _, _, _, run = excel_modules
    source = make_book(tmp_path / "source.xlsx")
    fake = FakeHermes(["bad", "still bad"])
    events: list[dict[str, object]] = []
    with pytest.raises(run.TaskError):
        run.run_task(source, tmp_path / "job", knowledge_path=None, rules={"rule_version": "rules-v1"}, command_runner=fake, emit=events.append)
    assert [event["event"] for event in events][-2:] == ["row_failed", "failed"]
    assert not list((tmp_path / "job").glob("*.xlsx"))


def test_run_task_repairs_well_formed_but_invalid_output_once(tmp_path: Path, excel_modules) -> None:
    _, _, _, run = excel_modules
    source = make_book(tmp_path / "source.xlsx")
    invalid = valid_result()
    invalid["head_titles"] = "=unsafe"
    fake = FakeHermes([json.dumps(invalid), json.dumps(valid_result())])

    report = run.run_task(source, tmp_path / "job", knowledge_path=None, rules={"rule_version": "rules-v1"}, command_runner=fake, emit=lambda event: None)

    assert Path(report["output_path"]).is_file()
    assert len(fake.calls) == 2
    assert "head_titles" in fake.calls[1][1]
    assert "formula prefix" in fake.calls[1][1]
    assert "SECRET" not in fake.calls[1][1]


def test_run_task_limits_schema_repair_to_one_retry(tmp_path: Path, excel_modules) -> None:
    _, _, _, run = excel_modules
    source = make_book(tmp_path / "source.xlsx")
    invalid = valid_result()
    invalid["head_titles"] = "=unsafe"
    fake = FakeHermes([json.dumps(invalid), json.dumps(invalid), json.dumps(valid_result())])
    with pytest.raises(run.TaskError) as raised:
        run.run_task(source, tmp_path / "job", knowledge_path=None, rules={"rule_version": "rules-v1"}, command_runner=fake, emit=lambda event: None)
    assert raised.value.code == "invalid_model_output"
    assert len(fake.calls) == 2


def test_product_note_containing_instruction_or_template_is_not_instruction_row(tmp_path: Path, excel_modules) -> None:
    inspect, _, _, _ = excel_modules
    source = make_book(tmp_path / "product-note.xlsx", rows=(("SKU-1", "Costume instruction card and template-inspired print", 10, "air"),))
    manifest = inspect.inspect_workbook(source, tmp_path / "operation")
    assert [row["row_number"] for row in manifest["rows"]] == [5]
