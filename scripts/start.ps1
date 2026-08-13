[CmdletBinding()]
param(
    [string]$DataDirectory,
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$FrontendPath = Join-Path $ProjectRoot "frontend"
$VitePath = Join-Path $FrontendPath "node_modules\vite\bin\vite.js"
$ProfileId = "etsy-performance-us"
$ProviderId = "openai-codex"
$BackendPort = 8765
$FrontendPort = 5173

if ([string]::IsNullOrWhiteSpace($DataDirectory)) {
    $DataDirectory = Join-Path $env:LOCALAPPDATA "etsy-performance-employee\data"
}
$DataDirectory = [IO.Path]::GetFullPath($DataDirectory)

function Assert-SafeDataDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $root = [IO.Path]::GetPathRoot($Path)
    if ([string]::IsNullOrWhiteSpace($root) -or $Path.TrimEnd('\') -eq $root.TrimEnd('\')) {
        throw "DataDirectory must be a dedicated directory, not a drive root."
    }
    $cursor = $Path
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "DataDirectory must not contain links or reparse points."
            }
        }
        if ($cursor.TrimEnd('\') -eq $root.TrimEnd('\')) { break }
        $cursor = Split-Path -Parent $cursor
    }
}

Assert-SafeDataDirectory -Path $DataDirectory
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
$RuntimePath = Join-Path $DataDirectory "runtime"
New-Item -ItemType Directory -Path $RuntimePath -Force | Out-Null
$PidFile = Join-Path $RuntimePath ".start-pids.json"
$env:ETSY_EMPLOYEE_DATA_DIR = $DataDirectory

function Get-ProcessInfo {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
}

function Test-RecordedProcess {
    param([Parameter(Mandatory = $true)]$Record)

    if ($null -eq $Record.pid -or $null -eq $Record.creationTimeUtcTicks -or
        $null -eq $Record.executablePath -or $null -eq $Record.commandMarker) {
        return $false
    }
    $process = Get-ProcessInfo -ProcessId ([int]$Record.pid)
    if ($null -eq $process -or $null -eq $process.CreationDate -or $null -eq $process.CommandLine) {
        return $false
    }
    return $process.CreationDate.ToUniversalTime().Ticks -eq [int64]$Record.creationTimeUtcTicks -and
        [string]::Equals($process.ExecutablePath, [string]$Record.executablePath, [StringComparison]::OrdinalIgnoreCase) -and
        $process.CommandLine.Contains([string]$Record.commandMarker)
}

function Get-ProcessTreeIds {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    $allProcesses = @(Get-CimInstance Win32_Process)
    $descendants = [Collections.Generic.List[int]]::new()
    $queue = [Collections.Generic.Queue[int]]::new()
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
    param([Parameter(Mandatory = $true)]$Record)

    if (-not (Test-RecordedProcess -Record $Record)) {
        throw "Recorded service identity no longer matches; refusing to stop an unowned process."
    }
    $treeIds = @(Get-ProcessTreeIds -RootProcessId ([int]$Record.pid))
    [array]::Reverse($treeIds)
    foreach ($processId in $treeIds) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
    Stop-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
}

function Stop-UnrecordedStartedProcess {
    param(
        $Process,
        [Parameter(Mandatory = $true)][string]$ExpectedExecutable,
        [Parameter(Mandatory = $true)][string]$CommandMarker
    )

    if ($null -eq $Process -or $Process.HasExited) { return }
    $info = Get-ProcessInfo -ProcessId $Process.Id
    if ($null -eq $info -or $null -eq $info.CommandLine -or
        -not [string]::Equals([string]$info.ExecutablePath, $ExpectedExecutable, [StringComparison]::OrdinalIgnoreCase) -or
        -not $info.CommandLine.Contains($CommandMarker)) {
        Write-Warning "An unrecorded startup process could not be verified and was not stopped."
        return
    }
    $treeIds = @(Get-ProcessTreeIds -RootProcessId $Process.Id)
    [array]::Reverse($treeIds)
    foreach ($processId in $treeIds) { Stop-Process -Id $processId -ErrorAction SilentlyContinue }
    Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
}

