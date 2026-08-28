[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageDirectory,
    [Parameter(Mandatory = $true)][string]$DataDirectory,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8766,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$NonInteractive,
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$modulePath = Join-Path $PSScriptRoot "FullMigration.psm1"
$bootstrap = Join-Path $PSScriptRoot "bootstrap-new-machine.ps1"
foreach ($required in @($modulePath, $bootstrap)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required restore file is missing: $required" }
}
Import-Module $modulePath -Force

$packageRoot = Resolve-MigrationLocalPath -Path $PackageDirectory -MustExist
$project = Resolve-MigrationLocalPath -Path $ProjectRoot -MustExist

# Package verification is deliberately completed before any target directory write.
$manifest = Test-MigrationManifest -PackageDirectory $packageRoot
$target = Assert-EmptyMigrationTarget -Path $DataDirectory
if (Test-Path -LiteralPath $target) {
    if (@(Get-ChildItem -LiteralPath $target -Force).Count -ne 0) { throw "DataDirectory must be empty before restore." }
}

$git = (Get-Command git -ErrorAction Stop).Source
$currentCommit = (& $git -C $project rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $currentCommit -cne [string]$manifest.source.git_commit) {
    throw "The checked-out repository commit does not match the migration package. Clone repository.bundle and check out its master branch first."
}

$profilePath = Join-Path $env:LOCALAPPDATA "hermes\profiles\etsy-performance-us"
if (Test-Path -LiteralPath $profilePath) {
    throw "The target Hermes Profile already exists. Back it up and move it aside before full restore."
}

if (-not (Test-Path -LiteralPath $target)) { [void](New-Item -ItemType Directory -Path $target) }
$sourceData = Join-Path $packageRoot "data-full"
$robocopy = (Get-Command robocopy -ErrorAction Stop).Source
& $robocopy $sourceData $target /E /COPY:DAT /DCOPY:DAT /R:2 /W:1
$copyExit = $LASTEXITCODE
if ($copyExit -gt 7) { throw "robocopy restore failed with exit code $copyExit." }

$actualInventory = @(Get-MigrationInventory -Root $target)
[void](Compare-MigrationInventories -Expected @($manifest.data.files) -Actual $actualInventory)
foreach ($forbidden in @("runtime", "browser-profile", "migration-workspace", "migration-packages")) {
    if (Test-Path -LiteralPath (Join-Path $target $forbidden)) {
        throw "Restored DataDirectory contains a forbidden transient or identity-bearing path."
    }
}

$arguments = @{
    DataDirectory = $target
    BackendPort = $BackendPort
    FrontendPort = $FrontendPort
    Provider = "openai-codex"
    ModelId = "gpt-5.6-sol"
}
if ($NonInteractive) { $arguments.NonInteractive = $true }
if ($Start) { $arguments.Start = $true }

& $bootstrap @arguments
if ($LASTEXITCODE -ne 0) { throw "Official account bootstrap failed." }

Write-Host "Full migration restore completed and destination hashes match."
Write-Host "DataDirectory: $target"
Write-Host "Provider: openai-codex; model: gpt-5.6-sol"
if (-not $Start) {
    Write-Host "Next: .\scripts\start-configured.ps1 -DataDirectory `"$target`" -BackendPort $BackendPort -FrontendPort $FrontendPort"
}
