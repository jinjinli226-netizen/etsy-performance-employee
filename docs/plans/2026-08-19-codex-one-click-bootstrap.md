# Codex One-Click Bootstrap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a README-driven, safe one-command Windows bootstrap that configures the repository, provisions or verifies the Hermes employee, completes the two login gates, verifies the runtime, and optionally starts the local website.

**Architecture:** Keep orchestration in a new `bootstrap-new-machine.ps1` and keep runtime environment selection in a small `start-configured.ps1` wrapper around the existing hardened `start.ps1`. The bootstrap fails closed on missing system tools, existing invalid Profiles, failed logins, failed model checks, or unsafe data paths; it never installs system software, reads credentials, or overwrites a Profile.

**Tech Stack:** Windows PowerShell 5.1/PowerShell 7, existing PowerShell provisioning/start scripts, pytest documentation and script-contract tests, GitHub Markdown.

---

### Task 1: Lock the configured launcher contract with failing tests

**Files:**
- Modify: `backend/tests/test_startup_docs.py`
- Create: `scripts/start-configured.ps1`

**Step 1: Write the failing tests**

Add constants:

```python
BOOTSTRAP = ROOT / "scripts" / "bootstrap-new-machine.ps1"
CONFIGURED_START = ROOT / "scripts" / "start-configured.ps1"
```

Add a test that reads `CONFIGURED_START` and requires:

```python
def test_configured_start_sets_verified_non_secret_runtime_and_delegates() -> None:
    script = read(CONFIGURED_START)
    assert "$env:ETSY_EMPLOYEE_MODEL_ENGINE = \"codex\"" in script
    assert "$env:ETSY_EMPLOYEE_CODEX_MODEL = $ModelId" in script
    assert "$env:ETSY_EMPLOYEE_ROW_WORKERS = \"3\"" in script
    assert "$env:ETSY_EMPLOYEE_HERMES_MAX_TURNS = \"30\"" in script
    assert "$env:ETSY_EMPLOYEE_EXCEL_WORKER_TIMEOUT_SECONDS = \"3600\"" in script
    assert "start.ps1" in script
    for forwarded in ("DataDirectory", "BackendPort", "FrontendPort", "HermesExecutable", "HermesHome"):
        assert forwarded in script
    assert "[switch]$Stop" in script
```

Extend the existing PowerShell parser test so it parses `CONFIGURED_START`.

**Step 2: Run the test to verify RED**

Run:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_startup_docs.py::test_configured_start_sets_verified_non_secret_runtime_and_delegates -q
```

Expected: FAIL because `scripts/start-configured.ps1` does not exist.

**Step 3: Implement the minimal configured launcher**

Create `scripts/start-configured.ps1` with strict mode and these parameters:

```powershell
[CmdletBinding()]
param(
    [string]$DataDirectory,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8765,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173,
    [string]$ModelId = "gpt-5.4",
    [string]$HermesExecutable = "hermes",
    [string]$HermesHome,
    [switch]$Stop
)
```

Set only the five non-secret environment variables from the design. Resolve `start.ps1` from `$PSScriptRoot`; assemble a PowerShell splat containing DataDirectory, ports, Hermes executable, optional HermesHome, and Stop; invoke the existing script and propagate a nonzero native/script exit.

**Step 4: Run the test to verify GREEN**

Run the single test, then all `test_startup_docs.py` tests. Expected: PASS.

**Step 5: Commit**

```powershell
git add backend/tests/test_startup_docs.py scripts/start-configured.ps1
git commit -m "feat: add configured production launcher"
```

### Task 2: Define the bootstrap safety contract with failing tests

**Files:**
- Modify: `backend/tests/test_startup_docs.py`
- Create: `scripts/bootstrap-new-machine.ps1`

**Step 1: Write the failing contract test**

Add `test_codex_bootstrap_is_bounded_idempotent_and_fail_closed`. It must require all of the following strings or structures in the bootstrap script:

```python
required = (
    "uv", "sync", "--extra", "dev", "--frozen",
    "pnpm", "install", "--frozen-lockfile",
    "provision-employee.ps1", "verify-employee.ps1",
    "-RunModelCheck", "-RunDoctor",
    "auth", "add", "openai-codex", "--type", "oauth",
    "login", "status", "start-configured.ps1",
    "NonInteractive", "Test-Path", "LASTEXITCODE",
)
```

Also assert forbidden operations are absent:

```python
for forbidden in (
    "winget", "Set-ExecutionPolicy", "Remove-Item $Profile",
    "Get-Content $Profile", "api_key", "access_token",
):
    assert forbidden.casefold() not in script.casefold()
