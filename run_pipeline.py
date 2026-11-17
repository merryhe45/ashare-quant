import asyncio
import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from factors import (
    add_breakout_400_factor,
    add_pit_signal_factor,
)

from selector import (
    select_breakout_400,
    select_pit_signal,
)


# ============================================================
# CONFIGURATION
# ============================================================

BREAKOUT_400_DAYS = 400

# Formula 2
PIT_ZF_MIN = 4.90
PIT_ZF_MAX = 9.00

PIT_XGTS = 5
PIT_TJTS = 30
PIT_KSL = 2
PIT_MA_DAYS = 250

PIT_AMPLITUDE_DAYS = 60
PIT_AMPLITUDE_MAX = 0.50

# Safe historical lookback for Formula 2.
#
# MA250 + COUNT(A,30)
PIT_LOOKBACK = PIT_MA_DAYS + PIT_TJTS

FACTOR_LOOKBACKS = {
    "breakout_400": BREAKOUT_400_DAYS,
    "pit_signal": PIT_LOOKBACK,
}

# Extra trading-day buffer.
LOOKBACK_BUFFER = 120

# Number of calendar days backwards to search
# for the latest available trading day.
LATEST_DATE_SEARCH_DAYS = 20


# ============================================================
# DATE HELPERS
# ============================================================

def calculate_start_date(
    scan_date: str,
) -> str:
    """
    Calculate enough historical calendar time to satisfy
    the largest factor lookback.

    We convert approximate trading days to calendar days
    and add a safety buffer.
    """

    max_factor_lookback = max(
        FACTOR_LOOKBACKS.values()
    )

    required_trading_days = (
        max_factor_lookback
        + LOOKBACK_BUFFER
    )

    # Approximate:
    #
    # 245 trading days ~= 365 calendar days
    #
    calendar_days = int(
        required_trading_days
        * 365
        / 245
    )

    end_date = datetime.strptime(
        scan_date,
        "%Y-%m-%d",
    )

    start_date = (
        end_date
        - timedelta(days=calendar_days)
    )

    return start_date.strftime(
        "%Y-%m-%d"
    )


# ============================================================
# MCP DAILY BARS LOADER
# ============================================================

async def load_daily_bars(
    session: ClientSession,
    start: str,
    end: str,
    symbols=None,
    universe=None,
) -> pd.DataFrame:
    """
    Load daily bars through ashare-lake MCP.

    IMPORTANT ARCHITECTURE:

    universe="all_a"
        is used ONLY for one-day current-universe queries.

    Historical queries:
        use explicit symbols
        and universe=None.

    This avoids the historical truncation problem previously
    discovered when querying all_a over long date ranges.
    """

    all_rows = []
    columns = None

    limit = 2000
    offset = 0

    while True:

        args = {
            "dataset": "daily_bars",
            "start": start,
            "end": end,
            "adjust": "hfq",
            "limit": limit,
            "offset": offset,
        }

        if symbols:
            args["symbols"] = symbols

        if universe:
            args["universe"] = universe

        result = await session.call_tool(
            "query_bars",
            arguments=args,
        )

        if not result.content:
            break

        raw_text = result.content[0].text

        payload = json.loads(
            raw_text
        )

        if columns is None:
            columns = payload.get(
                "columns",
                [],
            )

        rows = payload.get(
            "rows",
            [],
        )

        if rows:
            all_rows.extend(
                rows
            )

        truncated = payload.get(
            "truncated",
            False,
        )

        if not truncated:
            break

        offset += limit

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        all_rows,
        columns=columns,
    )

    if "trade_date" in df.columns:

        df["trade_date"] = pd.to_datetime(
            df["trade_date"]
        )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]

    for col in numeric_columns:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce",
            )

    return df


# ============================================================
# FIND LATEST AVAILABLE A-SHARE TRADING DAY
# ============================================================