function Read-ProcessMetadata {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return $null }
    $item = Get-Item -LiteralPath $PidFile -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -gt 65536) {
        throw "The service metadata file is unsafe."
    }
    try {
        $metadata = Get-Content -LiteralPath $PidFile -Raw -Encoding utf8 | ConvertFrom-Json
    }
    catch {
        throw "The service metadata file is invalid; refusing an unverified stop."
    }
    if ($metadata.schemaVersion -ne 1 -or
        -not [string]::Equals([string]$metadata.projectRoot, $ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([string]$metadata.dataDirectory, $DataDirectory, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Recorded metadata does not belong to this project or data directory."
    }
    $backendRecord = $metadata.services.backend
    $frontendRecord = $metadata.services.frontend
    if ($null -eq $backendRecord -or $null -eq $frontendRecord -or
        $backendRecord.role -cne "backend" -or $frontendRecord.role -cne "frontend" -or
        $backendRecord.port -ne $BackendPort -or $frontendRecord.port -ne $FrontendPort -or
        $backendRecord.commandMarker -cne $BackendPath -or $frontendRecord.commandMarker -cne $VitePath -or
        [IO.Path]::GetFileName([string]$backendRecord.executablePath) -notin @("python.exe", "python3.exe") -or
        [IO.Path]::GetFileName([string]$frontendRecord.executablePath) -cne "node.exe") {
        throw "Recorded service metadata is not an expected backend/frontend pair."
    }
    return $metadata
}

function Stop-RecordedServices {
    param($Metadata)

    if ($null -eq $Metadata) { return }
    $records = @($Metadata.services.PSObject.Properties | ForEach-Object Value) |
        Where-Object { $null -ne $_ }
    $liveRecords = @($records | Where-Object { $null -ne (Get-ProcessInfo -ProcessId ([int]$_.pid)) })
    foreach ($record in $liveRecords) {
        if (-not (Test-RecordedProcess -Record $record)) {
            throw "A recorded PID belongs to another process; no service was stopped."
        }
    }
    foreach ($record in $liveRecords) {
        Stop-RecordedProcessTree -Record $record
    }
}

function Test-PortInUse {
    param([Parameter(Mandatory = $true)][int]$Port)
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Test-RecordedServicePort {
    param([Parameter(Mandatory = $true)][int]$Port, [Parameter(Mandatory = $true)]$Record)

    if (-not (Test-RecordedProcess -Record $Record)) { return $false }
    $ownedIds = @(Get-ProcessTreeIds -RootProcessId ([int]$Record.pid)) + [int]$Record.pid
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    return $null -ne ($listeners | Where-Object { $ownedIds -contains $_.OwningProcess })
}

function New-ProcessRecord {
    param($Process, [int]$Port, [string]$CommandMarker, [string]$Role)

    $deadline = (Get-Date).AddSeconds(5)
    do {
        $info = Get-ProcessInfo -ProcessId $Process.Id
        if ($null -ne $info -and $null -ne $info.CommandLine) { break }
        Start-Sleep -Milliseconds 50
    } while ((Get-Date) -lt $deadline)
    if ($null -eq $info -or -not $info.CommandLine.Contains($CommandMarker)) {
        throw "Could not verify the started service process."
    }
    return [pscustomobject]@{
        pid = $Process.Id
        port = $Port
        role = $Role
        creationTimeUtcTicks = $info.CreationDate.ToUniversalTime().Ticks
        executablePath = $info.ExecutablePath
        commandMarker = $CommandMarker
    }
}

function Wait-ForServicePort {
    param([int]$Port, $Record)

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-RecordedProcess -Record $Record)) {
            throw "Service process for port $Port exited before becoming ready."
        }
        if (Test-RecordedServicePort -Port $Port -Record $Record) { return }
        Start-Sleep -Milliseconds 200
    }
    throw "Service process for port $Port did not bind before the deadline."
}

