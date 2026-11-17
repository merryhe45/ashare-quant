param(
    [string]$Config = $env:ASHARE_LAKE_CONFIG
)

$ErrorActionPreference = "Continue"

# Force Python / ASL subprocesses to use UTF-8 on Windows.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"


# ============================================================
# PRE-FLIGHT
# ============================================================

if ([string]::IsNullOrWhiteSpace($Config)) {

    Write-Host ""
    Write-Error @"
ashare-lake config was not specified.

Use either:

  .\update_lake.ps1 -Config "C:\path\to\ashare-lake.toml"

or set:

  ASHARE_LAKE_CONFIG=C:\path\to\ashare-lake.toml
"@

    exit 1
}

if (-not (Test-Path $Config)) {

    Write-Error "ashare-lake config not found: $Config"
    exit 1
}

$AslCommand = Get-Command asl -ErrorAction SilentlyContinue

if (-not $AslCommand) {

    Write-Error @"
The 'asl' command was not found.

Install/configure ashare-lake and make sure 'asl' is available on PATH.
"@

    exit 1
}


Write-Host ""
Write-Host "====================================="
Write-Host "A-SHARE DATA LAKE UPDATE"
Write-Host "====================================="
Write-Host ""

Write-Host "Config:"
Write-Host $Config
Write-Host ""


# ============================================================
# STEP 1
# Determine latest A-share trading day
# ============================================================

Write-Host "STEP 1: Determining latest trading day..."
Write-Host ""

$StatusOutput = & asl status `
    --config $Config `
    --datasets 2>&1

$StatusExitCode = $LASTEXITCODE

$StatusText = $StatusOutput | Out-String

$StatusOutput | ForEach-Object {
    Write-Host $_
}

# asl status may return non-zero when datasets are stale.
# That is expected before the update.

if ($StatusText -match 'last trading day:\s*(\d{4}-\d{2}-\d{2})') {

    $TradeDate = $Matches[1]

}
else {

    Write-Host ""
    Write-Host "====================================="
    Write-Host "FAILED TO DETERMINE TRADE DATE"
    Write-Host "====================================="
    Write-Host ""

    Write-Host "asl status exit code: $StatusExitCode"
    Write-Host ""

    Write-Error "Could not find 'last trading day' in asl status output."
    exit 1
}

Write-Host ""
Write-Host "Target trading day: $TradeDate"
Write-Host ""

# Return exact date to the parent workflow.
$env:QUANT_TRADE_DATE = $TradeDate


# ============================================================
# STEP 2
# Update stale daily datasets
# ============================================================

Write-Host "STEP 2: Updating stale datasets to $TradeDate..."
Write-Host ""

& asl run daily `
    --config $Config `
    --trade-date $TradeDate `
    --stale-only

$DailyExitCode = $LASTEXITCODE

if ($DailyExitCode -ne 0) {

    Write-Host ""
    Write-Host "====================================="
    Write-Host "DATA LAKE UPDATE FAILED"
    Write-Host "====================================="
    Write-Host ""

    Write-Error "asl run daily returned exit code $DailyExitCode."
    exit $DailyExitCode
}

Write-Host ""
Write-Host "Daily datasets updated successfully."
Write-Host ""


# ============================================================
# STEP 3
# Derive adjustment factors
# ============================================================

Write-Host "STEP 3: Updating adjustment factors..."
Write-Host ""

& asl derive adj_factors `
    --config $Config

$AdjExitCode = $LASTEXITCODE

if ($AdjExitCode -ne 0) {

    Write-Host ""
    Write-Host "====================================="
    Write-Host "ADJ FACTOR UPDATE FAILED"
    Write-Host "====================================="
    Write-Host ""

    Write-Error "asl derive adj_factors returned exit code $AdjExitCode."
    exit $AdjExitCode
}

Write-Host ""
Write-Host "Adjustment factors updated successfully."
Write-Host ""


# ============================================================
# STEP 4
# Final freshness check
# ============================================================

Write-Host "STEP 4: Final Data Lake status..."
Write-Host ""

$FinalStatusOutput = & asl status `
    --config $Config `
    --datasets 2>&1

$FinalStatusExitCode = $LASTEXITCODE

$FinalStatusOutput | ForEach-Object {
    Write-Host $_
}

Write-Host ""

if ($FinalStatusExitCode -ne 0) {

    Write-Host "====================================="
    Write-Host "DATA LAKE STILL HAS STALE DATASETS"
    Write-Host "====================================="
    Write-Host ""

    Write-Host "asl status exit code: $FinalStatusExitCode"
    Write-Host ""

    Write-Error @"
The update commands completed, but asl status still reports
one or more stale datasets.

Quant pipeline will NOT run because the Data Lake is not fully current.
"@

    exit 1
}


# ============================================================
# COMPLETE
# ============================================================

Write-Host ""
Write-Host "====================================="
Write-Host "DATA LAKE UPDATE COMPLETE"
Write-Host "Trade date: $TradeDate"
Write-Host "====================================="
Write-Host ""

exit 0