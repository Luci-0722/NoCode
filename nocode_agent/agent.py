"""Agent 构建：主代理 + 子代理 tool。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient

from nocode_agent.compression import (
    AutoCompactor,
    CompressionLifecycleMiddleware,
    CompressionConfig,
    FileReadTracker,
    MicrocompactMiddleware,
    SessionMemoryExtractor,
    build_auto_compact_config,
    build_session_memory_config,
)
from nocode_agent.interactive import InteractiveSessionBroker, PendingUserInputMiddleware
from nocode_agent.persistence import CheckpointerManager, resolve_checkpoint_path
from nocode_agent.prompts import (
    build_main_system_prompt,
    build_subagent_system_prompt,
    build_explore_subagent_prompt,
    build_plan_subagent_prompt,
    build_verification_subagent_prompt,
)
from nocode_agent.skills.registry import init_skill_registry
from nocode_agent.skills.tool import invoke_skill
from nocode_agent.subagents import get_builtin_agents, build_readonly_tool_names
from nocode_agent.tools import build_core_tools, build_readonly_tools, make_agent_tool

logger = logging.getLogger(__name__)


def _is_retryable_error(exc: Exception) -> bool:
    """判断是否为可重试的 API 错误（429、5xx、网络超时等）。"""
    exc_str = str(exc).lower()
    # HTTP 429 rate limit
    if "429" in exc_str or "rate" in exc_str or "速率" in exc_str:
        return True
    # HTTP 5xx server errors
    if any(code in exc_str for code in ("500", "502", "503", "504")):
        return True
    # Connection / timeout errors
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True
    for klass in (
        "ConnectionError",
        "TimeoutError",
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
    ):
        if klass in type(exc).__name__:
            return True
    return False


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


def _normalize_subagent_type(agent_name: str) -> str:
    mapping = {
        "subagent_general_purpose": "general-purpose",
        "subagent_explore": "Explore",
        "subagent_plan": "Plan",
        "subagent_verification": "verification",
    }
    return mapping.get(agent_name, agent_name or "subagent")


def _subagent_key_from_namespace(namespace: tuple[str, ...]) -> tuple[str, ...]:
    if not namespace:
        return ()
    return (namespace[0],)


def _parent_tool_call_id_from_namespace(namespace: tuple[str, ...]) -> str:
    if not namespace:
        return ""
    head = namespace[0]
    if ":" not in head:
        return ""
    node_name, task_id = head.split(":", 1)
    if node_name != "tools":
        return ""
    return task_id


class MainAgent:
    """主代理负责协调工具和子代理。"""

    def __init__(
        self,
        agent,
        checkpointer: CheckpointerManager,
        interactive_broker: InteractiveSessionBroker,
        thread_id: str | None = None,
        model_name: str = "",
        subagent_model_name: str = "",
    ):
        self._agent = agent
        self._checkpointer = checkpointer
        self._interactive_broker = interactive_broker
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

    async def enqueue_user_input(self, text: str) -> None:
        await self._interactive_broker.enqueue_user_input(text)

    async def submit_question_answer(self, answer: str) -> None:
        await self._interactive_broker.submit_question_answer(answer)

    async def drain_runtime_events(self) -> list[dict[str, Any]]:
        return await self._interactive_broker.drain_events()

    async def chat(self, user_input: str):
        """异步生成器，yield (event_type, *data)。包含自动重试。"""
        logger.debug("chat() called: thread=%s, input=%s", self._thread_id[:20], user_input[:200])
        await self._checkpointer.ensure_setup()
        config = {"configurable": {"thread_id": self._thread_id}}
        subgraph_meta_by_key: dict[tuple[str, ...], dict[str, str]] = {}
        subgraph_text_by_key: dict[tuple[str, ...], list[str]] = {}

        max_retries = 5
        base_delay = 2.0

        for attempt in range(max_retries + 1):
            try:
                async for chunk in self._agent.astream(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config=config,
                    stream_mode=["messages", "updates"],
                    subgraphs=True,
                    version="v2",
                ):
                    namespace = tuple(chunk.get("ns", ()))
                    chunk_type = chunk.get("type")

                    if chunk_type == "messages":
                        token, metadata = chunk["data"]
                        agent_name = str(metadata.get("lc_agent_name") or "")
                        if namespace and agent_name and agent_name != "mainagent_supervisor":
                            subagent_key = _subagent_key_from_namespace(namespace)
                            parent_tool_call_id = _parent_tool_call_id_from_namespace(namespace)
                            if (
                                subagent_key
                                and parent_tool_call_id
                                and subagent_key not in subgraph_meta_by_key
                            ):
                                subgraph_meta_by_key[subagent_key] = {
                                    "parent_tool_call_id": parent_tool_call_id,
                                    "subagent_id": " / ".join(subagent_key),
                                    "subagent_type": _normalize_subagent_type(agent_name),
                                }
                                yield (
                                    "subagent_start",
                                    {
                                        "type": "subagent_start",
                                        "parent_tool_call_id": parent_tool_call_id,
                                        "subagent_id": " / ".join(subagent_key),
                                        "subagent_type": _normalize_subagent_type(agent_name),
                                        "thread_id": " / ".join(subagent_key),
                                    },
                                )
                            if isinstance(token, AIMessageChunk) and token.text and subagent_key in subgraph_meta_by_key:
                                subgraph_text_by_key.setdefault(subagent_key, []).append(token.text)
                            continue

                        if metadata.get("langgraph_node") != "model":
                            continue
                        if isinstance(token, AIMessageChunk) and token.text:
                            yield ("text", token.text)
                        runtime_events = await self._interactive_broker.drain_events()
                        for event in runtime_events:
                            yield ("runtime_event", event)
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
                                    subagent_key = _subagent_key_from_namespace(namespace)
                                    if subagent_key and subagent_key in subgraph_meta_by_key:
                                        subgraph_meta = subgraph_meta_by_key.get(subagent_key, {})
                                        parent_tool_call_id = subgraph_meta.get("parent_tool_call_id", "")
                                        for tool_call in message.tool_calls:
                                            yield (
                                                "subagent_tool_start",
                                                {
                                                    "type": "subagent_tool_start",
                                                    "parent_tool_call_id": parent_tool_call_id,
                                                    "subagent_id": subgraph_meta.get("subagent_id", " / ".join(subagent_key)),
                                                    "subagent_type": subgraph_meta.get("subagent_type", "subagent"),
                                                    "name": tool_call["name"],
                                                    "args": tool_call.get("args", {}),
                                                    "tool_call_id": tool_call.get("id", ""),
                                                },
                                            )
                                        continue
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
                                    subagent_key = _subagent_key_from_namespace(namespace)
                                    if subagent_key and subagent_key in subgraph_meta_by_key:
                                        subgraph_meta = subgraph_meta_by_key.get(subagent_key, {})
                                        parent_tool_call_id = subgraph_meta.get("parent_tool_call_id", "")
                                        yield (
                                            "subagent_tool_end",
                                            {
                                                "type": "subagent_tool_end",
                                                "parent_tool_call_id": parent_tool_call_id,
                                                "subagent_id": subgraph_meta.get("subagent_id", " / ".join(subagent_key)),
                                                "subagent_type": subgraph_meta.get("subagent_type", "subagent"),
                                                "name": message.name or "tool",
                                                "output": _render_tool_output(message.content),
                                                "tool_call_id": getattr(message, "tool_call_id", ""),
                                            },
                                        )
                                        continue
                                    tool_call_id = getattr(message, "tool_call_id", "")
                                    if (message.name or "") == "delegate_code":
                                        finished_keys = [
                                            key
                                            for key, meta in subgraph_meta_by_key.items()
                                            if meta.get("parent_tool_call_id") == str(tool_call_id or "")
                                        ]
                                        for subagent_key in finished_keys:
                                            subgraph_meta = subgraph_meta_by_key.get(subagent_key, {})
                                            summary = "".join(subgraph_text_by_key.get(subagent_key, [])).strip()
                                            yield (
                                                "subagent_finish",
                                                {
                                                    "type": "subagent_finish",
                                                    "parent_tool_call_id": str(tool_call_id or ""),
                                                    "subagent_id": subgraph_meta.get("subagent_id", " / ".join(subagent_key)),
                                                    "subagent_type": subgraph_meta.get("subagent_type", "subagent"),
                                                    "summary": _render_tool_output(summary),
                                                },
                                            )
                                            subgraph_meta_by_key.pop(subagent_key, None)
                                            subgraph_text_by_key.pop(subagent_key, None)

                                    yield (
                                        "tool_end",
                                        message.name or "tool",
                                        _render_tool_output(message.content),
                                        tool_call_id,
                                    )
                        runtime_events = await self._interactive_broker.drain_events()
                        for event in runtime_events:
                            yield ("runtime_event", event)

                # 流正常结束，退出重试循环
                runtime_events = await self._interactive_broker.drain_events()
                for event in runtime_events:
                    yield ("runtime_event", event)
                return
            except Exception as exc:
                is_retryable = _is_retryable_error(exc)
                if not is_retryable or attempt >= max_retries:
                    raise

                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "请求失败 (attempt %d/%d): %s，%.1f 秒后重试...",
                    attempt + 1, max_retries, exc, delay,
                )
                yield ("retry", str(exc), attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)


# 已知模型的上下文窗口大小
# 匹配规则: model_name 包含 key 即命中（小写比较）
_CONTEXT_WINDOWS: dict[str, int] = {
    # ── 智谱 GLM 系列 ─────────────────────────────
    "glm-4-long": 1_000_000,
    "glm-4v": 8_000,
    "glm-4v-plus": 8_000,
    "glm-4": 128_000,
    # ── Anthropic Claude 系列 ──────────────────────
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    # ── OpenAI 系列 ───────────────────────────────
    "gpt-4.5": 128_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4-32k": 32_768,
    "gpt-4": 8_192,
    "gpt-3.5": 16_385,
    "o3-mini": 200_000,
    "o3": 200_000,
    "o4-mini": 200_000,
    "o1-mini": 128_000,
    "o1-preview": 128_000,
    "o1": 200_000,
}


def _resolve_context_window(model: str) -> int:
    """根据模型名称解析上下文窗口大小。

    使用子串匹配，按 key 长度降序优先（避免 "gpt-4" 先于 "gpt-4-32k" 命中）。
    """
    model_lower = model.lower()
    for key in sorted(_CONTEXT_WINDOWS, key=len, reverse=True):
        if key in model_lower:
            return _CONTEXT_WINDOWS[key]
    return 128_000  # 默认值


def _build_middleware(
    compression: dict | None,
    context_window: int = 128_000,
    auto_compactor: AutoCompactor | None = None,
    sm_extractor: SessionMemoryExtractor | None = None,
):
    middleware: list[Any] = []

    if compression:
        config = CompressionConfig.from_yaml(compression, context_window=context_window)
        middleware.append(MicrocompactMiddleware(config).as_langchain_middleware())

    if auto_compactor or sm_extractor:
        middleware.append(
            CompressionLifecycleMiddleware(
                auto_compactor=auto_compactor,
                sm_extractor=sm_extractor,
            )
        )

    return middleware


def _build_model(
    api_key: str,
    model: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
    proxy: str = "",
    no_proxy: list[str] | None = None,
) -> ChatOpenAI:
    kwargs: dict[str, Any] = dict(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=6,
    )
    if proxy:
        # 用显式 httpx client 支持 no_proxy，而不是仅传 openai_proxy。
        kwargs["http_client"] = httpx.Client(
            proxy=proxy,
            mounts=_build_no_proxy_mounts(no_proxy),
        )
        kwargs["http_async_client"] = httpx.AsyncClient(
            proxy=proxy,
            mounts=_build_no_proxy_mounts(no_proxy),
        )
    return ChatOpenAI(**kwargs)


def _build_no_proxy_mounts(no_proxy: list[str] | None) -> dict[str, None] | None:
    """把 no_proxy 配置转成 httpx mounts 规则。"""
    mounts: dict[str, None] = {}
    for raw_item in no_proxy or []:
        item = str(raw_item).strip()
        if not item:
            continue
        if item == "*":
            # `all://` 直连表示完全禁用代理，优先级最高。
            return {"all://": None}
        if "://" in item:
            mounts[item] = None
            continue
        if item.startswith("[") and item.endswith("]"):
            mounts[f"all://{item}"] = None
            continue
        if "/" in item:
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                mounts[f"all://*{item}"] = None
            else:
                mounts[f"all://{item}"] = None
            continue
        try:
            ipaddress.ip_address(item)
        except ValueError:
            pass
        else:
            if ":" in item:
                mounts[f"all://[{item}]"] = None
            else:
                mounts[f"all://{item}"] = None
            continue
        if item.lower() == "localhost":
            mounts[f"all://{item}"] = None
            continue
        mounts[f"all://*{item}"] = None
    return mounts or None


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
    auto_compact: dict | None = None,
    session_memory: dict | None = None,
    subagent_model: str | None = None,
    subagent_temperature: float = 0.1,
    thread_id: str | None = None,
    persistence_config: dict | None = None,
    mcp_servers: list[Any] | None = None,
    proxy: str = "",
    no_proxy: list[str] | None = None,
) -> MainAgent:
    """创建主代理和代码子代理。"""
    logger.info(
        "Creating MainAgent: model=%s, base_url=%s, max_tokens=%d, temperature=%.2f, proxy=%s, no_proxy=%s",
        model, base_url, max_tokens, temperature, proxy or "(none)", ",".join(no_proxy or []) or "(none)",
    )
    context_window = _resolve_context_window(model)
    checkpointer = CheckpointerManager(resolve_checkpoint_path(persistence_config))
    saver = checkpointer.get()

    main_llm = _build_model(
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        proxy=proxy,
        no_proxy=no_proxy,
    )
    subagent_llm = _build_model(
        api_key=api_key,
        model=subagent_model or model,
        base_url=base_url,
        temperature=subagent_temperature,
        max_tokens=max_tokens,
        proxy=proxy,
        no_proxy=no_proxy,
    )

    # ── Session Memory (Layer 2) ──────────────────────────────
    sm_config = build_session_memory_config(session_memory)
    sm_extractor = None
    resolved_thread_id = thread_id or f"mainagent-{uuid4().hex}"
    if sm_config:
        sm_llm = _build_model(
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=0.1,
            max_tokens=4096,
            proxy=proxy,
            no_proxy=no_proxy,
        )
        sm_extractor = SessionMemoryExtractor(
            config=sm_config,
            llm=sm_llm,
            thread_id=resolved_thread_id,
        )

    # ── Auto-Compact (Layer 3) ────────────────────────────────
    file_tracker = FileReadTracker()
    auto_compactor = None
    ac_config = build_auto_compact_config(auto_compact, context_window=context_window)
    if ac_config:
        summary_llm = _build_model(
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=0.1,
            max_tokens=ac_config.max_summary_tokens,
            proxy=proxy,
            no_proxy=no_proxy,
        )
        auto_compactor = AutoCompactor(
            config=ac_config,
            context_window=context_window,
            llm=summary_llm,
            file_tracker=file_tracker,
            sm_extractor=sm_extractor,
        )

    middleware = _build_middleware(
        compression,
        context_window=context_window,
        auto_compactor=auto_compactor,
        sm_extractor=sm_extractor,
    )
    interactive_broker = InteractiveSessionBroker()
    main_middleware = [*middleware, PendingUserInputMiddleware(interactive_broker)]

    core_tools = build_core_tools(interactive_broker.ask_user_question)
    readonly_tools = build_readonly_tools(interactive_broker.ask_user_question)

    # Initialize skill system — discover skills from all sources
    init_skill_registry(Path.cwd())
    skill_tools = [invoke_skill]
    mcp_tools = await _load_mcp_tools(mcp_servers)
    logger.info("Loaded %d MCP tools, %d core tools, %d skill tools", len(mcp_tools), len(core_tools), len(skill_tools))

    # ── 创建多类型子代理 ────────────────────────────────────
    # general-purpose：拥有全部核心工具（可读写）
    general_purpose_agent = create_agent(
        model=subagent_llm,
        tools=core_tools,
        system_prompt=build_subagent_system_prompt(),
        checkpointer=saver,
        middleware=middleware,
        name="subagent_general_purpose",
    )

    # Explore / Plan / verification：只读工具集
    explore_agent = create_agent(
        model=subagent_llm,
        tools=readonly_tools,
        system_prompt=build_explore_subagent_prompt(),
        checkpointer=saver,
        middleware=middleware,
        name="subagent_explore",
    )
    plan_agent = create_agent(
        model=subagent_llm,
        tools=readonly_tools,
        system_prompt=build_plan_subagent_prompt(),
        checkpointer=saver,
        middleware=middleware,
        name="subagent_plan",
    )
    verification_agent = create_agent(
        model=subagent_llm,
        tools=readonly_tools,
        system_prompt=build_verification_subagent_prompt(),
        checkpointer=saver,
        middleware=middleware,
        name="subagent_verification",
    )

    subagents_map = {
        "general-purpose": general_purpose_agent,
        "Explore": explore_agent,
        "Plan": plan_agent,
        "verification": verification_agent,
    }

    tools = [*core_tools, *skill_tools, *mcp_tools, make_agent_tool(subagents_map)]
    agent = create_agent(
        model=main_llm,
        tools=tools,
        system_prompt=build_main_system_prompt(),
        checkpointer=saver,
        middleware=main_middleware,
        name="mainagent_supervisor",
    )

    logger.info("MainAgent created: thread_id=%s, context_window=%d", resolved_thread_id, context_window)

    return MainAgent(
        agent=agent,
        checkpointer=checkpointer,
        interactive_broker=interactive_broker,
        thread_id=resolved_thread_id,
        model_name=model,
        subagent_model_name=subagent_model or model,
    )