function Invoke-HermesCredentialStatus {
    param([Parameter(Mandatory = $true)][string]$HermesCommand)

    $arguments = @("-p", $ProfileId, "auth", "status", $ProviderId)
    $previousErrorActionPreference = $ErrorActionPreference
    $hadNativePreference = Test-Path Variable:PSNativeCommandUseErrorActionPreference
    if ($hadNativePreference) { $previousNativePreference = $PSNativeCommandUseErrorActionPreference }
    try {
        $ErrorActionPreference = "Continue"
        if ($hadNativePreference) { $PSNativeCommandUseErrorActionPreference = $false }
        $lines = @(& $HermesCommand @arguments 2>&1 | ForEach-Object { [string]$_ })
        $exitCode = $LASTEXITCODE
        return $exitCode -eq 0 -and $lines.Count -ge 1 -and $lines[0].Trim() -ceq "${ProviderId}: logged in"
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        if ($hadNativePreference) { $PSNativeCommandUseErrorActionPreference = $previousNativePreference }
    }
}

function Invoke-ProfileVerifier {
    param(
        [Parameter(Mandatory = $true)][string]$PowerShellCommand,
        [Parameter(Mandatory = $true)][string]$VerifyScript,
        [Parameter(Mandatory = $true)][string]$HermesCommand
    )

    $stdoutPath = Join-Path $RuntimePath (".preflight-stdout-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $stderrPath = Join-Path $RuntimePath (".preflight-stderr-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $process = Start-Process -FilePath $PowerShellCommand -ArgumentList @(
            "-NoProfile", "-NonInteractive", "-File", ('"' + $VerifyScript + '"'),
            "-HermesCommand", ('"' + $HermesCommand + '"')
        ) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -Wait -PassThru
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -LiteralPath $stdoutPath -Raw -Encoding utf8 } else { "" }
            Stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw -Encoding utf8 } else { "" }
        }
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

$previousMetadata = Read-ProcessMetadata
if ($Stop) {
    Stop-RecordedServices -Metadata $previousMetadata
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Etsy Performance Employee services stopped."
    exit 0
}
if ($null -ne $previousMetadata) {
    Stop-RecordedServices -Metadata $previousMetadata
    Remove-Item -LiteralPath $PidFile -Force
}
if ((Test-PortInUse -Port $BackendPort) -or (Test-PortInUse -Port $FrontendPort)) {
    throw "Port 8765 or 5173 is already in use; no process was stopped."
}

$HermesPath = (Get-Command hermes -ErrorAction Stop).Source
$NodePath = (Get-Command node -ErrorAction Stop).Source
$PnpmPath = (Get-Command pnpm -ErrorAction Stop).Source
$PowerShellPath = (Get-Command powershell -ErrorAction Stop).Source
if ([IO.Path]::GetFileName($PowerShellPath) -notin @("powershell.exe", "pwsh.exe")) {
    throw "A trusted PowerShell executable could not be resolved."
}
$PythonPath = (& py -3.11 -c "import sys; print(sys.executable)").Trim()
if ([string]::IsNullOrWhiteSpace($PythonPath)) { throw "Python 3.11 is unavailable." }
if (-not (Test-Path -LiteralPath $VitePath -PathType Leaf)) {
    throw "Frontend dependencies are missing. Run pnpm install in the frontend directory."
}

