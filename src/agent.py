"""Agent 构建：使用 LangChain create_agent + GLM 模型 + SummarizationMiddleware。"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

from src.tools import bash

SYSTEM_PROMPT = """你是一个名叫**小智**的 AI 伙伴，性格开朗、聪明、乐于助人。

你会记住用户告诉你的事情，并主动提供帮助。
回答要简洁友好，像一个好朋友。"""


def create_bf_agent(
    api_key: str,
    model: str = "glm-4-flash",
    base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    trigger_tokens: int = 4000,
    keep_messages: int = 20,
):
    """创建基于 LangChain 的 Best Friend Agent。

    Args:
        api_key: 智谱 AI API Key。
        model: 模型名称，默认 glm-4-flash。
        base_url: API 基础 URL。
        max_tokens: 最大生成 token 数。
        temperature: 温度参数。
        trigger_tokens: 上下文压缩触发 token 阈值。
        keep_messages: 压缩时保留的最近消息数。
    """
    llm = init_chat_model(
        model,
        model_provider="openai",
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    agent = create_agent(
        model=llm,
        tools=[bash],
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
        middleware=[
            SummarizationMiddleware(
                model=llm,
                trigger=("tokens", trigger_tokens),
                keep=("messages", keep_messages),
            ),
        ],
    )

    return agent
