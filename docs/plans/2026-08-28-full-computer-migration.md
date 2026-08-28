# Full Computer Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and verify a safe, one-command full-data export and restore workflow for moving the Etsy employee to another Windows computer that will use a newly authenticated official OpenAI account.

**Architecture:** Add a small shared PowerShell inventory/safety library plus thin export and restore entrypoints. Export stops verified services, copies only durable business data, creates a Git bundle, and publishes a hash manifest; restore verifies everything before writing to an empty target and then delegates official-account setup to the existing bootstrap. Contract tests execute the pure helpers against temporary directories and inspect the orchestration scripts and Chinese documentation.

**Tech Stack:** Windows PowerShell 5.1/PowerShell 7, Git Bundle, robocopy, SHA-256, JSON, pytest, existing Hermes/Codex bootstrap scripts.

---

### Task 1: Add failing full-migration contract tests

**Files:**
- Create: `backend/tests/test_full_migration_scripts.py`
- Modify: `backend/tests/test_startup_docs.py`

**Step 1: Define script paths and parsing coverage**

Add constants for:

```python
FULL_MIGRATION_MODULE = ROOT / "scripts" / "FullMigration.psm1"
EXPORT_FULL = ROOT / "scripts" / "export-full-migration.ps1"
RESTORE_FULL = ROOT / "scripts" / "restore-full-migration.ps1"
```

Extend the PowerShell parse test to include both entrypoints and require the module file to exist.

**Step 2: Add red contract assertions**

Cover these observable contracts:

```python
def test_full_export_is_stopped_hash_verified_and_secret_excluding() -> None:
    script = read(EXPORT_FULL)
    for required in (
        "git bundle create", "git bundle verify", "robocopy", "Get-MigrationInventory",
        "runtime", "browser-profile", "migration-workspace", "migration-packages",
        "start-configured.ps1", "-Stop", "migration-manifest.json", "RESTORE-README.md",
    ):
        assert required in script


def test_full_restore_verifies_before_copy_and_uses_official_login() -> None:
    script = read(RESTORE_FULL)
    assert script.index("Test-MigrationManifest") < script.index("robocopy")
    assert "openai-codex" in script
    assert "gpt-5.6-sol" in script
    assert "bootstrap-new-machine.ps1" in script
    assert "DataDirectory must be empty" in script
```

Add subprocess tests that import the module and verify safe relative-path normalization, forbidden path rejection, manifest tamper rejection, and empty-target enforcement using only `tmp_path` fixtures.

**Step 3: Run the tests and confirm RED**

Run:

```powershell
uv run --project backend pytest -q backend/tests/test_full_migration_scripts.py backend/tests/test_startup_docs.py
```

Expected: failures because the module and full-migration scripts do not exist and bootstrap/docs still use the older model defaults.

**Step 4: Commit tests**

```powershell
git add backend/tests/test_full_migration_scripts.py backend/tests/test_startup_docs.py
git commit -m "test: define full migration contracts"
```

### Task 2: Implement the shared inventory and safety module

**Files:**
- Create: `scripts/FullMigration.psm1`
- Test: `backend/tests/test_full_migration_scripts.py`

**Step 1: Implement safe path resolution**

Export functions that:

- canonicalize local paths with `[IO.Path]::GetFullPath`;
- reject drive roots, UNC paths, paths under OneDrive, and any existing reparse-point ancestor;
- reject relative paths that are rooted, contain `..`, contain `:` or normalize outside the package root;
- require a destination to be missing or empty.

**Step 2: Implement deterministic inventory generation**

`Get-MigrationInventory -Root <path>` returns sorted objects:

```powershell
[pscustomobject]@{
    path = $relative.Replace('\', '/')
    size_bytes = [int64]$item.Length
    sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
```

It rejects reparse points and forbidden exact path segments such as `runtime`, `browser-profile`, `migration-workspace`, `migration-packages`, `.env`, `.start-pids.json`, `migration-capability`, `auth.json`, and credential/token files.

**Step 3: Implement manifest validation**

`Test-MigrationManifest` validates schema version `1`, canonical relative paths, uniqueness, sizes, hashes, total bytes, bundle hash, and the absence of forbidden paths. It returns normalized manifest metadata and throws before any destination write.

**Step 4: Run focused tests and confirm GREEN**

```powershell
uv run --project backend pytest -q backend/tests/test_full_migration_scripts.py
```

Expected: all module helper tests pass.

**Step 5: Commit**

```powershell
git add scripts/FullMigration.psm1 backend/tests/test_full_migration_scripts.py
git commit -m "feat: add full migration inventory safeguards"
```

### Task 3: Implement stopped full-data export

**Files:**
- Create: `scripts/export-full-migration.ps1`
- Test: `backend/tests/test_full_migration_scripts.py`

**Step 1: Add bounded parameters and preflight**

Parameters include `DataDirectory`, `OutputDirectory`, `BackendPort=8766`, `FrontendPort=5173`, and `ProjectRoot`. Require a clean worktree, a new output directory, sufficient free space, valid data files, and no active Excel jobs. When the API port is listening but health or job enumeration fails, stop with an error rather than guessing.

**Step 2: Stop services and copy durable data**

Call:

```powershell
& $ConfiguredStart -DataDirectory $dataRoot -BackendPort $BackendPort -FrontendPort $FrontendPort -Stop
```

Use a newly created sibling partial directory and:

```powershell
robocopy $dataRoot $dataTarget /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 `
    /XD $runtime $browserProfile $migrationWorkspace $migrationPackages
