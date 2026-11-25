# mcp-bench

通用的 **MCP（Model Context Protocol）服务测试与评测工具**。支持 `stdio` / `SSE` / `Streamable HTTP` 三种传输，能对**任意** MCP 服务做两件事：

1. **性能探测（probe）** —— 连接/握手延迟、工具发现延迟、单个工具调用延迟
2. **工具选择准确率（eval）** —— 给定「自然语言任务 → 期望工具」评测集，用**任意 OpenAI 兼容模型**测它是否选对工具

> 动机：MCP 服务（尤其是几十上百个工具、描述相近的服务）最隐蔽的坑不是跑得慢，而是 **LLM 在相近工具之间选错**。这个工具把你关心的两个指标量化出来，改完工具描述后可以随时回归。

---

[![CI](https://github.com/Zhang2070466526/mcp-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/Zhang2070466526/mcp-bench/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 特性

- ✅ 三种传输统一封装：`streamable_http` / `sse` / `stdio`
- ✅ 一次连多个服务，跨服务路由也能测（工具目录自动合并）
- ✅ 任意 OpenAI 兼容 LLM（DeepSeek / OpenAI / 本地 vLLM / OpenRouter…），`base_url` + `model` 可配
- ✅ 兼容推理型模型（`content` 为空时回退到 `reasoning_content`）
- ✅ 输出 JSON（明细）+ Markdown（报告），可直接进 CI
- ✅ 零硬编码：端点、模型、评测集全部走配置

---

## 安装

```bash
git clone <你的仓库地址>
cd mcp-bench
pip install -e .
```

或直接用（无需安装）：

```bash
python -m mcp_bench --help
```

依赖：`mcp>=1.0`、`openai>=1.0`（`pip install mcp openai`）。

---

## 快速开始

### 1. 写配置 `config.json`

```bash
cp config.example.json config.json
```

```json
{
  "servers": [
    { "name": "my-http-server", "type": "streamable_http", "url": "http://127.0.0.1:8000/mcp" },
    { "name": "filesystem", "type": "stdio", "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"] }
  ],
  "llm": {
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-v4-pro",
    "api_key_env": "DEEPSEEK_API_KEY"
  }
}
```

> `api_key_env` 指向环境变量；也可以直接用 `"api_key": "sk-..."`（**别提交到 git**，`config.json` 已在 `.gitignore`）。

### 2. 三个子命令

```bash
# 枚举所有服务的工具，导出 output/catalog.json
mcp-bench catalog --config config.json

# 性能探测：握手 / 列工具 / （可选）工具调用延迟
mcp-bench probe --config config.json
mcp-bench probe --config config.json --call get_current_time --args '{}' --repeat 5

# 工具选择准确率评测
mcp-bench eval --config config.json --eval-set examples/eval_set.example.json

# 推理型模型有随机性：每题采样 3 次取多数投票，结果更稳
mcp-bench eval --config config.json --eval-set examples/eval_set.example.json --repeat 3

# 一键全跑
mcp-bench all --config config.json --eval-set examples/eval_set.example.json
```

也可以不安装、直接 `python -m mcp_bench ...`。

---

## 配置说明

### `servers[]` —— 每个元素是一个 MCP 服务

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 服务名（工具目录里用作分组标签） |
| `type` | string | `streamable_http`（默认）/ `sse` / `stdio` |
| `url` | string | http/sse 类型必填 |
| `command` | string | stdio 类型必填（如 `npx`、`uvx`、`node`） |
| `args` | list | stdio 参数 |
| `env` | dict | stdio 子进程环境变量 |

### `llm` —— 评测用的模型

| 字段 | 说明 |
|---|---|
| `base_url` | OpenAI 兼容 API 地址 |
| `model` | 模型名 |
| `api_key_env` / `api_key` | 密钥：环境变量名，或直接写（不推荐提交） |
| `temperature` | 默认 `0`（选工具求稳定） |
| `max_tokens` | 默认 `2000`（推理型模型要给够，否则答案被截断成空） |

---

## 评测集格式（`--eval-set`）

```json
{
  "tasks": [
    { "id": 1, "task": "查一下当前时间", "expected": "get_current_time", "category": "time" }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `task` | ✅ | 自然语言任务（通常用中文/用户真实问法） |
| `expected` | ✅ | 期望模型选中的工具名（必须与目录里完全一致） |
| `id` | 否 | 默认按顺序 1..N |
| `category` | 否 | 用于分项统计（建议按「相近工具组」归类） |

**写评测集的经验**：把注意力放在**名字/描述相近的工具组**上（同步 vs 异步、状态 vs 结果、公共库 vs 个人库、低通/高通/带通/带阻），这些才是准确率的真正失分点。

---

## 输出

所有产物写入 `--out-dir`（默认 `output/`）：

- `catalog.json` —— 全部服务工具目录（可作 `--catalog` 缓存复用）
- `probe_results.json` —— 各服务延迟明细
- `eval_results.json` —— 逐题明细（期望/预测/正确/延迟/token）
- `eval_report.md` —— 汇总报告（准确率 + 按类别 + 错误用例）

`eval` 支持 `--catalog output/catalog.json` 复用已抓取的目录，评测多次时不重复连接服务。

---

## 常见问题

- **模型返回空内容**：推理型模型把 token 花在思考上，调大 `llm.max_tokens`（如 4000）。
- **同一题结果时对时错**：推理型模型即使 `temperature=0` 也可能有随机性。用 `eval --repeat 3`（多数投票）稳住结果。
- **stdio 在 Windows 上连不上**：stdio 传输在 Windows 可能有事件循环兼容问题，优先用 `streamable_http`；或安装 `winloop` 并 `pip install mcp[cli]`。
- **工具名对不上**：`expected` 必须和 `catalog` 输出里的 `name` 完全一致。

## License

MIT
