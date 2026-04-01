"""Agent 构建：主代理 + 子代理 tool。"""

from __future__ import annotations

from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from src.compression import CompressionMiddleware, CompressionStrategy
from src.prompts import build_main_system_prompt, build_subagent_system_prompt
from src.tools import build_core_tools, make_subagent_tool


class CodeAgent:
    """CodeAgent：主代理负责协调工具和子代理。"""

    def __init__(
        self,
        agent,
        thread_id: str | None = None,
        model_name: str = "",
        subagent_model_name: str = "",
    ):
        self._agent = agent
        self._thread_id = thread_id or self._new_thread_id()
        self._model_name = model_name
        self._subagent_model_name = subagent_model_name

    @staticmethod
    def _new_thread_id() -> str:
        return f"codeagent-{uuid4().hex}"

    @property
    def thread_id(self) -> str:
        return self._thread_id

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def subagent_model_name(self) -> str:
        return self._subagent_model_name

    def clear(self):
        self._thread_id = self._new_thread_id()

    async def chat(self, user_input: str):
        """异步生成器，yield (event_type, *data)。"""
        config = {"configurable": {"thread_id": self._thread_id}}

        async for chunk in self._agent.astream(
            {"messages": [{"role": "user", "content": user_input}]},
            config=config,
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            chunk_type = chunk.get("type")

            if chunk_type == "messages":
                token, metadata = chunk["data"]
                if metadata.get("langgraph_node") != "model":
                    continue
                if isinstance(token, AIMessageChunk) and token.text:
                    yield ("text", token.text)
                continue

            if chunk_type != "updates":
                continue

            for step, data in chunk["data"].items():
                if not isinstance(data, dict):
                    continue
                new_messages = data.get("messages", [])
                if not isinstance(new_messages, list):
                    continue

                if step == "model":
                    for message in new_messages:
                        if isinstance(message, AIMessage):
                            for tool_call in message.tool_calls:
                                yield ("tool_start", tool_call["name"])
                elif step == "tools":
                    for message in new_messages:
                        if isinstance(message, ToolMessage):
                            yield ("tool_end", message.name or "tool")


def _build_middleware(compression: dict | None):
    if not compression:
        return []

    strategy = CompressionStrategy(
        trigger_tokens=compression.get("trigger_tokens", 8000),
        keep_recent=compression.get("keep_recent", 10),
        compressible_tools=tuple(
            compression.get(
                "compressible_tools",
                ("read", "write", "edit", "glob", "grep", "bash", "delegate_code"),
            )
        ),
    )
    return [CompressionMiddleware(strategy).as_langchain_middleware()]


def _build_model(
    api_key: str,
    model: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def create_codeagent(
    api_key: str,
    model: str = "glm-4-flash",
    base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    compression: dict | None = None,
    subagent_model: str | None = None,
    subagent_temperature: float = 0.1,
) -> CodeAgent:
    """创建主代理和代码子代理。"""
    middleware = _build_middleware(compression)

    main_llm = _build_model(
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    subagent_llm = _build_model(
        api_key=api_key,
        model=subagent_model or model,
        base_url=base_url,
        temperature=subagent_temperature,
        max_tokens=max_tokens,
    )

    core_tools = build_core_tools()

    subagent = create_agent(
        model=subagent_llm,
        tools=core_tools,
        system_prompt=build_subagent_system_prompt(),
        checkpointer=InMemorySaver(),
        middleware=middleware,
        name="code_subagent",
    )

    tools = [*core_tools, make_subagent_tool(subagent)]
    agent = create_agent(
        model=main_llm,
        tools=tools,
        system_prompt=build_main_system_prompt(),
        checkpointer=InMemorySaver(),
        middleware=middleware,
        name="codeagent_supervisor",
    )

    return CodeAgent(
        agent=agent,
        model_name=model,
        subagent_model_name=subagent_model or model,
    )
