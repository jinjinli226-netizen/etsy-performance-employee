Set-StrictMode -Version Latest

function Set-EmployeeRuntimeEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$DataDirectory,
        [Parameter(Mandatory = $true)][string]$HermesExecutable,
        [Parameter(Mandatory = $true)][string]$HermesHome
    )

    $canonicalDataDirectory = [IO.Path]::GetFullPath($DataDirectory)
    $canonicalHermesExecutable = [IO.Path]::GetFullPath($HermesExecutable)
    $canonicalHermesHome = [IO.Path]::GetFullPath($HermesHome)

    # Remove inherited overrides that could silently select test adapters,
    # another database, credentials, or a different migration authority.
    foreach ($name in @(
        "ETSY_EMPLOYEE_DATABASE_URL",
        "ETSY_EMPLOYEE_TEST_MODE",
        "ETSY_EMPLOYEE_MIGRATION_CAPABILITY"
    )) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }

    $env:ETSY_EMPLOYEE_DATA_DIR = $canonicalDataDirectory
    $env:ETSY_EMPLOYEE_HERMES_EXECUTABLE = $canonicalHermesExecutable
    $env:ETSY_EMPLOYEE_HERMES_PROFILE = "etsy-performance-us"
    $env:HERMES_HOME = $canonicalHermesHome
}
