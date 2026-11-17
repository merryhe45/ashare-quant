from __future__ import annotations

import polars as pl


def add_breakout_400_factor(
    df: pl.DataFrame,
    lookback: int = 400,
) -> pl.DataFrame:
    """
    Formula 1

    C > REF(HHV(C,400),1)

    Today close must be above the highest close of the
    previous 400 trading bars, excluding today.
    """

    df = df.sort(["symbol", "trade_date"])

    return (
        df
        .with_columns(
            pl.col("close")
            .shift(1)
            .rolling_max(
                window_size=lookback,
                min_samples=lookback,
            )
            .over("symbol")
            .alias("prev_400d_high_close")
        )
        .with_columns(
            (
                pl.col("close")
                > pl.col("prev_400d_high_close")
            )
            .alias("breakout_400"),

            pl.when(
                pl.col("prev_400d_high_close").is_not_null()
            )
            .then(pl.lit("ready"))
            .otherwise(pl.lit("insufficient_history"))
            .alias("breakout_400_status"),
        )
    )


def add_pit_signal_factor(
    df: pl.DataFrame,
    zf_min: float = 4.90,
    zf_max: float = 9.00,
    xgts: int = 5,
    tjts: int = 30,
    ksl: int = 2,
    ma_days: int = 250,
    amplitude_days: int = 60,
    amplitude_max: float = 0.50,
) -> pl.DataFrame:
    """
    Formula 2

    Event A:

        C > O
        AND 4.9% < daily rise <= 9.0%
        AND C < REF(HHV(H,5),1)
        AND C > MA(C,250)

    Final signal:

        COUNT(A,30) >= 2
        AND C >= MA(C,250)
        AND 60-day amplitude <= 50%

    60-day amplitude:

        (HHV(H,60) - LLV(L,60)) / LLV(L,60)
    """

    df = df.sort(["symbol", "trade_date"])

    # ---------------------------------------------------------
    # Base rolling values
    # ---------------------------------------------------------

    df = df.with_columns(
        pl.col("close")
        .shift(1)
        .over("symbol")
        .alias("pit_prev_close"),

        pl.col("close")
        .rolling_mean(
            window_size=ma_days,
            min_samples=ma_days,
        )
        .over("symbol")
        .alias("pit_ma_250"),

        pl.col("high")
        .shift(1)
        .rolling_max(
            window_size=xgts,
            min_samples=xgts,
        )
        .over("symbol")
        .alias("pit_prev_xgts_high"),

        pl.col("high")
        .rolling_max(
            window_size=amplitude_days,
            min_samples=amplitude_days,
        )
        .over("symbol")
        .alias("pit_60d_high"),

        pl.col("low")
        .rolling_min(
            window_size=amplitude_days,
            min_samples=amplitude_days,
        )
        .over("symbol")
        .alias("pit_60d_low"),
    )

    # ---------------------------------------------------------
    # Derived values
    # ---------------------------------------------------------

    df = df.with_columns(
        (
            (
                pl.col("close")
                - pl.col("pit_prev_close")
            )
            / pl.col("pit_prev_close")
            * 100.0
        )
        .alias("pit_pct_change"),

        (
            (
                pl.col("pit_60d_high")
                - pl.col("pit_60d_low")
            )
            / pl.col("pit_60d_low")
        )
        .alias("pit_amplitude_60"),
    )

    # ---------------------------------------------------------
    # Event A readiness
    # ---------------------------------------------------------

    event_ready = (
        pl.col("pit_prev_close").is_not_null()
        & pl.col("pit_ma_250").is_not_null()
        & pl.col("pit_prev_xgts_high").is_not_null()
    )

    event_condition = (
        (pl.col("close") > pl.col("open"))
        & (pl.col("pit_pct_change") > zf_min)
        & (pl.col("pit_pct_change") <= zf_max)
        & (
            pl.col("close")
            < pl.col("pit_prev_xgts_high")
        )
        & (
            pl.col("close")
            > pl.col("pit_ma_250")
        )
    )

    # event_numeric must remain NULL while the event itself
    # is not evaluable. This preserves the same semantics as
    # the validated pandas implementation.
    df = df.with_columns(
        pl.when(event_ready)
        .then(
            pl.when(event_condition)
            .then(pl.lit(1.0))
            .otherwise(pl.lit(0.0))
        )
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("pit_event_numeric"),

        pl.when(event_ready)
        .then(event_condition)
        .otherwise(pl.lit(None, dtype=pl.Boolean))
        .alias("pit_event_a"),
    )

    # ---------------------------------------------------------
    # COUNT(A,30)
    # ---------------------------------------------------------

    df = df.with_columns(
        pl.col("pit_event_numeric")
        .rolling_sum(
            window_size=tjts,
            min_samples=tjts,
        )
        .over("symbol")
        .alias("pit_count")
    )

    # ---------------------------------------------------------
    # Final signal
    # ---------------------------------------------------------

    final_ready = (
        pl.col("pit_count").is_not_null()
        & pl.col("pit_ma_250").is_not_null()
        & pl.col("pit_amplitude_60").is_not_null()
    )

    final_signal = (
        (pl.col("pit_count") >= ksl)
        & (
            pl.col("close")
            >= pl.col("pit_ma_250")
        )
        & (
            pl.col("pit_amplitude_60")
            <= amplitude_max
        )
    )

    return df.with_columns(
        pl.when(final_ready)
        .then(final_signal)
        .otherwise(pl.lit(False))
        .alias("pit_signal"),

        pl.when(final_ready)
        .then(pl.lit("ready"))
        .otherwise(pl.lit("insufficient_history"))
        .alias("pit_signal_status"),
    )


def add_ma20_ma60_cross_factor(
    df: pl.DataFrame,
    fast_days: int = 20,
    slow_days: int = 60,
) -> pl.DataFrame:
    """
    Formula 3

    CROSS(MA(C,20), MA(C,60))

    Equivalent to:

        MA20_today > MA60_today
        AND
        MA20_yesterday <= MA60_yesterday
    """

    df = df.sort(["symbol", "trade_date"])

    df = df.with_columns(
        pl.col("close")
        .rolling_mean(
            window_size=fast_days,
            min_samples=fast_days,
        )
        .over("symbol")
        .alias("ma20"),

        pl.col("close")
        .rolling_mean(
            window_size=slow_days,
            min_samples=slow_days,
        )
        .over("symbol")
        .alias("ma60"),
    )

    df = df.with_columns(
        pl.col("ma20")
        .shift(1)
        .over("symbol")
        .alias("prev_ma20"),

        pl.col("ma60")
        .shift(1)
        .over("symbol")
        .alias("prev_ma60"),
    )

    ready = (
        pl.col("ma20").is_not_null()
        & pl.col("ma60").is_not_null()
        & pl.col("prev_ma20").is_not_null()
        & pl.col("prev_ma60").is_not_null()
    )

    signal = (
        (pl.col("ma20") > pl.col("ma60"))
        & (
            pl.col("prev_ma20")
            <= pl.col("prev_ma60")
        )
    )

    return df.with_columns(
        pl.when(ready)
        .then(signal)
        .otherwise(pl.lit(False))
        .alias("ma20_ma60_cross"),

        pl.when(ready)
        .then(pl.lit("ready"))
        .otherwise(pl.lit("insufficient_history"))
        .alias("ma20_ma60_cross_status"),
    )