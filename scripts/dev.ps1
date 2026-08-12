$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $PSScriptRoot ".dev-pids.json"
$backendPath = Join-Path $projectRoot "backend"
$frontendPath = Join-Path $projectRoot "frontend"

function Stop-ChildProcess {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
    }
}

if (Test-Path $pidFile) {
    $previous = Get-Content -Raw $pidFile | ConvertFrom-Json
    foreach ($processId in @($previous.backend, $previous.frontend)) {
        if ($null -ne $processId) {
            Stop-ChildProcess -ProcessId $processId
        }
    }
    Remove-Item -LiteralPath $pidFile -Force
}

$backend = Start-Process -FilePath "py" -ArgumentList "-3.11", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765" -WorkingDirectory $backendPath -WindowStyle Hidden -PassThru
$frontend = Start-Process -FilePath "pnpm" -ArgumentList "dev", "--host", "127.0.0.1", "--port", "5173" -WorkingDirectory $frontendPath -WindowStyle Hidden -PassThru

@{ backend = $backend.Id; frontend = $frontend.Id } | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

try {
    Wait-Process -Id $backend.Id, $frontend.Id
}
finally {
    Stop-ChildProcess -ProcessId $backend.Id
    Stop-ChildProcess -ProcessId $frontend.Id
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
