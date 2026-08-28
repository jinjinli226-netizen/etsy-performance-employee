[CmdletBinding()]
param(
    [string]$DataDirectory,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8765,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]
    [string]$ModelId = "gpt-5.6-sol",
    [ValidateSet("openai-codex")]
    [string]$Provider = "openai-codex",
    [ValidateSet("minimal", "low", "medium", "high", "xhigh", "max", "ultra")]
    [string]$ReasoningEffort = "high",
    [string]$HermesExecutable = "hermes",
    [string]$CodexExecutable = "codex",
    [string]$PythonLauncher = "py",
    [string]$NodeExecutable = "node",
    [string]$PnpmExecutable = "pnpm",
    [string]$UvExecutable = "uv",
    [switch]$NonInteractive,
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProfileId = "etsy-performance-us"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$FrontendPath = Join-Path $ProjectRoot "frontend"
$ProvisionScript = Join-Path $PSScriptRoot "provision-employee.ps1"
$VerifyScript = Join-Path $PSScriptRoot "verify-employee.ps1"
$ConfiguredStartScript = Join-Path $PSScriptRoot "start-configured.ps1"
$PowerShellPath = (Get-Process -Id $PID).Path

function Resolve-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$DisplayName
    )

    $resolved = Get-Command $Name -ErrorAction SilentlyContinue |
        Where-Object CommandType -In @("Application", "ExternalScript") |
        Select-Object -First 1
    if ($null -eq $resolved -or [string]::IsNullOrWhiteSpace($resolved.Source)) {
        throw "Missing prerequisite: $DisplayName. Install it and ensure the command is available on PATH."
    }
    return [IO.Path]::GetFullPath($resolved.Source)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $hadNativePreference = Test-Path Variable:PSNativeCommandUseErrorActionPreference
    if ($hadNativePreference) { $previousNativePreference = $PSNativeCommandUseErrorActionPreference }
    $pushed = $false
    try {
        $ErrorActionPreference = "Continue"
        if ($hadNativePreference) { $PSNativeCommandUseErrorActionPreference = $false }
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            Push-Location -LiteralPath $WorkingDirectory
            $pushed = $true
        }
        & $FilePath @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        if ($pushed) { Pop-Location }
        $ErrorActionPreference = $previousErrorActionPreference
        if ($hadNativePreference) { $PSNativeCommandUseErrorActionPreference = $previousNativePreference }
    }
    if ($exitCode -ne 0) {
        throw "$DisplayName failed with exit code $exitCode."
    }
}

function Invoke-CapturedStatus {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $hadNativePreference = Test-Path Variable:PSNativeCommandUseErrorActionPreference
    if ($hadNativePreference) { $previousNativePreference = $PSNativeCommandUseErrorActionPreference }
    try {
        $ErrorActionPreference = "Continue"
        if ($hadNativePreference) { $PSNativeCommandUseErrorActionPreference = $false }
        $lines = @(& $FilePath @Arguments 2>&1 | ForEach-Object { [string]$_ })
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        if ($hadNativePreference) { $PSNativeCommandUseErrorActionPreference = $previousNativePreference }
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Lines = $lines }
}

function Invoke-PowerShellScriptChecked {
    param(
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string[]]$Arguments = @()
    )

    $scriptArguments = @("-NoProfile", "-File", $ScriptPath) + @($Arguments)
    Invoke-Checked -DisplayName $DisplayName -FilePath $PowerShellPath -Arguments $scriptArguments
}

function Assert-SafeDataDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $canonical = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($canonical)
    if ([string]::IsNullOrWhiteSpace($root) -or $canonical.TrimEnd('\') -eq $root.TrimEnd('\')) {
        throw "DataDirectory must be a dedicated directory, not a drive root."
    }
    $cursor = $canonical
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
    return $canonical
}

if ($BackendPort -eq $FrontendPort) {
    throw "BackendPort and FrontendPort must be different."
}
foreach ($requiredFile in @($ProvisionScript, $VerifyScript, $ConfiguredStartScript)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required repository script is missing: $([IO.Path]::GetFileName($requiredFile))."
    }
}
if ([string]::IsNullOrWhiteSpace($DataDirectory)) {
    $DataDirectory = Join-Path $env:LOCALAPPDATA "etsy-performance-employee\data"
}
$DataDirectory = Assert-SafeDataDirectory -Path $DataDirectory

# Resolve and version-check every system prerequisite before dependency or Profile writes.
$GitPath = Resolve-RequiredCommand -Name "git" -DisplayName "Git for Windows"
$PythonPath = Resolve-RequiredCommand -Name $PythonLauncher -DisplayName "Python 3.11 launcher"
$NodePath = Resolve-RequiredCommand -Name $NodeExecutable -DisplayName "Node.js 24"
$PnpmPath = Resolve-RequiredCommand -Name $PnpmExecutable -DisplayName "pnpm 11"
$UvPath = Resolve-RequiredCommand -Name $UvExecutable -DisplayName "uv"
$HermesPath = Resolve-RequiredCommand -Name $HermesExecutable -DisplayName "Hermes CLI 0.18.2"
$CodexPath = Resolve-RequiredCommand -Name $CodexExecutable -DisplayName "Codex CLI"

