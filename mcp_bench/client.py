"""MCP 客户端工厂：统一封装 stdio / SSE / Streamable HTTP 三种传输。"""
from contextlib import asynccontextmanager

from mcp import ClientSession


@asynccontextmanager
async def connect_server(server):
    """按 server 配置建立连接，yield (read_stream, write_stream)。"""
    t = server.get("type", "streamable_http")

    if t == "streamable_http":
        from mcp.client import streamable_http
        factory = (getattr(streamable_http, "streamable_http_client", None)
                   or getattr(streamable_http, "streamablehttp_client", None))
        if factory is None:
            raise ImportError("当前 mcp SDK 不支持 streamable_http 传输")
        async with factory(server["url"]) as streams:
            read, write = streams[0], streams[1]
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


# ---- mcp 1.x (camelCase) vs 2.x (snake_case) 的 Tool 字段兼容 ---- #
def tool_input_schema(t):
    """Tool 的 inputSchema（兼容 mcp 1.x inputSchema / 2.x input_schema）。"""
    return getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}


def tool_output_schema(t):
    return getattr(t, "output_schema", None) or getattr(t, "outputSchema", None)


def tool_annotations(t):
    return getattr(t, "annotations", None)


def tool_annotation(t, camel, snake):
    """读 annotations 里的一个字段（兼容 camelCase / snake_case）。"""
    a = tool_annotations(t)
    if a is None:
        return None
    if isinstance(a, dict):
        return a.get(camel)
    v = getattr(a, snake, None)
    return v if v is not None else getattr(a, camel, None)
