$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Start-Process -FilePath "powershell" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "dev.ps1") -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru

function Test-LocalPort {
    param([int]$Port)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect("127.0.0.1", $Port)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

try {
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline -and -not ((Test-LocalPort 8765) -and (Test-LocalPort 5173))) {
        Start-Sleep -Milliseconds 250
    }

    if (-not ((Test-LocalPort 8765) -and (Test-LocalPort 5173))) {
        throw "Local services did not bind both expected ports."
    }
}
finally {
    if (-not $launcher.HasExited) {
        $metadataPath = Join-Path $PSScriptRoot ".dev-pids.json"
        if (Test-Path $metadataPath) {
            $metadata = Get-Content -Raw $metadataPath | ConvertFrom-Json
            $backend = Get-CimInstance Win32_Process -Filter "ProcessId = $($metadata.services.backend.pid)" -ErrorAction SilentlyContinue
            if ($null -ne $backend) {
                Invoke-CimMethod -InputObject $backend -MethodName Terminate | Out-Null
            }
        }
        $launcher.WaitForExit(10000)
    }
}

Start-Sleep -Milliseconds 500
if ((Test-LocalPort 8765) -or (Test-LocalPort 5173)) {
    throw "A project-owned dev port remained open after launcher cleanup."
}

if (Test-Path (Join-Path $PSScriptRoot ".dev-pids.json")) {
    throw "PID metadata remained after launcher cleanup."
}

Write-Output "Lifecycle cleanup verified: ports 8765 and 5173 are closed."
