[CmdletBinding()]
param(
    [switch]$VerifyOnly,
    [string]$Provider = "custom",
    [string]$ModelId = "gpt-5.6-sol",
    [string]$BaseUrl,
    [ValidateSet("low", "medium", "high")]
    [string]$ReasoningEffort = "high",
    [switch]$ConfigureApiKey,
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

function Assert-Parameters {
    if ($ProfileId -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*$') {
        throw "Invalid Profile ID format."
    }
    if ([string]::IsNullOrWhiteSpace($Provider) -or $Provider -notmatch '^[a-zA-Z0-9._-]+$') {
        throw "Invalid provider format."
    }
    if ([string]::IsNullOrWhiteSpace($ModelId) -or $ModelId -notmatch '^[a-zA-Z0-9._:/-]+$') {
        throw "Invalid model ID format."
    }
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        throw "A non-secret HTTPS base URL is required. Pass -BaseUrl explicitly."
    }

    $ParsedBaseUrl = $null
    if (-not [Uri]::TryCreate($BaseUrl, [UriKind]::Absolute, [ref]$ParsedBaseUrl) -or
        $ParsedBaseUrl.Scheme -ne "https" -or
        [string]::IsNullOrWhiteSpace($ParsedBaseUrl.Host) -or
        -not [string]::IsNullOrEmpty($ParsedBaseUrl.UserInfo)) {
        throw "Invalid base URL format. Use an absolute HTTPS URL without embedded credentials."
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
$KeyConfigured = $false

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
        "model.base_url" = $NormalizedBaseUrl
        "agent.reasoning_effort" = $ReasoningEffort
    }
    foreach ($Entry in $Settings.GetEnumerator()) {
        $SetArguments = @("-p", $ProfileId, "config", "set", $Entry.Key, [string]$Entry.Value)
        Invoke-Hermes -Arguments $SetArguments
    }

    Copy-Item -LiteralPath $SourceSoul -Destination (Join-Path $ProfileHome "SOUL.md") -Force
    $SkillDestinationRoot = Join-Path $ProfileHome "skills"
    [void](New-Item -ItemType Directory -Path $SkillDestinationRoot -Force)
    Copy-Item -LiteralPath $SourceSkill -Destination $SkillDestinationRoot -Recurse -Force

    if ($ConfigureApiKey) {
        $SecureApiKey = Read-Host "Profile API key" -AsSecureString
        $KeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureApiKey)
        try {
            $PlainApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($KeyPointer)
            if ([string]::IsNullOrWhiteSpace($PlainApiKey)) {
                throw "The API key was empty."
            }
            $KeyArguments = @("-p", $ProfileId, "config", "set", "model.api_key", $PlainApiKey)
            & $HermesCommand @KeyArguments *> $null
            if ($LASTEXITCODE -ne 0) {
                throw "Hermes rejected the credential configuration."
            }
            $KeyConfigured = $true
        }
        finally {
            if ($KeyPointer -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($KeyPointer)
            }
            Remove-Variable PlainApiKey -ErrorAction SilentlyContinue
            Remove-Variable SecureApiKey -ErrorAction SilentlyContinue
        }
    }
    else {
        Write-Warning "Non-secret assets are configured; the credential step is pending. This provisioner will refuse to modify the existing Profile, so enter the credential separately only after review."
    }

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
        reasoningEffort = $ReasoningEffort
        workspace = $NormalizedWorkspace
        keyConfigured = $KeyConfigured
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
