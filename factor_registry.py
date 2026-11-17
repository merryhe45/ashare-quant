from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import polars as pl

from factors_v2 import (
    add_breakout_400_factor,
    add_pit_signal_factor,
    add_ma20_ma60_cross_factor,
)


@dataclass(frozen=True)
class FactorSpec:
    name: str
    display_name: str
    lookback: int
    calculator: Callable[[pl.DataFrame], pl.DataFrame]
    signal_column: str
    status_column: str
    output_columns: tuple[str, ...]
    formula_text: str


FACTOR_REGISTRY: dict[str, FactorSpec] = {

    # =========================================================
    # Formula 1
    # =========================================================

    "breakout_400": FactorSpec(
        name="breakout_400",
        display_name="FORMULA 1: BREAKOUT 400",
        lookback=401,
        calculator=add_breakout_400_factor,
        signal_column="breakout_400",
        status_column="breakout_400_status",
        output_columns=(
            "symbol",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "prev_400d_high_close",
            "breakout_400",
        ),
        formula_text=(
            "C > REF(HHV(C,400),1)"
        ),
    ),

    # =========================================================
    # Formula 2
    # =========================================================

    "pit_signal": FactorSpec(
        name="pit_signal",
        display_name="FORMULA 2: PIT SIGNAL",
        lookback=280,
        calculator=add_pit_signal_factor,
        signal_column="pit_signal",
        status_column="pit_signal_status",
        output_columns=(
            "symbol",
            "trade_date",
            "close",
            "pit_pct_change",
            "pit_ma_250",
            "pit_prev_xgts_high",
            "pit_amplitude_60",
            "pit_event_a",
            "pit_count",
            "pit_signal",
        ),
        formula_text=(
            "Event A:\n"
            "C > O\n"
            "AND 4.9% < daily rise <= 9.0%\n"
            "AND C < REF(HHV(H,5),1)\n"
            "AND C > MA(C,250)\n\n"
            "Final signal:\n"
            "COUNT(A,30) >= 2\n"
            "AND C >= MA(C,250)\n"
            "AND 60-day amplitude <= 50%"
        ),
    ),

    # =========================================================
    # Formula 3
    # =========================================================

    "ma20_ma60_cross": FactorSpec(
        name="ma20_ma60_cross",
        display_name="FORMULA 3: MA20 / MA60 GOLDEN CROSS",
        lookback=61,
        calculator=add_ma20_ma60_cross_factor,
        signal_column="ma20_ma60_cross",
        status_column="ma20_ma60_cross_status",
        output_columns=(
            "symbol",
            "trade_date",
            "close",
            "ma20",
            "ma60",
            "prev_ma20",
            "prev_ma60",
            "ma20_ma60_cross",
        ),
        formula_text=(
            "CROSS(MA(C,20), MA(C,60))"
        ),
    ),
}


def get_max_lookback() -> int:
    return max(
        spec.lookback
        for spec in FACTOR_REGISTRY.values()
    )