async def find_latest_scan_date(
    session: ClientSession,
) -> tuple[str, pd.DataFrame]:
    """
    Find the latest trading date available in the Data Lake.

    Efficient strategy:

    1. Use one long-established A-share stock (000001.SZ)
       as a lightweight probe.
    2. Starting from today in China time, move backwards
       through recent calendar dates.
    3. Skip Saturday and Sunday immediately.
    4. Query only ONE symbol for ONE day.
    5. Once a valid trading date is found, query that exact
       date with universe="all_a" to obtain the current
       A-share universe.

    This avoids expensive market-wide recent-range scans.
    """

    china_today = datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).date()

    # Ping An Bank / 平安银行:
    # a long-established A-share used only as a trading-day probe.
    probe_symbol = "000001.SZ"

    print("")
    print(
        "Searching Data Lake for latest "
        "available A-share trading day..."
    )
    print(
        f"Lightweight probe symbol: {probe_symbol}"
    )
    print("")

    latest_date = None

    # ========================================================
    # STEP A:
    # Search backwards using ONE symbol only.
    # ========================================================

    for days_back in range(
        LATEST_DATE_SEARCH_DAYS
    ):

        candidate_date = (
            china_today
            - timedelta(days=days_back)
        )

        # Monday = 0
        # ...
        # Saturday = 5
        # Sunday = 6
        #
        # No need to query known weekends.
        if candidate_date.weekday() >= 5:
            continue

        candidate = candidate_date.strftime(
            "%Y-%m-%d"
        )

        print(
            f"Checking {candidate}...",
            end=" ",
            flush=True,
        )

        probe_df = await load_daily_bars(
            session=session,
            start=candidate,
            end=candidate,
            symbols=[probe_symbol],
            universe=None,
        )

        if not probe_df.empty:

            valid_rows = probe_df[
                probe_df["trade_date"]
                == pd.Timestamp(candidate)
            ]

            if not valid_rows.empty:

                latest_date = candidate

                print("FOUND")

                break

        print("no data")

    if latest_date is None:

        raise RuntimeError(
            "Could not find a valid A-share trading day "
            f"within the previous "
            f"{LATEST_DATE_SEARCH_DAYS} calendar days."
        )

    print("")
    print(
        f"Latest available trading day: "
        f"{latest_date}"
    )

    # ========================================================
    # STEP B:
    # Now that we KNOW this is a valid trading date,
    # obtain the actual A-share universe for that ONE day.
    # ========================================================

    print(
        f"Loading A-share universe for "
        f"{latest_date}..."
    )

    current_df = await load_daily_bars(
        session=session,
        start=latest_date,
        end=latest_date,
        symbols=None,
        universe="all_a",
    )

    if current_df.empty:

        raise RuntimeError(
            "A valid trading date was found, but "
            "the all_a universe query returned no rows "
            f"for {latest_date}."
        )

    current_symbols = (
        current_df["symbol"]
        .dropna()
        .drop_duplicates()
    )

    if current_symbols.empty:

        raise RuntimeError(
            "No A-share symbols found for "
            f"{latest_date}."
        )

    print(
        f"A-share universe found: "
        f"{len(current_symbols):,} symbols"
    )

    return latest_date, current_df


# ============================================================
# MAIN
# ============================================================

