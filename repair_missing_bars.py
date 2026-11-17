from datetime import date
from pathlib import Path
import json
import sqlite3

import polars as pl

from ashare_lake.config import load_config
from ashare_lake.adapters.baostock.delisted_bars import fetch_delisted_bars
from ashare_lake.steps.http_common import write_fetched


CONFIG_PATH = r"C:\Users\merry\configs\ashare-lake.toml"
MAIN_RUN_ID = "71eabce3-8585-4414-9a40-371ef2d60d91"
REPAIR_RUN_ID = "manual-baostock-repair-117"

START = date(2016, 1, 1)
END = date(2026, 8, 15)


def get_missing_symbols(config) -> list[str]:
    db = Path(config.data_root) / "meta" / "manifest.db"

    con = sqlite3.connect(db)

    rows = con.execute(
        """
        SELECT symbols_json
        FROM ingestion_batches
        WHERE run_id = ?
          AND dataset = 'daily_bars'
          AND status = 'failed'
        """,
        (MAIN_RUN_ID,),
    ).fetchall()

    con.close()

    failed_symbols = set()

    for (symbols_json,) in rows:
        failed_symbols.update(json.loads(symbols_json))

    # Existing main-run staging bars
    bars_root = (
        Path(config.data_root)
        / "staging"
        / "daily_bars"
        / f"run_id={MAIN_RUN_ID}"
    )

    files = list(bars_root.glob("*.parquet"))

    bars = (
        pl.scan_parquet([str(f) for f in files])
        .select("symbol")
        .collect()
    )

    present = set(bars["symbol"].unique().to_list())
    missing = sorted(failed_symbols - present)

    # Instruments metadata
    inst_root = Path(config.data_root) / "curated" / "instruments"
    inst_files = list(inst_root.rglob("*.parquet"))

    instruments = pl.scan_parquet(
        [str(f) for f in inst_files]
    ).collect()

    cutoff = date(2016, 1, 1)

    should_have_history = (
        instruments
        .filter(pl.col("symbol").is_in(missing))
        .filter(
            pl.col("delist_date").is_null()
            | (pl.col("delist_date") >= cutoff)
        )
        .select("symbol")
    )

    return sorted(
        should_have_history["symbol"].to_list()
    )


def main():
    config = load_config(CONFIG_PATH)

    symbols = get_missing_symbols(config)

    print("Symbols to repair:", len(symbols))

    rows, failed = fetch_delisted_bars(
        symbols,
        START,
        END,
        config=config,
    )

    print("Rows fetched:", len(rows))
    print("Failed symbols:", len(failed))

    if failed:
        print("FAILED:")
        for s in failed:
            print("  ", s)

    if not rows:
        print("No rows returned. Nothing written.")
        return

    df = pl.DataFrame(rows)

    print("Recovered symbols:", df["symbol"].n_unique())
    print("Min date:", df["trade_date"].min())
    print("Max date:", df["trade_date"].max())

    result = write_fetched(
        config,
        REPAIR_RUN_ID,
        "daily_bars",
        df,
        source="baostock",
        batch_id="missing-117",
    )

    print("Write result:")
    print(result)

    recovered = set(df["symbol"].unique().to_list())
    still_missing = sorted(set(symbols) - recovered)

    print()
    print("Expected symbols:", len(symbols))
    print("Recovered symbols:", len(recovered))
    print("Still missing:", len(still_missing))

    if still_missing:
        print("STILL MISSING:")
        for s in still_missing:
            print("  ", s)


if __name__ == "__main__":
    main()