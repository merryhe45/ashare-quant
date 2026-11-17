"""Future outcomes; these must never be inputs to factor calculations."""
import polars as pl


def forward_returns(prices: pl.DataFrame, horizon: int = 20) -> pl.DataFrame:
    """Adjusted close(t+h)/close(t)-1; require every session in [t,t+h]."""
    if horizon < 1:
        raise ValueError("horizon must be positive")
    d = prices.select("symbol", "trade_date", "analytical_close").sort("symbol", "trade_date")
    return d.with_columns(
        pl.when(pl.col("analytical_close").is_not_null().cast(pl.Int64).rolling_sum(horizon + 1).shift(-horizon).over("symbol") == horizon + 1)
        .then(pl.col("analytical_close").shift(-horizon).over("symbol") / pl.col("analytical_close") - 1)
        .otherwise(None).alias("forward_return")
    ).select("symbol", "trade_date", "forward_return")
