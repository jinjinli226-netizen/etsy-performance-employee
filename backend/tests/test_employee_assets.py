from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOUL_PATH = REPOSITORY_ROOT / "employee" / "SOUL.md"
SKILL_PATH = (
    REPOSITORY_ROOT
    / "employee"
    / "skills"
    / "etsy-performance-listing"
    / "SKILL.md"
)
CONTRACT_PATH = SKILL_PATH.parent / "references" / "output-contract.md"
SCRIPT_PATHS = tuple((SKILL_PATH.parent / "scripts").glob("*.py"))
PROVISION_PATH = REPOSITORY_ROOT / "scripts" / "provision-employee.ps1"
VERIFY_PATH = REPOSITORY_ROOT / "scripts" / "verify-employee.ps1"
CONFIG_INSPECTOR_PATH = REPOSITORY_ROOT / "scripts" / "inspect-employee-config.py"
MANIFEST_NAME = "provisioning-manifest.json"

FIXED_HEADERS = (
    "head titles",
    "13 tags",
    "SPECIFICATION",
    "Category",
    "Instructions for buyers",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def powershell_executable() -> str:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not available")
    return powershell


def create_verifier_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    hermes_home = tmp_path / "hermes"
    profile_home = hermes_home / "profiles" / "etsy-performance-us"
    skill_home = profile_home / "skills" / "etsy-performance-listing"
    references_home = skill_home / "references"
    workspace = profile_home / "workspace"
    references_home.mkdir(parents=True)
    workspace.mkdir()

    profile_assets = {
        "SOUL.md": profile_home / "SOUL.md",
        "skills/etsy-performance-listing/SKILL.md": skill_home / "SKILL.md",
        "skills/etsy-performance-listing/references/output-contract.md": (
            references_home / "output-contract.md"
        ),
    }
    source_assets = {
        "SOUL.md": SOUL_PATH,
        "skills/etsy-performance-listing/SKILL.md": SKILL_PATH,
        "skills/etsy-performance-listing/references/output-contract.md": CONTRACT_PATH,
    }
    for script_path in SCRIPT_PATHS:
        relative = f"skills/etsy-performance-listing/scripts/{script_path.name}"
        source_assets[relative] = script_path
        destination = skill_home / "scripts" / script_path.name
        destination.parent.mkdir(exist_ok=True)
        profile_assets[relative] = destination
    for relative_path, destination in profile_assets.items():
        shutil.copyfile(source_assets[relative_path], destination)

    (profile_home / ".env").write_text(
        "# Hermes mirrors terminal.backend here.\nTERMINAL_ENV=local\n",
        encoding="utf-8",
    )
    (profile_home / "config.yaml").write_text(
        "model: {}\ngateway: {}\n",
        encoding="utf-8",
    )
    for name, content in {
        "SOUL.md": "default soul",
        "config.yaml": "default config",
        ".env": "# default placeholder",
    }.items():
        (hermes_home / name).write_text(content, encoding="utf-8")

    values = {
        "terminal.backend": "local",
        "terminal.cwd": workspace.as_posix(),
        "terminal.home_mode": "profile",
        "memory.memory_enabled": "true",
        "memory.user_profile_enabled": "true",
        "memory.write_approval": "true",
        "skills.write_approval": "true",
        "model.provider": "custom",
        "model.default": "gpt-5.6-sol",
        "model.base_url": "https://relay.example/v1",
        "agent.reasoning_effort": "high",
    }
    manifest = {
        "schemaVersion": 1,
        "profileId": "etsy-performance-us",
        "provider": values["model.provider"],
        "model": values["model.default"],
        "baseUrl": values["model.base_url"],
        "hasBaseUrl": True,
        "reasoningEffort": values["agent.reasoning_effort"],
        "workspace": values["terminal.cwd"],
        "keyConfigured": False,
        "assetHashes": {
            relative_path: sha256(source_path)
            for relative_path, source_path in source_assets.items()
        },
        "defaultBaseline": {
            name: sha256(hermes_home / name)
            for name in ("SOUL.md", "config.yaml", ".env")
        },
    }
    manifest_path = profile_home / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    fake_hermes = tmp_path / "fake-hermes.ps1"
    return hermes_home, profile_home, fake_hermes, values


def write_fake_hermes(
    path: Path,
    values: dict[str, str],
    *,
    doctor_output: str | None = None,
    provision_profile_home: Path | None = None,
    command_log: Path | None = None,
    missing_config_output: str | None = None,
    missing_config_to_stderr: bool = False,
) -> None:
    escaped_values = ((key, value.replace("'", "''")) for key, value in values.items())
    literal_values = "\n".join(
        f"    '{key}' = '{escaped_value}'" for key, escaped_value in escaped_values
    )
    provision_block = ""
    if provision_profile_home is not None:
        escaped_home = str(provision_profile_home).replace("'", "''")
        provision_block = (
            f"$ProfileHome = '{escaped_home}'\n"
            "if ($args[0] -eq 'profile' -and $args[1] -eq 'show') { "
            "if (Test-Path -LiteralPath $ProfileHome) { exit 0 } else { exit 1 } }\n"
            "if ($args[0] -eq 'profile' -and $args[1] -eq 'create') { "
            "New-Item -ItemType Directory -Path (Join-Path $ProfileHome 'skills') -Force | Out-Null; "
            "Set-Content -LiteralPath (Join-Path $ProfileHome '.env') -Value '# generated' -Encoding UTF8; "
            "Set-Content -LiteralPath (Join-Path $ProfileHome 'config.yaml') -Value 'model: {}' -Encoding UTF8; exit 0 }\n"
            "if ($args[0] -eq '-p' -and $args[2] -eq 'config' -and $args[3] -eq 'set') { "
            "$Values[$args[4]] = $args[5]; "
            "if ($args[4] -eq 'terminal.backend') { Add-Content -LiteralPath (Join-Path $ProfileHome '.env') -Value 'TERMINAL_ENV=local' }; "
            "$Values | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ProfileHome 'fake-values.json') -Encoding UTF8; exit 0 }\n"
        )
    doctor_block = ""
    if doctor_output is not None:
        escaped_doctor = doctor_output.replace("'", "''")
        doctor_block = (
            "if ($args[$args.Count - 1] -eq 'doctor') { "
            f"Write-Output '{escaped_doctor}'; exit 0 }}\n"
        )
    log_block = ""
    if command_log is not None:
        escaped_log = str(command_log).replace("'", "''")
        log_block = f"Add-Content -LiteralPath '{escaped_log}' -Value ($args -join '|')\n"
    missing_output = missing_config_output or "Config key not set: model.base_url"
    escaped_missing_output = missing_output.replace("'", "''")
    missing_output_command = f"Write-Output '{escaped_missing_output}'"
    if missing_config_to_stderr:
        missing_output_command = (
            f"[Console]::Error.WriteLine('{escaped_missing_output}')"
        )
    path.write_text(
        "$Values = @{\n"
        f"{literal_values}\n"
        "}\n"
        f"{log_block}"
        f"{provision_block}"
        f"{doctor_block}"
        "$Key = $args[$args.Count - 1]\n"
        "if ($Values.ContainsKey($Key)) { Write-Output $Values[$Key]; exit 0 }\n"
        f"{missing_output_command}\n"
        "exit 1\n",
        encoding="utf-8-sig",
    )


def run_verifier(
    hermes_home: Path,
    fake_hermes: Path,
    *,
    initial_provision: bool = False,
    run_doctor: bool = False,
) -> subprocess.CompletedProcess[str]:
    arguments = [
        powershell_executable(),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(VERIFY_PATH),
        "-HermesHome",
        str(hermes_home),
        "-HermesCommand",
        str(fake_hermes),
    ]
    if initial_provision:
        arguments.append("-InitialProvision")
    if run_doctor:
        arguments.append("-RunDoctor")
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
    )


