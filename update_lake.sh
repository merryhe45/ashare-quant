#!/bin/bash

set -u
set -o pipefail

CONFIG="${ASHARE_LAKE_CONFIG:-/app/config/ashare-lake.toml}"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

echo
echo "====================================="
echo "A-SHARE DATA LAKE UPDATE"
echo "====================================="
echo

# ============================================================
# PRE-FLIGHT
# ============================================================

if [ ! -f "$CONFIG" ]; then
    echo "ERROR: ashare-lake config not found: $CONFIG" >&2
    exit 1
fi

if ! command -v asl >/dev/null 2>&1; then
    echo "ERROR: The 'asl' command was not found on PATH." >&2
    exit 1
fi

echo "Config:"
echo "$CONFIG"
echo


# ============================================================
# STEP 1
# Determine latest A-share trading day
# ============================================================

echo "STEP 1: Determining latest trading day..."
echo

# Use the same calendar resolver as ashare-lake 0.5.0 status, without
# inspecting the dataset catalog. Keep errors separate from the date output.
TRADE_DATE=$(python "$(dirname "${BASH_SOURCE[0]}")/docker/lake_trade_date.py" \
    --config "$CONFIG")
CALENDAR_EXIT_CODE=$?

if [ "$CALENDAR_EXIT_CODE" -ne 0 ] || [ -z "$TRADE_DATE" ]; then
    echo "ERROR: Could not determine latest trading day (calendar exit code: $CALENDAR_EXIT_CODE)." >&2
    exit 1
fi

echo
echo "Target trading day: $TRADE_DATE"
echo

export QUANT_TRADE_DATE="$TRADE_DATE"


# ============================================================
# STEP 2
# Update stale daily datasets
# ============================================================

echo "STEP 2: Updating stale datasets to $TRADE_DATE..."
echo

asl run daily \
    --config "$CONFIG" \
    --trade-date "$TRADE_DATE" \
    --stale-only

DAILY_EXIT_CODE=$?

if [ "$DAILY_EXIT_CODE" -ne 0 ]; then
    echo
    echo "====================================="
    echo "DATA LAKE UPDATE FAILED"
    echo "====================================="
    echo
    echo "ERROR: asl run daily returned exit code $DAILY_EXIT_CODE." >&2
    exit "$DAILY_EXIT_CODE"
fi

echo
echo "Daily datasets updated successfully."
echo


# ============================================================
# STEP 3
# Derive adjustment factors
# ============================================================

echo "STEP 3: Updating adjustment factors..."
echo

asl derive adj_factors \
    --config "$CONFIG"

ADJ_EXIT_CODE=$?

if [ "$ADJ_EXIT_CODE" -ne 0 ]; then
    echo
    echo "====================================="
    echo "ADJ FACTOR UPDATE FAILED"
    echo "====================================="
    echo
    echo "ERROR: asl derive adj_factors returned exit code $ADJ_EXIT_CODE." >&2
    exit "$ADJ_EXIT_CODE"
fi

echo
echo "Adjustment factors updated successfully."
echo


# ============================================================
# STEP 4
# Final freshness check
# ============================================================

echo "STEP 4: Final Data Lake status..."
echo

FINAL_STATUS_OUTPUT=$(asl status \
    --config "$CONFIG" \
    --datasets 2>&1)
FINAL_STATUS_EXIT_CODE=$?

echo "$FINAL_STATUS_OUTPUT"
echo

if [ "$FINAL_STATUS_EXIT_CODE" -ne 0 ]; then
    echo "====================================="
    echo "DATA LAKE STILL HAS STALE DATASETS"
    echo "====================================="
    echo
    echo "asl status exit code: $FINAL_STATUS_EXIT_CODE"
    echo
    echo "ERROR: Update completed, but one or more datasets are still stale." >&2
    echo "Quant pipeline will NOT run because the Data Lake is not fully current." >&2
    exit 1
fi


# ============================================================
# COMPLETE
# ============================================================

echo
echo "====================================="
echo "DATA LAKE UPDATE COMPLETE"
echo "Trade date: $TRADE_DATE"
echo "====================================="
echo

exit 0