```

Extend the PowerShell parser test to parse `BOOTSTRAP`.

**Step 2: Run the test to verify RED**

Run the single new test. Expected: FAIL because the bootstrap file does not exist.

**Step 3: Add a real missing-prerequisite behavior test**

Run the bootstrap through PowerShell with `-PythonLauncher` pointing to a nonexistent file, a temporary `LOCALAPPDATA`, `-NonInteractive`, and no `-Start`. Assert:

- exit code is nonzero;
- combined output names `Python 3.11`;
- no `hermes/profiles` directory was created.

This test proves prerequisites fail before any provisioning write.

**Step 4: Run the behavior test to verify RED**

Expected: FAIL because the bootstrap script does not exist.

### Task 3: Implement the safe bootstrap orchestrator

**Files:**
- Create: `scripts/bootstrap-new-machine.ps1`
- Test: `backend/tests/test_startup_docs.py`

**Step 1: Implement parameter and path validation**

Use this public parameter surface:

```powershell
[CmdletBinding()]
param(
    [string]$DataDirectory,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8765,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173,
    [string]$ModelId = "gpt-5.4",
    [ValidateSet("minimal", "low", "medium", "high", "xhigh", "max", "ultra")]
    [string]$ReasoningEffort = "high",
    [string]$HermesExecutable = "hermes",
    [string]$CodexExecutable = "codex",
    [string]$PythonLauncher = "py",
    [string]$NodeExecutable = "node",
    [string]$PnpmExecutable = "pnpm",
    [string]$UvExecutable = "uv",
    [switch]$NonInteractive,
    [switch]$Start
)
```

Resolve repository paths from `$PSScriptRoot`. Reject equal frontend/backend ports. Default DataDirectory to `%LOCALAPPDATA%\etsy-performance-employee\data`; reject a drive root and any existing reparse-point ancestor, matching `start.ps1` safety behavior.

**Step 2: Implement checked command helpers**

Add small functions:

- `Resolve-RequiredCommand`: resolve an application/script and throw `Missing prerequisite: <name>`;
- `Invoke-Checked`: invoke a command with an argument array and throw on `$LASTEXITCODE -ne 0`;
- `Invoke-CapturedStatus`: capture status output without returning or logging credential material, temporarily neutralizing PowerShell native error preference as existing scripts do.

Resolve all prerequisites before running dependency installation or provision. Verify Python using `py -3.11 --version`, and invoke Node, pnpm, uv, Hermes, and Codex version commands.

**Step 3: Install locked project dependencies**

Invoke:

```powershell
& $UvPath sync --project $BackendPath --extra dev --frozen
& $PnpmPath --dir $FrontendPath install --frozen-lockfile
```

Check the exit code after each command. Do not run an update command and do not change lock files.

**Step 4: Provision or verify without overwrite**

Compute the Profile path from `%LOCALAPPDATA%\hermes\profiles\etsy-performance-us`. If it does not exist, invoke:

```powershell
& $ProvisionScript -Provider openai-codex -ModelId $ModelId `
  -ReasoningEffort $ReasoningEffort -HermesCommand $HermesPath
```

If it exists, do not write to it. Continue to login status and the verifier; any mismatch fails closed.

**Step 5: Implement the two login gates**

Hermes readiness is true only when `hermes -p etsy-performance-us auth status openai-codex` exits zero and reports the exact logged-in state. Codex readiness is true only when `codex login status` exits zero.

When a gate is not ready:

- with `-NonInteractive`, throw an error naming the required login command;
- otherwise call the official login command in the visible terminal, then re-run status and fail if it is still not ready.

Never capture or print login response bodies beyond the safe status summary.

**Step 6: Run full Profile verification**

Invoke:

```powershell
& $VerifyScript -HermesCommand $HermesPath -RunModelCheck -RunDoctor
```

Stop on any nonzero exit. Only after this command succeeds, print a bounded summary containing Profile ID, DataDirectory, ports, model, and worker count; do not print config or credential values.

**Step 7: Optionally start the service**

If `-Start` is present, invoke `start-configured.ps1` with DataDirectory, ports, model, and Hermes executable. Otherwise print the exact next command.

**Step 8: Run tests to verify GREEN**

Run the contract test, behavior test, parser test, then all startup docs tests. Expected: PASS.

**Step 9: Commit**

```powershell
git add backend/tests/test_startup_docs.py scripts/bootstrap-new-machine.ps1
git commit -m "feat: add safe one-click employee bootstrap"
```

### Task 4: Make README the Codex deployment contract

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/HermesAgent配置指南.md`
- Modify: `backend/tests/test_startup_docs.py`

**Step 1: Write the failing README test**

Require a top-level “交给 Codex 一键配置” section containing:

- the single `bootstrap-new-machine.ps1` command with `-Start`;
- the automatic steps list;
- the two OAuth/login human gates;
- “不得读取或输出凭据文件”；
- the exact success checks for `/api/health`, `/api/employee/status`, and `/excel`;
- a link to the HermesAgent configuration guide and migration guide.

Run the test and verify it fails because the section is missing.

**Step 2: Update README and Hermes guide**

Place the Codex section near the top of README, before manual installation. Explain that the script will not install missing system programs or overwrite an existing invalid Profile. Replace duplicated manual environment/start commands in the Hermes guide with the wrapper command while retaining the detailed reference.

**Step 3: Run documentation tests**

Run `backend/tests/test_startup_docs.py -q`. Expected: PASS.

**Step 4: Commit**

```powershell
git add README.md docs/operations/HermesAgent配置指南.md backend/tests/test_startup_docs.py
git commit -m "docs: make README the Codex bootstrap entrypoint"
```

### Task 5: Full verification, secret scan, and GitHub delivery

**Files:**
- Verify only; no planned production edits

**Step 1: Run backend tests**

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Expected: zero failures.

**Step 2: Build frontend**

```powershell
pnpm --dir frontend build
```

Expected: exit code 0.

**Step 3: Verify current employee Profile**

```powershell
.\scripts\verify-employee.ps1
```

Expected: `Employee Profile verification passed.` No login mutation is performed.

**Step 4: Run repository hygiene checks**

```powershell
git diff --check
git status --short
```

Scan tracked changes for credential patterns while reporting file names only. Confirm no user workbooks, Profile files, `.env`, tokens, or runtime data are staged.

**Step 5: Push and verify remote identity**

```powershell
git push origin master
git ls-remote origin refs/heads/master
git rev-parse HEAD
```

Expected: local and remote commit hashes match and the working tree is clean.