def test_soul_defines_only_the_etsy_performance_costume_role() -> None:
    soul = read(SOUL_PATH)
    lowered = soul.lower()

    assert "etsy" in lowered
    assert "表演服" in soul
    assert "中文" in soul and "english" in lowered
    assert "长期教学" in soul and "独立" in soul and "工作簿" in soul
    assert "不得编造" in soul
    assert "竞品" in soul and "复制" in soul
    assert "原始竞品证据" in soul and "生效的抽象知识" in soul
    assert "源工作簿" in soul and "不可变" in soul
    assert "警告" in soul
    assert "发布" in soul and "财务" in soul and "账户" in soul
    assert "审批" in soul


def test_skill_owns_dynamic_workbook_interpretation_and_is_prompt_injection_safe() -> None:
    skill = read(SKILL_PATH)
    lowered = skill.lower()

    assert "动态" in skill and "表头" in skill and "语义" in skill
    assert "网站不解释业务表头" in skill
    assert "提示注入" in skill
    assert "不可信" in skill
    assert "工作簿工具契约" in skill
    assert "inspect_workbook.py" in skill and "run_task.py" in skill
    assert "源工作簿" in skill and "不可变" in skill
    assert "原创" in skill
    assert "active" in lowered


def test_output_contract_has_each_fixed_header_exactly_once() -> None:
    contract = read(CONTRACT_PATH)

    for header in FIXED_HEADERS:
        assert contract.count(header) == 1, header


