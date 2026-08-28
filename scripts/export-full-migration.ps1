[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8766,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$modulePath = Join-Path $PSScriptRoot "FullMigration.psm1"
$configuredStart = Join-Path $PSScriptRoot "start-configured.ps1"
foreach ($required in @($modulePath, $configuredStart)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required migration file is missing: $required" }
}
Import-Module $modulePath -Force

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory
    )
    $previous = $ErrorActionPreference
    $hadNative = Test-Path Variable:PSNativeCommandUseErrorActionPreference
    if ($hadNative) { $previousNative = $PSNativeCommandUseErrorActionPreference }
    $pushed = $false
    try {
        $ErrorActionPreference = "Continue"
        if ($hadNative) { $PSNativeCommandUseErrorActionPreference = $false }
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            Push-Location -LiteralPath $WorkingDirectory
            $pushed = $true
        }
        & $FilePath @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        if ($pushed) { Pop-Location }
        $ErrorActionPreference = $previous
        if ($hadNative) { $PSNativeCommandUseErrorActionPreference = $previousNative }
    }
    if ($exitCode -ne 0) { throw "$DisplayName failed with exit code $exitCode." }
}

function Test-PortListening {
    param([Parameter(Mandatory = $true)][int]$Port)
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Assert-NoActiveExcelJobs {
    param([Parameter(Mandatory = $true)][int]$Port)

    $apiBase = "http://127.0.0.1:$Port"
    try {
        $health = Invoke-RestMethod -Uri "$apiBase/api/health" -TimeoutSec 5
        if ([string]$health.status -cne "ok") { throw "Local API health response was not ready." }
    }
    catch {
        if (Test-PortListening -Port $Port) {
            throw "Backend port $Port is listening but the local API health check failed. Stop and investigate before export."
        }
        Write-Host "Local API is already stopped; active-job API check was not available."
        return
    }

    $offset = 0
    $active = @()
    do {
        $page = Invoke-RestMethod -Uri "$apiBase/api/excel-jobs?limit=100&offset=$offset" -TimeoutSec 10
        foreach ($job in @($page.items)) {
            if ([string]$job.status -in @("queued", "running", "needs_review")) { $active += $job }
        }
        $offset += [int]$page.limit
    } while ($offset -lt [int]$page.total)
    if ($active.Count -gt 0) {
        $names = @($active | ForEach-Object { [string]$_.source_filename }) -join ", "
        throw "Full migration export is blocked while Excel jobs are active or awaiting review: $names"
    }
}

function Get-IncludedSourceBytes {
    param([Parameter(Mandatory = $true)][string]$Root)
    $excluded = @("runtime", "browser-profile", "migration-workspace", "migration-packages")
    $total = [int64]0
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -File -Force -Recurse)) {
        $relative = $item.FullName.Substring($Root.TrimEnd('\').Length + 1).Replace('\', '/')
        $first = $relative.Split('/')[0].ToLowerInvariant()
        if ($first -notin $excluded) { $total += [int64]$item.Length }
    }
    return $total
}

$project = Resolve-MigrationLocalPath -Path $ProjectRoot -MustExist
$dataRoot = Resolve-MigrationLocalPath -Path $DataDirectory -MustExist
$target = Resolve-MigrationLocalPath -Path $OutputDirectory
if (-not (Test-Path -LiteralPath (Join-Path $dataRoot "app.db") -PathType Leaf)) {
    throw "DataDirectory does not contain app.db."
}
if (Test-Path -LiteralPath $target) { throw "OutputDirectory must be a new path." }
$dataPrefix = $dataRoot.TrimEnd('\') + '\'
$targetPrefix = $target.TrimEnd('\') + '\'
if ($targetPrefix.StartsWith($dataPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $dataPrefix.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory and DataDirectory must not contain each other."
}

$git = (Get-Command git -ErrorAction Stop).Source
$robocopy = (Get-Command robocopy -ErrorAction Stop).Source
$status = @(& $git -C $project status --porcelain 2>&1)
if ($LASTEXITCODE -ne 0) { throw "Git status check failed." }
if ($status.Count -gt 0) { throw "The Git worktree must be clean before full migration export." }
$commit = (& $git -C $project rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw "Git commit could not be resolved." }
$branch = (& $git -C $project branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) { throw "Full migration export requires a named Git branch." }

$parent = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Path $parent) }
$parent = Resolve-MigrationLocalPath -Path $parent -MustExist
$sourceBytes = Get-IncludedSourceBytes -Root $dataRoot
$safetyBytes = [Math]::Max(1GB, [int64][Math]::Ceiling($sourceBytes * 0.15))
$driveName = [IO.Path]::GetPathRoot($parent).TrimEnd('\').TrimEnd(':')
$freeBytes = [int64](Get-PSDrive -Name $driveName -ErrorAction Stop).Free
if ($freeBytes -lt ($sourceBytes + $safetyBytes)) {
    throw "Output drive does not have enough free space for the full migration and safety margin."
}

Assert-NoActiveExcelJobs -Port $BackendPort
& $configuredStart -DataDirectory $dataRoot -BackendPort $BackendPort -FrontendPort $FrontendPort -Stop
if ($LASTEXITCODE -ne 0) { throw "Verified service shutdown failed." }

$partial = Join-Path $parent ((Split-Path -Leaf $target) + ".partial-" + [Guid]::NewGuid().ToString("N"))
$createdPartial = $false
try {
    [void](New-Item -ItemType Directory -Path $partial)
    $createdPartial = $true
    $dataTarget = Join-Path $partial "data-full"
    [void](New-Item -ItemType Directory -Path $dataTarget)

    $runtime = Join-Path $dataRoot "runtime"
    $browserProfile = Join-Path $dataRoot "browser-profile"
    $migrationWorkspace = Join-Path $dataRoot "migration-workspace"
    $migrationPackages = Join-Path $dataRoot "migration-packages"
    & $robocopy $dataRoot $dataTarget /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XD $runtime $browserProfile $migrationWorkspace $migrationPackages
    $copyExit = $LASTEXITCODE
    if ($copyExit -gt 7) { throw "robocopy failed with exit code $copyExit." }

    $bundlePath = Join-Path $partial "repository.bundle"
    Write-Host "Running git bundle create and git bundle verify."
    Invoke-NativeChecked -DisplayName "git bundle create" -FilePath $git -Arguments @("bundle", "create", $bundlePath, "--all") -WorkingDirectory $project
    Invoke-NativeChecked -DisplayName "git bundle verify" -FilePath $git -Arguments @("bundle", "verify", $bundlePath) -WorkingDirectory $project

    $inventory = @(Get-MigrationInventory -Root $dataTarget)
    $totalBytes = [int64]0
    foreach ($entry in $inventory) { $totalBytes += [int64]$entry.size_bytes }
    $bundle = Get-Item -LiteralPath $bundlePath -Force
    $manifest = [ordered]@{
        schema_version = 1
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        source = [ordered]@{ git_commit = $commit; git_branch = $branch }
        data = [ordered]@{
            total_bytes = $totalBytes
            files = $inventory
            category_counts = Get-MigrationCategoryCounts -Inventory $inventory
        }
        repository_bundle = [ordered]@{
            path = "repository.bundle"
            size_bytes = [int64]$bundle.Length
            sha256 = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        target_auth = [ordered]@{
            provider = "openai-codex"
            model = "gpt-5.6-sol"
            credentials_included = $false
        }
    }
    Write-MigrationJsonAtomic -Value $manifest -Path (Join-Path $partial "migration-manifest.json") -Depth 10

    $restoreReadme = @'
# Restore this Etsy employee on another Windows computer

1. Install Git, Python 3.11, Node.js, pnpm, uv, Hermes CLI, and Codex CLI.
2. Clone `repository.bundle` and check out `master`.
3. From the cloned repository run:

```powershell
.\scripts\restore-full-migration.ps1 -PackageDirectory '<this folder>' -DataDirectory 'D:\EtsyEmployeeData' -Start
```

The restore validates every file before copying. Old relay settings and credentials are not included. You must authorize the new official account when Hermes OAuth and `codex login` open.
'@
    [IO.File]::WriteAllText((Join-Path $partial "RESTORE-README.md"), $restoreReadme, [Text.UTF8Encoding]::new($false))
    [void](Test-MigrationManifest -PackageDirectory $partial)
    Move-Item -LiteralPath $partial -Destination $target
    $createdPartial = $false
}
finally {
    if ($createdPartial -and (Test-Path -LiteralPath $partial)) {
        Remove-Item -LiteralPath $partial -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Full migration export completed."
Write-Host "Package: $target"
Write-Host "Source commit: $commit"
Write-Host "Durable data bytes: $sourceBytes"
Write-Host "Source services remain stopped."
