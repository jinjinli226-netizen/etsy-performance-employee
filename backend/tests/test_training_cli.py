from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from app.training import cli
from app.training.service import TrainingRunSummary


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "train-vision-listings.ps1"


def make_xlsx(path: Path) -> str:
    path.write_bytes(b"PK\x03\x04test-workbook")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_options_default_to_one_and_resolve_existing_xlsx(tmp_path: Path) -> None:
    workbook = tmp_path / "shops.xlsx"
    make_xlsx(workbook)
    data_dir = tmp_path / "runtime"

    options = cli.parse_options(
        ["--workbook", str(workbook), "--data-directory", str(data_dir)]
    )

    assert options.workbook == workbook.resolve()
    assert options.data_directory == data_dir.resolve()
    assert options.limit == 1
    assert options.delay == 20


@pytest.mark.parametrize(
    "extra",
    [
        ["--delay", "14.9"],
        ["--delay", "25.1"],
        ["--limit", "0"],
        ["--limit", "-1"],
        ["--batch", "--limit", "2"],
    ],
)
def test_options_reject_unsafe_limits_and_delays(tmp_path: Path, extra: list[str]) -> None:
    workbook = tmp_path / "shops.xlsx"
    make_xlsx(workbook)

    with pytest.raises(SystemExit):
        cli.parse_options(["--workbook", str(workbook), *extra])


@pytest.mark.parametrize("name", ["missing.xlsx", "shops.xls", "shops.txt"])
def test_options_require_an_existing_xlsx(tmp_path: Path, name: str) -> None:
    workbook = tmp_path / name
    if name != "missing.xlsx":
        workbook.write_bytes(b"not-xlsx")

    with pytest.raises(SystemExit):
        cli.parse_options(["--workbook", str(workbook)])


def test_run_initializes_database_and_performs_preflights_before_training(
    tmp_path: Path, monkeypatch
) -> None:
    workbook = tmp_path / "shops.xlsx"
    source_hash = make_xlsx(workbook)
    data_dir = tmp_path / "runtime"
    options = cli.parse_options(
        [
            "--workbook",
            str(workbook),
            "--data-directory",
            str(data_dir),
            "--limit",
            "2",
            "--delay",
            "15",
        ]
    )
    events: list[str] = []

    class FakeEmployee:
        def __init__(self, **_kwargs) -> None:
            events.append("employee-created")

        def check_available(self) -> None:
            events.append("employee-ready")

    class FakeKnowledge:
        def __init__(self, _factory, **_kwargs) -> None:
            events.append("knowledge-created")

        def require_capacity_ready(self) -> None:
            events.append("capacity-ready")

    class FakeBrowser:
        def __init__(self, _data_dir, **_kwargs) -> None:
            events.append("browser-created")

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            events.append("browser-closed")

    class FakeClient:
        def __enter__(self):
            events.append("client-created")
            return self

        def __exit__(self, *_args) -> None:
            events.append("client-closed")

    class FakeService:
        def __init__(self, **_kwargs) -> None:
            events.append("service-created")

        async def run(self, path, *, limit, delay, shop_substr=None):
            events.append("service-run")
            assert Path(path) == workbook.resolve()
            assert (limit, delay, shop_substr) == (2, 15, None)
            return TrainingRunSummary("tr-run-safe", "completed", {"completed": 2})

    real_init = cli.init_db

    def recorded_init(engine) -> None:
        events.append("database-init")
        real_init(engine)

    monkeypatch.setattr(cli, "SubprocessHermesAdapter", FakeEmployee)
    monkeypatch.setattr(cli, "KnowledgeService", FakeKnowledge)
    monkeypatch.setattr(cli, "resolve_visible_browser", lambda: events.append("browser-ready") or tmp_path / "browser.exe")
    monkeypatch.setattr(cli, "VisibleEtsyBrowser", FakeBrowser)
    monkeypatch.setattr(cli, "make_http_client", lambda: FakeClient())
    monkeypatch.setattr(cli, "VisionTrainingService", FakeService)
    monkeypatch.setattr(cli, "init_db", recorded_init)

    summary = asyncio.run(cli.run_options(options))

    assert summary.counts == {"completed": 2}
    assert events.index("database-init") < events.index("employee-ready")
    assert events.index("employee-ready") < events.index("browser-ready")
    assert events.index("browser-ready") < events.index("capacity-ready")
    assert events.index("capacity-ready") < events.index("service-run")
    assert events[-2:] == ["client-closed", "browser-closed"]
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == source_hash
    assert (data_dir / "app.db").is_file()


def test_main_prints_only_bounded_summary_and_returns_nonzero_on_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workbook = tmp_path / "shops.xlsx"
    make_xlsx(workbook)

    async def success(_options):
        return TrainingRunSummary("tr-safe", "completed", {"completed": 1})

    monkeypatch.setattr(cli, "run_options", success)
    assert cli.main(["--workbook", str(workbook)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "run_id": "tr-safe",
        "status": "completed",
        "counts": {"completed": 1},
    }

    async def failure(_options):
        raise ValueError("secret path and shop details must not leak")

    monkeypatch.setattr(cli, "run_options", failure)
    assert cli.main(["--workbook", str(workbook)]) == 1
    captured = capsys.readouterr()
    assert "secret" not in captured.err
    assert json.loads(captured.err) == {"status": "failed", "code": "training_failed"}


def test_powershell_wrapper_is_literal_safe_and_batch_requires_explicit_switch() -> None:
    script = WRAPPER.read_text(encoding="utf-8")

    assert "[switch]$Batch" in script and "[int]$Limit = 1" in script
    assert 'ContainsKey("Limit")' in script
    assert '"--batch"' in script and '"--limit"' in script
    assert "Test-Path -LiteralPath" in script
    assert "& $UvCommand.Source @Arguments" in script
    assert "Invoke-Expression" not in script
