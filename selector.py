import pandas as pd


def _latest_per_symbol(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return the latest available row for each symbol.
    """

    return (
        df
        .sort_values(
            ["symbol", "trade_date"]
        )
        .groupby(
            "symbol"
        )
        .tail(1)
        .sort_values(
            "symbol"
        )
        .copy()
    )


# ============================================================
# SELECTOR 1: BREAKOUT 400
# ============================================================

def select_breakout_400(
    df: pd.DataFrame,
):
    """
    Select stocks whose latest row satisfies:

        C > REF(HHV(C,400),1)

    Returns:
        selected
        not_evaluable
    """

    latest = _latest_per_symbol(df)

    ready = latest[
        latest[
            "breakout_400_status"
        ] == "ready"
    ].copy()

    selected = ready[
        ready["breakout_400"] == True
    ].copy()

    not_evaluable = latest[
        latest[
            "breakout_400_status"
        ] != "ready"
    ].copy()

    return (
        selected,
        not_evaluable,
    )


# ============================================================
# SELECTOR 2: PIT SIGNAL
# ============================================================

def select_pit_signal(
    df: pd.DataFrame,
):
    """
    Select stocks whose latest row satisfies:

        COUNT(A, TJTS) >= KSL

    where:

        A :=
            daily percentage rise > ZF
            AND C < REF(HHV(H,XGTS),1)
            AND C > MA(C,250)

    Returns:
        selected
        not_evaluable
    """

    latest = _latest_per_symbol(df)

    ready = latest[
        latest[
            "pit_signal_status"
        ] == "ready"
    ].copy()

    selected = ready[
        ready["pit_signal"] == True
    ].copy()

    not_evaluable = latest[
        latest[
            "pit_signal_status"
        ] != "ready"
    ].copy()

    return (
        selected,
        not_evaluable,
    )