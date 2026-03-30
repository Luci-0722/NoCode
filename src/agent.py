"""Agent 构建：ChatOpenAI + Middleware 链 + read/bash 工具。"""

from __future__ import annotations

from langchain_core.messages import (
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI

from src.compression import CompressionMiddleware, CompressionStrategy, Middleware
from src.tools import bash, read

SYSTEM_PROMPT = """你是一个名叫**小智**的 AI 伙伴，性格开朗、聪明、乐于助人。

你会记住用户告诉你的事情，并主动提供帮助。
回答要简洁友好，像一个好朋友。"""

MAX_TOOL_ROUNDS = 10


class BFAgent:
    """Best Friend Agent：支持中间件链和工具调用。"""

    def __init__(
        self,
        llm,
        tools: list,
        middlewares: list[Middleware] | None = None,
        system_prompt: str = "",
    ):
        self._llm = llm
        self._tool_map = {t.name: t for t in tools}
        self._middlewares = middlewares or []
        self._system_prompt = system_prompt
        self._messages: list = []

    def clear(self):
        self._messages.clear()

    def _build_messages(self) -> list:
        return [SystemMessage(content=self._system_prompt)] + list(self._messages)

    def _apply_middlewares(self, messages: list) -> list:
        for mw in self._middlewares:
            messages = mw.process(messages)
        return messages

    async def _invoke_tool(self, tool_call: dict) -> str:
        tool = self._tool_map.get(tool_call["name"])
        if not tool:
            return f"错误：未知工具 {tool_call['name']}"
        result = await tool.ainvoke(tool_call["args"])
        return str(result)

    async def chat(self, user_input: str):
        """异步生成器，yield (event_type, *data) 事件。

        事件类型：
          - ("text", str)           模型输出的文本片段
          - ("tool_start", str)     开始调用工具（工具名）
          - ("tool_end", str)       工具调用完成
        """
        self._messages.append(HumanMessage(content=user_input))

        for _ in range(MAX_TOOL_ROUNDS):
            full = self._apply_middlewares(self._build_messages())

            # 流式接收模型输出
            accumulated = AIMessageChunk(content="")
            async for chunk in self._llm.astream(full):
                accumulated = accumulated + chunk
                if chunk.content:
                    yield ("text", chunk.content)

            self._messages.append(accumulated)

            # 无工具调用 → 对话结束
            if not accumulated.tool_calls:
                return

            # 执行所有工具调用
            for tc in accumulated.tool_calls:
                yield ("tool_start", tc["name"])
                result = await self._invoke_tool(tc)
                self._messages.append(
                    ToolMessage(
                        content=result,
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
                yield ("tool_end", tc["name"])


def create_bf_agent(
    api_key: str,
    model: str = "glm-4-flash",
    base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    compression: dict | None = None,
) -> BFAgent:
    """创建 Best Friend Agent 实例。

    Args:
        api_key: 智谱 AI API Key。
        model: 模型名称。
        base_url: API 基础 URL。
        max_tokens: 最大生成 token 数。
        temperature: 温度参数。
        compression: 压缩策略配置字典，包含：
            - trigger_tokens: 触发阈值
            - keep_recent: 保留最近消息数
            - compressible_tools: 可压缩工具名列表
    """
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    ).bind_tools([read, bash])

    middlewares: list[Middleware] = []
    if compression:
        strategy = CompressionStrategy(
            trigger_tokens=compression.get("trigger_tokens", 8000),
            keep_recent=compression.get("keep_recent", 10),
            compressible_tools=tuple(compression.get("compressible_tools", ("read", "bash", "glob", "grep"))),
        )
        middlewares.append(CompressionMiddleware(strategy))

    return BFAgent(
        llm=llm,
        tools=[read, bash],
        middlewares=middlewares,
        system_prompt=SYSTEM_PROMPT,
    )
