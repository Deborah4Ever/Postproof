"""
Standalone MCP client smoke test - connects to the running server's /mcp
endpoint as a genuine MCP client (mcp.client.sse + ClientSession from the
installed mcp==1.2.0 package), initializes a session, and calls
check_job_posting with real arguments. This is the one thing that proves
"any MCP client can call this directly" is actually true, not just a
claim in the code - run it against a live `uvicorn app.main:app` process.

Usage:
    python verify_mcp.py
"""
import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client

MCP_URL = "http://localhost:8000/mcp/sse"


async def main():
    print(f"Connecting to {MCP_URL} ...")
    async with sse_client(MCP_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            print("Initializing session...")
            init_result = await session.initialize()
            print("Initialized. Server info:", init_result.serverInfo)
            print("Protocol version:", init_result.protocolVersion)

            print("\nListing tools...")
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description[:80]}...")

            print("\nCalling check_job_posting with a real URL...")
            result = await session.call_tool(
                "check_job_posting",
                {"url": "https://wellfound.com/jobs/4716713-comedian-copywriter"},
            )
            print("\n=== RAW CallToolResult ===")
            print(result)

            print("\n=== Extracted content ===")
            for content_item in result.content:
                if hasattr(content_item, "text"):
                    print(content_item.text)


if __name__ == "__main__":
    asyncio.run(main())
