from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ashare_lake.config import load_config
from ashare_lake.query.reader import load

from factor_registry import (
    FACTOR_REGISTRY,
    get_max_lookback,
)


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_CONFIG_PATH = os.environ.get(
    "ASHARE_LAKE_CONFIG",    
)

LOOKBACK_BUFFER = 120

PROJECT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = PROJECT_DIR / "results"

JSON_SCHEMA_VERSION = "1.0"


# ============================================================
# VALIDATED REGRESSION BASELINE
# ============================================================

REGRESSION_BASELINES = {
    "2026-08-28": {
        "breakout_400": {
            "000017.SZ",
            "002084.SZ",
            "002396.SZ",
            "002742.SZ",
            "002870.SZ",
            "300562.SZ",
            "300864.SZ",
            "300951.SZ",
            "600127.SH",
            "600162.SH",
            "600354.SH",
            "600479.SH",
            "600613.SH",
            "601101.SH",
            "601233.SH",
            "601700.SH",
            "603333.SH",
            "603580.SH",
            "603679.SH",
            "603880.SH",
            "688209.SH",
        },
        "pit_signal": {
            "000887.SZ",
            "000949.SZ",
            "002250.SZ",
            "002350.SZ",
            "002832.SZ",
            "002850.SZ",
            "003033.SZ",
            "300017.SZ",
            "300246.SZ",
            "300467.SZ",
            "301218.SZ",
            "301345.SZ",
            "600362.SH",
            "601101.SH",
            "603979.SH",
            "688087.SH",
            "688133.SH",
            "688690.SH",
        },
    },
}


# ============================================================
# TIMING
# ============================================================

def elapsed(start: float) -> str:
    return f"{time.perf_counter() - start:.2f}s"


# ============================================================
# SCAN DATE
# ============================================================

