"""MCP 客户端工厂：统一封装 stdio / SSE / Streamable HTTP 三种传输。"""
from contextlib import asynccontextmanager

from mcp import ClientSession


@asynccontextmanager
async def connect_server(server):
    """按 server 配置建立连接，yield (read_stream, write_stream)。"""
    t = server.get("type", "streamable_http")

    if t == "streamable_http":
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(server["url"]) as (read, write, _):
            yield read, write

    elif t == "sse":
        from mcp.client.sse import sse_client
        async with sse_client(server["url"]) as (read, write):
            yield read, write

    elif t == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=server["command"],
            args=server.get("args", []),
            env=server.get("env"),
        )
        async with stdio_client(params) as (read, write):
            yield read, write

    else:
        raise ValueError(f"未知传输类型: {t}")


async def get_catalog(server):
    """连接 server，返回 (initialize 结果, [Tool 对象])。"""
    async with connect_server(server) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            return init, tools
