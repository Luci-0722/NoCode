"""Agent 构建：主代理 + 子代理 tool。"""

from __future__ import annotations

import json
from uuid import uuid4
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient

from nocode_agent.compression import CompressionMiddleware, CompressionStrategy
from nocode_agent.persistence import CheckpointerManager, resolve_checkpoint_path
from nocode_agent.prompts import build_main_system_prompt, build_subagent_system_prompt
from nocode_agent.skill_tools import load_skill, search_skills
from nocode_agent.tools import build_core_tools, make_subagent_tool


def _render_tool_output(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:4000] + ("..." if len(content) > 4000 else "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
                parts.append(json.dumps(item, ensure_ascii=False))
                continue
            parts.append(str(item))
        rendered = "\n".join(part for part in parts if part).strip()
        return rendered[:4000] + ("..." if len(rendered) > 4000 else "")
    rendered = str(content)
    return rendered[:4000] + ("..." if len(rendered) > 4000 else "")


class MainAgent:
    """主代理负责协调工具和子代理。"""

    def __init__(
        self,
        agent,
        checkpointer: CheckpointerManager,
        thread_id: str | None = None,
        model_name: str = "",
        subagent_model_name: str = "",
    ):
        self._agent = agent
        self._checkpointer = checkpointer
        self._thread_id = thread_id or self._new_thread_id()
        self._model_name = model_name
        self._subagent_model_name = subagent_model_name

    @staticmethod
    def _new_thread_id() -> str:
        return f"mainagent-{uuid4().hex}"

    @property
    def thread_id(self) -> str:
        return self._thread_id

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def subagent_model_name(self) -> str:
        return self._subagent_model_name

    async def clear(self):
        await self._checkpointer.delete_thread(self._thread_id)

    async def chat(self, user_input: str):
        """异步生成器，yield (event_type, *data)。"""
        await self._checkpointer.ensure_setup()
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
                                yield (
                                    "tool_start",
                                    tool_call["name"],
                                    tool_call.get("args", {}),
                                    tool_call.get("id", ""),
                                )
                elif step == "tools":
                    for message in new_messages:
                        if isinstance(message, ToolMessage):
                            yield (
                                "tool_end",
                                message.name or "tool",
                                _render_tool_output(message.content),
                                getattr(message, "tool_call_id", ""),
                            )


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


def _mcp_env_to_dict(items: list[Any] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items or []:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            value = str(item.get("value", ""))
        else:
            name = str(getattr(item, "name", "")).strip()
            value = str(getattr(item, "value", ""))
        if name:
            env[name] = value
    return env


def _normalize_mcp_server(server: Any) -> tuple[str, dict[str, Any]] | None:
    if isinstance(server, dict):
        payload = server
    elif hasattr(server, "model_dump"):
        payload = server.model_dump(by_alias=False)
    else:
        payload = {
            "name": getattr(server, "name", ""),
            "command": getattr(server, "command", ""),
            "args": getattr(server, "args", []),
            "env": getattr(server, "env", []),
            "url": getattr(server, "url", ""),
            "type": getattr(server, "type", ""),
        }

    name = str(payload.get("name", "")).strip()
    if not name:
        return None

    command = str(payload.get("command", "")).strip()
    if command:
        return (
            name,
            {
                "transport": "stdio",
                "command": command,
                "args": [str(item) for item in payload.get("args", []) or []],
                "env": _mcp_env_to_dict(payload.get("env")),
            },
        )

    url = str(payload.get("url", "")).strip()
    transport_type = str(payload.get("type", "")).strip().lower()
    if not url or transport_type not in {"http", "sse"}:
        return None

    return (
        name,
        {
            "transport": "streamable_http" if transport_type == "http" else "sse",
            "url": url,
        },
    )


async def _load_mcp_tools(mcp_servers: list[Any] | None) -> list[Any]:
    if not mcp_servers:
        return []

    connections: dict[str, dict[str, Any]] = {}
    for server in mcp_servers:
        normalized = _normalize_mcp_server(server)
        if normalized is None:
            continue
        name, connection = normalized
        connections[name] = connection

    if not connections:
        return []

    client = MultiServerMCPClient(connections, tool_name_prefix=True)
    return await client.get_tools()


async def create_mainagent(
    api_key: str,
    model: str = "glm-4-flash",
    base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    compression: dict | None = None,
    subagent_model: str | None = None,
    subagent_temperature: float = 0.1,
    thread_id: str | None = None,
    persistence_config: dict | None = None,
    mcp_servers: list[Any] | None = None,
) -> MainAgent:
    """创建主代理和代码子代理。"""
    middleware = _build_middleware(compression)
    checkpointer = CheckpointerManager(resolve_checkpoint_path(persistence_config))
    saver = checkpointer.get()

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

    # 技能工具始终与本地核心工具一起暴露。
    skill_tools = [load_skill, search_skills]
    mcp_tools = await _load_mcp_tools(mcp_servers)

    subagent = create_agent(
        model=subagent_llm,
        tools=core_tools,
        system_prompt=build_subagent_system_prompt(),
        checkpointer=saver,
        middleware=middleware,
        name="mainagent_subagent",
    )

    tools = [*core_tools, *skill_tools, *mcp_tools, make_subagent_tool(subagent)]
    agent = create_agent(
        model=main_llm,
        tools=tools,
        system_prompt=build_main_system_prompt(),
        checkpointer=saver,
        middleware=middleware,
        name="mainagent_supervisor",
    )

    return MainAgent(
        agent=agent,
        checkpointer=checkpointer,
        thread_id=thread_id,
        model_name=model,
        subagent_model_name=subagent_model or model,
    )
