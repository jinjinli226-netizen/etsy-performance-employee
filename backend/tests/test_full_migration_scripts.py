from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "FullMigration.psm1"
EXPORT_FULL = ROOT / "scripts" / "export-full-migration.ps1"
RESTORE_FULL = ROOT / "scripts" / "restore-full-migration.ps1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is not available")
    return executable


def run_powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def ps_literal(path: Path) -> str:
    return str(path).replace("'", "''")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(package: Path, relative: str = "attachments/sample.txt") -> None:
    data_file = package / "data-full" / Path(relative)
    bundle = package / "repository.bundle"
    manifest = {
        "schema_version": 1,
        "source": {"git_commit": "a" * 40, "git_branch": "master"},
        "data": {
            "total_bytes": data_file.stat().st_size,
            "files": [
                {
                    "path": relative,
                    "size_bytes": data_file.stat().st_size,
                    "sha256": sha256(data_file),
                }
            ],
            "category_counts": {"attachments": 1},
        },
        "repository_bundle": {
            "path": "repository.bundle",
            "size_bytes": bundle.stat().st_size,
            "sha256": sha256(bundle),
        },
    }
    (package / "migration-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def test_full_migration_scripts_exist_and_parse() -> None:
    for path in (MODULE, EXPORT_FULL, RESTORE_FULL):
        assert path.is_file(), f"missing migration script: {path.name}"
    for path in (EXPORT_FULL, RESTORE_FULL):
        command = (
            "$errors=$null; "
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{ps_literal(path)}', "
            "[ref]$null, [ref]$errors); "
            "if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }"
        )
        result = run_powershell(command)
        assert result.returncode == 0, result.stdout + result.stderr


def test_full_export_is_stopped_hash_verified_and_secret_excluding() -> None:
    assert EXPORT_FULL.is_file()
    script = read(EXPORT_FULL)
    for required in (
        "git bundle create",
        "git bundle verify",
        "robocopy",
        "Get-MigrationInventory",
        "runtime",
        "browser-profile",
        "migration-workspace",
        "migration-packages",
        "start-configured.ps1",
        "-Stop",
        "migration-manifest.json",
        "RESTORE-README.md",
        "Get-PSDrive",
        "api/excel-jobs",
    ):
        assert required in script
    for forbidden in ("api_key=", "access_token=", "codex\\auth.json"):
        assert forbidden.casefold() not in script.casefold()


def test_full_restore_verifies_before_copy_and_uses_official_login() -> None:
    assert RESTORE_FULL.is_file()
    script = read(RESTORE_FULL)
    assert script.index("Test-MigrationManifest") < script.index("robocopy")
    for required in (
        "Assert-EmptyMigrationTarget",
        "bootstrap-new-machine.ps1",
        "openai-codex",
        "gpt-5.6-sol",
        "DataDirectory must be empty",
        "Get-MigrationInventory",
    ):
        assert required in script
    assert "relay" not in script.casefold()


def test_powershell_script_delegates_do_not_read_a_stale_native_exit_code() -> None:
    export_script = read(EXPORT_FULL)
    restore_script = read(RESTORE_FULL)
    assert "& $configuredStart" in export_script
    assert re.search(r"& \$configuredStart[^\n]*\nif \(\$LASTEXITCODE", export_script) is None
    assert "& $bootstrap @arguments" in restore_script
    assert re.search(r"& \$bootstrap @arguments[^\n]*\nif \(\$LASTEXITCODE", restore_script) is None


def test_full_export_allows_a_dedicated_package_below_a_drive_root() -> None:
    script = read(EXPORT_FULL)
    assert "$parent = Resolve-MigrationLocalPath -Path $parent -MustExist" not in script
    assert "$parent = [IO.Path]::GetFullPath($parent)" in script


def test_inventory_uses_canonical_relative_paths_and_sha256(tmp_path: Path) -> None:
    assert MODULE.is_file()
    root = tmp_path / "data-full"
    sample = root / "training-evidence" / "产品图.txt"
    sample.parent.mkdir(parents=True)
    sample.write_text("evidence", encoding="utf-8")
    command = (
        f"Import-Module '{ps_literal(MODULE)}' -Force; "
        f"$inventory = @(Get-MigrationInventory -Root '{ps_literal(root)}'); "
        "ConvertTo-Json -InputObject $inventory -Compress"
    )
    result = run_powershell(command)
    assert result.returncode == 0, result.stdout + result.stderr
    inventory = json.loads(result.stdout)
    assert inventory == [
        {
            "path": "training-evidence/产品图.txt",
            "size_bytes": sample.stat().st_size,
            "sha256": sha256(sample),
        }
    ]


def test_inventory_emits_one_record_per_file_for_manifest_totals(tmp_path: Path) -> None:
    root = tmp_path / "data-full"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    command = (
        f"Import-Module '{ps_literal(MODULE)}' -Force; "
        f"$inventory = @(Get-MigrationInventory -Root '{ps_literal(root)}'); "
        "$summary = [pscustomobject]@{ "
        "count = $inventory.Count; "
        "first_size_type = $inventory[0].size_bytes.GetType().FullName }; "
        "$summary | ConvertTo-Json -Compress"
    )
    result = run_powershell(command)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary == {"count": 2, "first_size_type": "System.Int64"}


def test_inventory_rejects_forbidden_runtime_and_credentials(tmp_path: Path) -> None:
    assert MODULE.is_file()
    root = tmp_path / "data-full"
    forbidden = root / "runtime" / ".start-pids.json"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_text("{}", encoding="utf-8")
    command = (
        f"Import-Module '{ps_literal(MODULE)}' -Force; "
        f"Get-MigrationInventory -Root '{ps_literal(root)}'"
    )
    result = run_powershell(command)
    assert result.returncode != 0
    assert "forbidden" in (result.stdout + result.stderr).casefold()


def test_manifest_validation_passes_then_rejects_tampering(tmp_path: Path) -> None:
    assert MODULE.is_file()
    package = tmp_path / "package"
    data_file = package / "data-full" / "attachments" / "sample.txt"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("safe", encoding="utf-8")
    (package / "repository.bundle").write_bytes(b"bundle")
    write_manifest(package)
    command = (
        f"Import-Module '{ps_literal(MODULE)}' -Force; "
        f"Test-MigrationManifest -PackageDirectory '{ps_literal(package)}' | Out-Null"
    )
    valid = run_powershell(command)
    assert valid.returncode == 0, valid.stdout + valid.stderr

    data_file.write_text("evil", encoding="utf-8")
    invalid = run_powershell(command)
    assert invalid.returncode != 0
    assert "hash" in (invalid.stdout + invalid.stderr).casefold()


def test_restore_target_must_be_missing_or_empty(tmp_path: Path) -> None:
    assert MODULE.is_file()
    target = tmp_path / "target"
    target.mkdir()
    command = (
        f"Import-Module '{ps_literal(MODULE)}' -Force; "
        f"Assert-EmptyMigrationTarget -Path '{ps_literal(target)}' | Out-Null"
    )
    empty = run_powershell(command)
    assert empty.returncode == 0, empty.stdout + empty.stderr

    (target / "existing.db").write_text("occupied", encoding="utf-8")
    occupied = run_powershell(command)
    assert occupied.returncode != 0
    assert "DataDirectory must be empty" in occupied.stdout + occupied.stderr