```

Treat robocopy exit codes above `7` as failures. Never restart the source automatically.

**Step 3: Create repository bundle and manifest**

Create `repository.bundle` with `git bundle create --all`, verify it, inventory `data-full`, hash the bundle, record source commit/branch and category counts, and write `migration-manifest.json` atomically. Generate `RESTORE-README.md` from a fixed non-secret here-string.

**Step 4: Publish atomically**

Re-run forbidden-path and manifest validation against the partial directory, then rename it to the requested final directory. On failure, remove only the exact partial directory created by this process.

**Step 5: Run focused tests**

```powershell
uv run --project backend pytest -q backend/tests/test_full_migration_scripts.py
```

Expected: export orchestration contracts pass.

**Step 6: Commit**

```powershell
git add scripts/export-full-migration.ps1 backend/tests/test_full_migration_scripts.py
git commit -m "feat: export stopped full employee data"
```

### Task 4: Implement verified restore and official-account bootstrap

**Files:**
- Create: `scripts/restore-full-migration.ps1`
- Modify: `scripts/bootstrap-new-machine.ps1`
- Modify: `scripts/start-configured.ps1`
- Modify: `backend/tests/test_full_migration_scripts.py`
- Modify: `backend/tests/test_startup_docs.py`

**Step 1: Update official defaults**

Change default model values from `gpt-5.4` to `gpt-5.6-sol`. Keep the migration bootstrap provider explicitly `openai-codex`; do not copy or activate a relay URL. Keep Hermes OAuth and `codex login` interactive unless `-NonInteractive` is used, in which case missing login must fail closed.

**Step 2: Verify package before destination creation**

Restore reads `migration-manifest.json`, calls `Test-MigrationManifest` against `data-full` and `repository.bundle`, and verifies that the checked-out repository commit matches the source commit contained in the bundle. Only then may it create or use an empty destination.

**Step 3: Copy and verify the destination**

Copy `data-full` with robocopy, generate a fresh destination inventory, and compare every path, size, and hash with the source manifest. Confirm forbidden runtime and credential paths are absent.

**Step 4: Delegate environment and official login setup**

Call `bootstrap-new-machine.ps1` with the restored `DataDirectory`, selected ports, `-ModelId gpt-5.6-sol`, and optional `-Start`. The bootstrap provisions `openai-codex`, opens Hermes OAuth and Codex login in interactive mode, requires model and Doctor checks, and never reports success when the real model marker fails.

**Step 5: Run focused tests**

```powershell
uv run --project backend pytest -q backend/tests/test_full_migration_scripts.py backend/tests/test_startup_docs.py
```

Expected: restore, bootstrap, parsing, and official-default tests pass.

**Step 6: Commit**

```powershell
git add scripts/restore-full-migration.ps1 scripts/bootstrap-new-machine.ps1 scripts/start-configured.ps1 backend/tests/test_full_migration_scripts.py backend/tests/test_startup_docs.py
git commit -m "feat: restore full migration with official auth"
```

### Task 5: Make README the one-document handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/网站与数字员工部署迁移指南.md`
- Modify: `docs/operations/HermesAgent配置指南.md`
- Modify: `docs/superpowers/specs/2026-08-28-full-computer-migration-design.md`
- Test: `backend/tests/test_startup_docs.py`

**Step 1: Update the root handoff**

Put the full migration commands near the top:

```powershell
.\scripts\export-full-migration.ps1 `
  -DataDirectory "$env:LOCALAPPDATA\etsy-performance-employee\data" `
  -OutputDirectory 'E:\EtsyEmployeeFullMigration'
```

On the target: clone the included bundle, then run `restore-full-migration.ps1`. State plainly that old relay settings and credentials are excluded and that the user must authorize a new official account.

**Step 2: Replace stale release facts**

Update old commit/date/model/row-count claims, port examples, `gpt-5.4`, row-worker count, timeout guidance, and the claim that no Git remote exists. Preserve the distinction between portable ZIP and full clone.

**Step 3: Document optional LAN exposure safely**

Explain that target IPv4 changes. LAN forwarding/firewall setup on port `5174` is optional, requires administrator approval, and is never enabled silently by restore.

**Step 4: Update documentation tests and run them**

```powershell
uv run --project backend pytest -q backend/tests/test_startup_docs.py
```

Expected: all documentation contracts pass without replacement characters.

**Step 5: Commit**

```powershell
git add README.md docs/operations backend/tests/test_startup_docs.py docs/superpowers/specs/2026-08-28-full-computer-migration-design.md
git commit -m "docs: add one-document full migration handoff"
```

### Task 6: Full verification, merge, and real package creation

**Files:**
- No additional source files expected.

**Step 1: Parse and lint changed files**

```powershell
git diff --check master...HEAD
```

Run PowerShell parser checks for the module and all changed scripts.

**Step 2: Run complete automated verification**

```powershell
uv run --project backend pytest -q backend/tests
pnpm --dir frontend exec vitest run
pnpm --dir frontend run build
```

Expected: no test failures and production frontend build exits `0`.

**Step 3: Merge the feature branch into `master` and push**

Fast-forward merge only after the main checkout is clean. Re-run focused migration tests on the merged result, then push `master`.

**Step 4: Generate the real migration directory**

Choose an output location with sufficient free space, verify there are no active Excel jobs, and run the export script against the production `DataDirectory`. The export intentionally stops the production backend/frontend and leaves the LAN proxy with no upstream until the source is explicitly restarted.

**Step 5: Verify the generated package without restoring over live data**

Run the module manifest validator against the finished package, run `git bundle verify`, compare category counts, and confirm forbidden paths are absent. Do not run restore into the production data directory.

**Step 6: Hand off**

Report the exact migration directory, total bytes, manifest hash, source commit, official-login requirement, and the single target-computer README command. Mention that the current relay outage is irrelevant because the target uses a new official account.