def find_latest_trading_day(
    config_path: str,
) -> str:
    """
    Ask ashare-lake status for its latest trading day.

    This avoids probing an arbitrary stock and works correctly
    across weekends and non-trading days.
    """

    env = os.environ.copy()

    # Important on Windows:
    # prevent ASL / Click output from falling back to cp1252.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [
            "asl",
            "status",
            "--config",
            config_path,
            "--datasets",
        ],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
    )

    text = (
        (result.stdout or "")
        + "\n"
        + (result.stderr or "")
    )

    match = re.search(
        r"last trading day:\s*(\d{4}-\d{2}-\d{2})",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        raise RuntimeError(
            "Could not determine latest trading day "
            "from ashare-lake status."
        )

    return match.group(1)


# ============================================================
# HISTORY START DATE
# ============================================================

def calculate_start_date(
    scan_date: str,
) -> str:

    max_lookback = get_max_lookback()

    trading_days_needed = (
        max_lookback
        + LOOKBACK_BUFFER
    )

    calendar_days = int(
        trading_days_needed
        * 365
        / 245
    )

    scan_dt = datetime.strptime(
        scan_date,
        "%Y-%m-%d",
    )

    start_dt = scan_dt - timedelta(
        days=calendar_days
    )

    return start_dt.strftime("%Y-%m-%d")


# ============================================================
# GENERIC FACTOR RUNNER
# ============================================================

def run_factor_engine(
    history_df: pl.DataFrame,
    scan_date: str,
):
    """
    Run every enabled factor in FACTOR_REGISTRY.

    Adding a new factor should not require changes here.
    """

    working_df = history_df

    factor_results = {}

    scan_date_expr = (
        pl.lit(scan_date).str.to_date()
    )

    for factor_name, spec in FACTOR_REGISTRY.items():

        print("")
        print(
            f"Calculating {factor_name}..."
        )

        factor_start = time.perf_counter()

        working_df = spec.calculator(
            working_df
        )

        latest = (
            working_df
            .filter(
                pl.col("trade_date")
                == scan_date_expr
            )
        )

        evaluable = latest.filter(
            pl.col(spec.status_column)
            == "ready"
        )

        insufficient = latest.filter(
            pl.col(spec.status_column)
            != "ready"
        )

        selected = latest.filter(
            (
                pl.col(spec.status_column)
                == "ready"
            )
            & pl.col(spec.signal_column)
        )

        factor_results[factor_name] = {
            "spec": spec,
            "latest": latest,
            "evaluable": evaluable,
            "insufficient": insufficient,
            "selected": selected,
        }

        print(
            f"  evaluable: "
            f"{evaluable.height:,}"
        )

        print(
            f"  insufficient: "
            f"{insufficient.height:,}"
        )

        print(
            f"  selected: "
            f"{selected.height:,}"
        )

        print(
            f"  calculation time: "
            f"{elapsed(factor_start)}"
        )

    return factor_results


# ============================================================
# CONSOLE OUTPUT
# ============================================================

def print_results(
    scan_date: str,
    factor_results: dict,
):

    for result in factor_results.values():

        spec = result["spec"]
        selected = result["selected"]

        print("")
        print(
            "==================================="
        )
        print(spec.display_name)
        print(
            "==================================="
        )

        print("")
        print("Formula:")
        print(spec.formula_text)

        print("")
        print(
            f"Scan date: {scan_date}"
        )

        print(
            f"Selected count: "
            f"{selected.height:,}"
        )

        if selected.height == 0:
            continue

        columns = [
            col
            for col in spec.output_columns
            if col in selected.columns
        ]

        print("")

        print(
            selected
            .select(columns)
            .sort("symbol")
        )


# ============================================================
# REGRESSION
# ============================================================

def selected_symbol_set(
    selected: pl.DataFrame,
) -> set[str]:

    if (
        selected.is_empty()
        or "symbol" not in selected.columns
    ):
        return set()

    return set(
        selected
        .get_column("symbol")
        .to_list()
    )


def run_regression_check(
    scan_date: str,
    factor_results: dict,
) -> dict[str, Any]:

    baseline = REGRESSION_BASELINES.get(
        scan_date
    )

    if baseline is None:
        return {
            "applicable": False,
            "passed": None,
            "baseline_date": None,
            "factors": {},
        }

    print("")
    print(
        "==================================="
    )
    print(
        "REGRESSION CHECK"
    )
    print(
        "==================================="
    )

    regression_ok = True
    factor_checks = {}

    for factor_name, expected_symbols in baseline.items():

        if factor_name not in factor_results:

            regression_ok = False

            factor_checks[factor_name] = {
                "passed": False,
                "expected_count": len(
                    expected_symbols
                ),
                "actual_count": 0,
                "missing_symbols": sorted(
                    expected_symbols
                ),
                "unexpected_symbols": [],
            }

            print("")
            print(
                f"{factor_name}: "
                "[FAIL - factor missing]"
            )

            continue

        actual_symbols = selected_symbol_set(
            factor_results[factor_name]
            ["selected"]
        )

        missing_symbols = sorted(
            expected_symbols
            - actual_symbols
        )

        unexpected_symbols = sorted(
            actual_symbols
            - expected_symbols
        )

        passed = (
            actual_symbols
            == expected_symbols
        )

        if not passed:
            regression_ok = False

        factor_checks[factor_name] = {
            "passed": passed,
            "expected_count": len(
                expected_symbols
            ),
            "actual_count": len(
                actual_symbols
            ),
            "missing_symbols": (
                missing_symbols
            ),
            "unexpected_symbols": (
                unexpected_symbols
            ),
        }

        status = (
            "PASS"
            if passed
            else "FAIL"
        )

        print("")
        print(
            f"{factor_name}: "
            f"expected={len(expected_symbols)}, "
            f"actual={len(actual_symbols)} "
            f"[{status}]"
        )

        if missing_symbols:

            print(
                "  Missing symbols:"
            )

            for symbol in missing_symbols:
                print(
                    f"    - {symbol}"
                )

        if unexpected_symbols:

            print(
                "  Unexpected symbols:"
            )

            for symbol in unexpected_symbols:
                print(
                    f"    + {symbol}"
                )

    print("")

    if regression_ok:

        print(
            "Exact symbol sets match "
            "the validated baseline."
        )

    else:

        print(
            "WARNING: regression mismatch detected."
        )

        print(
            "The selected symbol set does not "
            "match the validated baseline."
        )

    return {
        "applicable": True,
        "passed": regression_ok,
        "baseline_date": scan_date,
        "factors": factor_checks,
    }


# ============================================================
# JSON SERIALIZATION
# ============================================================

def json_default(
    value: Any,
):

    if isinstance(
        value,
        (datetime, date),
    ):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    return str(value)


def factor_json_payload(
    factor_results: dict,
) -> dict[str, Any]:

    payload = {}

    for factor_name, result in factor_results.items():

        spec = result["spec"]
        selected = result["selected"]

        output_columns = [
            col
            for col in spec.output_columns
            if col in selected.columns
        ]

        selected_sorted = (
            selected
            .select(output_columns)
            .sort("symbol")
        )

        symbols = sorted(
            selected_symbol_set(
                selected
            )
        )

        payload[factor_name] = {
            "display_name": (
                spec.display_name
            ),
            "lookback": (
                spec.lookback
            ),
            "formula": (
                spec.formula_text
            ),
            "signal_column": (
                spec.signal_column
            ),
            "status_column": (
                spec.status_column
            ),
            "evaluable_count": (
                result["evaluable"].height
            ),
            "insufficient_count": (
                result["insufficient"].height
            ),
            "selected_count": (
                selected.height
            ),
            "symbols": symbols,
            "selected_rows": (
                selected_sorted.to_dicts()
            ),
        }

    return payload


def write_json_atomic(
    path: Path,
    payload: dict[str, Any],
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temp_path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
            default=json_default,
        )

        file.write("\n")

    temp_path.replace(path)


def persist_results(
    *,
    scan_date: str,    
    start_date: str,
    current_symbols: list[str],
    loaded_symbols: int,
    history_rows: int,
    current_rows: int,
    factor_results: dict,
    regression_result: dict[str, Any],
    runtime_seconds: float,
) -> tuple[Path, Path]:

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "schema_version": (
            JSON_SCHEMA_VERSION
        ),
        "engine": (
            "ashare-quant-v2"
        ),
        "generated_at": (
            datetime
            .now()
            .astimezone()
            .isoformat()
        ),
        "trade_date": (
            scan_date
        ),
        "runtime_seconds": round(
            runtime_seconds,
            4,
        ),
        "data": {            
            "history_start_date": (
                start_date
            ),
            "history_end_date": (
                scan_date
            ),
            "maximum_registered_lookback": (
                get_max_lookback()
            ),
            "universe_count": (
                len(current_symbols)
            ),
            "current_rows": (
                current_rows
            ),
            "historical_rows": (
                history_rows
            ),
            "historical_symbols": (
                loaded_symbols
            ),
        },
        "factors": factor_json_payload(
            factor_results
        ),
        "regression": (
            regression_result
        ),
    }

    dated_path = (
        RESULTS_DIR
        / f"quant_{scan_date}.json"
    )

    latest_path = (
        RESULTS_DIR
        / "latest.json"
    )

    write_json_atomic(
        dated_path,
        payload,
    )

    # Do not publish a failed validated regression
    # as "latest".
    regression_failed = (
        regression_result["applicable"]
        and regression_result["passed"] is False
    )

    if regression_failed:

        print("")
        print(
            "Regression failed: "
            "latest.json was NOT replaced."
        )

    else:

        write_json_atomic(
            latest_path,
            payload,
        )

    return (
        dated_path,
        latest_path,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
    "--config",
    type=str,
    default=DEFAULT_CONFIG_PATH,
    help=(
        "Path to ashare-lake TOML config. "
        "Default: ASHARE_LAKE_CONFIG environment variable."
       ),
    )    

    parser.add_argument(
    "--scan-date",
    type=str,
    default=None,
    help=(
        "Trading date to scan in YYYY-MM-DD format. "
        "If omitted, use the latest trading day from ashare-lake status."
    ),
)
    args = parser.parse_args()

    config_path = args.config

    if not config_path:
        raise RuntimeError(
            "ashare-lake config was not specified. "
            "Use --config or set ASHARE_LAKE_CONFIG."
        )

    total_start = time.perf_counter()

    print("")
    print(
        "==================================="
    )
    print(
        "A-SHARE QUANT ENGINE V2"
    )
    print(
        "==================================="
    )

    print("")
    print(
        f"Config: {config_path}"
    )

    config = load_config(
        config_path
    )

    # ========================================================
    # STEP 1
    # Scan date
    # ========================================================

    print("")
    print(
        "STEP 1: Determining scan date..."
    )

    if args.scan_date:
        scan_date = args.scan_date
    else:
        scan_date = (
            find_latest_trading_day(
                config_path
            )
        )

    print(
        f"Scan date: {scan_date}"
    )

    # ========================================================
    # STEP 2
    # Current A-share universe
    # ========================================================

    print("")
    print(
        "STEP 2: Loading current "
        "A-share universe..."
    )

    universe_start = time.perf_counter()

    current_df = load(
        "daily_bars",
        config=config,
        start=scan_date,
        end=scan_date,
        universe="all_a",
        adjust="hfq",
    )

    if current_df.is_empty():

        raise RuntimeError(
            f"No A-share data found for "
            f"{scan_date}."
        )

    current_symbols = (
        current_df
        .select("symbol")
        .unique()
        .sort("symbol")
        .get_column("symbol")
        .to_list()
    )

    print(
        f"A-share universe: "
        f"{len(current_symbols):,}"
    )

    print(
        f"Universe load time: "
        f"{elapsed(universe_start)}"
    )

    # ========================================================
    # STEP 3
    # Complete historical data
    # ========================================================

    print("")
    print(
        "STEP 3: Loading complete "
        "historical bars..."
    )

    start_date = calculate_start_date(
        scan_date
    )

    print(
        f"Historical range: "
        f"{start_date} -> {scan_date}"
    )

    print(
        f"Maximum registered lookback: "
        f"{get_max_lookback()}"
    )

    print(
        f"Symbols requested: "
        f"{len(current_symbols):,}"
    )

    history_start = time.perf_counter()

    history_df = load(
        "daily_bars",
        config=config,
        start=start_date,
        end=scan_date,
        symbols=current_symbols,

        # IMPORTANT:
        # Never apply historical universe
        # filtering here.
        universe=None,

        adjust="hfq",
    )

    print(
        f"Historical rows loaded: "
        f"{history_df.height:,}"
    )

    loaded_symbols = (
        history_df
        .select("symbol")
        .unique()
        .height
    )

    print(
        f"Historical symbols loaded: "
        f"{loaded_symbols:,}"
    )

    print(
        f"Historical load time: "
        f"{elapsed(history_start)}"
    )

    # ========================================================
    # STEP 4
    # Factor engine
    # ========================================================

    print("")
    print(
        "STEP 4: Running factor engine..."
    )

    factor_start = time.perf_counter()

    factor_results = run_factor_engine(
        history_df,
        scan_date,
    )

    print("")

    print(
        f"Total factor calculation time: "
        f"{elapsed(factor_start)}"
    )

    # ========================================================
    # STEP 5
    # Universe summary
    # ========================================================

    print("")
    print(
        "==================================="
    )
    print(
        "UNIVERSE SUMMARY"
    )
    print(
        "==================================="
    )

    print(
        f"A-share universe: "
        f"{len(current_symbols):,}"
    )

    print(
        f"Historical symbols loaded: "
        f"{loaded_symbols:,}"
    )

    print(
        f"Current on scan date: "
        f"{current_df.height:,}"
    )

    # ========================================================
    # STEP 6
    # Results
    # ========================================================

    print_results(
        scan_date,
        factor_results,
    )

    # ========================================================
    # STEP 7
    # Exact-symbol regression
    # ========================================================

    regression_result = (
        run_regression_check(
            scan_date,
            factor_results,
        )
    )

    # ========================================================
    # STEP 8
    # JSON persistence
    # ========================================================

    print("")
    print(
        "==================================="
    )
    print(
        "JSON RESULT PERSISTENCE"
    )
    print(
        "==================================="
    )

    runtime_before_json = (
        time.perf_counter()
        - total_start
    )

    dated_path, latest_path = (
        persist_results(
            scan_date=scan_date,            
            start_date=start_date,
            current_symbols=current_symbols,
            loaded_symbols=loaded_symbols,
            history_rows=history_df.height,
            current_rows=current_df.height,
            factor_results=factor_results,
            regression_result=regression_result,
            runtime_seconds=runtime_before_json,
        )
    )

    print(
        f"Saved: {dated_path}"
    )

    regression_failed = (
        regression_result["applicable"]
        and regression_result["passed"] is False
    )

    if not regression_failed:

        print(
            f"Latest: {latest_path}"
        )

    # ========================================================
    # COMPLETE
    # ========================================================

    print("")
    print(
        "==================================="
    )
    print(
        "QUANT ENGINE V2 COMPLETE"
    )
    print(
        "==================================="
    )

    print(
        f"Total runtime: "
        f"{elapsed(total_start)}"
    )

    # On our validated baseline date,
    # regression mismatch is considered
    # a real test failure.
    if regression_failed:

        raise RuntimeError(
            "Exact-symbol regression failed."
        )


if __name__ == "__main__":
    main()