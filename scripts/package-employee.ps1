[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:8765",
    [string]$DataDirectory = (Join-Path $HOME ".etsy-performance-employee"),
    [string]$OutputPath = (Join-Path $PWD "etsy-performance-us.zip")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$uri = $null
if (-not [Uri]::TryCreate($ApiBase, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne "http" -or $uri.Host -notin @("127.0.0.1", "localhost") -or -not [string]::IsNullOrEmpty($uri.UserInfo)) {
    throw "ApiBase must be a local HTTP URL without embedded credentials."
}
$target = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($target) -ne ".zip" -or (Test-Path -LiteralPath $target)) {
    throw "OutputPath must be a new .zip path."
}
$capabilityPath = Join-Path ([IO.Path]::GetFullPath($DataDirectory)) "runtime\migration-capability"
if (-not (Test-Path -LiteralPath $capabilityPath -PathType Leaf)) {
    throw "The local migration capability file is unavailable. Start the application first."
}
$capability = (Get-Content -LiteralPath $capabilityPath -Raw -Encoding ascii).Trim()
if ($capability.Length -lt 32) { throw "The local migration capability file is invalid." }
$headers = @{ "X-Migration-Capability" = $capability }
$temporary = Join-Path ([IO.Path]::GetDirectoryName($target)) (".migration-download-" + [Guid]::NewGuid().ToString("N") + ".tmp")
try {
    $response = Invoke-RestMethod -Method Post -Uri ($uri.AbsoluteUri.TrimEnd("/") + "/api/migration/exports") -Headers $headers
    $download = $uri.AbsoluteUri.TrimEnd("/") + "/api/migration/exports/" + [Uri]::EscapeDataString([string]$response.filename)
    Invoke-WebRequest -Method Get -Uri $download -Headers $headers -OutFile $temporary
    if ((Get-Item -LiteralPath $temporary).Length -ne [int64]$response.size_bytes) { throw "Downloaded package size mismatch." }
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() -ne ([string]$response.file_sha256).ToLowerInvariant()) { throw "Downloaded package checksum mismatch." }
    $magic = [IO.File]::ReadAllBytes($temporary)[0..3]
    if (-not ($magic[0] -eq 0x50 -and $magic[1] -eq 0x4b -and $magic[2] -eq 0x03 -and $magic[3] -eq 0x04)) { throw "Downloaded file is not a ZIP package." }
    Move-Item -LiteralPath $temporary -Destination $target
}
finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}
Write-Host "Employee migration package created. Model credentials were not included."
