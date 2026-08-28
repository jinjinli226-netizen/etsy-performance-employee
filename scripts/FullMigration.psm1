Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ForbiddenSegments = @(
    "runtime",
    "browser-profile",
    "migration-workspace",
    "migration-packages"
)
$script:ForbiddenNames = @(
    ".env",
    ".start-pids.json",
    "migration-capability",
    "auth.json",
    "credentials.json",
    "oauth.json",
    "access-token",
    "refresh-token"
)

function Test-MigrationReparseAncestors {
    param([Parameter(Mandatory = $true)][string]$Path)

    $cursor = $Path
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $null -ne $item.LinkType) {
                throw "Migration paths must not contain links or reparse points."
            }
        }
        $parent = Split-Path -Parent $cursor
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Resolve-MigrationLocalPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$MustExist
    )

    if ([string]::IsNullOrWhiteSpace($Path)) { throw "Migration path is required." }
    $canonical = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($canonical)
    if ([string]::IsNullOrWhiteSpace($root) -or $canonical.TrimEnd('\') -eq $root.TrimEnd('\')) {
        throw "Migration path must be a dedicated directory, not a drive root."
    }
    if ($canonical.StartsWith("\\", [StringComparison]::Ordinal)) {
        throw "Migration paths must be on a local drive, not a UNC path."
    }
    foreach ($oneDriveName in @("OneDrive", "OneDriveConsumer", "OneDriveCommercial")) {
        $oneDrive = [Environment]::GetEnvironmentVariable($oneDriveName)
        if (-not [string]::IsNullOrWhiteSpace($oneDrive)) {
            $oneDriveRoot = [IO.Path]::GetFullPath($oneDrive).TrimEnd('\') + '\'
            if (($canonical.TrimEnd('\') + '\').StartsWith($oneDriveRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Migration paths must not be inside OneDrive."
            }
        }
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $canonical -PathType Container)) {
        throw "Migration directory does not exist: $canonical"
    }
    Test-MigrationReparseAncestors -Path $canonical
    return $canonical
}

function Assert-MigrationPathAllowed {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    if ([string]::IsNullOrWhiteSpace($RelativePath)) { throw "Migration relative path is empty." }
    $normalized = $RelativePath.Replace('\', '/')
    if ([IO.Path]::IsPathRooted($normalized) -or $normalized.Contains(':')) {
        throw "Migration relative path must not be rooted."
    }
    $segments = @($normalized.Split('/') | Where-Object { $_ -ne "" })
    if ($segments.Count -eq 0 -or $segments.Count -ne $normalized.Split('/').Count) {
        throw "Migration relative path is not canonical."
    }
    foreach ($segment in $segments) {
        if ($segment -in @(".", "..")) { throw "Migration relative path escaped its root." }
        if ($segment.ToLowerInvariant() -in $script:ForbiddenSegments) {
            throw "Migration contains a forbidden path segment: $segment"
        }
    }
    $name = $segments[-1].ToLowerInvariant()
    if ($name -in $script:ForbiddenNames -or $name.EndsWith(".token", [StringComparison]::Ordinal)) {
        throw "Migration contains a forbidden credential or runtime file: $name"
    }
    return ($segments -join '/')
}

function Get-MigrationInventory {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    $canonicalRoot = (Resolve-MigrationLocalPath -Path $Root -MustExist).TrimEnd('\')
    $prefix = $canonicalRoot + '\'
    $items = @(Get-ChildItem -LiteralPath $canonicalRoot -Force -Recurse)
    foreach ($item in $items) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $null -ne $item.LinkType) {
            throw "Migration inventory contains a link or reparse point."
        }
    }
    $results = @()
    foreach ($item in @($items | Where-Object { -not $_.PSIsContainer })) {
        $fullName = [IO.Path]::GetFullPath($item.FullName)
        if (-not $fullName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Migration inventory escaped its root."
        }
        $relative = Assert-MigrationPathAllowed -RelativePath $fullName.Substring($prefix.Length)
        $results += [pscustomobject]@{
            path = $relative
            size_bytes = [int64]$item.Length
            sha256 = (Get-FileHash -LiteralPath $fullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $sorted = @($results | Sort-Object path)
    Write-Output -NoEnumerate $sorted
}

function Get-MigrationCategoryCounts {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Inventory)

    $counts = [ordered]@{}
    foreach ($category in @("database", "attachments", "excel-jobs", "training-evidence", "trust", "other")) {
        $counts[$category] = 0
    }
    foreach ($entry in @($Inventory)) {
        $path = [string]$entry.path
        $category = if ($path -ceq "app.db") { "database" }
            elseif ($path.StartsWith("attachments/", [StringComparison]::OrdinalIgnoreCase)) { "attachments" }
            elseif ($path.StartsWith("excel-jobs/", [StringComparison]::OrdinalIgnoreCase)) { "excel-jobs" }
            elseif ($path.StartsWith("training-evidence/", [StringComparison]::OrdinalIgnoreCase)) { "training-evidence" }
            elseif ($path.StartsWith("trust/", [StringComparison]::OrdinalIgnoreCase)) { "trust" }
            else { "other" }
        $counts[$category] = [int]$counts[$category] + 1
    }
    return [pscustomobject]$counts
}

function Assert-EmptyMigrationTarget {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $canonical = Resolve-MigrationLocalPath -Path $Path
    if (Test-Path -LiteralPath $canonical) {
        $item = Get-Item -LiteralPath $canonical -Force
        if (-not $item.PSIsContainer) { throw "DataDirectory must be an empty directory." }
        if (@(Get-ChildItem -LiteralPath $canonical -Force).Count -ne 0) {
            throw "DataDirectory must be empty before restore."
        }
    }
    return $canonical
}

function Compare-MigrationInventories {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)]$Actual
    )

    $expectedItems = @($Expected | Sort-Object path)
    $actualItems = @($Actual | Sort-Object path)
    if ($expectedItems.Count -ne $actualItems.Count) { throw "Migration inventory file count mismatch." }
    for ($index = 0; $index -lt $expectedItems.Count; $index++) {
        $left = $expectedItems[$index]
        $right = $actualItems[$index]
        if ([string]$left.path -cne [string]$right.path) { throw "Migration inventory path mismatch." }
        if ([int64]$left.size_bytes -ne [int64]$right.size_bytes) { throw "Migration inventory size mismatch for $($left.path)." }
        if ([string]$left.sha256 -cne [string]$right.sha256) { throw "Migration inventory hash mismatch for $($left.path)." }
    }
    return $true
}

function Test-MigrationManifest {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$PackageDirectory)

    $packageRoot = Resolve-MigrationLocalPath -Path $PackageDirectory -MustExist
    $manifestPath = Join-Path $packageRoot "migration-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "Migration manifest is missing." }
    $manifestItem = Get-Item -LiteralPath $manifestPath -Force
    if ($manifestItem.Length -gt 64MB -or ($manifestItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Migration manifest is unsafe."
    }
    try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json }
    catch { throw "Migration manifest is invalid JSON." }
    if ([int]$manifest.schema_version -ne 1) { throw "Unsupported migration manifest schema." }
    if ([string]$manifest.source.git_commit -notmatch '^[0-9a-f]{40}$') { throw "Migration manifest commit is invalid." }
    $expectedFiles = @($manifest.data.files)
    $paths = @{}
    foreach ($entry in $expectedFiles) {
        $normalized = Assert-MigrationPathAllowed -RelativePath ([string]$entry.path)
        if ($normalized -cne [string]$entry.path) { throw "Migration manifest path is not canonical." }
        if ($paths.ContainsKey($normalized)) { throw "Migration manifest contains duplicate paths." }
        $paths[$normalized] = $true
        if ([int64]$entry.size_bytes -lt 0 -or [string]$entry.sha256 -notmatch '^[0-9a-f]{64}$') {
            throw "Migration manifest file metadata is invalid."
        }
    }
    $dataRoot = Join-Path $packageRoot "data-full"
    $actualFiles = @(Get-MigrationInventory -Root $dataRoot)
    [void](Compare-MigrationInventories -Expected $expectedFiles -Actual $actualFiles)
    $actualTotal = [int64]0
    foreach ($entry in $actualFiles) { $actualTotal += [int64]$entry.size_bytes }
    if ($actualTotal -ne [int64]$manifest.data.total_bytes) { throw "Migration manifest total byte count mismatch." }

    if ([string]$manifest.repository_bundle.path -cne "repository.bundle") { throw "Migration bundle path is invalid." }
    $bundlePath = Join-Path $packageRoot "repository.bundle"
    if (-not (Test-Path -LiteralPath $bundlePath -PathType Leaf)) { throw "Migration repository bundle is missing." }
    $bundle = Get-Item -LiteralPath $bundlePath -Force
    if (($bundle.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Migration repository bundle is unsafe." }
    if ([int64]$bundle.Length -ne [int64]$manifest.repository_bundle.size_bytes) { throw "Migration bundle size mismatch." }
    $bundleHash = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($bundleHash -cne [string]$manifest.repository_bundle.sha256) { throw "Migration bundle hash mismatch." }
    Write-Output -NoEnumerate $manifest
}

function Write-MigrationJsonAtomic {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateRange(2, 20)][int]$Depth = 8
    )

    $target = [IO.Path]::GetFullPath($Path)
    if (Test-Path -LiteralPath $target) { throw "Migration JSON target already exists." }
    $temporary = $target + ".tmp-" + [Guid]::NewGuid().ToString("N")
    try {
        $json = $Value | ConvertTo-Json -Depth $Depth
        [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $target
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

Export-ModuleMember -Function @(
    "Resolve-MigrationLocalPath",
    "Assert-MigrationPathAllowed",
    "Get-MigrationInventory",
    "Get-MigrationCategoryCounts",
    "Assert-EmptyMigrationTarget",
    "Compare-MigrationInventories",
    "Test-MigrationManifest",
    "Write-MigrationJsonAtomic"
)
