"""Trailing calendar-grid factors; no future labels are accepted."""
import polars as pl

from ..factors import momentum
from .registry import FactorSpec

KEYS = ["symbol", "trade_date"]


def calculate(panel: pl.DataFrame, spec: FactorSpec, liquidity_enabled=True):
    d = panel.sort(KEYS)
    if d.height != d.unique(KEYS).height:
        raise ValueError("Duplicate observation keys")
    price = pl.col("analytical_close")
    if spec.kind == "momentum":
        values = momentum(d, spec.sessions).rename({"momentum": "factor_value"})
        d = d.join(values, on=KEYS, validate="1:1")
    elif spec.kind == "ma_distance":
        mean = price.rolling_mean(spec.sessions, min_samples=spec.sessions).over("symbol")
        d = d.with_columns((price / mean - 1).alias("factor_value"))
    elif spec.kind == "volatility":
        d = d.with_columns((price / price.shift(1).over("symbol") - 1).alias("daily_return"))
        d = d.with_columns(pl.col("daily_return").rolling_std(spec.sessions, min_samples=spec.sessions, ddof=spec.ddof).over("symbol").alias("factor_value"))
    elif spec.kind == "liquidity":
        d = d.with_columns(pl.col("valid_amount").rolling_mean(spec.sessions, min_samples=spec.sessions).over("symbol").alias("factor_value"))
    else:
        raise ValueError("Unknown factor kind")
    days = sorted(d["trade_date"].unique().to_list())
    index = {day: i for i, day in enumerate(days)}
    earliest = {day: days[max(0, i-spec.prior_sessions)] for i, day in enumerate(days)}
    reason = (
        pl.when(~pl.col("listing_eligible")).then(pl.lit("not_listing_eligible"))
        .when(pl.col("status_evidence").is_in(["restricted", "conflict"])).then(pl.lit("restricted_or_conflicting_status"))
        .when(pl.col("price_reason") != "ready").then(pl.col("price_reason"))
        .when(pl.col("trade_date").replace_strict(index, return_dtype=pl.Int64) < spec.prior_sessions).then(pl.lit("insufficient_loaded_warmup"))
        .when(pl.col("list_date") > pl.col("trade_date").replace_strict(earliest, return_dtype=pl.Date)).then(pl.lit("insufficient_listing_history"))
    )
    if spec.kind == "liquidity" and not liquidity_enabled:
        reason = pl.lit("liquidity_gate_disabled")
    else:
        reason = reason.when(pl.col("factor_value").is_null()).then(pl.lit("broken_amount_window" if spec.kind == "liquidity" else "broken_historical_price_window")).when(~pl.col("factor_value").is_finite()).then(pl.lit("nonfinite_factor")).otherwise(pl.lit("ready"))
    return d.with_columns(reason.alias("factor_reason"), pl.lit(spec.factor_id).alias("factor_id")).select(*KEYS, "factor_id", "factor_value", "factor_reason")
