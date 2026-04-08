from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from threading import Lock
from typing import Any

from acp import (
    Agent,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    PROTOCOL_VERSION,
    RequestError,
    SetSessionModeResponse,
    SetSessionModelResponse,
    run_agent,
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message,
    update_tool_call,
)
from acp.schema import (
    AgentCapabilities,
    CloseSessionResponse,
    ForkSessionResponse,
    Implementation,
    ListSessionsResponse,
    ResumeSessionResponse,
    SessionInfo,
    SetSessionConfigOptionResponse,
)

from nocode_agent.agent import MainAgent, create_mainagent
from nocode_agent.config import load_config, resolve_api_key, resolve_proxy
from nocode_agent.log import setup_logging

logger = logging.getLogger(__name__)


def _resolve_acp_sessions_path(config: dict[str, Any]) -> str:
    return str(config.get("acp_sessions_path") or "nocode_agent/.state/acp-sessions.json")


def _dump_mcp_servers(mcp_servers: list[Any] | None) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for server in mcp_servers or []:
        if isinstance(server, dict):
            serialized.append(dict(server))
        elif hasattr(server, "model_dump"):
            serialized.append(server.model_dump(by_alias=False))
    return serialized


class SessionStore:
    def __init__(self, path: str) -> None:
        self._path = os.path.abspath(os.path.expanduser(path))
        self._lock = Lock()
        self._sessions: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self._path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        sessions = payload.get("sessions", {})
        if not isinstance(sessions, dict):
            return {}
        return {
            str(session_id): dict(data)
            for session_id, data in sessions.items()
            if isinstance(data, dict)
        }

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as handle:
            json.dump({"sessions": self._sessions}, handle, ensure_ascii=False, indent=2)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._sessions.get(session_id)
            return dict(data) if data else None

    def set(self, session_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._sessions[session_id] = dict(data)
            self._save()

    def delete(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions.pop(session_id, None)
                self._save()

    def list(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return [(session_id, dict(data)) for session_id, data in self._sessions.items()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nocode-acp", description="Run NoCode as an ACP stdio agent.")
    parser.add_argument("--config", help="Path to YAML config file.")
    parser.add_argument("--model", help="Override the primary model.")
    parser.add_argument("--subagent-model", dest="subagent_model", help="Override the subagent model.")
    parser.add_argument("--base-url", dest="base_url", help="Override the model API base URL.")
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, help="Override max tokens.")
    parser.add_argument("--temperature", type=float, help="Override temperature.")
    return parser.parse_args()


def _merge_config(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    merged = dict(config)
    for key in ("model", "subagent_model", "base_url", "max_tokens"):
        value = getattr(args, key, None)
        if value:
            merged[key] = value
    if args.temperature is not None:
        merged["temperature"] = args.temperature
    return merged


def _build_runtime_config(config_path: str | None, args: argparse.Namespace) -> dict[str, Any]:
    return _merge_config(load_config(config_path), args)


def _extract_prompt_text(prompt: list[Any]) -> str:
    parts: list[str] = []
    for item in prompt:
        if getattr(item, "type", "") != "text":
            continue
        text = str(getattr(item, "text", "")).strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _acp_tool_kind(tool_name: str) -> str:
    normalized = tool_name.strip().lower()
    if normalized in {"read", "cat"}:
        return "read"
    if normalized in {"write", "edit", "patch"}:
        return "edit"
    if normalized in {"delete", "remove", "rm"}:
        return "delete"
    if normalized in {"move", "rename", "mv"}:
        return "move"
    if normalized in {"grep", "glob", "search", "find", "list_dir", "ls"}:
        return "search"
    if normalized in {"bash", "execute", "run", "shell"}:
        return "execute"
    if normalized in {"web_fetch", "fetch"}:
        return "fetch"
    if normalized in {"think"}:
        return "think"
    return "other"


class ACPAgentPool:
    def __init__(self, config: dict[str, Any], session_store: SessionStore) -> None:
        self._config = config
        self._session_store = session_store
        self._agents: dict[str, MainAgent] = {}
        self._lock = Lock()

        api_key = resolve_api_key(config)
        if not api_key:
            raise RuntimeError("missing API key: set NOCODE_API_KEY/OPENAI_API_KEY/OLLAMA_API_KEY/ZHIPU_API_KEY, or configure api_key")
        self._api_key = api_key

    async def get(self, session_id: str) -> MainAgent:
        with self._lock:
            existing = self._agents.get(session_id)
        if existing is not None:
            return existing

        session_data = self._session_store.get(session_id) or {}
        thread_id = str(session_data.get("thread_id") or "").strip() or None
        agent = await create_mainagent(
            api_key=self._api_key,
            model=self._config.get("model", "glm-4-flash"),
            base_url=self._config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
            max_tokens=self._config.get("max_tokens", 4096),
            temperature=self._config.get("temperature", 0.7),
            compression=self._config.get("compression"),
            subagent_model=self._config.get("subagent_model"),
            subagent_temperature=self._config.get("subagent_temperature", 0.1),
            thread_id=thread_id,
            persistence_config=self._config,
            mcp_servers=session_data.get("mcp_servers"),
            proxy=resolve_proxy(self._config),
        )
        with self._lock:
            cached = self._agents.get(session_id)
            if cached is not None:
                return cached
            self._agents[session_id] = agent
        return agent

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._agents.pop(session_id, None)

    async def clear_memory(self, session_id: str) -> str:
        agent = await self.get(session_id)
        await agent.clear()
        return agent.thread_id


class NoCodeAgent(Agent):
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._session_store = SessionStore(_resolve_acp_sessions_path(config))
        self._pool = ACPAgentPool(config, self._session_store)
        self._conn = None
        self._active_prompts: dict[str, asyncio.Task[None]] = {}
        logger.info("NoCodeAgent initialized, sessions path: %s", self._session_store._path)

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def initialize(self, protocol_version: int, client_capabilities=None, client_info=None, **kwargs) -> InitializeResponse:
        logger.info("ACP initialize: protocol_version=%d", protocol_version)
        return InitializeResponse(
            protocol_version=min(protocol_version, PROTOCOL_VERSION),
            agent_capabilities=AgentCapabilities(loadSession=True),
            agent_info=Implementation(
                name="nocode",
                title="NoCode",
                version="0.2.0",
            ),
        )

    async def new_session(self, cwd: str | None = None, mcp_servers=None) -> NewSessionResponse:
        session_id = f"session-{os.urandom(8).hex()}"
        logger.info("New session created: %s", session_id)
        self._session_store.set(
            session_id,
            {
                "cwd": cwd or os.getcwd(),
                "thread_id": f"mainagent-{os.urandom(8).hex()}",
                "title": "NoCode Session",
                "mcp_servers": _dump_mcp_servers(mcp_servers),
            },
        )
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs) -> LoadSessionResponse | None:
        session = self._session_store.get(session_id)
        if session is None:
            raise RequestError.invalid_params(f"Session {session_id} not found")
        if mcp_servers is not None:
            updated = dict(session)
            updated["cwd"] = cwd
            updated["mcp_servers"] = _dump_mcp_servers(mcp_servers)
            self._session_store.set(session_id, updated)
            self._pool.drop(session_id)
        return LoadSessionResponse()

    async def list_sessions(self, cursor: str | None = None, cwd: str | None = None, **kwargs) -> ListSessionsResponse:
        sessions = [
            SessionInfo(
                cwd=str(data.get("cwd", os.getcwd())),
                session_id=session_id,
                title=str(data.get("title", "NoCode Session")),
            )
            for session_id, data in self._session_store.list()
            if cwd is None or str(data.get("cwd", os.getcwd())) == cwd
        ]
        return ListSessionsResponse(sessions=sessions)

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs) -> SetSessionModeResponse | None:
        if self._session_store.get(session_id) is None:
            raise RequestError.invalid_params(f"Session {session_id} not found")
        return SetSessionModeResponse()

    async def set_session_model(self, model_id: str, session_id: str, **kwargs) -> SetSessionModelResponse | None:
        if self._session_store.get(session_id) is None:
            raise RequestError.invalid_params(f"Session {session_id} not found")
        return SetSessionModelResponse()

    async def set_config_option(self, config_id: str, session_id: str, value: str | bool, **kwargs) -> SetSessionConfigOptionResponse | None:
        if self._session_store.get(session_id) is None:
            raise RequestError.invalid_params(f"Session {session_id} not found")
        return SetSessionConfigOptionResponse()

    async def authenticate(self, method_id: str, **kwargs):
        return None

    async def prompt(self, prompt: list[Any], session_id: str, **kwargs) -> PromptResponse:
        if self._conn is None:
            raise RequestError.internal_error("ACP connection not initialized")
        if self._session_store.get(session_id) is None:
            self._session_store.set(
                session_id,
                {
                    "cwd": os.getcwd(),
                    "thread_id": f"mainagent-{os.urandom(8).hex()}",
                    "title": "NoCode Session",
                },
            )

        text = _extract_prompt_text(prompt)
        if not text:
            return PromptResponse(stop_reason="end_turn")

        agent = await self._pool.get(session_id)
        current = asyncio.current_task()
        if current is not None:
            self._active_prompts[session_id] = current

        logger.debug("Prompt started: session=%s, text=%s", session_id, text[:200])

        try:
            async for event_type, *data in agent.chat(text):
                if event_type == "text":
                    chunk = data[0]
                    if chunk:
                        await self._conn.session_update(
                            session_id=session_id,
                            update=update_agent_message(text_block(chunk)),
                        )
                    continue

                if event_type == "retry":
                    logger.warning(
                        "重试中: session=%s, attempt=%d/%d, %.1fs",
                        session_id, data[1], data[2], data[3],
                    )
                    continue

                if event_type == "tool_start":
                    tool_name = str(data[0] or "tool")
                    tool_args = data[1] if len(data) > 1 and isinstance(data[1], dict) else {}
                    tool_call_id = str(data[2] if len(data) > 2 else "") or f"{tool_name}-{session_id}"
                    await self._conn.session_update(
                        session_id=session_id,
                        update=start_tool_call(
                            tool_call_id,
                            title=tool_name,
                            kind=_acp_tool_kind(tool_name),
                            status="pending",
                            raw_input=tool_args,
                        ),
                    )
                    continue

                if event_type == "tool_end":
                    tool_name = str(data[0] or "tool")
                    output = str(data[1] if len(data) > 1 else "")
                    tool_call_id = str(data[2] if len(data) > 2 else "") or f"{tool_name}-{session_id}"
                    await self._conn.session_update(
                        session_id=session_id,
                        update=update_tool_call(
                            tool_call_id,
                            title=tool_name,
                            status="completed",
                            content=[tool_content(text_block(output or "(无输出)"))],
                            raw_output={"text": output},
                        ),
                    )
        except asyncio.CancelledError:
            logger.info("Prompt cancelled: session=%s", session_id)
            raise
        finally:
            active = self._active_prompts.get(session_id)
            if active is current:
                self._active_prompts.pop(session_id, None)

        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str) -> None:
        task = self._active_prompts.get(session_id)
        if task is not None and not task.done():
            task.cancel()

    async def fork_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs) -> ForkSessionResponse:
        source_session = self._session_store.get(session_id)
        if source_session is None:
            raise RequestError.invalid_params(f"Session {session_id} not found")
        new_session = await self.new_session(cwd=cwd, mcp_servers=mcp_servers)
        self._session_store.set(
            new_session.session_id,
            {
                "cwd": cwd,
                "thread_id": str(source_session.get("thread_id", f"mainagent-{os.urandom(8).hex()}")),
                "title": str(source_session.get("title", "NoCode Session")),
                "mcp_servers": _dump_mcp_servers(mcp_servers) or list(source_session.get("mcp_servers", [])),
            },
        )
        self._pool.drop(new_session.session_id)
        return ForkSessionResponse(session_id=new_session.session_id)

    async def resume_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs) -> ResumeSessionResponse:
        session = self._session_store.get(session_id)
        if session is None:
            raise RequestError.invalid_params(f"Session {session_id} not found")
        if mcp_servers is not None:
            updated = dict(session)
            updated["cwd"] = cwd
            updated["mcp_servers"] = _dump_mcp_servers(mcp_servers)
            self._session_store.set(session_id, updated)
            self._pool.drop(session_id)
        return ResumeSessionResponse()

    async def close_session(self, session_id: str, **kwargs) -> CloseSessionResponse | None:
        logger.info("Closing session: %s", session_id)
        self._session_store.delete(session_id)
        self._pool.drop(session_id)
        self._active_prompts.pop(session_id, None)
        return CloseSessionResponse()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "clear":
            raise RequestError.method_not_found(f"Ext method {method} not found")

        session_id = str(params.get("session_id", "")).strip()
        if not session_id:
            raise RequestError.invalid_params("session_id is required")
        session = self._session_store.get(session_id)
        if session is None:
            raise RequestError.invalid_params(f"Session {session_id} not found")

        thread_id = await self._pool.clear_memory(session_id)
        updated = dict(session)
        updated["thread_id"] = thread_id
        self._session_store.set(session_id, updated)
        return {"ok": True, "session_id": session_id, "thread_id": thread_id}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


async def main_async() -> int:
    args = _parse_args()
    setup_logging()
    config = _build_runtime_config(args.config, args)
    logger.info("Starting NoCode ACP server (model=%s)", config.get("model", "glm-4-flash"))
    await run_agent(NoCodeAgent(config))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
