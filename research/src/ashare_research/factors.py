"""Historical factors only. This module never imports forward labels."""
import polars as pl


def momentum(prices: pl.DataFrame, lookback: int = 60) -> pl.DataFrame:
    """P(t)/P(t-60)-1; require all 61 calendar-grid prices, including endpoints."""
    if lookback < 1:
        raise ValueError("lookback must be positive")
    d = prices.select("symbol", "trade_date", "analytical_close").sort("symbol", "trade_date")
    return d.with_columns(
        pl.when(pl.col("analytical_close").is_not_null().cast(pl.Int64).rolling_sum(lookback + 1).over("symbol") == lookback + 1)
        .then(pl.col("analytical_close") / pl.col("analytical_close").shift(lookback).over("symbol") - 1)
        .otherwise(None).alias("momentum")
    ).select("symbol", "trade_date", "momentum")
