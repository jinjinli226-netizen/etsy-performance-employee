[CmdletBinding()]
param(
    [string]$Workbook,
    [string]$DataDirectory,
    [ValidateRange(15, 25)][double]$Delay = 20,
    [ValidateRange(1, 2147483647)][int]$Limit = 1,
    [string]$Shop,
    [switch]$Batch,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host @"
Image + Listing training

Usage:
  .\scripts\train-vision-listings.ps1 -Workbook <shops.xlsx>
  .\scripts\train-vision-listings.ps1 -Workbook <shops.xlsx> -Limit 5
  .\scripts\train-vision-listings.ps1 -Workbook <shops.xlsx> -Batch

The safe default trains one eligible Listing. -Batch must be explicit to remove the limit.
The source workbook is read only; evidence and training state are stored under DataDirectory.
"@
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Workbook)) {
    throw "Workbook is required."
}
if ($Batch -and $PSBoundParameters.ContainsKey("Limit")) {
    throw "Batch and Limit cannot be used together."
}

$SourcePath = [IO.Path]::GetFullPath($Workbook)
if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw "Workbook must be an existing file."
}
if (-not [string]::Equals([IO.Path]::GetExtension($SourcePath), ".xlsx", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Workbook must be an .xlsx file."
}

if ([string]::IsNullOrWhiteSpace($DataDirectory)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is unavailable; specify DataDirectory."
    }
    $DataDirectory = Join-Path $env:LOCALAPPDATA "etsy-performance-employee\data"
}
$CanonicalDataDirectory = [IO.Path]::GetFullPath($DataDirectory)
$DataRoot = [IO.Path]::GetPathRoot($CanonicalDataDirectory)
if ([string]::Equals($CanonicalDataDirectory.TrimEnd('\'), $DataRoot.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw "DataDirectory must be a dedicated directory, not a drive root."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$UvCommand = Get-Command "uv" -ErrorAction Stop
$Arguments = @(
    "run",
    "python",
    "-m",
    "app.training.cli",
    "--workbook",
    $SourcePath,
    "--data-directory",
    $CanonicalDataDirectory,
    "--delay",
    ([string]::Format([Globalization.CultureInfo]::InvariantCulture, "{0}", $Delay))
)
if ($Batch) {
    $Arguments += "--batch"
}
else {
    $Arguments += @("--limit", ([string]$Limit))
}
if (-not [string]::IsNullOrWhiteSpace($Shop)) {
    $Arguments += @("--shop", $Shop)
}

Push-Location -LiteralPath $BackendPath
try {
    & $UvCommand.Source @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
