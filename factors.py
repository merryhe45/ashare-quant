import pandas as pd


# ============================================================
# FACTOR 1: BREAKOUT 400
# ============================================================

def add_breakout_400_factor(
    df: pd.DataFrame,
    lookback: int = 400,
) -> pd.DataFrame:
    """
    Formula:

        C > REF(HHV(C,400),1)

    Meaning:
        Today's close is above the highest closing price
        of the previous 400 trading days.
    """

    df = (
        df
        .sort_values(["symbol", "trade_date"])
        .copy()
    )

    # Previous 400 trading days' highest CLOSE,
    # excluding today's bar.
    df["prev_400d_high_close"] = (
        df.groupby("symbol")["close"]
        .transform(
            lambda s:
            s.shift(1)
            .rolling(
                window=lookback,
                min_periods=lookback,
            )
            .max()
        )
    )

    df["breakout_400"] = (
        df["close"]
        > df["prev_400d_high_close"]
    )

    df["breakout_400_status"] = "ready"

    df.loc[
        df["prev_400d_high_close"].isna(),
        "breakout_400_status",
    ] = "insufficient_history"

    return df


# ============================================================
# FACTOR 2: PIT SIGNAL
# ============================================================

def add_pit_signal_factor(
    df: pd.DataFrame,
    zf_min: float = 4.90,
    zf_max: float = 9.00,
    xgts: int = 5,
    tjts: int = 30,
    ksl: int = 2,
    ma_days: int = 250,
    amplitude_days: int = 60,
    amplitude_max: float = 0.50,
) -> pd.DataFrame:
    """
    PIT SIGNAL

    Event A:

        C > O

        AND

        zf_min <
        (C - REF(C,1)) / REF(C,1) * 100
        <= zf_max

        AND

        C < REF(HHV(H,XGTS),1)

        AND

        C > MA(C,250)


    Final signal:

        COUNT(A,TJTS) >= KSL

        AND

        C >= MA(C,250)

        AND

        60-day amplitude <= 50%


    Default parameters:

        ZF MIN = 4.90%
        ZF MAX = 9.00%
        XGTS   = 5
        TJTS   = 30
        KSL    = 2
        MA     = 250 days

        amplitude_days = 60
        amplitude_max  = 50%
    """

    df = (
        df
        .sort_values(["symbol", "trade_date"])
        .copy()
    )

    grouped = df.groupby(
        "symbol",
        group_keys=False,
    )

    # ========================================================
    # 1. Previous close
    #
    # REF(C,1)
    # ========================================================

    df["prev_close"] = (
        grouped["close"]
        .shift(1)
    )

    # ========================================================
    # 2. Daily percentage change
    #
    # (C - REF(C,1)) / REF(C,1) * 100
    # ========================================================

    df["pit_pct_change"] = (
        (
            df["close"]
            - df["prev_close"]
        )
        / df["prev_close"]
        * 100.0
    )

    # ========================================================
    # 3. MA(C,250)
    #
    # Includes today's close.
    # ========================================================

    df["pit_ma_250"] = (
        grouped["close"]
        .transform(
            lambda s:
            s.rolling(
                window=ma_days,
                min_periods=ma_days,
            )
            .mean()
        )
    )

    # ========================================================
    # 4. REF(HHV(H,XGTS),1)
    #
    # Highest HIGH over previous XGTS trading days,
    # excluding today.
    # ========================================================

    df["pit_prev_xgts_high"] = (
        grouped["high"]
        .transform(
            lambda s:
            s.shift(1)
            .rolling(
                window=xgts,
                min_periods=xgts,
            )
            .max()
        )
    )

    # ========================================================
    # 5. 60-day amplitude
    #
    # (HHV(H,60) - LLV(L,60)) / LLV(L,60)
    # ========================================================

    df["pit_60d_high"] = (
        grouped["high"]
        .transform(
            lambda s:
            s.rolling(
                window=amplitude_days,
                min_periods=amplitude_days,
            )
            .max()
        )
    )

    df["pit_60d_low"] = (
        grouped["low"]
        .transform(
            lambda s:
            s.rolling(
                window=amplitude_days,
                min_periods=amplitude_days,
            )
            .min()
        )
    )

    df["pit_amplitude_60"] = (
        (
            df["pit_60d_high"]
            - df["pit_60d_low"]
        )
        / df["pit_60d_low"]
    )

    # ========================================================
    # 6. Is Event A evaluable?
    # ========================================================

    event_ready = (
        df["prev_close"].notna()
        & df["pit_ma_250"].notna()
        & df["pit_prev_xgts_high"].notna()
    )

    # ========================================================
    # 7. Event A
    # ========================================================

    event_condition = (

        # ----------------------------------------------------
        # Must be a bullish candle:
        #
        # C > O
        # ----------------------------------------------------
        (df["close"] > df["open"])

        # ----------------------------------------------------
        # Rise must be between 4.9% and 9%
        #
        # 4.9% < rise <= 9%
        # ----------------------------------------------------
        & (
            df["pit_pct_change"]
            > zf_min
        )

        & (
            df["pit_pct_change"]
            <= zf_max
        )

        # ----------------------------------------------------
        # C < REF(HHV(H,XGTS),1)
        #
        # Today's close has not exceeded the highest HIGH
        # of the previous XGTS days.
        # ----------------------------------------------------
        & (
            df["close"]
            < df["pit_prev_xgts_high"]
        )

        # ----------------------------------------------------
        # C > MA(C,250)
        #
        # Event A occurs above the annual moving average.
        # ----------------------------------------------------
        & (
            df["close"]
            > df["pit_ma_250"]
        )
    )

    # ========================================================
    # 8. Numeric Event A
    #
    # Needed for COUNT(A,TJTS).
    #
    # IMPORTANT:
    # Unevaluable rows remain NaN.
    # We do NOT silently treat insufficient history as False.
    # ========================================================

    event_numeric = pd.Series(
        float("nan"),
        index=df.index,
        dtype="float64",
    )

    event_numeric.loc[event_ready] = (
        event_condition.loc[event_ready]
        .astype(int)
    )

    # Friendly display version
    df["pit_event_a"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="boolean",
    )

    df.loc[
        event_ready,
        "pit_event_a",
    ] = event_condition.loc[
        event_ready
    ]

    # ========================================================
    # 9. COUNT(A,TJTS)
    #
    # Default:
    #
    # COUNT(A,30)
    # ========================================================

    df["pit_count"] = (
        event_numeric
        .groupby(df["symbol"])
        .transform(
            lambda s:
            s.rolling(
                window=tjts,
                min_periods=tjts,
            )
            .sum()
        )
    )

    # ========================================================
    # 10. Final signal
    #
    # COUNT(A,30) >= 2
    #
    # AND
    #
    # C >= MA(C,250)
    #
    # AND
    #
    # 60-day amplitude <= 50%
    # ========================================================

    df["pit_signal"] = (

        # At least KSL valid A events
        # during previous/current TJTS bars.
        (
            df["pit_count"]
            >= ksl
        )

        # Current close must remain
        # on or above annual MA.
        & (
            df["close"]
            >= df["pit_ma_250"]
        )

        # 60-day amplitude <= 50%
        & (
            df["pit_amplitude_60"]
            <= amplitude_max
        )
    )

    # ========================================================
    # 11. Status
    # ========================================================

    df["pit_signal_status"] = "ready"

    df.loc[
        (
            df["pit_count"].isna()
            | df["pit_ma_250"].isna()
            | df["pit_amplitude_60"].isna()
        ),
        "pit_signal_status",
    ] = "insufficient_history"

    return df