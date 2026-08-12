[CmdletBinding()]
param(
    [switch]$RunModelCheck,
    [switch]$RunDoctor,
    [switch]$InitialProvision,
    [string]$BaselinePath,
    [string]$ManifestPath,
    [string]$HermesCommand = "hermes",
    [string]$PythonCommand = "py",
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA "hermes")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProfileId = "etsy-performance-us"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$ProfileHome = Join-Path $HermesHome "profiles\$ProfileId"
$DefaultManifestPath = Join-Path $ProfileHome "provisioning-manifest.json"
$ConfigInspectorPath = Join-Path $PSScriptRoot "inspect-employee-config.py"
$Failures = [Collections.Generic.List[string]]::new()
$Warnings = [Collections.Generic.List[string]]::new()

function Add-Failure {
    param([string]$Message)
    $Failures.Add($Message)
}

function Invoke-HermesCapture {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $PreviousErrorActionPreference = $ErrorActionPreference
    $HadNativePreference = Test-Path Variable:PSNativeCommandUseErrorActionPreference
    if ($HadNativePreference) {
        $PreviousNativePreference = $PSNativeCommandUseErrorActionPreference
    }
    try {
        $ErrorActionPreference = "Continue"
        if ($HadNativePreference) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        $Lines = @(& $HermesCommand @Arguments 2>&1)
        $ExitCode = $LASTEXITCODE
        return [pscustomobject]@{
            ExitCode = $ExitCode
            Output = (($Lines | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
        }
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
        if ($HadNativePreference) {
            $PSNativeCommandUseErrorActionPreference = $PreviousNativePreference
        }
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
        [Parameter(Mandatory = $true)][string]$Expected,
        [switch]$IgnoreCase
    )

    $Actual = Get-SafeConfigValue -Key $Key
    if ($null -eq $Actual) {
        return
    }
    $Comparison = [StringComparison]::Ordinal
    if ($IgnoreCase) {
        $Comparison = [StringComparison]::OrdinalIgnoreCase
    }
    if (-not [string]::Equals($Actual, $Expected, $Comparison)) {
        Add-Failure "$Key does not exactly match the provisioning manifest."
    }
}

function Test-ConfigUnset {
    param([Parameter(Mandatory = $true)][string]$Key)

    $Arguments = @("-p", $ProfileId, "config", "get", $Key)
    $Result = Invoke-HermesCapture -Arguments $Arguments
    if ($Result.ExitCode -eq 0) {
        Add-Failure "$Key must be unset according to the provisioning manifest."
    }
    elseif ($Result.Output.Trim() -cne "Config key not set: $Key") {
        Add-Failure "Could not verify that $Key is unset."
    }
}

function Get-FileHashOrMissing {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "NOT_FOUND"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-DefaultHashes {
    $Results = [ordered]@{}
    foreach ($Name in @("SOUL.md", "config.yaml", ".env")) {
        $Results[$Name] = Get-FileHashOrMissing -Path (Join-Path $HermesHome $Name)
    }
    return $Results
}

function Test-SecureBaseUrl {
    param([string]$Value)

    $Parsed = $null
    if ([string]::IsNullOrWhiteSpace($Value) -or
        -not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$Parsed) -or
        $Parsed.Scheme -ne "https" -or
        [string]::IsNullOrWhiteSpace($Parsed.Host) -or
        -not [string]::IsNullOrEmpty($Parsed.UserInfo) -or
        -not [string]::IsNullOrEmpty($Parsed.Query) -or
        -not [string]::IsNullOrEmpty($Parsed.Fragment) -or
        $Value -ne $Parsed.AbsoluteUri.TrimEnd("/")) {
        Add-Failure "Provisioning manifest base URL must be normalized HTTPS without credentials, query, or fragment."
        return $false
    }
    return $true
}

function Test-AssetIsolation {
    param([Parameter(Mandatory = $true)]$Manifest)

    $AssetPaths = [ordered]@{
        "SOUL.md" = "employee\SOUL.md"
        "skills/etsy-performance-listing/SKILL.md" = "employee\skills\etsy-performance-listing\SKILL.md"
        "skills/etsy-performance-listing/references/output-contract.md" = "employee\skills\etsy-performance-listing\references\output-contract.md"
        "skills/etsy-performance-listing/scripts/inspect_workbook.py" = "employee\skills\etsy-performance-listing\scripts\inspect_workbook.py"
        "skills/etsy-performance-listing/scripts/run_task.py" = "employee\skills\etsy-performance-listing\scripts\run_task.py"
        "skills/etsy-performance-listing/scripts/validate_output.py" = "employee\skills\etsy-performance-listing\scripts\validate_output.py"
        "skills/etsy-performance-listing/scripts/write_workbook.py" = "employee\skills\etsy-performance-listing\scripts\write_workbook.py"
    }
    foreach ($RelativePath in $AssetPaths.Keys) {
        $ProfileAssetPath = Join-Path $ProfileHome ($RelativePath.Replace("/", "\"))
        $RepositoryAssetPath = Join-Path $RepositoryRoot $AssetPaths[$RelativePath]
        $ProfileHash = Get-FileHashOrMissing -Path $ProfileAssetPath
        $RepositoryHash = Get-FileHashOrMissing -Path $RepositoryAssetPath
        $ManifestHash = [string]$Manifest.assetHashes.$RelativePath
        if ($ProfileHash -eq "NOT_FOUND" -or $RepositoryHash -eq "NOT_FOUND" -or
            $ProfileHash -ne $RepositoryHash -or $ProfileHash -ne $ManifestHash) {
            Add-Failure "Employee asset hash mismatch for $RelativePath."
        }
    }

    $SkillsRoot = Join-Path $ProfileHome "skills"
    if (Test-Path -LiteralPath $SkillsRoot -PathType Container) {
        $AllowedEntries = @(
            "etsy-performance-listing",
            "etsy-performance-listing/SKILL.md",
            "etsy-performance-listing/references",
            "etsy-performance-listing/references/output-contract.md",
            "etsy-performance-listing/scripts",
            "etsy-performance-listing/scripts/inspect_workbook.py",
            "etsy-performance-listing/scripts/run_task.py",
            "etsy-performance-listing/scripts/validate_output.py",
            "etsy-performance-listing/scripts/write_workbook.py"
        )
        $SkillEntries = @(Get-ChildItem -LiteralPath $SkillsRoot -Recurse -Force |
            ForEach-Object {
                $_.FullName.Substring($SkillsRoot.Length).TrimStart("\", "/").Replace("\", "/")
            })
        foreach ($Entry in $SkillEntries) {
            if ($Entry -notin $AllowedEntries) {
                Add-Failure "Additional skill content is not allowed in the isolated Profile."
                break
            }
        }
    }
}

function Test-EnvironmentIsolation {
    $EnvironmentPath = Join-Path $ProfileHome ".env"
    if (-not (Test-Path -LiteralPath $EnvironmentPath -PathType Leaf)) {
        Add-Failure "Profile .env is missing the expected Hermes terminal mirror."
        return
    }
    $AllowedAssignmentCount = 0
    foreach ($Line in [IO.File]::ReadAllLines($EnvironmentPath)) {
        $Trimmed = $Line.Trim()
        if (-not $Trimmed -or $Trimmed.StartsWith("#")) {
            continue
        }
        if ($Trimmed -ceq "TERMINAL_ENV=local") {
            $AllowedAssignmentCount++
        }
        else {
            Add-Failure "Profile contains an unexpected or invalid environment assignment."
            return
        }
    }
    if ($AllowedAssignmentCount -ne 1) {
        Add-Failure "Profile must contain exactly one TERMINAL_ENV=local environment assignment."
    }
}

function Test-StructuredConfigIsolation {
    param([Parameter(Mandatory = $true)][bool]$KeyConfigured)

    $ConfigPath = Join-Path $ProfileHome "config.yaml"
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        Add-Failure "Profile config.yaml is missing."
        return
    }

    if (-not (Test-Path -LiteralPath $ConfigInspectorPath -PathType Leaf)) {
        Add-Failure "The structural configuration inspector is missing."
        return
    }
    $InspectorArguments = @("-3.11", $ConfigInspectorPath, $ConfigPath)
    $InspectorLines = @(& $PythonCommand @InspectorArguments 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Add-Failure "Profile configuration could not be structurally inspected."
        return
    }
    try {
        $Inspection = (($InspectorLines | ForEach-Object { [string]$_ }) -join "") | ConvertFrom-Json
    }
    catch {
        Add-Failure "Profile configuration inspection returned invalid metadata."
        return
    }
    if (@($Inspection.forbidden_paths).Count -gt 0) {
        Add-Failure "Profile configuration contains gateway or messaging-channel configuration."
    }
    if ($KeyConfigured -ne [bool]$Inspection.model_api_key_present) {
        Add-Failure "Credential presence does not match the non-secret keyConfigured manifest flag."
    }
}

function Test-StateIsolation {
    param([Parameter(Mandatory = $true)][string]$BoundManifestPath)

    $RootStateFiles = @()
    foreach ($StateName in @("MEMORY.md", "USER.md", "state.db")) {
        $StatePath = Join-Path $ProfileHome $StateName
        if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
            $RootStateFiles += Get-Item -LiteralPath $StatePath -Force
        }
    }
    if ($InitialProvision) {
        foreach ($StateFile in $RootStateFiles) {
            Add-Failure "Unexpected initial employee state file exists: $($StateFile.Name)."
        }
    }

    $EmployeeStateFiles = @($RootStateFiles)
    foreach ($StateDirectoryName in @("memories", "sessions", "logs")) {
        $StateDirectory = Join-Path $ProfileHome $StateDirectoryName
        if (Test-Path -LiteralPath $StateDirectory -PathType Container) {
            $DirectoryItem = Get-Item -LiteralPath $StateDirectory -Force
            if (($DirectoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                Add-Failure "Employee state path must not be a reparse point: $StateDirectoryName."
                continue
            }
            $StateFiles = @(Get-ChildItem -LiteralPath $StateDirectory -Recurse -Force -File -ErrorAction SilentlyContinue)
            $ReparseEntries = @(Get-ChildItem -LiteralPath $StateDirectory -Recurse -Force -ErrorAction SilentlyContinue |
                Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 })
            if ($ReparseEntries.Count -gt 0) {
                Add-Failure "Employee state paths must not contain reparse points."
            }
            if ($InitialProvision -and $StateFiles.Count -gt 0) {
                Add-Failure "Initial $StateDirectoryName directory contains files."
            }
            $EmployeeStateFiles += $StateFiles
        }
    }

    if ($InitialProvision -or $EmployeeStateFiles.Count -eq 0) {
        return
    }
    $ManifestItem = Get-Item -LiteralPath $BoundManifestPath -Force
    if (($ManifestItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Add-Failure "The provisioning manifest must not be a reparse point."
        return
    }
    $EarliestAllowedUtc = $ManifestItem.CreationTimeUtc.Subtract([TimeSpan]::FromSeconds(2))
    foreach ($StateFile in $EmployeeStateFiles) {
        if (($StateFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Add-Failure "Employee state files must not be reparse points."
        }
        elseif ($StateFile.CreationTimeUtc -lt $EarliestAllowedUtc -or
            $StateFile.LastWriteTimeUtc -lt $EarliestAllowedUtc) {
            Add-Failure "Employee state file predates the bound provisioning manifest."
        }
    }
}

if ($ProfileId -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*$') {
    Add-Failure "The fixed Profile ID is invalid."
}

$Manifest = $null
if (-not (Test-Path -LiteralPath $ProfileHome -PathType Container)) {
    Add-Failure "Profile $ProfileId is absent at the expected location."
}
else {
    if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
        $ManifestPath = $DefaultManifestPath
    }
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        Add-Failure "The non-secret provisioning manifest is missing."
    }
    else {
        try {
            $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
        }
        catch {
            Add-Failure "The provisioning manifest is invalid JSON."
        }
    }
}

if ($null -ne $Manifest) {
    $AllowedManifestFields = @(
        "schemaVersion", "profileId", "provider", "model", "baseUrl", "hasBaseUrl",
        "reasoningEffort", "workspace", "keyConfigured", "assetHashes",
        "defaultBaseline"
    )
    foreach ($ManifestField in $Manifest.PSObject.Properties.Name) {
        if ($ManifestField -notin $AllowedManifestFields) {
            Add-Failure "Provisioning manifest contains an unexpected field."
        }
    }
    if ($Manifest.schemaVersion -ne 1 -or $Manifest.profileId -cne $ProfileId) {
        Add-Failure "Provisioning manifest identity or schema is invalid."
    }
    $HasBaseUrl = [bool]$Manifest.hasBaseUrl
    $ProviderRequiresBaseUrl = ([string]$Manifest.provider).ToLowerInvariant() -notin @("openai-codex")
    if ($ProviderRequiresBaseUrl -and -not $HasBaseUrl) {
        Add-Failure "A custom provider manifest must include a validated base URL."
    }
    if (-not $ProviderRequiresBaseUrl -and $HasBaseUrl) {
        Add-Failure "The built-in provider manifest must not include a base URL override."
    }
    if ($HasBaseUrl) {
        [void](Test-SecureBaseUrl -Value ([string]$Manifest.baseUrl))
    }
    elseif ($null -ne $Manifest.baseUrl) {
        Add-Failure "Provisioning manifest base URL must be null when hasBaseUrl is false."
    }

    Test-ConfigEquals -Key "terminal.backend" -Expected "local" -IgnoreCase
    Test-ConfigEquals -Key "terminal.home_mode" -Expected "profile" -IgnoreCase
    Test-ConfigEquals -Key "memory.memory_enabled" -Expected "true" -IgnoreCase
    Test-ConfigEquals -Key "memory.user_profile_enabled" -Expected "true" -IgnoreCase
    Test-ConfigEquals -Key "memory.write_approval" -Expected "true" -IgnoreCase
    Test-ConfigEquals -Key "skills.write_approval" -Expected "true" -IgnoreCase
    Test-ConfigEquals -Key "model.provider" -Expected ([string]$Manifest.provider)
    Test-ConfigEquals -Key "model.default" -Expected ([string]$Manifest.model)
    if ($HasBaseUrl) {
        Test-ConfigEquals -Key "model.base_url" -Expected ([string]$Manifest.baseUrl)
    }
    else {
        Test-ConfigUnset -Key "model.base_url"
    }
    Test-ConfigEquals -Key "agent.reasoning_effort" -Expected ([string]$Manifest.reasoningEffort)

    $ExpectedWorkspace = (Join-Path $ProfileHome "workspace").Replace("\", "/").TrimEnd("/")
    $ManifestWorkspace = ([string]$Manifest.workspace).Replace("\", "/").TrimEnd("/")
    if ($ManifestWorkspace -cne $ExpectedWorkspace) {
        Add-Failure "Manifest workspace is not the dedicated Profile workspace."
    }
    Test-ConfigEquals -Key "terminal.cwd" -Expected $ManifestWorkspace

    Test-AssetIsolation -Manifest $Manifest
    Test-EnvironmentIsolation
    Test-StructuredConfigIsolation -KeyConfigured ([bool]$Manifest.keyConfigured)
    Test-StateIsolation -BoundManifestPath $ManifestPath

    $CurrentDefaultHashes = Get-DefaultHashes
    foreach ($Name in @("SOUL.md", "config.yaml", ".env")) {
        if ([string]$Manifest.defaultBaseline.$Name -cne [string]$CurrentDefaultHashes[$Name]) {
            Add-Failure "Default Hermes baseline mismatch for $Name."
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
                if ($ExpectedEntry.Count -ne 1 -or [string]$ExpectedEntry[0].Hash -cne [string]$CurrentHashes[$Name]) {
                    Add-Failure "Optional default Hermes baseline mismatch for $Name."
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
        Add-Failure "DOCTOR_CORE_FAILURE: Hermes Doctor could not complete."
    }
    else {
        $Escape = [string][char]27
        $DoctorText = [regex]::Replace($DoctorResult.Output, "$Escape\[[0-9;?]*[ -/]*[@-~]", "")
        if ($DoctorText -match '(?i)All checks passed!') {
            Write-Host "DOCTOR_CLEAN"
        }
        else {
            $HasIssueSummary = $DoctorText -match '(?i)Found\s+\d+\s+issue\(s\)\s+to address'
            $HasCoreFailure = $DoctorText -match '(?i)(?:invalid|missing|failed|failure|broken|mismatch|unavailable|unknown|unrecogni[sz]ed|migration required).{0,80}(?:model(?:\.|\s+)?provider|model|provider|config(?:uration)?|terminal|python|required package)|(?:model(?:\.|\s+)?provider|model|provider|config(?:uration)?|terminal|python|required package).{0,80}(?:invalid|missing|failed|failure|broken|mismatch|unavailable|unknown|unrecogni[sz]ed|migration required)'
            if ($HasIssueSummary -and $HasCoreFailure) {
                Add-Failure "DOCTOR_CORE_FAILURE: Hermes Doctor found a core profile, model, terminal, or configuration problem."
            }
            else {
                $Warnings.Add("DOCTOR_WARN: Hermes Doctor returned optional or unclassified diagnostics. Review Doctor locally; this verifier does not expose its raw output.")
            }
        }
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
Write-Host "Manifest checks detect accidental drift; the unsigned manifest is not attacker-proof."
if (-not $RunModelCheck) {
    Write-Host "Model/network check was not requested."
}
if (-not $RunDoctor) {
    Write-Host "Hermes Doctor was not requested."
}
exit 0
