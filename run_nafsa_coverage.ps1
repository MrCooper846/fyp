<#
.SYNOPSIS
Run the NAFSA pipeline under coverage and save all coverage artefacts.

.DESCRIPTION
Uses the active Python interpreter from your terminal, so if you launch this
from your development environment it will reuse that environment's installed
`coverage` module and project dependencies.

.EXAMPLE
.\run_nafsa_coverage.ps1 -Country IT -Limit 237 -RunName nafsa_it_hybrid_v3

.EXAMPLE
.\run_nafsa_coverage.ps1 -Country IT -Limit 25 -RunName smoke_it -NoDebug
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Country,

    [string]$RunName = "",

    [ValidateSet("universities", "companies")]
    [string]$Source = "universities",

    [int]$Limit = 0,

    [ValidateSet("heuristic_only", "generated_slug_only", "real_link_only", "hybrid")]
    [string]$DiscoveryMode = "hybrid",

    [int]$PerTargetMax = 15,

    [int]$Concurrency = 6,

    [string]$OutputCsv = "",

    [string]$CompaniesCsv = "",

    [string]$PythonExe = "python",

    [switch]$Classify,

    [switch]$IgnoreRobots,

    [switch]$NoDebug
)

$ErrorActionPreference = "Stop"

function Invoke-LoggedNativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$LogFile,

        [switch]$Append
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        if ($Append) {
            & $Executable @Arguments 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $LogFile -Append
        }
        else {
            & $Executable @Arguments 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $LogFile
        }
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

$projectRoot = $PSScriptRoot
if (-not $RunName) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $RunName = ("nafsa_{0}_{1}" -f $Country.ToLowerInvariant(), $stamp)
}

if (-not $OutputCsv) {
    $OutputCsv = "{0}.csv" -f $RunName
}

$coverageRoot = Join-Path $projectRoot "coverage_runs"
$runRoot = Join-Path $coverageRoot $RunName
$coverDir = Join-Path $runRoot "html"
$debugDir = Join-Path $projectRoot ("debug_logs\{0}" -f $RunName)
$logPath = Join-Path $runRoot "run.log"
$reportPath = Join-Path $runRoot "coverage_report.txt"
$jsonPath = Join-Path $runRoot "coverage.json"
$dataFile = Join-Path $runRoot ".coverage"

New-Item -ItemType Directory -Force -Path $coverageRoot | Out-Null
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
if (-not $NoDebug) {
    New-Item -ItemType Directory -Force -Path $debugDir | Out-Null
}

Push-Location $projectRoot
try {
    $previousNativeErrorPreference = $null
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    $coverageCheckExit = Invoke-LoggedNativeCommand -Executable $PythonExe -Arguments @("-c", "import sys, coverage; print(sys.executable); print(coverage.__version__)") -LogFile $logPath
    if ($coverageCheckExit -ne 0) {
        throw "Coverage is not available in the selected interpreter: $PythonExe"
    }

    $runArgs = @(
        "-m", "coverage", "run",
        "--source=gc_contacts,gc_contacts_cli",
        "--data-file=$dataFile",
        "gc_contacts_cli.py", "nafsa", $Country.ToUpperInvariant(),
        "--source", $Source,
        "--output", $OutputCsv,
        "--per-target-max", "$PerTargetMax",
        "--concurrency", "$Concurrency",
        "--discovery-mode", $DiscoveryMode
    )

    if ($Limit -gt 0) {
        $runArgs += @("--limit", "$Limit")
    }
    if ($Source -eq "companies" -and $CompaniesCsv) {
        $runArgs += @("--companies-csv", $CompaniesCsv)
    }
    if ($Classify) {
        $runArgs += "--classify"
    }
    if ($IgnoreRobots) {
        $runArgs += "--ignore-robots"
    }
    if (-not $NoDebug) {
        $runArgs += @("--debug", "--debug-dir", $debugDir)
    }

    "Running: $PythonExe $($runArgs -join ' ')" | Tee-Object -FilePath $logPath -Append
    $runExit = Invoke-LoggedNativeCommand -Executable $PythonExe -Arguments $runArgs -LogFile $logPath -Append
    if ($runExit -ne 0) {
        throw "Covered NAFSA run failed with exit code $runExit"
    }

    $reportExit = Invoke-LoggedNativeCommand -Executable $PythonExe -Arguments @("-m", "coverage", "report", "--data-file=$dataFile") -LogFile $reportPath
    if ($reportExit -ne 0) {
        throw "coverage report failed"
    }

    $htmlExit = Invoke-LoggedNativeCommand -Executable $PythonExe -Arguments @("-m", "coverage", "html", "--data-file=$dataFile", "-d", $coverDir) -LogFile $logPath -Append
    if ($htmlExit -ne 0) {
        throw "coverage html failed"
    }

    $jsonExit = Invoke-LoggedNativeCommand -Executable $PythonExe -Arguments @("-m", "coverage", "json", "--data-file=$dataFile", "-o", $jsonPath) -LogFile $logPath -Append
    if ($jsonExit -ne 0) {
        throw "coverage json failed"
    }

    Write-Host ""
    Write-Host "Coverage run complete."
    Write-Host "  CSV:      $OutputCsv"
    Write-Host "  Data:     $dataFile"
    Write-Host "  Report:   $reportPath"
    Write-Host "  HTML:     $coverDir\index.html"
    Write-Host "  JSON:     $jsonPath"
    if (-not $NoDebug) {
        Write-Host "  Debug:    $debugDir"
    }
}
finally {
    if ($null -ne $previousNativeErrorPreference) {
        $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
    }
    Pop-Location
}
