from __future__ import annotations

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

FIXED_HEADERS = (
    "head titles",
    "13 tags",
    "SPECIFICATION",
    "Category",
    "Instructions for buyers",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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
    for safe_model_field in (
        "model.provider",
        "model.default",
        "model.base_url",
        "agent.reasoning_effort",
    ):
        assert safe_model_field in script
    assert "model.api_key" not in script
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
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not available")

    command = (
        "$errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$null, [ref]$errors); "
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_verifier_fails_safely_for_an_absent_profile(tmp_path: Path) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not available")

    result = subprocess.run(
        [
            powershell,
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
