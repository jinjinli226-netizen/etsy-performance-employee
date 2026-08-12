[CmdletBinding()]
param(
    [switch]$RunModelCheck,
    [switch]$RunDoctor,
    [string]$BaselinePath,
    [string]$HermesCommand = "hermes",
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA "hermes")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProfileId = "etsy-performance-us"
$ProfileHome = Join-Path $HermesHome "profiles\$ProfileId"
$ExpectedWorkspace = (Join-Path $ProfileHome "workspace").Replace("\", "/").TrimEnd("/")
$Failures = [Collections.Generic.List[string]]::new()
$Warnings = [Collections.Generic.List[string]]::new()

function Add-Failure {
    param([string]$Message)
    $Failures.Add($Message)
}

function Invoke-HermesCapture {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $Lines = @(& $HermesCommand @Arguments 2>&1)
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output = (($Lines | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
    }
}

function Get-SafeConfigValue {
    param([Parameter(Mandatory = $true)][string]$Key)

    $Arguments = @("-p", $ProfileId, "config", "get", $Key)
    $Result = Invoke-HermesCapture -Arguments $Arguments
    if ($Result.ExitCode -ne 0) {
        Add-Failure "Could not read safe configuration field $Key."
        return $null
    }
    return $Result.Output.Trim().Trim('"').Trim("'")
}

function Test-ConfigEquals {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Expected
    )

    $Actual = Get-SafeConfigValue -Key $Key
    if ($null -ne $Actual -and $Actual.ToLowerInvariant() -ne $Expected.ToLowerInvariant()) {
        Add-Failure "$Key does not match the isolated employee requirement."
    }
}

function Get-DefaultHashes {
    $Results = @()
    foreach ($Name in @("SOUL.md", "config.yaml", ".env")) {
        $Path = Join-Path $HermesHome $Name
        $Hash = "NOT_FOUND"
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $Hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        }
        $Results += [pscustomobject]@{ Name = $Name; Hash = $Hash }
    }
    return $Results
}

if ($ProfileId -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*$') {
    Add-Failure "The fixed Profile ID is invalid."
}

if (-not (Test-Path -LiteralPath $ProfileHome -PathType Container)) {
    Add-Failure "Profile $ProfileId is absent at the expected location."
}
else {
    $SoulPath = Join-Path $ProfileHome "SOUL.md"
    $SkillPath = Join-Path $ProfileHome "skills\etsy-performance-listing\SKILL.md"
    $ContractPath = Join-Path $ProfileHome "skills\etsy-performance-listing\references\output-contract.md"
    foreach ($RequiredAsset in @($SoulPath, $SkillPath, $ContractPath)) {
        if (-not (Test-Path -LiteralPath $RequiredAsset -PathType Leaf)) {
            Add-Failure "A dedicated employee asset is missing."
        }
    }

    Test-ConfigEquals -Key "terminal.backend" -Expected "local"
    Test-ConfigEquals -Key "terminal.home_mode" -Expected "profile"
    Test-ConfigEquals -Key "memory.memory_enabled" -Expected "true"
    Test-ConfigEquals -Key "memory.user_profile_enabled" -Expected "true"
    Test-ConfigEquals -Key "memory.write_approval" -Expected "true"
    Test-ConfigEquals -Key "skills.write_approval" -Expected "true"

    foreach ($SafeModelField in @(
        "model.provider",
        "model.default",
        "model.base_url",
        "agent.reasoning_effort"
    )) {
        $SafeModelValue = Get-SafeConfigValue -Key $SafeModelField
        if ([string]::IsNullOrWhiteSpace($SafeModelValue)) {
            Add-Failure "$SafeModelField is not explicitly configured."
        }
    }

    $ActualWorkspace = Get-SafeConfigValue -Key "terminal.cwd"
    if ($null -ne $ActualWorkspace) {
        $NormalizedActualWorkspace = $ActualWorkspace.Replace("\", "/").TrimEnd("/")
        if ($NormalizedActualWorkspace -ne $ExpectedWorkspace) {
            Add-Failure "terminal.cwd is not the dedicated Profile workspace."
        }
    }

    foreach ($ForbiddenName in @("MEMORY.md", "USER.md", "state.db")) {
        $Found = @(Get-ChildItem -LiteralPath $ProfileHome -Recurse -Force -File -ErrorAction SilentlyContinue |
            Where-Object Name -eq $ForbiddenName)
        if ($Found.Count -gt 0) {
            Add-Failure "Unexpected initial employee state file exists: $ForbiddenName."
        }
    }

    foreach ($StateDirectoryName in @("memories", "sessions", "logs")) {
        $StateDirectory = Join-Path $ProfileHome $StateDirectoryName
        if (Test-Path -LiteralPath $StateDirectory -PathType Container) {
            $StateFiles = @(Get-ChildItem -LiteralPath $StateDirectory -Recurse -Force -File -ErrorAction SilentlyContinue)
            if ($StateFiles.Count -gt 0) {
                Add-Failure "Initial $StateDirectoryName directory contains files."
            }
        }
    }

    $ConfigPath = Join-Path $ProfileHome "config.yaml"
    $EnvironmentPath = Join-Path $ProfileHome ".env"
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        $ConfigText = [IO.File]::ReadAllText($ConfigPath)
        if ($ConfigText -match '(?im)^\s*(gateway_?token|bot_?token|webhook_?url|telegram_?token|discord_?token|slack_?token|whatsapp_?token|signal_?token)\s*:\s*\S+') {
            Add-Failure "Profile configuration contains gateway or messaging-channel credential inheritance."
        }
    }
    if (Test-Path -LiteralPath $EnvironmentPath -PathType Leaf) {
        $EnvironmentText = [IO.File]::ReadAllText($EnvironmentPath)
        if ($EnvironmentText -match '(?im)^\s*(GATEWAY|TELEGRAM|DISCORD|SLACK|WHATSAPP|SIGNAL)[A-Z0-9_]*\s*=\s*\S+') {
            Add-Failure "Profile environment contains gateway or messaging-channel credentials."
        }
    }
}