Invoke-Checked -DisplayName "Git version check" -FilePath $GitPath -Arguments @("--version")
Invoke-Checked -DisplayName "Python 3.11 version check" -FilePath $PythonPath -Arguments @("-3.11", "--version")
Invoke-Checked -DisplayName "Node.js version check" -FilePath $NodePath -Arguments @("--version")
Invoke-Checked -DisplayName "pnpm version check" -FilePath $PnpmPath -Arguments @("--version")
Invoke-Checked -DisplayName "uv version check" -FilePath $UvPath -Arguments @("--version")
Invoke-Checked -DisplayName "Hermes version check" -FilePath $HermesPath -Arguments @("--version")
Invoke-Checked -DisplayName "Codex version check" -FilePath $CodexPath -Arguments @("--version")

Invoke-Checked -DisplayName "Backend locked dependency installation" -FilePath $UvPath -Arguments @(
    "sync", "--project", $BackendPath, "--extra", "dev", "--frozen"
)
Invoke-Checked -DisplayName "Frontend locked dependency installation" -FilePath $PnpmPath -Arguments @(
    "install", "--frozen-lockfile"
) -WorkingDirectory $ProjectRoot

$ProfilePath = Join-Path $env:LOCALAPPDATA "hermes\profiles\$ProfileId"
if (-not (Test-Path -LiteralPath $ProfilePath -PathType Container)) {
    Invoke-PowerShellScriptChecked -DisplayName "Hermes employee provisioning" -ScriptPath $ProvisionScript -Arguments @(
        "-Provider", $Provider,
        "-ModelId", $ModelId,
        "-ReasoningEffort", $ReasoningEffort,
        "-HermesCommand", $HermesPath
    )
}

$hermesStatus = Invoke-CapturedStatus -FilePath $HermesPath -Arguments @(
    "-p", $ProfileId, "auth", "status", $Provider
)
$hermesReady = $hermesStatus.ExitCode -eq 0 -and $hermesStatus.Lines.Count -ge 1 -and
    $hermesStatus.Lines[0].Trim() -ceq "${Provider}: logged in"
if (-not $hermesReady) {
    if ($NonInteractive) {
        throw "Hermes login is required. Run: hermes -p $ProfileId auth add $Provider --type oauth"
    }
    Invoke-Checked -DisplayName "Hermes OAuth login" -FilePath $HermesPath -Arguments @(
        "-p", $ProfileId, "auth", "add", $Provider, "--type", "oauth"
    )
    $hermesStatus = Invoke-CapturedStatus -FilePath $HermesPath -Arguments @(
        "-p", $ProfileId, "auth", "status", $Provider
    )
    if ($hermesStatus.ExitCode -ne 0 -or $hermesStatus.Lines.Count -lt 1 -or
        $hermesStatus.Lines[0].Trim() -cne "${Provider}: logged in") {
        throw "Hermes login did not reach the required logged-in state."
    }
}

$codexStatus = Invoke-CapturedStatus -FilePath $CodexPath -Arguments @("login", "status")
if ($codexStatus.ExitCode -ne 0) {
    if ($NonInteractive) {
        throw "Codex login is required. Run: codex login"
    }
    Invoke-Checked -DisplayName "Codex login" -FilePath $CodexPath -Arguments @("login")
    $codexStatus = Invoke-CapturedStatus -FilePath $CodexPath -Arguments @("login", "status")
    if ($codexStatus.ExitCode -ne 0) {
        throw "Codex login did not reach the required logged-in state."
    }
}

Invoke-PowerShellScriptChecked -DisplayName "Hermes employee verification" -ScriptPath $VerifyScript -Arguments @(
    "-HermesCommand", $HermesPath, "-RunModelCheck", "-RunDoctor"
)

Write-Host "Etsy employee configuration verified."
Write-Host "Profile: $ProfileId"
Write-Host "DataDirectory: $DataDirectory"
Write-Host "Website: http://127.0.0.1:$FrontendPort"
Write-Host "API: http://127.0.0.1:$BackendPort"
Write-Host "Excel model: $ModelId; row workers: 3"

$startArguments = @(
    "-DataDirectory", $DataDirectory,
    "-BackendPort", ([string]$BackendPort),
    "-FrontendPort", ([string]$FrontendPort),
    "-ModelId", $ModelId,
    "-HermesExecutable", $HermesPath
)
if ($Start) {
    Invoke-PowerShellScriptChecked -DisplayName "Configured website startup" -ScriptPath $ConfiguredStartScript -Arguments $startArguments
}
else {
    Write-Host "Next: .\scripts\start-configured.ps1 -DataDirectory `"$DataDirectory`" -BackendPort $BackendPort -FrontendPort $FrontendPort"
}
