from __future__ import annotations

import shutil
import subprocess
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
START = ROOT / "scripts" / "start.ps1"
START_ENV = ROOT / "scripts" / "start-environment.ps1"
CLEAN_E2E = ROOT / "scripts" / "clean-e2e-data.ps1"
README = ROOT / "README.md"
RUNBOOK = ROOT / "docs" / "operations" / "mvp-runbook.md"
MIGRATION_GUIDE = ROOT / "docs" / "operations" / "网站与数字员工部署迁移指南.md"
VITE_CONFIG = ROOT / "frontend" / "vite.config.ts"
UV_LOCK = ROOT / "backend" / "uv.lock"


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
    environment_script = read(START_ENV)
    assert "Set-StrictMode -Version Latest" in script
    assert "ETSY_EMPLOYEE_DATA_DIR" in environment_script and "GetFullPath" in script
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
    assert '"--port", ([string]$BackendPort)' in script
    assert '([string]$FrontendPort), "--strictPort"' in script
    assert "if ((Test-PortInUse -Port $BackendPort) -or (Test-PortInUse -Port $FrontendPort))" in script


def test_startup_preflight_is_read_only_and_never_handles_credentials() -> None:
    script = read(START)
    lowered = script.casefold()

    assert "verify-employee.ps1" in script
    assert '@("-p", $ProfileId, "auth", "status", $Provider)' in script
    assert "Get-EmployeeProfileManifest" in script
    assert "keyConfigured" in script
    assert "if (-not [bool]$profileManifest.keyConfigured" in script
    assert "Hermes API-key credential is not configured" in script
    assert "Hermes OAuth credential is not ready" in script
    assert "RunModelCheck" not in script and "RunDoctor" not in script
    assert "--api-key" not in lowered
    assert "read-host" not in lowered
    assert "invoke-expression" not in lowered
    assert "CredentialResult.Output" not in script
    assert "CredentialResult.Lines" not in script


