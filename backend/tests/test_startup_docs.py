from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
START = ROOT / "scripts" / "start.ps1"
README = ROOT / "README.md"
RUNBOOK = ROOT / "docs" / "operations" / "mvp-runbook.md"
VITE_CONFIG = ROOT / "frontend" / "vite.config.ts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_production_start_script_parses_and_uses_bounded_owned_processes() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not available")

    parsed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$errors=$null; "
                f"[void][System.Management.Automation.Language.Parser]::ParseFile('{START}', "
                "[ref]$null, [ref]$errors); "
                "if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr

    script = read(START)
    assert "Set-StrictMode -Version Latest" in script
    assert "ETSY_EMPLOYEE_DATA_DIR" in script and "GetFullPath" in script
    assert "ReparsePoint" in script
    assert ".start-pids.json" in script
    assert "creationTimeUtcTicks" in script
    assert "schemaVersion -ne 1" in script
    assert "Recorded metadata does not belong to this project or data directory" in script
    assert '-Role "backend"' in script and '-Role "frontend"' in script
    assert 'commandMarker -cne $BackendPath' in script
    assert 'commandMarker -cne $VitePath' in script
    assert 'port -ne $BackendPort' in script and 'port -ne $FrontendPort' in script
    assert 'python.exe' in script and 'node.exe' in script
    assert '"powershell.exe", "pwsh.exe"' in script
    assert "Get-CimInstance Win32_Process" in script
    assert "Get-NetTCPConnection" in script and "OwningProcess" in script
    assert "Stop-RecordedProcessTree" in script and "Stop-Process" in script
    assert "Stop-UnrecordedStartedProcess" in script
    assert "$startedBackend = $null" in script and "$startedFrontend = $null" in script
    assert "-Process $startedBackend -ExpectedExecutable $PythonPath -CommandMarker $BackendPath" in script
    assert "-Process $startedFrontend -ExpectedExecutable $NodePath -CommandMarker $VitePath" in script
    assert "taskkill" not in script.casefold()
    assert "Stop-Process -Name" not in script
    assert '"--port", "8765"' in script
    assert "5173" in script and "--strictPort" in script
    assert "if ((Test-PortInUse -Port $BackendPort) -or (Test-PortInUse -Port $FrontendPort))" in script


def test_startup_preflight_is_read_only_and_never_handles_credentials() -> None:
    script = read(START)
    lowered = script.casefold()

    assert "verify-employee.ps1" in script
    assert '@("-p", $ProfileId, "auth", "status", $ProviderId)' in script
    assert "hermes -p etsy-performance-us auth add openai-codex --type oauth" in script
    assert "RunModelCheck" not in script and "RunDoctor" not in script
    assert "--api-key" not in lowered
    assert "read-host" not in lowered
    assert "invoke-expression" not in lowered
    assert "CredentialResult.Output" not in script
    assert "CredentialResult.Lines" not in script


def test_startup_refuses_profile_drift_before_starting_any_process() -> None:
    script = read(START)

    verify_index = script.index('"verify-employee.ps1"')
    backend_index = script.index("$startedBackend = Start-Process")
    frontend_index = script.index("$startedFrontend = Start-Process")
    assert verify_index < backend_index < frontend_index
    assert "$verifyResult.ExitCode -ne 0" in script


def test_startup_builds_and_serves_the_production_frontend_with_local_api_proxy() -> None:
    script = read(START)
    vite = read(VITE_CONFIG)

    assert "run build" in script and "pnpm" in script.casefold()
    assert '"preview"' in script and "vite.js" in script
    assert '"app.main:app"' in script and '"uvicorn"' in script
    assert "http://127.0.0.1:5173" in script
    assert "preview:" in vite and "server:" in vite
    assert '"/api"' in vite
    assert '"http://127.0.0.1:8765"' in vite


def test_chinese_readme_documents_truthful_setup_start_stop_and_storage() -> None:
    document = read(README)
    assert "\ufffd" not in document
    for required in (
        "Windows",
        "Python 3.11",
        "Node.js",
        "pnpm",
        "Hermes",
        ".\\scripts\\start.ps1",
        "-DataDirectory",
        "Ctrl+C",
        "etsy-performance-us",
        "auth add openai-codex --type oauth",
        "http://127.0.0.1:5173",
        "五个",
        "不会自动发布",
    ):
        assert required in document


def test_runbook_covers_recovery_migration_capacity_and_known_limits() -> None:
    document = read(RUNBOOK)
    assert "\ufffd" not in document
    for required in (
        "507",
        "knowledge_capacity_exceeded",
        "cleanup_failed",
        "interrupted",
        "worker_unavailable",
        "invalid_worker_event",
        "originality_failed",
        "source_modified",
        "failed",
        "重试",
        "备份",
        "导出",
        "导入",
        "package-employee.ps1",
        "dry_run=true",
        "migration-capability",
        "credential_status",
        "C:\\Users\\<用户名>\\AppData\\Local\\hermes\\profiles\\etsy-performance-us",
        "excel-jobs",
        "migration-packages",
        "限制",
    ):
        assert required in document
