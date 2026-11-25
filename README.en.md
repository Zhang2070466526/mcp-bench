# mcp-bench

> English | [简体中文](README.md)

A general-purpose **MCP (Model Context Protocol) server testing and evaluation tool**. It supports `stdio`, `SSE`, and `Streamable HTTP` transports, and does two things for **any** MCP server:

1. **Performance probe** — connect/handshake latency, tool-discovery latency, single tool-call latency
2. **Tool-selection accuracy eval** — given a set of "natural-language task → expected tool" cases, measure whether an arbitrary OpenAI-compatible model picks the right tool

> Motivation: the sneakiest failure mode of an MCP server (especially one with dozens or hundreds of near-identical tools) isn't being slow — it's the **LLM picking the wrong tool among similar ones**. This tool turns the two metrics you care about into numbers, so you can re-run the suite after tweaking tool descriptions.

[![CI](https://github.com/Zhang2070466526/mcp-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/Zhang2070466526/mcp-bench/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- ✅ Unified client for `streamable_http` / `sse` / `stdio` transports
- ✅ Connect multiple servers at once; cross-server routing is covered (tool catalogs are auto-merged)
- ✅ Any OpenAI-compatible LLM (DeepSeek / OpenAI / local vLLM / OpenRouter…), `base_url` + `model` configurable
- ✅ Reasoning-model friendly (falls back to `reasoning_content` when `content` is empty)
- ✅ Emits JSON (per-case details) + Markdown (summary report), CI-ready
- ✅ Zero hardcoding — endpoints, model, and eval set are all config-driven

## Install

```bash
git clone https://github.com/Zhang2070466526/mcp-bench.git
cd mcp-bench
pip install -e .
```

Or run without installing:

```bash
python -m mcp_bench --help
```

Dependencies: `mcp>=1.0`, `openai>=1.0` (`pip install mcp openai`).

## Quick start

### 1. Write `config.json`

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

> `api_key_env` points to an environment variable; you can also use `"api_key": "sk-..."` directly (**never commit it** — `config.json` is already in `.gitignore`).

### 2. Three subcommands

```bash
# Enumerate every tool on every server, exported to output/catalog.json
mcp-bench catalog --config config.json

# Performance probe: handshake / list-tools / (optional) tool-call latency
mcp-bench probe --config config.json
mcp-bench probe --config config.json --call get_current_time --args '{}' --repeat 5

# Tool-selection accuracy eval
mcp-bench eval --config config.json --eval-set examples/eval_set.example.json

# Reasoning models can be non-deterministic: sample each case 3x and majority-vote
mcp-bench eval --config config.json --eval-set examples/eval_set.example.json --repeat 3

# Run everything
mcp-bench all --config config.json --eval-set examples/eval_set.example.json
```

You can also use `python -m mcp_bench ...` instead of the `mcp-bench` entry point.

## Configuration

### `servers[]` — one entry per MCP server

| Field | Type | Notes |
|---|---|---|
| `name` | string | Server name (used as a group label in the catalog) |
| `type` | string | `streamable_http` (default) / `sse` / `stdio` |
| `url` | string | Required for http/sse |
| `command` | string | Required for stdio (e.g. `npx`, `uvx`, `node`) |
| `args` | list | stdio arguments |
| `env` | dict | Extra environment variables for the stdio subprocess |

### `llm` — the model used for eval

| Field | Notes |
|---|---|
| `base_url` | OpenAI-compatible API base URL |
| `model` | Model name |
| `api_key_env` / `api_key` | Credential: env var name, or inline key (not recommended to commit) |
| `temperature` | Default `0` (deterministic tool selection) |
| `max_tokens` | Default `2000` (reasoning models need headroom or the answer gets truncated to empty) |

## Eval set format (`--eval-set`)

```json
{
  "tasks": [
    { "id": 1, "task": "What time is it?", "expected": "get_current_time", "category": "time" }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `task` | ✅ | Natural-language task (use the phrasing your users actually type) |
| `expected` | ✅ | The tool the model *should* select (must match a catalog `name` exactly) |
| `id` | — | Defaults to 1..N |
| `category` | — | For per-group stats (group by near-duplicate tools) |

**Writing a good eval set**: focus on **near-duplicate tool groups** — sync vs async, status vs result, public vs personal library, lowpass/highpass/bandpass/bandstop. Those are where accuracy actually breaks.

## Output

Everything lands in `--out-dir` (default `output/`):

- `catalog.json` — full tool catalog (reusable via `--catalog`)
- `probe_results.json` — per-server latency details
- `eval_results.json` — per-case details (expected/predicted/correct/latency/tokens)
- `eval_report.md` — summary report (accuracy + by-category + wrong cases)

`eval` accepts `--catalog output/catalog.json` to reuse a cached catalog instead of reconnecting.

## FAQ

- **Model returns empty content**: reasoning models spend tokens thinking — raise `llm.max_tokens` (e.g. 4000).
- **Same case flips between right/wrong**: reasoning models can be non-deterministic even at `temperature=0`. Use `eval --repeat 3` (majority vote).
- **stdio fails on Windows**: stdio can hit event-loop compatibility issues on Windows — prefer `streamable_http`, or install `winloop` and `pip install mcp[cli]`.
- **Tool name mismatch**: `expected` must match a `name` in the `catalog` output exactly.

## License

MIT