def test_output_contract_matches_generated_listing_fields() -> None:
    contract = read(CONTRACT_PATH)

    for field in (
        "head_titles",
        "tags",
        "specification",
        "category",
        "instructions_for_buyers",
        "confidence",
        "fact_warnings",
        "quality_warnings",
        "rule_version",
    ):
        assert f'"{field}"' in contract
    assert "extra" in contract.lower() and "forbid" in contract.lower()
    assert "可版本化" in contract and "可配置" in contract
    assert "永久" in contract and "硬编码" in contract


def test_employee_assets_contain_no_secret_literal_or_unrelated_employee_state() -> None:
    combined = "\n".join(read(path) for path in (SOUL_PATH, SKILL_PATH, CONTRACT_PATH))
    lowered = combined.lower()

    secret_literal_patterns = (
        r"sk-[a-z0-9_-]{12,}",
        r"(?i)(?:api[_ -]?key|cookie|token)\s*[:=]\s*[\"']?[a-z0-9_./+-]{12,}",
        r"(?i)bearer\s+[a-z0-9._~+/-]{12,}",
    )
    for pattern in secret_literal_patterns:
        assert re.search(pattern, combined) is None

    assert "ynd-tk-us" not in lowered
    assert "tiktok" not in lowered
    assert "memory.md" not in lowered
    assert "user.md" not in lowered
    assert "state.db" not in lowered


def test_provisioner_encodes_isolation_and_safe_secret_handling() -> None:
    script = read(PROVISION_PATH)
    lowered = script.lower()

    assert '"etsy-performance-us"' in script
    assert "VerifyOnly" in script
    assert "Get-FileHash" in script
    assert '"SOUL.md", "config.yaml", ".env"' in script
    assert (
        '@("profile", "create", $ProfileId, "--no-skills", "--description", $Description)'
        in script
    )
    assert "@(" in script and "& $HermesCommand" in script
    assert 'profile", "show"' in script
    assert "--clone" not in lowered
    assert "--yolo" not in lowered
    for setting in (
        "terminal.backend",
        "terminal.cwd",
        "terminal.home_mode",
        "memory.memory_enabled",
        "memory.user_profile_enabled",
        "memory.write_approval",
        "skills.write_approval",
        "model.provider",
        "model.default",
        "model.base_url",
        "agent.reasoning_effort",
    ):
        assert setting in script
    assert "Read-Host" not in script
    assert "-AsSecureString" not in script
    assert "model.api_key" not in script
    assert "ConfigureApiKey" not in script
    assert "credential configuration pending" in lowered
    assert "try" in lowered and "finally" in lowered
    assert "Remove-Item" not in script
    assert "Invoke-Expression" not in script
    assert "Start-Process" not in script
    assert "Profile ID" in script and "base URL" in script
    assert "manual recovery" in lowered
    assert '& $VerifyScript -HermesCommand $HermesCommand' in script
    assert MANIFEST_NAME in script
    assert 'Copy-Item -LiteralPath $SourceSkill -Destination $SkillDestinationRoot -Recurse' not in script
    for script_name in ("inspect_workbook.py", "run_task.py", "validate_output.py", "write_workbook.py"):
        assert script_name in script
    assert "assetHashes" in script and "defaultBaseline" in script
    assert "keyConfigured" in script
    assert 'ValidateSet("minimal", "low", "medium", "high", "xhigh", "max", "ultra")' in script
    assert "__pycache__" not in script