$verifyScript = Join-Path $PSScriptRoot "verify-employee.ps1"
$verifyResult = Invoke-ProfileVerifier -PowerShellCommand $PowerShellPath -VerifyScript $verifyScript -HermesCommand $HermesPath
if ($verifyResult.ExitCode -ne 0) {
    if (-not [string]::IsNullOrWhiteSpace($verifyResult.Stdout)) { Write-Host $verifyResult.Stdout.Trim() }
    if (-not [string]::IsNullOrWhiteSpace($verifyResult.Stderr)) { [Console]::Error.WriteLine($verifyResult.Stderr.Trim()) }
    throw "The Hermes employee Profile verification failed. No service was started."
}
if (-not [string]::IsNullOrWhiteSpace($verifyResult.Stdout)) { Write-Host $verifyResult.Stdout.Trim() }
if (-not (Invoke-HermesCredentialStatus -HermesCommand $HermesPath)) {
    throw "Hermes credential is not ready. Run: hermes -p etsy-performance-us auth add openai-codex --type oauth"
}

& $PythonPath -c "import fastapi, openpyxl, sqlalchemy, uvicorn"
if ($LASTEXITCODE -ne 0) { throw "Backend dependencies are missing. Install backend[dev] first." }
& $PnpmPath --dir $FrontendPath run build
if ($LASTEXITCODE -ne 0) { throw "The frontend production build failed." }

$metadata = [pscustomobject]@{
    schemaVersion = 1
    projectRoot = $ProjectRoot
    dataDirectory = $DataDirectory
    services = [pscustomobject]@{}
}
$startedBackend = $null
$startedFrontend = $null
try {
    $startedBackend = Start-Process -FilePath $PythonPath -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", $BackendPath, "--host", "127.0.0.1", "--port", "8765" -WorkingDirectory $BackendPath -WindowStyle Hidden -PassThru
    $metadata.services | Add-Member -NotePropertyName backend -NotePropertyValue (New-ProcessRecord -Process $startedBackend -Port $BackendPort -CommandMarker $BackendPath -Role "backend")

    $startedFrontend = Start-Process -FilePath $NodePath -ArgumentList $VitePath, "preview", "--host", "127.0.0.1", "--port", "5173", "--strictPort" -WorkingDirectory $FrontendPath -WindowStyle Hidden -PassThru
    $metadata.services | Add-Member -NotePropertyName frontend -NotePropertyValue (New-ProcessRecord -Process $startedFrontend -Port $FrontendPort -CommandMarker $VitePath -Role "frontend")

    $temporaryPidFile = Join-Path $RuntimePath (".start-pids-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $metadata | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryPidFile -Encoding utf8
    Move-Item -LiteralPath $temporaryPidFile -Destination $PidFile

    Wait-ForServicePort -Port $BackendPort -Record $metadata.services.backend
    Wait-ForServicePort -Port $FrontendPort -Record $metadata.services.frontend
    Write-Host "Etsy Performance Employee is running at http://127.0.0.1:5173"
    Write-Host "Press Ctrl+C to stop only these verified service processes."

    while ($true) {
        Start-Sleep -Milliseconds 500
        foreach ($service in @($metadata.services.backend, $metadata.services.frontend)) {
            if (-not (Test-RecordedProcess -Record $service) -or
                -not (Test-RecordedServicePort -Port ([int]$service.port) -Record $service)) {
                throw "A service exited or no longer owns its recorded port."
            }
        }
    }
}
finally {
    try { Stop-RecordedServices -Metadata $metadata } catch { Write-Warning $_.Exception.Message }
    if ($metadata.services.PSObject.Properties.Name -notcontains "backend") {
        Stop-UnrecordedStartedProcess -Process $startedBackend -ExpectedExecutable $PythonPath -CommandMarker $BackendPath
    }
    if ($metadata.services.PSObject.Properties.Name -notcontains "frontend") {
        Stop-UnrecordedStartedProcess -Process $startedFrontend -ExpectedExecutable $NodePath -CommandMarker $VitePath
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    if (Test-Path Variable:temporaryPidFile) {
        Remove-Item -LiteralPath $temporaryPidFile -Force -ErrorAction SilentlyContinue
    }
}
