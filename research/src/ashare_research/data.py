"""Read curated files directly, avoiding lake caches, ingestion and writes."""
from datetime import date
from pathlib import Path
from collections import Counter
import hashlib

import polars as pl


def load_prices(root: Path, symbols: list[str], start: str, end: str):
    """Return a calendar grid, exclusion counts and hashes of every input file.

    Every absent/invalid observation stays null on the grid. No raw-price
    fallback, interpolation, forward filling or status-based fill assumption.
    """
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    if lo > hi or not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("Require ordered dates and a nonempty unique symbol list")
    if len(symbols) > 100 or (hi - lo).days > 1100:
        raise ValueError("Exploratory guard: at most 100 symbols and 1100 calendar days")
    fingerprints = {}
    counts = Counter()

    def read(path):
        fingerprints[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return pl.read_parquet(path, hive_partitioning=False)

    calendars = sorted((root / "curated/trading_calendar").glob("trade_date=*/*.parquet"))
    frames = [read(p) for p in calendars if int(start[:4]) <= int(p.parent.name.split("=")[1]) <= int(end[:4])]
    if not frames:
        raise ValueError("No calendar files")
    cal = pl.concat(frames).filter(pl.col("trade_date").is_between(lo, hi))
    if cal.height != cal.unique("trade_date").height:
        raise ValueError("Duplicate calendar dates")
    # A missing closed day could otherwise silently hide a missing open day.
    if cal.height != (hi - lo).days + 1:
        raise ValueError("Calendar does not cover every requested calendar date")
    dates = cal.filter(pl.col("is_trading")).select("trade_date").sort("trade_date")
    if dates.is_empty():
        raise ValueError("No trading sessions")
    pieces = []
    for day in dates["trade_date"]:
        def partition(layer):
            folder = root / layer / f"trade_date={day.isoformat()}"
            files = sorted(folder.glob("*.parquet"))
            return pl.concat([read(p) for p in files]) if files else None

        bars = partition("curated/daily_bars")
        factors = partition("derived/adj_factors")
        if bars is None:
            continue
        required = {"symbol", "trade_date", "open", "high", "low", "close"}
        if not required.issubset(bars.columns):
            raise ValueError("Missing required bar columns")
        bars = bars.filter(pl.col("symbol").is_in(symbols))
        if bars.height != bars.unique(["symbol", "trade_date"]).height:
            raise ValueError("Duplicate bar keys")
        if bars.filter(pl.col("trade_date") != day).height:
            raise ValueError("Bar partition/date mismatch")
        if factors is None:
            factors = pl.DataFrame(schema={"symbol": pl.String, "trade_date": pl.Date, "factor": pl.Float64})
        else:
            if not {"symbol", "trade_date", "factor", "adjust_type"}.issubset(factors.columns):
                raise ValueError("Missing required adjustment columns")
            factors = factors.filter(pl.col("symbol").is_in(symbols) & (pl.col("adjust_type") == "hfq"))
            if factors.height != factors.unique(["symbol", "trade_date"]).height:
                raise ValueError("Duplicate factor keys")
        joined = bars.join(factors.select("symbol", "trade_date", "factor"), on=["symbol", "trade_date"], how="left")
        valid_price = pl.all_horizontal([pl.col(c).is_not_null() & pl.col(c).is_finite() & (pl.col(c) > 0) for c in ["open", "high", "low", "close"]]) & (pl.col("high") >= pl.max_horizontal("open", "close", "low")) & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
        valid_factor = pl.col("factor").is_not_null() & pl.col("factor").is_finite() & (pl.col("factor") > 0)
        counts["invalid_ohlc"] += joined.filter(~valid_price).height
        counts["missing_adjustment"] += joined["factor"].null_count()
        counts["invalid_adjustment"] += joined.filter(pl.col("factor").is_not_null() & ~valid_factor).height
        pieces.append(joined.select("symbol", "trade_date", pl.when(valid_price & valid_factor).then(pl.col("close") * pl.col("factor")).otherwise(None).alias("analytical_close")))
    grid = pl.DataFrame({"symbol": symbols}).join(dates, how="cross").sort("symbol", "trade_date")
    obs = pl.concat(pieces) if pieces else pl.DataFrame(schema={"symbol": pl.String, "trade_date": pl.Date, "analytical_close": pl.Float64})
    counts["missing_bar"] = grid.join(obs, on=["symbol", "trade_date"], how="anti").height
    result = grid.join(obs, on=["symbol", "trade_date"], how="left")
    counts["unusable_price_sessions"] = result["analytical_close"].null_count()
    return result, dict(counts), fingerprints