if (-not [string]::IsNullOrWhiteSpace($BaselinePath)) {
    if (-not (Test-Path -LiteralPath $BaselinePath -PathType Leaf)) {
        Add-Failure "The supplied hashes-only baseline file does not exist."
    }
    else {
        try {
            $Baseline = @(Get-Content -LiteralPath $BaselinePath -Raw | ConvertFrom-Json)
            $CurrentHashes = Get-DefaultHashes
            foreach ($Name in @("SOUL.md", "config.yaml", ".env")) {
                $ExpectedEntry = @($Baseline | Where-Object Name -eq $Name)
                $CurrentEntry = @($CurrentHashes | Where-Object Name -eq $Name)
                if ($ExpectedEntry.Count -ne 1 -or $CurrentEntry.Count -ne 1 -or
                    $ExpectedEntry[0].Hash -ne $CurrentEntry[0].Hash) {
                    Add-Failure "Default Hermes baseline mismatch for $Name."
                }
            }
        }
        catch {
            Add-Failure "The supplied baseline could not be validated."
        }
    }
}

if ($RunModelCheck -and $Failures.Count -eq 0) {
    $Prompt = "系统集成测试：不要调用工具，不要修改记忆，只回复 PROFILE_READY"
    $ChatArguments = @("-p", $ProfileId, "chat", "-Q", "--source", "tool", "--max-turns", "1", "-q", $Prompt)
    $ChatResult = Invoke-HermesCapture -Arguments $ChatArguments
    if ($ChatResult.ExitCode -ne 0 -or $ChatResult.Output.Trim() -cne "PROFILE_READY") {
        Add-Failure "The optional model check did not return the exact usable marker PROFILE_READY."
    }
}

if ($RunDoctor -and $Failures.Count -eq 0) {
    $DoctorArguments = @("-p", $ProfileId, "doctor")
    $DoctorResult = Invoke-HermesCapture -Arguments $DoctorArguments
    if ($DoctorResult.ExitCode -ne 0) {
        Add-Failure "Hermes Doctor returned a non-zero exit code."
    }
    if (-not [string]::IsNullOrWhiteSpace($DoctorResult.Output)) {
        $Warnings.Add("Hermes Doctor produced diagnostic output; review it locally. Secret values were not displayed by this verifier.")
    }
}

foreach ($Warning in $Warnings) {
    Write-Warning $Warning
}
if ($Failures.Count -gt 0) {
    foreach ($Failure in $Failures) {
        [Console]::Error.WriteLine("ERROR: $Failure")
    }
    exit 1
}

Write-Host "Employee Profile verification passed."
if (-not $RunModelCheck) {
    Write-Host "Model/network check was not requested."
}
if (-not $RunDoctor) {
    Write-Host "Hermes Doctor was not requested."
}
exit 0