def test_verifier_is_read_only_and_checks_isolation() -> None:
    script = read(VERIFY_PATH)
    lowered = script.lower()

    assert '"etsy-performance-us"' in script
    assert "RunModelCheck" in script and "RunDoctor" in script
    assert "PROFILE_READY" in script
    assert '"--source", "tool"' in script
    assert '"--max-turns", "1"' in script
    for forbidden_state in (
        "MEMORY.md",
        "USER.md",
        "state.db",
        "sessions",
        "logs",
        "memories",
    ):
        assert forbidden_state in script
    assert "gateway" in lowered
    assert "BaselinePath" in script and "Get-FileHash" in script
    assert "ManifestPath" in script and MANIFEST_NAME in script
    assert "PSObject.Properties" in script
    assert "InitialProvision" in script
    for safe_model_field in (
        "model.provider",
        "model.default",
        "model.base_url",
        "agent.reasoning_effort",
    ):
        assert safe_model_field in script
    assert 'config", "get", "model.api_key"' not in script
    assert "__pycache__" in script and "*.pyc" in script
    assert "config get" not in lowered
    assert "config set" not in lowered
    assert "profile create" not in lowered
    assert "Copy-Item" not in script
    assert "New-Item" not in script
    assert "Remove-Item" not in script
    assert "Invoke-Expression" not in script
    assert "--yolo" not in lowered


def test_excel_scripts_disable_bytecode_before_dynamic_imports() -> None:
    scripts = SKILL_PATH.parent / "scripts"
    for name in ("inspect_workbook.py", "run_task.py", "validate_output.py", "write_workbook.py"):
        text = read(scripts / name)
        assert "sys.dont_write_bytecode = True" in text
        assert text.index("sys.dont_write_bytecode = True") < text.find("importlib", 0) if "importlib" in text else True


