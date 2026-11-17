import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
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

    print("1. Starting ASL MCP server...")

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:

            print("2. Initializing...")
            await session.initialize()
            print("   initialize OK")

            print("3. Listing tools BEFORE query...")
            tools = await session.list_tools()
            print("   list_tools OK")
            print("   tools:", [t.name for t in tools.tools])

            print("4. Calling query_bars...")
            result = await session.call_tool(
                "query_bars",
                {
                    "dataset": "daily_bars",
                    "symbols": ["000001.SZ"],
                    "start": "2026-08-28",
                    "end": "2026-08-28",
                    "adjust": "hfq",
                    "limit": 10,
                    "offset": 0,
                },
            )

            print("   query_bars OK")
            print(result)

            print("5. Listing tools AFTER query...")
            tools2 = await session.list_tools()
            print("   second list_tools OK")
            print("   tools:", [t.name for t in tools2.tools])


asyncio.run(main())
