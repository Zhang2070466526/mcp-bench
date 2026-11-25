"""命令行入口。"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter

from . import __version__
from .config import load
from .probe import run_probe
from .eval import run_eval, summarize, load_catalog
from .client import get_catalog
from .sweep import run_sweep, summarize_sweep
from .audit import run_audit, summarize_audit


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"  已保存: {path}")


def cmd_catalog(args):
    cfg = load(args.config)
    out_dir = _ensure_dir(args.out_dir)
    catalogs = {}
    for s in cfg["servers"]:
        init, tools = asyncio.run(get_catalog(s))
        catalogs[s["name"]] = [
            {"name": t.name, "description": (t.description or "").strip()}
            for t in tools
        ]
        print(f"  {s['name']}: {len(tools)} 个工具")
    path = os.path.join(out_dir, "catalog.json")
    _dump(catalogs, path)
    print(f"\n工具目录已导出到 {path}")


def cmd_probe(args):
    cfg = load(args.config)
    out_dir = _ensure_dir(args.out_dir)
    call_args = None
    if args.args:
        call_args = json.loads(args.args)
    results = asyncio.run(
        run_probe(cfg["servers"], args.call, call_args, args.repeat)
    )
    print("\n" + "=" * 74)
    for r in results:
        if "error" in r:
            print(f"  [{r['server']}] 连接失败: {r['error']}")
            continue
        print(f"  服务: {r['server']}  ({r['type']})")
        print(f"    服务端信息: {r['server_info']}")
        print(f"    连接:       {r['connect_ms']} ms")
        print(f"    初始化:     {r['initialize_ms']} ms")
        print(f"    列工具:     {r['list_tools_ms']} ms  ({r['tool_count']} 个)")
        if "call_tool" in r:
            lat = r["call_latency_ms"]
            print(f"    调用 {r['call_tool']}: {lat} ms  (错误={r['call_is_error']})")
    print("=" * 74)
    _dump(results, os.path.join(out_dir, "probe_results.json"))


def _print_eval(summary):
    n = summary["total"]
    c = summary["correct"]
    print("\n" + "=" * 74)
    print(f"  准确率: {c}/{n} = {summary['accuracy']*100:.1f}%")
    lat = summary["latency"]
    print(f"  延迟: avg {lat['avg_ms']}ms  p50 {lat['p50_ms']}ms  "
          f"p95 {lat['p95_ms']}ms  max {lat['max_ms']}ms")
    print("\n  按类别:")
    for cat, v in sorted(summary["by_category"].items(),
                         key=lambda kv: -kv[1]["correct"] / kv[1]["total"]):
        print(f"    {cat:<16} {v['correct']}/{v['total']}")
    print("\n  错误用例:")
    if not summary["wrong"]:
        print("    （无）")
    for r in summary["wrong"]:
        print(f"    #{r['id']} [{r['category']}] {r['task'][:40]}…")
        print(f"        期望={r['expected']}  预测={r['predicted']}")
    print("=" * 74)


def _write_eval_report(results, summary, model, out_dir):
    lat = summary["latency"]
    lines = [
        "# MCP 工具选择准确率评测报告",
        "",
        f"- 模型: `{model}`",
        f"- 任务数: {summary['total']}",
        f"- 准确率: **{summary['accuracy']*100:.1f}%** ({summary['correct']}/{summary['total']})",
        f"- 延迟: avg {lat['avg_ms']}ms / p50 {lat['p50_ms']}ms / p95 {lat['p95_ms']}ms / max {lat['max_ms']}ms",
        "",
        "## 按类别",
        "",
        "| 类别 | 正确/总数 | 准确率 |",
        "|---|---|---|",
    ]
    for cat, v in sorted(summary["by_category"].items(),
                         key=lambda kv: -kv[1]["correct"] / kv[1]["total"]):
        lines.append(f"| {cat} | {v['correct']}/{v['total']} | {v['correct']/v['total']*100:.0f}% |")
    lines += ["", "## 错误用例", ""]
    if not summary["wrong"]:
        lines.append("（无）")
    for r in summary["wrong"]:
        lines.append(f"- #{r['id']} [{r['category']}] `{r['task']}` → 期望 `{r['expected']}`，预测 `{r['predicted']}`")
    path = os.path.join(out_dir, "eval_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  已保存: {path}")


def cmd_eval(args):
    cfg = load(args.config)
    out_dir = _ensure_dir(args.out_dir)
    with open(args.eval_set, encoding="utf-8") as f:
        eval_set = json.load(f)
    results, _ = asyncio.run(
        run_eval(cfg, eval_set, model=args.model, catalog_file=args.catalog,
                 repeat=args.repeat)
    )
    for r in results:
        tag = "OK " if r["correct"] else "WRONG"
        extra = ""
        if r.get("votes"):
            dist = Counter(v["predicted"] for v in r["votes"])
            extra = "  votes=" + ",".join(f"{k}:{c}" for k, c in dist.most_common())
        print(f"  [{tag}] #{r['id']:>3} {str(r['category']):<14} "
              f"期望={r['expected']:<28} 预测={r['predicted']:<28} "
              f"{r['latency_s']*1000:6.0f}ms{extra}")
    summary = summarize(results)
    _print_eval(summary)
    _dump(results, os.path.join(out_dir, "eval_results.json"))
    _write_eval_report(results, summary, cfg["llm"]["model"] if not args.model else args.model, out_dir)


def cmd_sweep(args):
    cfg = load(args.config)
    out_dir = _ensure_dir(args.out_dir)
    skip = [s.strip() for s in (args.skip or "").split(",") if s.strip()] or None
    data = asyncio.run(run_sweep(cfg, skip=skip, repeat=args.repeat))
    for sv in data:
        print(f"\n=== {sv['server']} ===")
        for r in sv["results"]:
            if r["class"] == "readonly":
                print(f"  [只读] {r['name']:<38} {r['latency_ms']} ms  ok={not r.get('isError', False)}")
            elif r["class"] == "needs_args":
                ec = r.get("error_code", r.get("error", ""))
                print(f"  [需参] {r['name']:<38} {r['latency_ms']:7.1f}ms  ec={ec}")
            else:
                print(f"  [跳过] {r['name']:<38} {r['reason']}")
    for sm in summarize_sweep(data):
        print(f"\n--- {sm['server']} 汇总 ---")
        print(f"  总数 {sm['total']}：只读 {sm['readonly']} / 需参数 {sm['needs_args']} / 跳过 {sm['skip']}")
        if sm["readonly"]:
            print(f"  只读延迟 avg {sm['readonly_avg_ms']}ms  max {sm['readonly_max_ms']}ms")
            if sm["slowest"]:
                print("  最慢: " + ", ".join(f"{x['name']}={x['avg_ms']}ms" for x in sm["slowest"]))
            if sm["spikey"]:
                print(f"  ⚠️ 高方差(疑似间歇尖峰): {sm['spikey']}")
        if sm["needs_args"]:
            print(f"  参数校验生效: {sm['validation_ok']}/{sm['validation_total']}")
    _dump(data, os.path.join(out_dir, "sweep_results.json"))


def cmd_all(args):
    print("== 1/3 导出工具目录 ==")
    cmd_catalog(args)
    print("\n== 2/3 性能探测 ==")
    cmd_probe(args)
    print("\n== 3/3 准确率评测 ==")
    cmd_eval(args)


def cmd_audit(args):
    cfg = load(args.config)
    out_dir = _ensure_dir(args.out_dir)
    cases = None
    if args.cases:
        with open(args.cases, encoding="utf-8") as f:
            cases = json.load(f)
    skip = [s.strip() for s in (args.skip or "").split(",") if s.strip()] or None
    data = asyncio.run(run_audit(cfg, source_dir=args.source_dir, cases=cases,
                                 skip=skip, repeat=args.repeat))

    for sv in data:
        print(f"\n=== {sv['server']} 一致性审计 ===")
        if sv["schema_issues"]:
            print(f"  [schema] {len(sv['schema_issues'])} 个工具文档有问题:")
            for it in sv["schema_issues"]:
                print(f"    ● {it['name']:<40} {'; '.join(it['issues'])}")
        else:
            print("  [schema] 全部通过")
        if sv["static"]:
            print(f"  [static] {len(sv['static'])} 个工具 Returns 声明与源码 return 不一致:")
            for r in sv["static"]:
                if r.get("opaque"):
                    print(f"    ● {r['name']:<40} {r['note']}")
                    continue
                miss = f" 缺返回={r['missing']}" if r["missing"] else ""
                extra = f" 多返回={r['extra']}" if r["extra"] else ""
                nodoc = " (无Returns声明)" if not r.get("returns_doc") else ""
                print(f"    ● {r['name']:<40}{miss}{extra}{nodoc}")
        for r in sv["dynamic"]:
            if "error" in r:
                print(f"  [实测] {r['name']:<40} 调用异常: {r['error']}")
                continue
            tag = ""
            if r.get("missing") or r.get("extra"):
                tag = f" ⚠️ 声明↔实际不一致 missing={r['missing']} extra={r['extra']}"
            elif r.get("isError"):
                tag = " ⚠️ isError"
            print(f"  [实测] {r['name']:<40} keys={r['keys']}{tag}")

    for sm in summarize_audit(data):
        print(f"\n--- {sm['server']} 汇总 ---")
        print(f"  schema问题 {sm['schema_issue_tools']} / 静态不一致 {sm['static_mismatch']} "
              f"/ 实测 {sm['dynamic_called']} (不一致 {sm['dynamic_mismatch']})")
        if sm["mismatch_names"]:
            print(f"  不一致工具: {', '.join(sm['mismatch_names'])}")
    _dump(data, os.path.join(out_dir, "audit_report.json"))


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="mcp-bench",
        description="通用 MCP 服务测试与评测工具（性能探测 + 工具选择准确率）",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--config", default="config.json", help="配置文件路径 (默认 config.json)")
        sp.add_argument("--out-dir", default="output", help="输出目录 (默认 output)")

    c = sub.add_parser("catalog", help="枚举所有服务的工具")
    add_common(c)
    c.set_defaults(func=cmd_catalog)

    pr = sub.add_parser("probe", help="测性能：握手/列工具/工具调用延迟")
    add_common(pr)
    pr.add_argument("--call", help="额外测试某个工具的调用延迟")
    pr.add_argument("--args", help="调用参数 (JSON 字符串)")
    pr.add_argument("--repeat", type=int, default=1, help="工具调用重复次数 (默认 1)")
    pr.set_defaults(func=cmd_probe)

    e = sub.add_parser("eval", help="测工具选择准确率")
    add_common(e)
    e.add_argument("--eval-set", required=True, help="评测集 JSON 路径")
    e.add_argument("--model", help="覆盖配置中的模型")
    e.add_argument("--catalog", help="使用缓存的工具目录 (catalog.json)，避免重复抓取")
    e.add_argument("--repeat", type=int, default=1,
                   help="每题采样次数取多数投票 (默认 1，推理模型建议 3)")
    e.set_defaults(func=cmd_eval)

    a = sub.add_parser("all", help="catalog + probe + eval 全跑")
    add_common(a)
    a.add_argument("--eval-set", required=True)
    a.add_argument("--model", help="覆盖配置中的模型")
    a.add_argument("--repeat", type=int, default=1,
                   help="每题采样次数取多数投票 (默认 1，推理模型建议 3)")
    a.set_defaults(func=cmd_all)

    sw = sub.add_parser("sweep", help="全量扫测服务所有工具（分类+延迟+参数校验）")
    add_common(sw)
    sw.add_argument("--skip", help="额外跳过的工具名，逗号分隔")
    sw.add_argument("--repeat", type=int, default=3, help="只读工具重复次数 (默认 3)")
    sw.set_defaults(func=cmd_sweep)

    au = sub.add_parser("audit", help="一致性审计：工具描述/返回声明 与 实际返回结构是否对得上")
    add_common(au)
    au.add_argument("--source-dir", help="MCP 服务源码目录（做 Returns 声明 vs 源码 return 的静态比对）")
    au.add_argument("--cases", help="样例调用 JSON：{\"工具名\": {\"参数\": 值}}，用于有必填参数的工具实测")
    au.add_argument("--skip", help="额外跳过的工具名，逗号分隔")
    au.add_argument("--repeat", type=int, default=1, help="只读工具调用次数 (默认 1)")
    au.set_defaults(func=cmd_audit)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
