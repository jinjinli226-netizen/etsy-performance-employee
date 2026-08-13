[CmdletBinding()]
param([string]$DataDirectory)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($DataDirectory)) { $DataDirectory = Join-Path $projectRoot ".runtime-data" }
$env:ETSY_EMPLOYEE_DATA_DIR = [IO.Path]::GetFullPath($DataDirectory)
$pidFile = Join-Path $PSScriptRoot ".dev-pids.json"
$backendPath = Join-Path $projectRoot "backend"
$frontendPath = Join-Path $projectRoot "frontend"
$pythonPath = (& py -3.11 -c "import sys; print(sys.executable)").Trim()
$nodePath = (Get-Command node -ErrorAction Stop).Source
$vitePath = Join-Path $frontendPath "node_modules\vite\bin\vite.js"

function Get-ProcessInfo {
    param([int]$ProcessId)

    Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
}

function Test-RecordedProcess {
    param($Record)

    $process = Get-ProcessInfo -ProcessId $Record.pid
    if ($null -eq $process) { return $false }

    return $process.CreationDate.ToUniversalTime().Ticks -eq [int64]$Record.creationTimeUtcTicks -and
        $process.ExecutablePath -eq $Record.executablePath -and
        $process.CommandLine -like "*$($Record.commandMarker)*"
}

function Get-ProcessTreeIds {
    param([int]$RootProcessId)

    $allProcesses = Get-CimInstance Win32_Process
    $descendants = [System.Collections.Generic.List[int]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($RootProcessId)

    while ($queue.Count -gt 0) {
        $parentId = $queue.Dequeue()
        foreach ($child in @($allProcesses | Where-Object ParentProcessId -eq $parentId)) {
            $descendants.Add([int]$child.ProcessId)
            $queue.Enqueue([int]$child.ProcessId)
        }
    }

    return $descendants
}

function Stop-RecordedProcessTree {
    param($Record)

    if (-not (Test-RecordedProcess -Record $Record)) {
        return
    }

    $treeIds = Get-ProcessTreeIds -RootProcessId $Record.pid
    [array]::Reverse($treeIds)
    foreach ($processId in $treeIds) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $Record.pid -ErrorAction SilentlyContinue
}

function Stop-RecordedServices {
    param($Metadata)

    foreach ($service in @($Metadata.services.backend, $Metadata.services.frontend)) {
        if ($null -ne $service) {
            Stop-RecordedProcessTree -Record $service
        }
    }
}

function New-ProcessRecord {
    param($Process, [int]$Port, [string]$CommandMarker)

    $deadline = (Get-Date).AddSeconds(5)
    do {
        $info = Get-ProcessInfo -ProcessId $Process.Id
        if ($null -ne $info -and $null -ne $info.CommandLine) { break }
        Start-Sleep -Milliseconds 50
    } while ((Get-Date) -lt $deadline)

    if ($null -eq $info) {
        throw "Could not inspect started process $($Process.Id)."
    }

    [pscustomobject]@{
        pid = $Process.Id
        port = $Port
        creationTimeUtcTicks = $info.CreationDate.ToUniversalTime().Ticks
        executablePath = $info.ExecutablePath
        commandMarker = $CommandMarker
    }
}

function Test-LocalPort {
    param([int]$Port)

    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return $null -ne $listener
}

function Test-RecordedServicePort {
    param([int]$Port, $Record)

    if (-not (Test-RecordedProcess -Record $Record)) { return $false }
    $ownedIds = @(Get-ProcessTreeIds -RootProcessId $Record.pid) + $Record.pid
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    return $null -ne ($listeners | Where-Object { $ownedIds -contains $_.OwningProcess })
}

function Wait-ForServicePort {
    param([int]$Port, $Record)

    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-RecordedProcess -Record $Record)) {
            throw "Service process for port $Port exited before becoming ready."
        }
        if (Test-RecordedServicePort -Port $Port -Record $Record) { return }
        Start-Sleep -Milliseconds 200
    }
    throw "Service process for port $Port did not bind before the deadline."
}

if (Test-Path $pidFile) {
    $previous = Get-Content -Raw $pidFile | ConvertFrom-Json
    Stop-RecordedServices -Metadata $previous
    Remove-Item -LiteralPath $pidFile -Force
}

$metadata = [pscustomobject]@{ projectRoot = $projectRoot; services = [pscustomobject]@{} }
try {
    $backend = Start-Process -FilePath $pythonPath -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", $backendPath, "--host", "127.0.0.1", "--port", "8765" -WorkingDirectory $backendPath -WindowStyle Hidden -PassThru
    $metadata.services | Add-Member -NotePropertyName backend -NotePropertyValue (New-ProcessRecord -Process $backend -Port 8765 -CommandMarker $projectRoot)

    $frontend = Start-Process -FilePath $nodePath -ArgumentList $vitePath, "--host", "127.0.0.1", "--port", "5173" -WorkingDirectory $frontendPath -WindowStyle Hidden -PassThru
    $metadata.services | Add-Member -NotePropertyName frontend -NotePropertyValue (New-ProcessRecord -Process $frontend -Port 5173 -CommandMarker $projectRoot)

    $metadata | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding utf8
    Wait-ForServicePort -Port 8765 -Record $metadata.services.backend
    Wait-ForServicePort -Port 5173 -Record $metadata.services.frontend

    while ($true) {
        Start-Sleep -Milliseconds 250
        foreach ($service in @($metadata.services.backend, $metadata.services.frontend)) {
            if (-not (Test-RecordedProcess -Record $service)) {
                throw "A local dev service exited; stopping its verified companion."
            }
            if (-not (Test-RecordedServicePort -Port $service.port -Record $service)) {
                throw "A local dev service port is no longer owned by its verified process tree."
            }
        }
    }
}
finally {
    Stop-RecordedServices -Metadata $metadata
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
