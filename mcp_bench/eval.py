"""工具选择准确率评测。"""
import json
import re
import statistics
import time
from collections import Counter

from .client import get_catalog
from .llm import LLMClient

SYSTEM_PROMPT = (
    "你是一个 MCP 工具选择器。给你一份可用工具列表（工具名: 描述）和一个任务，"
    "你要选出完成该任务最合适的【一个】工具。\n"
    "规则：\n"
    "1. 只输出一个 JSON 对象，格式严格为 {\"tool\": \"<工具名>\"}，"
    "不要输出任何其他文字、解释或 markdown 代码块。\n"
    "2. 工具名必须与列表中的某个名字完全一致。\n"
    "3. 仔细区分名字相近的工具（如同步/异步、状态/结果、公共库/个人库等）。"
)


def build_catalog_text(catalogs):
    """catalogs: [{"server": name, "tools": [{"name","description"}]}] -> 文本"""
    parts = []
    for c in catalogs:
        parts.append(f"【{c['server']}】")
        for t in c["tools"]:
            desc = (t.get("description") or "").replace("\n", " ").strip()
            parts.append(f"- {t['name']}: {desc}")
    return "\n".join(parts)


def extract_tool(content, all_names):
    """从模型输出中抽取工具名。返回 (name, parse_ok)。"""
    c = (content or "").strip()
    c = re.sub(r"^```(?:json)?\s*", "", c)
    c = re.sub(r"\s*```$", "", c).strip()
    m = re.search(r"\{[^{}]*\"tool\"\s*:\s*\"([^\"]+)\"[^{}]*\}", c, re.DOTALL)
    if m:
        return m.group(1).strip(), True
    for name in all_names:
        if re.search(rf"\b{re.escape(name)}\b", c):
            return name, False
    return (c[:60] if c else "<empty>"), False


def majority_vote(preds):
    """返回出现次数最多的预测；并列时取先出现者。"""
    return Counter(preds).most_common(1)[0][0]


async def load_catalog(config, catalog_file=None):
    """返回 [{server, tools:[{name,description}]}]。优先用缓存文件，否则实时抓取。"""
    if catalog_file:
        cat = json.load(open(catalog_file, encoding="utf-8"))
        return [{"server": k, "tools": v} for k, v in cat.items()]

    catalogs = []
    for s in config["servers"]:
        _, tools = await get_catalog(s)
        catalogs.append({
            "server": s["name"],
            "tools": [{"name": t.name, "description": (t.description or "").strip()}
                      for t in tools],
        })
    return catalogs


async def run_eval(config, eval_set, model=None, catalog_file=None, repeat=1):
    """执行评测，返回 (results, catalogs)。

    repeat > 1 时对每题采样 repeat 次，取多数投票结果，以稳定推理型模型的随机性。
    """
    llm_cfg = dict(config["llm"])
    if model:
        llm_cfg["model"] = model
    llm = LLMClient(llm_cfg)

    catalogs = await load_catalog(config, catalog_file)
    cat_text = build_catalog_text(catalogs)
    all_names = {t["name"] for c in catalogs for t in c["tools"]}

    tasks = eval_set["tasks"]
    results = []
    for i, t in enumerate(tasks):
        user = (
            f"可用工具列表：\n{cat_text}\n\n"
            f"任务：{t['task']}\n\n"
            f"请只输出 JSON {{\"tool\": \"...\"}}。"
        )
        votes = []
        total_lat = 0.0
        total_tokens = [0, 0]
        any_err = None
        for _ in range(max(1, repeat)):
            t0 = time.perf_counter()
            try:
                content, tokens = llm.chat(SYSTEM_PROMPT, user)
                pred, parse_ok = extract_tool(content, all_names)
                total_tokens[0] += tokens[0]
                total_tokens[1] += tokens[1]
            except Exception as e:
                pred, parse_ok, any_err = "<error>", False, f"{type(e).__name__}: {e}"
            total_lat += time.perf_counter() - t0
            votes.append({"predicted": pred, "parse_ok": parse_ok})

        pred = majority_vote([v["predicted"] for v in votes])
        results.append({
            "id": t.get("id", i + 1),
            "task": t["task"],
            "expected": t["expected"],
            "predicted": pred,
            "correct": pred == t["expected"],
            "parse_ok": all(v["parse_ok"] for v in votes),
            "in_catalog": pred in all_names,
            "category": t.get("category"),
            "server": t.get("server"),
            "repeat": repeat,
            "votes": votes if repeat > 1 else None,
            "latency_s": round(total_lat, 2),
            "tokens": tuple(total_tokens),
            "error": any_err,
        })
    return results, catalogs


def summarize(results):
    n = len(results)
    correct = [r for r in results if r["correct"]]
    lats = sorted(r["latency_s"] for r in results)

    def pct(p):
        return lats[min(len(lats) - 1, int(p * len(lats)))] if lats else 0.0

    cats = {}
    for r in results:
        cats.setdefault(r["category"] or "未分类", []).append(r)

    summary = {
        "total": n,
        "correct": len(correct),
        "accuracy": len(correct) / n if n else 0.0,
        "latency": {
            "avg_ms": round(statistics.mean(lats) * 1000) if lats else 0,
            "p50_ms": round(pct(0.50) * 1000),
            "p95_ms": round(pct(0.95) * 1000),
            "max_ms": round(max(lats) * 1000) if lats else 0,
        },
        "by_category": {
            cat: {"correct": sum(r["correct"] for r in rs), "total": len(rs)}
            for cat, rs in cats.items()
        },
        "wrong": [r for r in results if not r["correct"]],
    }
    return summary
