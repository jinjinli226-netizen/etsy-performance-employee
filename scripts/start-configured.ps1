[CmdletBinding()]
param(
    [string]$DataDirectory,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8765,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]
    [string]$ModelId = "gpt-5.6-sol",
    [string]$HermesExecutable = "hermes",
    [string]$HermesHome,
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$startScript = Join-Path $PSScriptRoot "start.ps1"
if (-not (Test-Path -LiteralPath $startScript -PathType Leaf)) {
    throw "The production start script is missing."
}

$env:ETSY_EMPLOYEE_MODEL_ENGINE = "codex"
$env:ETSY_EMPLOYEE_CODEX_MODEL = $ModelId
$env:ETSY_EMPLOYEE_ROW_WORKERS = "2"
$env:ETSY_EMPLOYEE_HERMES_MAX_TURNS = "30"
$env:ETSY_EMPLOYEE_EXCEL_WORKER_TIMEOUT_SECONDS = "4800"

$startParameters = @{
    BackendPort = $BackendPort
    FrontendPort = $FrontendPort
    HermesExecutable = $HermesExecutable
}
if (-not [string]::IsNullOrWhiteSpace($DataDirectory)) {
    $startParameters.DataDirectory = $DataDirectory
}
if (-not [string]::IsNullOrWhiteSpace($HermesHome)) {
    $startParameters.HermesHome = $HermesHome
}
if ($Stop) {
    $startParameters.Stop = $true
}

& $startScript @startParameters
