"""离线单元测试（不依赖网络/MCP 服务/LLM）。"""
import json

from mcp_bench.config import load
from mcp_bench.eval import extract_tool, build_catalog_text, majority_vote
from mcp_bench.sweep import classify_tool


def test_extract_tool_json():
    names = {"run_tr_simulation", "run_mixer_simulation"}
    name, ok = extract_tool('{"tool": "run_tr_simulation"}', names)
    assert name == "run_tr_simulation"
    assert ok is True


def test_extract_tool_fenced():
    names = {"draw_chart"}
    name, ok = extract_tool('```json\n{"tool": "draw_chart"}\n```', names)
    assert name == "draw_chart"
    assert ok is True


def test_extract_tool_fallback():
    names = {"list_calculators"}
    name, ok = extract_tool("我认为应该用 list_calculators", names)
    assert name == "list_calculators"
    assert ok is False


def test_build_catalog_text():
    cats = [{"server": "srv", "tools": [{"name": "a", "description": "描述"}]}]
    text = build_catalog_text(cats)
    assert "【srv】" in text
    assert "- a: 描述" in text


def test_majority_vote():
    assert majority_vote(["a", "a", "b"]) == "a"
    assert majority_vote(["a", "b", "b"]) == "b"
    # 并列取先出现者
    assert majority_vote(["a", "b"]) == "a"


def test_classify_tool():
    assert classify_tool("get_status", "查状态", [])[0] == "readonly"
    assert classify_tool("launch_aedt", "启动", [])[0] == "skip"
    assert classify_tool("quit_aedt", "退出", [])[0] == "skip"
    assert classify_tool("do_thing", "⚠️ 破坏性", [])[0] == "skip"
    assert classify_tool("cleanup_orphans", "清理", [])[0] == "skip"
    assert classify_tool("simulate_antenna", "仿真", [])[0] == "skip"  # 重型计算
    assert classify_tool("delete_x", "删除", ["path"])[0] == "needs_args"
    # 显式跳过优先
    assert classify_tool("get_status", "查状态", [], {"get_status"})[0] == "skip"


def test_config_load(tmp_path):
    cfg = {
        "servers": [
            {"name": "x", "type": "streamable_http", "url": "http://localhost:1/mcp"},
            {"name": "y", "type": "stdio", "command": "npx", "args": ["-y", "pkg"]},
        ],
        "llm": {"base_url": "http://localhost", "model": "m", "api_key": "sk-test"},
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    loaded = load(str(p))
    assert len(loaded["servers"]) == 2
    assert loaded["llm"]["model"] == "m"
