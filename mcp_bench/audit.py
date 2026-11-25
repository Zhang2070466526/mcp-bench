"""一致性审计：工具的 description / Returns 声明 与 实际返回结构是否对得上。

三个互补的检查层（可独立也可组合）：
1. 静态 schema 审计 —— 所有 MCP 通用，无需源码/无需调用：
   查 description 缺失/过短、必填参数缺说明、outputSchema/annotations 缺失。
2. 源码 AST 比对（--source-dir）—— 需要 MCP 服务源码目录：
   提取每个 @mcp.tool() 的 Returns docstring 声明的字段 vs 源码 return 语句里的 dict key。
3. 动态返回审计 —— 只读工具空参调用 + --cases 指定工具真实调用：
   提取实际返回的顶层 key，与声明字段做 diff（missing / extra）。

价值：抓到「工具文档声称返回 X，但实际返回 Y」这类会让 agent 读错字段、
选错工具的根源问题（例如声明 result_path、实际在 details.netlist_path）。
"""
from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path

from mcp import ClientSession

from .client import connect_server, tool_input_schema, tool_output_schema, tool_annotation
from .sweep import classify_tool

# 从 Returns/返回 段落里抽出 dict key（"key": 形式，冒号前）；比抽所有引号更准，能排除示例值
_KEY_TOKEN = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
_RETURNS_HEAD = re.compile(r"^(Returns?|返回)[:\s]")


# --------------------------------------------------------------------------- #
# 1. 静态 schema 审计
# --------------------------------------------------------------------------- #
def audit_schema(tools):
    """对 tools/list 拿到的每个工具做静态文档质量检查。返回 [{name, description, issues}]。"""
    issues = []
    for t in tools:
        name = t.name
        desc = (t.description or "").strip()
        probs = []
        if not desc:
            probs.append("description 缺失")
        elif len(desc) < 15:
            probs.append(f"description 过短({len(desc)}字符)")

        schema = tool_input_schema(t)
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        for r in required:
            pd = ((props.get(r) or {}).get("description") or "").strip()
            if not pd:
                probs.append(f"必填参数 {r} 缺 description")

        if tool_output_schema(t) is None:
            probs.append("缺 outputSchema")

        ro = tool_annotation(t, "readOnlyHint", "read_only_hint")
        de = tool_annotation(t, "destructiveHint", "destructive_hint")
        if ro is None and de is None:
            probs.append("readOnlyHint/destructiveHint 注解缺失")

        if probs:
            issues.append({"name": name, "description": desc, "issues": probs})
    return issues


# --------------------------------------------------------------------------- #
# 2. 源码 AST 比对
# --------------------------------------------------------------------------- #
def _is_tool_decorator(dec):
    if isinstance(dec, ast.Call):
        f = dec.func
        return isinstance(f, ast.Attribute) and f.attr == "tool"
    if isinstance(dec, ast.Attribute):
        return dec.attr == "tool"
    if isinstance(dec, ast.Name):
        return dec.id == "tool"
    return False


def _returns_section(doc):
    """抽取 docstring 里 Returns/返回 段落（多行）。"""
    if not doc:
        return ""
    lines = doc.split("\n")
    out, on = [], False
    for ln in lines:
        s = ln.strip()
        if _RETURNS_HEAD.match(s):
            on = True
            out.append(s)
            continue
        if on:
            if re.match(r"^(Args?|Raises?|Note|Example|Usage|参数|示例|用法|注意)[:\s]", s):
                break
            out.append(s)
    return "\n".join(out).strip()


def _dict_keys(node):
    """递归收集 dict 字面量里的字符串 key。"""
    keys = set()
    if isinstance(node, ast.Dict):
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Return):
            continue
        keys |= _dict_keys(child)
    return keys


def ast_extract(source_dir):
    """AST 提取 source_dir 下所有 @mcp.tool() 的声明/返回。返回 {name: info}。"""
    rows = {}
    root = Path(source_dir)
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(_is_tool_decorator(d) for d in node.decorator_list):
                continue
            doc = ast.get_docstring(node) or ""
            rdoc = _returns_section(doc)
            declared = set(_KEY_TOKEN.findall(rdoc))
            keys = set()
            opaque = False
            for n in ast.walk(node):
                if isinstance(n, ast.Return) and n.value is not None:
                    if isinstance(n.value, (ast.Call, ast.Dict)):
                        keys |= _dict_keys(n.value)
                    else:
                        # return result / 变量 → 实际结构由 gRPC 后端决定，静态无法判定
                        opaque = True
            rows[node.name] = {
                "name": node.name,
                "file": str(path.relative_to(root)),
                "returns_doc": rdoc,
                "declared_keys": sorted(declared),
                "return_keys": sorted(keys),
                "opaque": opaque,
            }
    return rows


def _diff(declared, actual):
    d, a = set(declared), set(actual)
    return sorted(d - a), sorted(a - d)  # missing, extra


