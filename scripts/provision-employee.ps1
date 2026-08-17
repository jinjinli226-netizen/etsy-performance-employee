[CmdletBinding()]
param(
    [switch]$VerifyOnly,
    [string]$Provider = "custom",
    [string]$ModelId = "gpt-5.6-sol",
    [string]$BaseUrl,
    [ValidateSet("minimal", "low", "medium", "high", "xhigh", "max", "ultra")]
    [string]$ReasoningEffort = "high",
    [string]$HermesCommand = "hermes"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProfileId = "etsy-performance-us"
$Description = "Etsy US performance-costume Listing digital employee"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$SourceSoul = Join-Path $RepositoryRoot "employee\SOUL.md"
$SourceSkill = Join-Path $RepositoryRoot "employee\skills\etsy-performance-listing"
$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$ProfileHome = Join-Path $HermesHome "profiles\$ProfileId"
$Workspace = Join-Path $ProfileHome "workspace"
$VerifyScript = Join-Path $PSScriptRoot "verify-employee.ps1"
$ManifestPath = Join-Path $ProfileHome "provisioning-manifest.json"
$NormalizedBaseUrl = $null
$NoBaseUrlProviders = @("openai-codex")

function Assert-Parameters {
    if ($Provider -ceq "codex") {
        $script:Provider = "openai-codex"
    }
    if ($ProfileId -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*$') {
        throw "Invalid Profile ID format."
    }
    if ([string]::IsNullOrWhiteSpace($Provider) -or $Provider -notmatch '^[a-zA-Z0-9._-]+$') {
        throw "Invalid provider format."
    }
    if ([string]::IsNullOrWhiteSpace($ModelId) -or $ModelId -notmatch '^[a-zA-Z0-9._:/-]+$') {
        throw "Invalid model ID format."
    }
    $UsesBuiltInEndpoint = $Provider.ToLowerInvariant() -in $NoBaseUrlProviders
    if ($UsesBuiltInEndpoint) {
        if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
            throw "The built-in $Provider provider requires the base URL to be omitted."
        }
        $script:NormalizedBaseUrl = $null
        return
    }
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        throw "A non-secret HTTPS base URL is required. Pass -BaseUrl explicitly."
    }

    $ParsedBaseUrl = $null
    if (-not [Uri]::TryCreate($BaseUrl, [UriKind]::Absolute, [ref]$ParsedBaseUrl) -or
        $ParsedBaseUrl.Scheme -ne "https" -or
        [string]::IsNullOrWhiteSpace($ParsedBaseUrl.Host) -or
        -not [string]::IsNullOrEmpty($ParsedBaseUrl.UserInfo) -or
        -not [string]::IsNullOrEmpty($ParsedBaseUrl.Query) -or
        -not [string]::IsNullOrEmpty($ParsedBaseUrl.Fragment)) {
        throw "Invalid base URL format. Use an absolute HTTPS URL without credentials, query, or fragment."
    }
    $script:NormalizedBaseUrl = $ParsedBaseUrl.AbsoluteUri.TrimEnd("/")
}

function Get-DefaultHashes {
    $Names = @("SOUL.md", "config.yaml", ".env")
    $Results = @()
    foreach ($Name in $Names) {
        $Path = Join-Path $HermesHome $Name
        $Hash = "NOT_FOUND"
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $Hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        }
        $Results += [pscustomobject]@{
            Name = $Name
            Hash = $Hash
        }
    }
    return $Results
}

function Get-AssetHashes {
    return [ordered]@{
        "SOUL.md" = (Get-FileHash -LiteralPath $SourceSoul -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/SKILL.md" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "SKILL.md") -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/references/output-contract.md" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "references\output-contract.md") -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/scripts/inspect_workbook.py" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "scripts\inspect_workbook.py") -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/scripts/originality_guard.py" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "scripts\originality_guard.py") -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/scripts/run_task.py" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "scripts\run_task.py") -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/scripts/validate_output.py" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "scripts\validate_output.py") -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/scripts/visual_context.py" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "scripts\visual_context.py") -Algorithm SHA256).Hash
        "skills/etsy-performance-listing/scripts/write_workbook.py" = (Get-FileHash -LiteralPath (Join-Path $SourceSkill "scripts\write_workbook.py") -Algorithm SHA256).Hash
    }
}

function Assert-DefaultHashesUnchanged {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After
    )

    foreach ($Name in @("SOUL.md", "config.yaml", ".env")) {
        $BeforeValue = ($Before | Where-Object Name -eq $Name).Hash
        $AfterValue = ($After | Where-Object Name -eq $Name).Hash
        if ($BeforeValue -ne $AfterValue) {
            throw "Default Hermes baseline changed for $Name. Stop and investigate before continuing."
        }
    }
}

function Invoke-Hermes {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $HermesCommand @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes command failed with exit code $LASTEXITCODE."
    }
}

function Test-HermesProfileExists {
    $ShowArguments = @("profile", "show", $ProfileId)
    & $HermesCommand @ShowArguments *> $null
    return $LASTEXITCODE -eq 0
}

if ($VerifyOnly) {
    if (-not (Test-Path -LiteralPath $VerifyScript -PathType Leaf)) {
        throw "Verification script is missing."
    }
    & $VerifyScript -HermesCommand $HermesCommand
    exit $LASTEXITCODE
}

Assert-Parameters

if (-not (Test-Path -LiteralPath $SourceSoul -PathType Leaf)) {
    throw "Repository SOUL asset is missing."
}
if (-not (Test-Path -LiteralPath $SourceSkill -PathType Container)) {
    throw "Repository skill asset is missing."
}
if ((Test-Path -LiteralPath $ProfileHome) -or (Test-HermesProfileExists)) {
    throw "Profile $ProfileId already exists. Refusing to modify it; use -VerifyOnly for read-only checks."
}

