param(
    [string]$Config = $env:ASHARE_LAKE_CONFIG
)

$ErrorActionPreference = "Continue"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"


# ============================================================
# PROJECT PATHS
# ============================================================

# Portable:
# project directory = directory containing this script.
$ProjectDir = $PSScriptRoot

$UpdateScript = Join-Path `
    $ProjectDir `
    "update_lake.ps1"

$PipelineScript = Join-Path `
    $ProjectDir `
    "run_pipeline_v2.py"


Write-Host ""
Write-Host "====================================="
Write-Host "A-SHARE DAILY QUANT WORKFLOW"
Write-Host "====================================="
Write-Host ""

Set-Location $ProjectDir


# ============================================================
# PRE-FLIGHT CHECKS
# ============================================================

if ([string]::IsNullOrWhiteSpace($Config)) {

    Write-Error @"
ashare-lake config was not specified.

Use either:

  .\daily_quant.ps1 -Config "C:\path\to\ashare-lake.toml"

or set the ASHARE_LAKE_CONFIG environment variable.
"@

    exit 1
}

if (-not (Test-Path $Config)) {

    Write-Error "ashare-lake config not found: $Config"
    exit 1
}

if (-not (Test-Path $UpdateScript)) {

    Write-Error "update_lake.ps1 not found: $UpdateScript"
    exit 1
}

if (-not (Test-Path $PipelineScript)) {

    Write-Error "run_pipeline_v2.py not found: $PipelineScript"
    exit 1
}

$PythonCommand = Get-Command python `
    -ErrorAction SilentlyContinue

if (-not $PythonCommand) {

    Write-Error "Global Python command was not found."
    exit 1
}

$PythonExe = $PythonCommand.Source


Write-Host "Project:"
Write-Host $ProjectDir
Write-Host ""

Write-Host "Config:"
Write-Host $Config
Write-Host ""

Write-Host "Python:"
Write-Host $PythonExe
Write-Host ""


# ============================================================
# STEP 1
# Update Data Lake
# ============================================================

Write-Host "STEP 1: Updating Data Lake..."
Write-Host ""

# Remove any stale value inherited from a previous run.
Remove-Item Env:QUANT_TRADE_DATE `
    -ErrorAction SilentlyContinue

& $UpdateScript `
    -Config $Config

$UpdateExitCode = $LASTEXITCODE

if ($UpdateExitCode -ne 0) {

    Write-Host ""
    Write-Host "====================================="
    Write-Host "DATA LAKE UPDATE FAILED"
    Write-Host "====================================="
    Write-Host ""

    Write-Error "Quant pipeline will NOT run."
    exit $UpdateExitCode
}


# ============================================================
# STEP 2
# Get exact trade date produced by update script
# ============================================================

$TradeDate = $env:QUANT_TRADE_DATE

if ([string]::IsNullOrWhiteSpace($TradeDate)) {

    Write-Error @"
update_lake.ps1 completed but QUANT_TRADE_DATE was not set.

Quant pipeline will NOT run because the exact updated trading date
cannot be verified.
"@

    exit 1
}

Write-Host ""
Write-Host "====================================="
Write-Host "DATA LAKE READY"
Write-Host "Trade date: $TradeDate"
Write-Host "====================================="
Write-Host ""


# ============================================================
# STEP 3
# Verify Quant Engine v2 imports
# ============================================================

Write-Host "STEP 2: Checking Quant Engine v2..."
Write-Host ""

$PythonCheck = @'
import polars
import ashare_lake
from factor_registry import FACTOR_REGISTRY

print('Polars:', polars.__version__)
print('Factors:', list(FACTOR_REGISTRY.keys()))
'@

& $PythonExe -c $PythonCheck

$ImportExitCode = $LASTEXITCODE

if ($ImportExitCode -ne 0) {

    Write-Host ""
    Write-Host "====================================="
    Write-Host "QUANT ENGINE CHECK FAILED"
    Write-Host "====================================="
    Write-Host ""

    Write-Error "Required Python modules could not be loaded."
    exit $ImportExitCode
}


# ============================================================
# STEP 4
# Run Quant Engine v2 using exact updated trade date
# ============================================================

Write-Host ""
Write-Host "STEP 3: Running Quant Engine v2..."
Write-Host ""

Write-Host "Scan date: $TradeDate"
Write-Host ""

$PipelineStart = Get-Date

& $PythonExe `
    $PipelineScript `
    --scan-date $TradeDate `
    --config $Config

$PipelineExitCode = $LASTEXITCODE

$PipelineEnd = Get-Date

$PipelineDuration = $PipelineEnd - $PipelineStart

if ($PipelineExitCode -ne 0) {

    Write-Host ""
    Write-Host "====================================="
    Write-Host "QUANT PIPELINE FAILED"
    Write-Host "====================================="
    Write-Host ""

    Write-Error "run_pipeline_v2.py returned exit code $PipelineExitCode."
    exit $PipelineExitCode
}


# ============================================================
# COMPLETE
# ============================================================

Write-Host ""
Write-Host "====================================="
Write-Host "DAILY QUANT WORKFLOW COMPLETE"
Write-Host "====================================="
Write-Host ""

Write-Host "Trade date: $TradeDate"

Write-Host (
    "Quant runtime: {0:N2} seconds" -f `
    $PipelineDuration.TotalSeconds
)

Write-Host ""

exit 0