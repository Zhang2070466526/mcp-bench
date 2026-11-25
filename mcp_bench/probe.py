"""性能探测：握手延迟、工具发现延迟、单个工具调用延迟。"""
import time

from mcp import ClientSession

from .client import connect_server


async def _probe_one(server, call_tool=None, call_args=None, repeat=1):
    out = {"server": server["name"], "type": server.get("type", "streamable_http")}
    t0 = time.perf_counter()
    async with connect_server(server) as (read, write):
        out["connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        async with ClientSession(read, write) as session:
            t1 = time.perf_counter()
            init = await session.initialize()
            out["initialize_ms"] = round((time.perf_counter() - t1) * 1000, 1)
            si = getattr(init, "serverInfo", None)
            out["server_info"] = {
                "name": getattr(si, "name", None),
                "version": getattr(si, "version", None),
            }

            t2 = time.perf_counter()
            tools = (await session.list_tools()).tools
            out["list_tools_ms"] = round((time.perf_counter() - t2) * 1000, 1)
            out["tool_count"] = len(tools)

            if call_tool:
                lat = []
                for _ in range(repeat):
                    t3 = time.perf_counter()
                    res = await session.call_tool(call_tool, call_args or {})
                    lat.append(round((time.perf_counter() - t3) * 1000, 1))
                out["call_tool"] = call_tool
                out["call_latency_ms"] = lat
                out["call_is_error"] = bool(getattr(res, "isError", False))

    return out


async def run_probe(servers, call_tool=None, call_args=None, repeat=1):
    results = []
    for s in servers:
        try:
            results.append(await _probe_one(s, call_tool, call_args, repeat))
        except Exception as e:
            results.append(
                {"server": s.get("name", "?"), "error": f"{type(e).__name__}: {e}"}
            )
    return results