$OperationDirectory = Join-Path ([IO.Path]::GetTempPath()) ("etsy-employee-provision-" + [Guid]::NewGuid().ToString("N"))
$BaselinePath = Join-Path $OperationDirectory "default-hashes.json"
$BeforeHashes = $null
$PrimaryError = $null
$ProfileCreated = $false

try {
    [void](New-Item -ItemType Directory -Path $OperationDirectory)
    $BeforeHashes = Get-DefaultHashes
    $BeforeHashes | ConvertTo-Json | Set-Content -LiteralPath $BaselinePath -Encoding UTF8
    Write-Host "Saved the hashes-only default baseline to $BaselinePath."

    $CreateArguments = @("profile", "create", $ProfileId, "--no-skills", "--description", $Description)
    Invoke-Hermes -Arguments $CreateArguments
    $ProfileCreated = Test-Path -LiteralPath $ProfileHome -PathType Container
    if (-not $ProfileCreated) {
        throw "Hermes reported success but the Profile directory was not found."
    }

    [void](New-Item -ItemType Directory -Path $Workspace -Force)
    $NormalizedWorkspace = $Workspace.Replace("\", "/")
    $Settings = [ordered]@{
        "terminal.backend" = "local"
        "terminal.cwd" = $NormalizedWorkspace
        "terminal.home_mode" = "profile"
        "memory.memory_enabled" = "true"
        "memory.user_profile_enabled" = "true"
        "memory.write_approval" = "true"
        "skills.write_approval" = "true"
        "model.provider" = $Provider
        "model.default" = $ModelId
        "agent.reasoning_effort" = $ReasoningEffort
    }
    if ($null -ne $NormalizedBaseUrl) {
        $Settings["model.base_url"] = $NormalizedBaseUrl
    }
    foreach ($Entry in $Settings.GetEnumerator()) {
        $SetArguments = @("-p", $ProfileId, "config", "set", $Entry.Key, [string]$Entry.Value)
        Invoke-Hermes -Arguments $SetArguments
    }

    Copy-Item -LiteralPath $SourceSoul -Destination (Join-Path $ProfileHome "SOUL.md") -Force
    $SkillDestinationRoot = Join-Path $ProfileHome "skills"
    $SkillDestination = Join-Path $SkillDestinationRoot "etsy-performance-listing"
    $ReferenceDestination = Join-Path $SkillDestination "references"
    $ScriptDestination = Join-Path $SkillDestination "scripts"
    [void](New-Item -ItemType Directory -Path $ReferenceDestination -Force)
    [void](New-Item -ItemType Directory -Path $ScriptDestination -Force)
    Copy-Item -LiteralPath (Join-Path $SourceSkill "SKILL.md") -Destination (Join-Path $SkillDestination "SKILL.md") -Force
    Copy-Item -LiteralPath (Join-Path $SourceSkill "references\output-contract.md") -Destination (Join-Path $ReferenceDestination "output-contract.md") -Force
    foreach ($ScriptName in @("inspect_workbook.py", "originality_guard.py", "run_task.py", "validate_output.py", "visual_context.py", "write_workbook.py")) {
        Copy-Item -LiteralPath (Join-Path $SourceSkill "scripts\$ScriptName") -Destination (Join-Path $ScriptDestination $ScriptName) -Force
    }

    # Hermes v0.18.2 `config set` has no stdin-only value mode. Passing a key as
    # a native-process argument exposes it to process inspection, so this script
    # deliberately performs no credential operation.
    Write-Warning "Credential configuration pending. Configure it separately with an operator-controlled interactive Hermes workflow after review."

    $DefaultBaseline = [ordered]@{}
    foreach ($Entry in $BeforeHashes) {
        $DefaultBaseline[$Entry.Name] = $Entry.Hash
    }
    $Manifest = [ordered]@{
        schemaVersion = 1
        profileId = $ProfileId
        provider = $Provider
        model = $ModelId
        baseUrl = $NormalizedBaseUrl
        hasBaseUrl = $null -ne $NormalizedBaseUrl
        reasoningEffort = $ReasoningEffort
        workspace = $NormalizedWorkspace
        keyConfigured = $false
        assetHashes = Get-AssetHashes
        defaultBaseline = $DefaultBaseline
    }
    $Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
}
catch {
    $PrimaryError = $_
}
finally {
    if ($null -ne $BeforeHashes) {
        try {
            $AfterHashes = Get-DefaultHashes
            Assert-DefaultHashesUnchanged -Before $BeforeHashes -After $AfterHashes
        }
        catch {
            if ($null -eq $PrimaryError) {
                $PrimaryError = $_
            }
            else {
                Write-Error "Default baseline verification also failed."
            }
        }
    }
}

if ($null -ne $PrimaryError) {
    if ($ProfileCreated -or (Test-Path -LiteralPath $ProfileHome)) {
        Write-Error "Provisioning stopped after partial creation. No automatic deletion was attempted. Inspect $ProfileHome and perform manual recovery."
    }
    throw $PrimaryError
}

Write-Host "Profile assets and non-secret settings were provisioned. Run the read-only verifier before any model check."
Write-Host "Default baseline hashes are unchanged."
& $VerifyScript -HermesCommand $HermesCommand -ManifestPath $ManifestPath -InitialProvision
if ($LASTEXITCODE -ne 0) {
    throw "Initial read-only Profile verification failed. Inspect the partial Profile and perform manual recovery."
}