def test_verifier_reports_old_manifest_missing_script_hashes_without_strictmode_crash(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assetHashes"] = {
        key: value for key, value in manifest["assetHashes"].items() if "/scripts/" not in key
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "Employee asset hash missing" in combined
    assert "PropertyNotFoundStrict" not in combined
    assert "property" not in combined.casefold() or "missing" in combined.casefold()


@pytest.mark.parametrize("path", [PROVISION_PATH, VERIFY_PATH])
def test_powershell_scripts_parse(path: Path) -> None:
    command = (
        "$errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$null, [ref]$errors); "
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = subprocess.run(
        [powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_verifier_fails_safely_for_an_absent_profile(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(VERIFY_PATH),
            "-HermesHome",
            str(tmp_path),
            "-HermesCommand",
            "command-that-must-not-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Profile etsy-performance-us is absent" in (result.stdout + result.stderr)
    assert not any(tmp_path.iterdir())


def test_verifier_accepts_a_valid_isolated_fixture(tmp_path: Path) -> None:
    hermes_home, _, fake_hermes, values = create_verifier_fixture(tmp_path)
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 0, result.stdout + result.stderr


def test_verifier_rejects_exact_model_config_mismatch(tmp_path: Path) -> None:
    hermes_home, _, fake_hermes, values = create_verifier_fixture(tmp_path)
    values["model.default"] = "wrong-model"
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "model.default" in (result.stdout + result.stderr)


def test_verifier_rejects_manifest_with_unexpected_secret_field(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["apiKey"] = "must-not-be-stored"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "unexpected field" in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(
    "bad_url",
    ["http://relay.example/v1", "https://user:password@relay.example/v1"],
)
def test_verifier_rejects_insecure_or_credential_bearing_manifest_url(
    tmp_path: Path, bad_url: str
) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["baseUrl"] = bad_url
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    values["model.base_url"] = bad_url
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "base URL" in (result.stdout + result.stderr)


def test_verifier_rejects_altered_employee_asset(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    (profile_home / "SOUL.md").write_text("altered", encoding="utf-8")
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "asset hash" in (result.stdout + result.stderr).lower()


def test_verifier_rejects_additional_skill_content(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    extra_skill = profile_home / "skills" / "unrelated" / "SKILL.md"
    extra_skill.parent.mkdir()
    extra_skill.write_text("unrelated", encoding="utf-8")
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "additional skill" in (result.stdout + result.stderr).lower()


def test_verifier_rejects_nested_gateway_configuration(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    (profile_home / "config.yaml").write_text(
        "model:\n  api_key: placeholder-for-presence-test\n"
        "gateway:\n  telegram:\n    token: copied-value\n",
        encoding="utf-8",
    )
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "gateway or messaging-channel" in (result.stdout + result.stderr)


def test_verifier_allows_only_real_hermes_terminal_env_mirror(tmp_path: Path) -> None:
    hermes_home, _, fake_hermes, values = create_verifier_fixture(tmp_path)
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("assignment", ["OPENAI_API_KEY=copied-value", "TERMINAL_ENV=docker"])
def test_verifier_rejects_other_or_wrong_environment_assignment(
    tmp_path: Path, assignment: str
) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    (profile_home / ".env").write_text(f"{assignment}\n", encoding="utf-8")
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "environment assignment" in (result.stdout + result.stderr).lower()


def test_employee_owned_state_is_only_rejected_during_initial_provision(
    tmp_path: Path,
) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    memory_file = profile_home / "memories" / "employee-owned.md"
    memory_file.parent.mkdir()
    memory_file.write_text("employee-owned learning", encoding="utf-8")
    write_fake_hermes(fake_hermes, values)

    later_result = run_verifier(hermes_home, fake_hermes)
    initial_result = run_verifier(hermes_home, fake_hermes, initial_provision=True)

    assert later_result.returncode == 0, later_result.stdout + later_result.stderr
    assert initial_result.returncode == 1
    assert "initial memories" in (initial_result.stdout + initial_result.stderr).lower()


@pytest.mark.parametrize("state_name", ["MEMORY.md", "USER.md", "state.db"])
def test_later_employee_owned_root_state_after_manifest_is_allowed(
    tmp_path: Path, state_name: str
) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    (profile_home / state_name).write_text("employee-owned state", encoding="utf-8")
    write_fake_hermes(fake_hermes, values)

    later_result = run_verifier(hermes_home, fake_hermes)
    initial_result = run_verifier(hermes_home, fake_hermes, initial_provision=True)

    assert later_result.returncode == 0, later_result.stdout + later_result.stderr
    assert initial_result.returncode == 1
    assert state_name in (initial_result.stdout + initial_result.stderr)


def test_later_state_predating_manifest_is_rejected(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    state_file = profile_home / "state.db"
    state_file.write_text("copied old state", encoding="utf-8")
    timestamp_script = (
        f"$item = Get-Item -LiteralPath '{state_file}'; "
        "$past = [DateTime]::UtcNow.AddHours(-2); "
        "$item.CreationTimeUtc = $past; $item.LastWriteTimeUtc = $past"
    )
    subprocess.run(
        [powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", timestamp_script],
        check=True,
    )
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "predates" in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://relay.example/v1#fragment",
        "https://relay.example/v1?API_KEY=secret",
        "https://relay.example/v1?region=us",
    ],
)
def test_verifier_rejects_fragment_or_any_query_in_manifest_url(
    tmp_path: Path, bad_url: str
) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["baseUrl"] = bad_url
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    values["model.base_url"] = bad_url
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "base URL" in (result.stdout + result.stderr)


def test_structural_yaml_inspector_handles_quoted_nested_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'model:\n  "api_key": configured\n"gateway":\n  "telegram":\n    "token": copied\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["py", "-3.11", str(CONFIG_INSPECTOR_PATH), str(config_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {
        "model_api_key_present": True,
        "forbidden_paths": ["gateway.telegram.token"],
    }
    assert "copied" not in result.stdout
    assert "configured" not in result.stdout


def test_fake_real_hermes_provision_path_accepts_terminal_env_mirror(tmp_path: Path) -> None:
    local_app_data = tmp_path / "local-app-data"
    hermes_home = local_app_data / "hermes"
    profile_home = hermes_home / "profiles" / "etsy-performance-us"
    hermes_home.mkdir(parents=True)
    for name, content in {
        "SOUL.md": "default soul",
        "config.yaml": "default config",
        ".env": "# default placeholder",
    }.items():
        (hermes_home / name).write_text(content, encoding="utf-8")
    fake_hermes = tmp_path / "fake-hermes.ps1"
    command_log = tmp_path / "commands.log"
    values = {
        "terminal.backend": "local",
        "terminal.cwd": profile_home.joinpath("workspace").as_posix(),
        "terminal.home_mode": "profile",
        "memory.memory_enabled": "true",
        "memory.user_profile_enabled": "true",
        "memory.write_approval": "true",
        "skills.write_approval": "true",
        "model.provider": "custom",
        "model.default": "gpt-5.6-sol",
        "model.base_url": "https://relay.example/v1",
        "agent.reasoning_effort": "high",
    }
    write_fake_hermes(
        fake_hermes,
        values,
        provision_profile_home=profile_home,
        command_log=command_log,
    )
    environment = dict(os.environ)
    environment["LOCALAPPDATA"] = str(local_app_data)

    result = subprocess.run(
        [
            powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PROVISION_PATH),
            "-BaseUrl",
            "https://relay.example/v1",
            "-HermesCommand",
            str(fake_hermes),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "credential configuration pending" in (result.stdout + result.stderr).lower()
    assert "model.api_key" not in command_log.read_text(encoding="utf-8")


def test_fake_hermes_provision_accepts_codex_without_base_url_and_xhigh(
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "local-app-data"
    hermes_home = local_app_data / "hermes"
    profile_home = hermes_home / "profiles" / "etsy-performance-us"
    hermes_home.mkdir(parents=True)
    for name, content in {
        "SOUL.md": "default soul",
        "config.yaml": "default config",
        ".env": "# default placeholder",
    }.items():
        (hermes_home / name).write_text(content, encoding="utf-8")
    fake_hermes = tmp_path / "fake-hermes.ps1"
    command_log = tmp_path / "commands.log"
    values = {
        "terminal.backend": "local",
        "terminal.cwd": profile_home.joinpath("workspace").as_posix(),
        "terminal.home_mode": "profile",
        "memory.memory_enabled": "true",
        "memory.user_profile_enabled": "true",
        "memory.write_approval": "true",
        "skills.write_approval": "true",
        "model.provider": "openai-codex",
        "model.default": "gpt-5.6-sol",
        "agent.reasoning_effort": "xhigh",
    }
    write_fake_hermes(
        fake_hermes,
        values,
        provision_profile_home=profile_home,
        command_log=command_log,
    )
    environment = dict(os.environ)
    environment["LOCALAPPDATA"] = str(local_app_data)

    result = subprocess.run(
        [
            powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PROVISION_PATH),
            "-Provider",
            "codex",
            "-ReasoningEffort",
            "xhigh",
            "-HermesCommand",
            str(fake_hermes),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((profile_home / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
    assert manifest["provider"] == "openai-codex"
    assert manifest["hasBaseUrl"] is False
    assert manifest["baseUrl"] is None
    assert "config|set|model.base_url" not in command_log.read_text(encoding="utf-8")


def test_custom_provider_still_requires_base_url_before_profile_creation(
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "local-app-data"
    environment = dict(os.environ)
    environment["LOCALAPPDATA"] = str(local_app_data)

    result = subprocess.run(
        [
            powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PROVISION_PATH),
            "-Provider",
            "custom",
            "-HermesCommand",
            str(tmp_path / "must-not-run.ps1"),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode != 0
    assert "base URL" in (result.stdout + result.stderr)
    assert not (local_app_data / "hermes" / "profiles").exists()


def test_verifier_accepts_exactly_unset_base_url_for_codex(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"provider": "openai-codex", "baseUrl": None, "hasBaseUrl": False})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    values["model.provider"] = "openai-codex"
    values.pop("model.base_url")
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 0, result.stdout + result.stderr


def test_verifier_rejects_base_url_present_when_manifest_requires_absence(
    tmp_path: Path,
) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"provider": "openai-codex", "baseUrl": None, "hasBaseUrl": False})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    values["model.provider"] = "openai-codex"
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "model.base_url" in (result.stdout + result.stderr)


def test_verifier_rejects_custom_provider_manifest_without_base_url(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"baseUrl": None, "hasBaseUrl": False})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    values.pop("model.base_url")
    write_fake_hermes(fake_hermes, values)

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "custom provider" in (result.stdout + result.stderr).lower()


def test_verifier_does_not_treat_arbitrary_config_get_failure_as_unset(
    tmp_path: Path,
) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"provider": "openai-codex", "baseUrl": None, "hasBaseUrl": False})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    values["model.provider"] = "openai-codex"
    values.pop("model.base_url")
    write_fake_hermes(fake_hermes, values, missing_config_output="configuration unavailable")

    result = run_verifier(hermes_home, fake_hermes)

    assert result.returncode == 1
    assert "could not verify" in (result.stdout + result.stderr).lower()


def test_verifier_captures_expected_unset_from_native_stderr(tmp_path: Path) -> None:
    hermes_home, profile_home, _, values = create_verifier_fixture(tmp_path)
    manifest_path = profile_home / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {"provider": "openai-codex", "baseUrl": None, "hasBaseUrl": False}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    values["model.provider"] = "openai-codex"
    values.pop("model.base_url")

    delegate = tmp_path / "fake-hermes-delegate.ps1"
    write_fake_hermes(delegate, values, missing_config_to_stderr=True)
    native_wrapper = tmp_path / "fake-hermes.cmd"
    native_wrapper.write_text(
        "@echo off\n"
        f'powershell.exe -NoProfile -NonInteractive -File "{delegate}" %*\n'
        "exit /b %ERRORLEVEL%\n",
        encoding="utf-8",
    )

    result = run_verifier(hermes_home, native_wrapper)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NativeCommandError" not in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "bad_url",
    ["https://relay.example/v1#fragment", "https://relay.example/v1?region=us"],
)
def test_provisioner_rejects_noncanonical_url_before_profile_creation(
    tmp_path: Path, bad_url: str
) -> None:
    local_app_data = tmp_path / "local-app-data"
    environment = dict(os.environ)
    environment["LOCALAPPDATA"] = str(local_app_data)

    result = subprocess.run(
        [
            powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PROVISION_PATH),
            "-BaseUrl",
            bad_url,
            "-HermesCommand",
            str(tmp_path / "must-not-run.ps1"),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode != 0
    assert "base URL" in (result.stdout + result.stderr)
    assert not (local_app_data / "hermes" / "profiles").exists()


@pytest.mark.parametrize(
    ("doctor_output", "expected_code", "expected_text"),
    [
        ("All checks passed!", 0, "DOCTOR_CLEAN"),
        ("OpenRouter API (not configured)\nTelegram (optional, not configured)", 0, "DOCTOR_WARN"),
        ("Found 1 issue(s) to address:\n1. Invalid model/provider config", 1, "DOCTOR_CORE_FAILURE"),
        (
            "Found 1 issue(s) to address:\n1. model.provider 'bogus' is unknown",
            1,
            "DOCTOR_CORE_FAILURE",
        ),
        (
            "Found 1 issue(s) to address:\n1. model provider 'bogus' is unrecognized",
            1,
            "DOCTOR_CORE_FAILURE",
        ),
        (
            "Found 1 issue(s) to address:\n1. model provider 'bogus' is unrecognised",
            1,
            "DOCTOR_CORE_FAILURE",
        ),
        (
            "Found 1 issue(s) to address:\n1. Configuration migration required",
            1,
            "DOCTOR_CORE_FAILURE",
        ),
        (
            "Found 1 issue(s) to address:\n1. Required package 'llama-cpp-python' is missing",
            1,
            "DOCTOR_CORE_FAILURE",
        ),
    ],
)
def test_doctor_output_is_classified_without_false_pass(
    tmp_path: Path, doctor_output: str, expected_code: int, expected_text: str
) -> None:
    hermes_home, _, fake_hermes, values = create_verifier_fixture(tmp_path)
    write_fake_hermes(fake_hermes, values, doctor_output=doctor_output)

    result = run_verifier(hermes_home, fake_hermes, run_doctor=True)

    assert result.returncode == expected_code
    assert expected_text in (result.stdout + result.stderr)
    assert doctor_output not in (result.stdout + result.stderr)
