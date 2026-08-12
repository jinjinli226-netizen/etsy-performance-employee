from __future__ import annotations

import hashlib
import json
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
PROVISION_PATH = REPOSITORY_ROOT / "scripts" / "provision-employee.ps1"
VERIFY_PATH = REPOSITORY_ROOT / "scripts" / "verify-employee.ps1"
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
    for relative_path, destination in profile_assets.items():
        shutil.copyfile(source_assets[relative_path], destination)

    (profile_home / ".env").write_text(
        "# Per-profile secrets belong in config.yaml when explicitly approved.\n",
        encoding="utf-8",
    )
    (profile_home / "config.yaml").write_text(
        "model:\n  api_key: placeholder-for-presence-test\ngateway: {}\n",
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
        "reasoningEffort": values["agent.reasoning_effort"],
        "workspace": values["terminal.cwd"],
        "keyConfigured": True,
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


def write_fake_hermes(path: Path, values: dict[str, str]) -> None:
    escaped_values = ((key, value.replace("'", "''")) for key, value in values.items())
    literal_values = "\n".join(
        f"    '{key}' = '{escaped_value}'" for key, escaped_value in escaped_values
    )
    path.write_text(
        "$Values = @{\n"
        f"{literal_values}\n"
        "}\n"
        "$Key = $args[$args.Count - 1]\n"
        "if ($Values.ContainsKey($Key)) { Write-Output $Values[$Key]; exit 0 }\n"
        "exit 9\n",
        encoding="utf-8-sig",
    )


def run_verifier(
    hermes_home: Path, fake_hermes: Path, *, initial_provision: bool = False
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
    assert "未来工具契约" in skill
    assert "当前不存在" in skill
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
    assert "Read-Host" in script and "-AsSecureString" in script
    assert "model.api_key" in script
    assert "try" in lowered and "finally" in lowered
    assert "Remove-Item" not in script
    assert "Invoke-Expression" not in script
    assert "Start-Process" not in script
    assert "Profile ID" in script and "base URL" in script
    assert "manual recovery" in lowered
    assert '& $VerifyScript -HermesCommand $HermesCommand' in script
    assert MANIFEST_NAME in script
    assert "assetHashes" in script and "defaultBaseline" in script
    assert "keyConfigured" in script


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
    assert "InitialProvision" in script
    for safe_model_field in (
        "model.provider",
        "model.default",
        "model.base_url",
        "agent.reasoning_effort",
    ):
        assert safe_model_field in script
    assert 'config", "get", "model.api_key"' not in script
    assert "config get" not in lowered
    assert "config set" not in lowered
    assert "profile create" not in lowered
    assert "Copy-Item" not in script
    assert "New-Item" not in script
    assert "Remove-Item" not in script
    assert "Invoke-Expression" not in script
    assert "--yolo" not in lowered


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


def test_verifier_rejects_any_environment_assignment(tmp_path: Path) -> None:
    hermes_home, profile_home, fake_hermes, values = create_verifier_fixture(tmp_path)
    (profile_home / ".env").write_text("OPENAI_API_KEY=copied-value\n", encoding="utf-8")
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
