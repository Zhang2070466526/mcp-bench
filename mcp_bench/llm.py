"""任意 OpenAI 兼容 LLM 客户端（含推理模型处理）。"""
import os

from openai import OpenAI


class LLMClient:
    def __init__(self, llm_cfg):
        key = None
        if llm_cfg.get("api_key"):
            key = llm_cfg["api_key"]
        elif llm_cfg.get("api_key_env"):
            key = os.environ.get(llm_cfg["api_key_env"])
        if not key:
            raise ValueError(
                "无法获取 API key：请设置 llm.api_key，或 llm.api_key_env 指向的环境变量"
            )
        self.model = llm_cfg["model"]
        self.temperature = llm_cfg.get("temperature", 0)
        self.max_tokens = llm_cfg.get("max_tokens", 2000)
        self.client = OpenAI(api_key=key, base_url=llm_cfg["base_url"])

    def chat(self, system, user):
        """返回 (content, (prompt_tokens, completion_tokens))。

        兼容推理型模型：content 为空时回退到 reasoning_content。
        """
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        msg = r.choices[0].message
        content = msg.content or ""
        if not content.strip():
            content = getattr(msg, "reasoning_content", "") or ""
        usage = r.usage
        tokens = (
            (usage.prompt_tokens, usage.completion_tokens)
            if usage
            else (0, 0)
        )
        return content, tokens