# --------------------------------------------------------------------------- #
# 3. 动态返回审计
# --------------------------------------------------------------------------- #
def _top_level_keys(doc):
    """提取 Returns docstring 里第一个 { } 块的顶层 key（深度=1），排除嵌套与示例值。"""
    keys = set()
    depth = 0
    i, n = 0, len(doc)
    key_re = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
    while i < n:
        c = doc[i]
        if c == "{":
            depth += 1
            i += 1
        elif c == "}":
            depth -= 1
            i += 1
            if depth == 0:
                break
        elif c == '"':
            m = key_re.match(doc, i)
            if m:
                if depth == 1:
                    keys.add(m.group(1))
                i = m.end()
            else:
                j = doc.find('"', i + 1)
                i = j + 1 if j != -1 else n
        else:
            i += 1
    return keys


def _extract_result(res):
    """从 call_tool 结果提取 (顶层 key 列表, 信封信息)。"""
    sc = getattr(res, "structuredContent", None)
    keys = []
    info = {"isError": bool(getattr(res, "isError", False))}
    if isinstance(sc, dict):
        keys = list(sc.keys())
        for k in ("success", "error_code", "hint", "status", "message"):
            if k in sc:
                info[k] = sc[k]
    else:
        try:
            text = res.content[0].text if res.content else ""
            d = json.loads(text)
            if isinstance(d, dict):
                keys = list(d.keys())
                for k in ("success", "error_code", "hint", "status", "message"):
                    if k in d:
                        info[k] = d[k]
        except Exception:
            pass
    return keys, info


async def _call_tool(session, name, args):
    t0 = time.perf_counter()
    try:
        res = await session.call_tool(name, args or {})
        keys, info = _extract_result(res)
        return {"name": name, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "keys": keys, **info}
    except Exception as e:
        return {"name": name, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "keys": [], "error": f"{type(e).__name__}: {str(e)[:90]}"}


async def _audit_server(server, ast_rows, cases, skip, repeat):
    out = []
    async with connect_server(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            for t in tools:
                name = t.name
                desc = t.description or ""
                required = tool_input_schema(t).get("required", [])
                cls, _ = classify_tool(name, desc, required, skip)

                if cls == "readonly":
                    r = await _call_tool(session, name, {})
                elif name in cases:
                    r = await _call_tool(session, name, cases[name])
                else:
                    continue  # 有必填参且未提供样例 → 不动它

                # 与声明做 diff（顶层粒度，避免空数组导致的嵌套误报）
                info = ast_rows.get(name) or {}
                declared = _top_level_keys(info.get("returns_doc", ""))
                missing, extra = _diff(declared, r["keys"])
                r.update({"declared_keys": sorted(declared), "missing": missing, "extra": extra})
                out.append(r)
    return out


async def run_audit(config, source_dir=None, cases=None, skip=None, repeat=1):
    """对 config 里每个 server 做一致性审计。返回 [{server, schema_issues, static, dynamic}]。"""
    skip_extra = frozenset(skip or [])
    cases = cases or {}
    ast_rows = ast_extract(source_dir) if source_dir else {}

    data = []
    for s in config["servers"]:
        async with connect_server(s) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
        schema_issues = audit_schema(tools)
        tool_names = {t.name for t in tools}

        # 静态 AST 比对（声明 vs 源码 return）：只对该 server 实际存在的工具
        static = []
        for name in sorted(tool_names & set(ast_rows)):
            info = ast_rows[name]
            if info["opaque"]:
                static.append({"name": name, "opaque": True, "note": "返回由后端决定，静态无法判定"})
                continue
            missing, extra = _diff(info["declared_keys"], info["return_keys"])
            if missing or extra or not info["returns_doc"]:
                static.append({"name": name, "file": info["file"],
                               "returns_doc": info["returns_doc"],
                               "declared": info["declared_keys"],
                               "return": info["return_keys"],
                               "missing": missing, "extra": extra,
                               "opaque": False})

        dynamic = await _audit_server(s, ast_rows, cases, skip_extra, repeat)
        data.append({"server": s["name"], "schema_issues": schema_issues,
                     "static": static, "dynamic": dynamic})
    return data


def summarize_audit(data):
    """汇总：每个 server 的 schema 问题数、静态不一致数、动态不一致数。"""
    out = []
    for sv in data:
        dyn_bad = [r for r in sv["dynamic"] if r.get("missing") or r.get("extra") or r.get("isError")]
        out.append({
            "server": sv["server"],
            "schema_issue_tools": len(sv["schema_issues"]),
            "static_mismatch": len(sv["static"]),
            "dynamic_called": len(sv["dynamic"]),
            "dynamic_mismatch": len(dyn_bad),
            "mismatch_names": sorted({r["name"] for r in dyn_bad}),
        })
    return out
