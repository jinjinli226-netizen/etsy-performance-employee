[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:8765",
    [string]$OutputPath = (Join-Path $PWD "etsy-performance-us.zip")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$uri = $null
if (-not [Uri]::TryCreate($ApiBase, [UriKind]::Absolute, [ref]$uri) -or
    $uri.Scheme -ne "http" -or $uri.Host -notin @("127.0.0.1", "localhost") -or
    -not [string]::IsNullOrEmpty($uri.UserInfo)) {
    throw "ApiBase must be a local HTTP URL without embedded credentials."
}
$target = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($target) -ne ".zip") {
    throw "OutputPath must use the .zip extension."
}
if (Test-Path -LiteralPath $target) {
    throw "OutputPath already exists; refusing to overwrite it."
}
$capability = [Environment]::GetEnvironmentVariable("ETSY_EMPLOYEE_MIGRATION_CAPABILITY", "Process")
if ([string]::IsNullOrWhiteSpace($capability)) {
    throw "Set ETSY_EMPLOYEE_MIGRATION_CAPABILITY in this process from the local application startup output."
}
$headers = @{ "X-Migration-Capability" = $capability }
$response = Invoke-RestMethod -Method Post -Uri ($uri.AbsoluteUri.TrimEnd("/") + "/api/migration/exports") -Headers $headers
$download = $uri.AbsoluteUri.TrimEnd("/") + "/api/migration/exports/" + [Uri]::EscapeDataString([string]$response.filename)
Invoke-WebRequest -Method Get -Uri $download -Headers $headers -OutFile $target
Write-Host "Employee migration package written to $target. Model credentials were not included."
