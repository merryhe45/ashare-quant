import asyncio
import json

import pandas as pd

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SYMBOLS = [
    "000430.SZ",
    "000504.SZ",
    "300376.SZ",
    "000070.SZ",
]

# Use exactly the approximate window used by the current scanner
START = "2024-07-04"
END = "2026-08-17"


async def query_bars(session, symbol, use_universe):
    all_rows = []
    columns = None

    limit = 2000
    offset = 0

    while True:
        args = {
            "dataset": "daily_bars",
            "symbols": [symbol],
            "start": START,
            "end": END,
            "adjust": "hfq",
            "limit": limit,
            "offset": offset,
        }

        if use_universe:
            args["universe"] = "all_a"

        result = await session.call_tool(
            "query_bars",
            args,
        )

        if result.is_error:
            raise RuntimeError(result)

        payload = json.loads(
            result.content[0].text
        )

        if columns is None:
            columns = payload["columns"]

        rows = payload["rows"]
        all_rows.extend(rows)

        if not payload["truncated"]:
            break

        offset += len(rows)

    df = pd.DataFrame(
        all_rows,
        columns=columns,
    )

    if not df.empty:
        df["trade_date"] = pd.to_datetime(
            df["trade_date"]
        )

    return df


async def main():
    server_params = StdioServerParameters(
        command="asl",
        args=[
            "mcp",
            "--config",
            "C:/Users/merry/configs/ashare-lake.toml",
        ],
        env={"PYTHONUTF8": "1"},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print(
                "========================================"
            )
            print(
                "UNIVERSE FILTER COMPARISON"
            )
            print(
                "========================================"
            )

            print(
                f"Window: {START} -> {END}"
            )

            for symbol in SYMBOLS:

                normal = await query_bars(
                    session,
                    symbol,
                    use_universe=False,
                )

                all_a = await query_bars(
                    session,
                    symbol,
                    use_universe=True,
                )

                print()
                print(f"Symbol: {symbol}")

                print(
                    "Without universe:",
                    len(normal),
                )

                print(
                    "With all_a:",
                    len(all_a),
                )

                if not normal.empty:
                    print(
                        "Normal range:",
                        normal["trade_date"].min().date(),
                        "->",
                        normal["trade_date"].max().date(),
                    )

                if not all_a.empty:
                    print(
                        "all_a range:",
                        all_a["trade_date"].min().date(),
                        "->",
                        all_a["trade_date"].max().date(),
                    )


if __name__ == "__main__":
    asyncio.run(main())