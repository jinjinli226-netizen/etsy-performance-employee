[CmdletBinding()]
param(
    [string]$ManifestPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$E2ERoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".e2e-data"))
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = Join-Path $E2ERoot "e2e-run-manifest.json"
}
$ManifestPath = [IO.Path]::GetFullPath($ManifestPath)
if ([IO.Path]::GetDirectoryName($ManifestPath) -cne $E2ERoot -or
    [IO.Path]::GetFileName($ManifestPath) -cne "e2e-run-manifest.json") {
    throw "The E2E manifest path is outside the owned test root."
}
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    Write-Host "No owned E2E run manifest was found."
    exit 0
}
if (Get-NetTCPConnection -State Listen -LocalPort 58765 -ErrorAction SilentlyContinue) {
    throw "The E2E backend is still listening; refusing cleanup."
}

$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($Manifest.schemaVersion -ne 1 -or [string]::IsNullOrWhiteSpace([string]$Manifest.runDirectory)) {
    throw "The E2E run manifest is invalid."
}
$RunDirectory = [IO.Path]::GetFullPath([string]$Manifest.runDirectory)
if ([IO.Path]::GetDirectoryName($RunDirectory) -cne $E2ERoot -or
    [IO.Path]::GetFileName($RunDirectory) -notmatch '^run-[0-9a-f]{32}$') {
    throw "The E2E run directory is not an owned unique run."
}
if (Test-Path -LiteralPath $RunDirectory) {
    $OwnershipMarker = Join-Path $RunDirectory ".owned-e2e-run"
    if (-not (Test-Path -LiteralPath $OwnershipMarker -PathType Leaf) -or
        (Get-Content -LiteralPath $OwnershipMarker -Raw -Encoding utf8).Trim() -cne [IO.Path]::GetFileName($RunDirectory)) {
        throw "The E2E run ownership marker is invalid."
    }
    Get-ChildItem -LiteralPath $RunDirectory -Recurse -Force -File | ForEach-Object { $_.IsReadOnly = $false }
    Remove-Item -LiteralPath $RunDirectory -Recurse -Force
}
Remove-Item -LiteralPath $ManifestPath -Force
Write-Host "Owned E2E run data removed."
