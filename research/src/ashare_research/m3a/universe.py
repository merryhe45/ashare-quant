"""Retrospective listing eligibility, distinct from tradability evidence."""
from datetime import date
from hashlib import sha256

import polars as pl


def eligibility_reason(instrument: dict, day: date) -> str:
    if instrument["asset_type"] != "stock":
        return "non_stock"
    if instrument["exchange"] not in ("SH", "SZ"):
        return "excluded_market"
    if instrument["list_date"] is None:
        return "unknown_listing"
    if day < instrument["list_date"]:
        return "not_yet_listed"
    if instrument["delist_date"] is not None and day > instrument["delist_date"]:
        return "no_longer_listed"
    return "eligible"


def select_cohort(instruments: pl.DataFrame, cohort_date: date, seed: str, per_exchange=50):
    if instruments.height != instruments["symbol"].n_unique():
        raise ValueError("Instrument symbols must be unique")
    if instruments.filter(pl.col("delist_date").is_not_null() & (pl.col("list_date") > pl.col("delist_date"))).height:
        raise ValueError("Invalid instrument listing interval")
    candidates = []
    for row in instruments.to_dicts():
        if eligibility_reason(row, cohort_date) == "eligible":
            # No price, return, availability, or future-survival input to ordering.
            row["selection_hash"] = sha256((seed + "\0" + row["symbol"]).encode()).hexdigest()
            candidates.append(row)
    selected = []
    for exchange in ("SH", "SZ"):
        group = sorted((r for r in candidates if r["exchange"] == exchange), key=lambda r: (r["selection_hash"], r["symbol"]))
        if len(group) < per_exchange:
            raise ValueError(f"Insufficient historical candidates for {exchange}")
        selected.extend(group[:per_exchange])
    return pl.DataFrame(candidates, schema_overrides={"list_date": pl.Date, "delist_date": pl.Date}), pl.DataFrame(selected, schema_overrides={"list_date": pl.Date, "delist_date": pl.Date}).sort("symbol")


def universe_counts(instruments: pl.DataFrame, days: list[date]) -> pl.DataFrame:
    """Metadata only: interval counts, no full-market price panel."""
    records = instruments.to_dicts()
    reasons = ["non_stock", "excluded_market", "unknown_listing", "not_yet_listed", "no_longer_listed", "eligible"]
    rows = []
    for day in days:
        counts = dict.fromkeys(reasons, 0)
        for r in records:
            counts[eligibility_reason(r, day)] += 1
        rows.append({"trade_date": day, **counts})
    return pl.DataFrame(rows)


def add_eligibility(grid: pl.DataFrame, selected: pl.DataFrame) -> pl.DataFrame:
    d = grid.join(selected.select("symbol", "list_date", "delist_date"), on="symbol", how="left", validate="m:1")
    return d.with_columns(
        ((pl.col("trade_date") >= pl.col("list_date")) & (pl.col("delist_date").is_null() | (pl.col("trade_date") <= pl.col("delist_date")))).alias("listing_eligible")
    )