async def main():

    print("")
    print(
        "==================================="
    )
    print(
        "A-SHARE QUANT PIPELINE"
    )
    print(
        "==================================="
    )

    # --------------------------------------------------------
    # Start ashare-lake MCP server
    # --------------------------------------------------------

    server_params = StdioServerParameters(
    command="asl",
    args=[
        "mcp",
        "--config",
        "C:/Users/merry/configs/ashare-lake.toml",
    ],
    env={
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    },
)

    async with stdio_client(
        server_params
    ) as (
        read_stream,
        write_stream,
    ):

        async with ClientSession(
            read_stream,
            write_stream,
        ) as session:

            await session.initialize()

            # Preload and cache MCP tool definitions before the first call_tool().
            # This avoids the MCP client's lazy tool-schema validation path,
            # which can otherwise result in "Connection closed".
            tools = await session.list_tools()

            print(
                f"MCP connected: {len(tools.tools)} tools discovered"
            )

            # =================================================
            # STEP 1
            # Automatically determine latest trading day
            # =================================================

            print("")
            print(
                "STEP 1: Finding latest trading day..."
            )

            (
                scan_date,
                current_df,
            ) = await find_latest_scan_date(
                session
            )

            print("")
            print(
                "Latest available trading day:"
            )
            print(
                scan_date
            )

            # =================================================
            # Current A-share universe
            # =================================================

            current_symbols = (
                current_df["symbol"]
                .dropna()
                .drop_duplicates()
                .sort_values()
                .tolist()
            )

            print(
                f"A-share universe on {scan_date}: "
                f"{len(current_symbols):,}"
            )

            if not current_symbols:

                raise RuntimeError(
                    "No A-share symbols found "
                    f"for {scan_date}."
                )

            # =================================================
            # STEP 2
            # Calculate historical start date
            # =================================================

            start_date = calculate_start_date(
                scan_date
            )

            print("")
            print(
                "STEP 2: Loading complete historical bars..."
            )

            print(
                f"Historical range: "
                f"{start_date} -> {scan_date}"
            )

            print(
                f"Symbols requested: "
                f"{len(current_symbols):,}"
            )

            # -------------------------------------------------
            # CRITICAL:
            #
            # Historical query uses explicit symbols.
            # universe is intentionally NOT supplied.
            # -------------------------------------------------

            history_df = await load_daily_bars(
                session=session,
                start=start_date,
                end=scan_date,
                symbols=current_symbols,
                universe=None,
            )

            if history_df.empty:

                raise RuntimeError(
                    "Historical bars query returned no data."
                )

            print(
                f"Total historical rows loaded: "
                f"{len(history_df):,}"
            )

            historical_symbol_count = (
                history_df["symbol"]
                .nunique()
            )

            print(
                f"A-share symbols loaded: "
                f"{historical_symbol_count:,}"
            )

            # =================================================
            # STEP 3
            # Formula 1
            # =================================================

            print("")
            print(
                "STEP 3: Calculating breakout_400..."
            )

            factor_df = (
                add_breakout_400_factor(
                    history_df,
                    lookback=BREAKOUT_400_DAYS,
                )
            )

            # =================================================
            # STEP 4
            # Formula 2
            # =================================================

            print("")
            print(
                "STEP 4: Calculating pit_signal..."
            )

            factor_df = (
                add_pit_signal_factor(
                    factor_df,
                    zf_min=PIT_ZF_MIN,
                    zf_max=PIT_ZF_MAX,
                    xgts=PIT_XGTS,
                    tjts=PIT_TJTS,
                    ksl=PIT_KSL,
                    ma_days=PIT_MA_DAYS,
                    amplitude_days=PIT_AMPLITUDE_DAYS,
                    amplitude_max=PIT_AMPLITUDE_MAX,
                )
            )

            # =================================================
            # STEP 5
            # Run independent selectors
            # =================================================

            print("")
            print(
                "STEP 5: Running selectors..."
            )

            (
                breakout_selected,
                breakout_not_evaluable,
            ) = select_breakout_400(
                factor_df
            )

            (
                pit_selected,
                pit_not_evaluable,
            ) = select_pit_signal(
                factor_df
            )

            # =================================================
            # Keep only current scan date
            # =================================================

            scan_timestamp = pd.Timestamp(
                scan_date
            )

            breakout_selected = (
                breakout_selected[
                    breakout_selected[
                        "trade_date"
                    ]
                    == scan_timestamp
                ]
                .copy()
            )

            pit_selected = (
                pit_selected[
                    pit_selected[
                        "trade_date"
                    ]
                    == scan_timestamp
                ]
                .copy()
            )

            # =================================================
            # UNIVERSE DIAGNOSTICS
            # =================================================

            history_latest = (
                history_df
                .sort_values(
                    [
                        "symbol",
                        "trade_date",
                    ]
                )
                .groupby(
                    "symbol",
                    as_index=False,
                )
                .tail(1)
            )

            current_on_scan_date = (
                history_latest[
                    history_latest[
                        "trade_date"
                    ]
                    == scan_timestamp
                ]["symbol"]
                .nunique()
            )

            unexpected_stale = (
                historical_symbol_count
                - current_on_scan_date
            )

            breakout_evaluable = (
                len(current_symbols)
                - len(
                    breakout_not_evaluable
                )
            )

            pit_evaluable = (
                len(current_symbols)
                - len(
                    pit_not_evaluable
                )
            )

            print("")
            print(
                "==================================="
            )
            print(
                "UNIVERSE SUMMARY"
            )
            print(
                "==================================="
            )

            print(
                f"A-share universe: "
                f"{len(current_symbols):,}"
            )

            print(
                f"Historical symbols loaded: "
                f"{historical_symbol_count:,}"
            )

            print(
                f"Current on scan date: "
                f"{current_on_scan_date:,}"
            )

            print(
                f"Unexpected stale: "
                f"{unexpected_stale:,}"
            )

            print("")

            print(
                f"Breakout 400 evaluable: "
                f"{breakout_evaluable}"
            )

            print(
                f"Breakout 400 insufficient: "
                f"{len(breakout_not_evaluable)}"
            )

            print("")

            print(
                f"Pit signal evaluable: "
                f"{pit_evaluable}"
            )

            print(
                f"Pit signal insufficient: "
                f"{len(pit_not_evaluable)}"
            )

            # =================================================
            # FORMULA 1 OUTPUT
            # =================================================

            print("")
            print(
                "==================================="
            )
            print(
                "FORMULA 1: BREAKOUT 400"
            )
            print(
                "==================================="
            )

            print(
                "Formula:"
            )

            print(
                "C > REF(HHV(C,400),1)"
            )

            print("")

            print(
                f"Scan date: {scan_date}"
            )

            print(
                f"Selected count: "
                f"{len(breakout_selected)}"
            )

            print("")

            breakout_columns = [
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
            ]

            breakout_columns = [
                col
                for col in breakout_columns
                if col in breakout_selected.columns
            ]

            if breakout_selected.empty:

                print(
                    "No stocks selected."
                )

            else:

                print(
                    breakout_selected[
                        breakout_columns
                    ]
                    .sort_values(
                        "symbol"
                    )
                    .to_string(
                        index=False
                    )
                )

            # =================================================
            # FORMULA 2 OUTPUT
            # =================================================

            print("")
            print(
                "==================================="
            )
            print(
                "FORMULA 2: PIT SIGNAL"
            )
            print(
                "==================================="
            )

            print("")
            print(
                "Event A:"
            )

            print(
                "C > O"
            )

            print(
                f"AND "
                f"{PIT_ZF_MIN}% "
                f"< daily rise "
                f"<= {PIT_ZF_MAX}%"
            )

            print(
                f"AND C < "
                f"REF(HHV(H,{PIT_XGTS}),1)"
            )

            print(
                f"AND C > "
                f"MA(C,{PIT_MA_DAYS})"
            )

            print("")

            print(
                "Final signal:"
            )

            print(
                f"COUNT(A,{PIT_TJTS}) "
                f">= {PIT_KSL}"
            )

            print(
                f"AND C >= "
                f"MA(C,{PIT_MA_DAYS})"
            )

            print(
                f"AND {PIT_AMPLITUDE_DAYS}-day "
                f"amplitude <= "
                f"{PIT_AMPLITUDE_MAX * 100:.0f}%"
            )

            print("")

            print(
                f"Scan date: {scan_date}"
            )

            print(
                f"Selected count: "
                f"{len(pit_selected)}"
            )

            print("")

            pit_columns = [
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
            ]

            pit_columns = [
                col
                for col in pit_columns
                if col in pit_selected.columns
            ]

            if pit_selected.empty:

                print(
                    "No stocks selected."
                )

            else:

                print(
                    pit_selected[
                        pit_columns
                    ]
                    .sort_values(
                        "symbol"
                    )
                    .to_string(
                        index=False
                    )
                )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )