"""全量工具扫测：对服务每个工具分类（只读/需参数/跳过），测延迟与成功/错误。

规则（通用、保守）：
- 无必填参数 + 名字以 launch_/quit_/close_ 开头、或描述含 ⚠️/破坏、或名字含 cleanup → 跳过（可能有副作用/破坏性）
- 无必填参数（其余）→ 只读，空参调用 repeat 次
- 有必填参数 → 空参调用 1 次，测「参数校验错误」延迟（证明校验生效且不触发副作用）
"""
import json
import time

from mcp import ClientSession

from .client import connect_server

DANGEROUS_PREFIXES = ("launch_", "quit_", "close_")
DANGEROUS_MARKERS = ("⚠️", "破坏")
HEAVY_PREFIXES = ("simulate_", "run_", "solve_", "start_", "design_")


def classify_tool(name, description, required, skip_extra=frozenset()):
    """返回 (class, reason)。class ∈ {readonly, needs_args, skip}。"""
    if name in skip_extra:
        return "skip", "显式跳过"
    if not required:
        desc = description or ""
        if (name.startswith(DANGEROUS_PREFIXES)
                or "cleanup" in name
                or any(m in desc for m in DANGEROUS_MARKERS)):
            return "skip", "无必填参数但可能有副作用/破坏性"
        if name.startswith(HEAVY_PREFIXES):
            return "skip", "无必填参数但可能触发重型计算"
        return "readonly", ""
    return "needs_args", ""


def _extract_envelope(res):
    info = {"isError": bool(getattr(res, "isError", False))}
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        for k in ("success", "error_code", "hint", "status", "message"):
            if k in sc:
                info[k] = sc[k]
    if "success" not in info and "error_code" not in info:
        try:
            text = res.content[0].text if res.content else ""
            d = json.loads(text)
            if isinstance(d, dict):
                for k in ("success", "error_code", "hint", "status", "message"):
                    if k in d:
                        info[k] = d[k]
        except Exception:
            pass
    return info


async def _call_once(session, name):
    t0 = time.perf_counter()
    try:
        res = await session.call_tool(name, {})
        return time.perf_counter() - t0, _extract_envelope(res)
    except Exception as e:
        return time.perf_counter() - t0, {"error": f"{type(e).__name__}: {str(e)[:90]}"}


async def _sweep_server(server, skip_extra, repeat):
    out = []
    async with connect_server(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            for t in tools:
                name = t.name
                desc = t.description or ""
                required = (t.inputSchema or {}).get("required", [])
                cls, reason = classify_tool(name, desc, required, skip_extra)
                if cls == "skip":
                    out.append({"name": name, "class": "skip", "reason": reason})
                elif cls == "needs_args":
                    lat, env = await _call_once(session, name)
                    out.append({"name": name, "class": "needs_args", "required": required,
                                "latency_ms": round(lat * 1000, 1), **env})
                else:
                    lats, env = [], {}
                    for _ in range(repeat):
                        lat, e = await _call_once(session, name)
                        lats.append(round(lat * 1000, 1)); env = e
                    out.append({"name": name, "class": "readonly", "latency_ms": lats,
                                "min_ms": min(lats), "avg_ms": round(sum(lats) / len(lats), 1),
                                **env})
    return out


async def run_sweep(config, skip=None, repeat=3):
    """对 config 里每个 server 做全量扫测，返回 [{server, results}]。"""
    skip_extra = frozenset(skip or [])
    return [{"server": s["name"], "results": await _sweep_server(s, skip_extra, repeat)}
            for s in config["servers"]]


def summarize_sweep(sweep_data):
    """返回每个 server 的汇总统计。"""
    out = []
    for sv in sweep_data:
        results = sv["results"]
        ro = [r for r in results if r["class"] == "readonly"]
        na = [r for r in results if r["class"] == "needs_args"]
        sk = [r for r in results if r["class"] == "skip"]
        # 高方差（疑似间歇性尖峰）：max/min > 3
        spikey = [r["name"] for r in ro
                  if r["min_ms"] > 0 and max(r["latency_ms"]) / r["min_ms"] > 3]
        out.append({
            "server": sv["server"],
            "total": len(results),
            "readonly": len(ro),
            "needs_args": len(na),
            "skip": len(sk),
            "readonly_avg_ms": round(sum(r["avg_ms"] for r in ro) / len(ro), 1) if ro else None,
            "readonly_max_ms": max(r["avg_ms"] for r in ro) if ro else None,
            "needs_args_avg_ms": round(sum(r["latency_ms"] for r in na) / len(na), 1) if na else None,
            "validation_ok": sum(1 for r in na if r.get("error_code") == "MISSING_REQUIRED_ARGUMENT"),
            "validation_total": len(na),
            "slowest": [{"name": r["name"], "avg_ms": r["avg_ms"]}
                        for r in sorted(ro, key=lambda r: -r["avg_ms"])[:3]],
            "spikey": spikey,
        })
    return out