def test_startup_child_environment_ignores_conflicting_parent_values(tmp_path: Path) -> None:
    powershell = powershell_executable = shutil.which("pwsh") or shutil.which("powershell")
    if powershell_executable is None:
        pytest.skip("PowerShell is not available")
    hermes = tmp_path / "hermes.exe"
    hermes.write_bytes(b"MZ")
    data_dir = (tmp_path / "canonical-data").resolve()
    hermes_home = (tmp_path / "canonical-hermes-home").resolve()
    capture = tmp_path / "captured.json"
    child = tmp_path / "capture.ps1"
    child.write_text(
        "[ordered]@{data=$env:ETSY_EMPLOYEE_DATA_DIR; database=$env:ETSY_EMPLOYEE_DATABASE_URL; "
        "executable=$env:ETSY_EMPLOYEE_HERMES_EXECUTABLE; profile=$env:ETSY_EMPLOYEE_HERMES_PROFILE; "
        "home=$env:HERMES_HOME; testMode=$env:ETSY_EMPLOYEE_TEST_MODE; "
        "e2eBackend=$env:ETSY_E2E_BACKEND; e2eMode=$env:ETSY_E2E_TEST_MODE} | "
        "ConvertTo-Json | Set-Content -LiteralPath $args[0] -Encoding utf8",
        encoding="utf-8-sig",
    )
    command = (
        f"$env:ETSY_EMPLOYEE_DATA_DIR='C:\\wrong'; "
        "$env:ETSY_EMPLOYEE_DATABASE_URL='sqlite:///C:/wrong.db'; "
        "$env:ETSY_EMPLOYEE_HERMES_EXECUTABLE='C:\\wrong-hermes.exe'; "
        "$env:ETSY_EMPLOYEE_HERMES_PROFILE='wrong-profile'; $env:HERMES_HOME='C:\\wrong-home'; "
        "$env:ETSY_EMPLOYEE_TEST_MODE='1'; $env:ETSY_E2E_BACKEND='http://evil.invalid'; $env:ETSY_E2E_TEST_MODE='1'; "
        f". '{START_ENV}'; Set-EmployeeRuntimeEnvironment -DataDirectory '{data_dir}' "
        f"-HermesExecutable '{hermes}' -HermesHome '{hermes_home}'; "
        f"& '{powershell_executable}' -NoProfile -NonInteractive -File '{child}' '{capture}'"
    )
    result = subprocess.run(
        [powershell_executable, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    captured = json.loads(capture.read_text(encoding="utf-8-sig"))
    assert Path(captured["data"]) == data_dir
    assert captured["database"] is None
    assert Path(captured["executable"]) == hermes
    assert captured["profile"] == "etsy-performance-us"
    assert Path(captured["home"]) == hermes_home
    assert captured["testMode"] is None
    assert captured["e2eBackend"] is None
    assert captured["e2eMode"] is None


def test_start_script_uses_resolved_hermes_and_canonical_environment() -> None:
    script = read(START)
    assert "[string]$HermesExecutable" in script
    assert ". $StartEnvironmentScript" in script
    assert "Set-EmployeeRuntimeEnvironment" in script
    assert "-HermesHome $HermesHome" in script
    assert "$env:ETSY_EMPLOYEE_HERMES_PROFILE = \"etsy-performance-us\"" in read(START_ENV)


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
    assert "http://127.0.0.1:$FrontendPort" in script
    assert "preview:" in vite and "server:" in vite
    assert '"/api"' in vite
    assert '"http://127.0.0.1:8765"' in vite
    assert 'process.env.ETSY_E2E_TEST_MODE === "1"' in vite
    assert "ETSY_E2E_BACKEND" in vite
    assert "[ValidateRange(1024, 65535)][int]$BackendPort" in script
    assert "[ValidateRange(1024, 65535)][int]$FrontendPort" in script
    assert "$env:ETSY_EMPLOYEE_BACKEND_PORT = [string]$BackendPort" in script
    assert "ETSY_EMPLOYEE_BACKEND_PORT" in vite
    assert "127.0.0.1" in vite


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
        "uv sync --extra dev --frozen",
        "生产路径已经激活",
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
        "生产路径已激活",
        "logged out",
        "RunModelCheck",
        "RunDoctor",
    ):
        assert required in document


def test_migration_guide_covers_code_data_credentials_import_and_acceptance() -> None:
    document = read(MIGRATION_GUIDE)
    assert "\ufffd" not in document
    for required in (
        "git bundle create",
        "package-employee.ps1",
        "data-full",
        "migration-capability",
        "dry_run=true",
        "dry_run=false",
        "credential_status=pending",
        "provision-employee.ps1",
        "verify-employee.ps1 -RunModelCheck -RunDoctor",
        "hermes -p etsy-performance-us auth add openai-codex --type oauth",
        "codex login status",
        "ETSY_EMPLOYEE_MODEL_ENGINE",
        "ETSY_EMPLOYEE_ROW_WORKERS",
        "不发布残缺工作簿",
        "不是公网网站",
    ):
        assert required in document


def test_python_dependencies_use_a_committed_uv_lock() -> None:
    assert UV_LOCK.is_file()
    lock = read(UV_LOCK)
    assert 'name = "etsy-performance-employee-backend"' in lock
    assert "uv sync --extra dev --frozen" in read(README)


def test_e2e_cleanup_is_manifest_scoped_and_scripts_parse() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not available")
    for path in (START, START_ENV, CLEAN_E2E):
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", f"$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$e); if($e.Count){{exit 1}}"],
            check=False,
        )
        assert result.returncode == 0
    script = read(CLEAN_E2E)
    assert "e2e-run-manifest.json" in script
    assert "run-" in script and "GetFullPath" in script
    assert "Get-NetTCPConnection" in script
    assert "Remove-Item -LiteralPath $RunDirectory -Recurse -Force" in script
