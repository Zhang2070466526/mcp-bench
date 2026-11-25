"""配置加载与校验。"""
import json


def load(path):
    """加载 config.json 并校验。返回 dict。"""
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    if not isinstance(cfg.get("servers"), list) or not cfg["servers"]:
        raise ValueError(f"配置 '{path}' 必须包含非空的 'servers' 列表")

    valid_types = ("streamable_http", "sse", "stdio")
    for i, s in enumerate(cfg["servers"]):
        if "name" not in s:
            raise ValueError(f"servers[{i}] 缺少 'name'")
        s.setdefault("type", "streamable_http")
        t = s["type"]
        if t not in valid_types:
            raise ValueError(f"servers[{i}] ({s['name']}) 未知 type '{t}'，支持 {valid_types}")
        if t == "stdio" and "command" not in s:
            raise ValueError(f"servers[{i}] ({s['name']}) 是 stdio 类型，必须提供 'command'")
        if t in ("streamable_http", "sse") and "url" not in s:
            raise ValueError(f"servers[{i}] ({s['name']}) 是 {t} 类型，必须提供 'url'")

    if not isinstance(cfg.get("llm"), dict):
        raise ValueError("配置缺少 'llm' 段")
    llm = {
        "temperature": 0,
        "max_tokens": 2000,
        "api_key": None,
        "api_key_env": None,
    }
    llm.update(cfg["llm"])
    if "model" not in llm:
        raise ValueError("llm 缺少 'model'")
    if "base_url" not in llm:
        raise ValueError("llm 缺少 'base_url'")
    cfg["llm"] = llm

    return cfg
