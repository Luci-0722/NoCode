from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

from acp import Client as ACPClient
from acp import PROTOCOL_VERSION, RequestError, connect_to_agent, text_block
from acp.schema import EnvVariable, McpServerStdio

from multiagent_system.config import load_config


MENTION_PATTERN = re.compile(r"(?<![\w-])@([A-Za-z0-9_\-\u4e00-\u9fff]+)")
STATIC_DIR = Path(__file__).with_name("static")
DEFAULT_MAX_HOPS = 4


def _resolve_multiagent_state_path(config: dict[str, Any]) -> Path:
    raw = str(config.get("multiagent_state_path") or "multiagent_system/.state/state.json")
    return Path(raw).expanduser()


def _resolve_registry_dir(config: dict[str, Any]) -> Path:
    raw = str(config.get("session_registry_dir") or "multiagent_system/.state/session-registries")
    return Path(raw).expanduser()


def _resolve_workspace_payloads(config: dict[str, Any]) -> list[dict[str, Any]]:
    payload = config.get("work_environments", [])
    if isinstance(payload, list) and payload:
        resolved: list[dict[str, Any]] = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                continue
            cwd = str(item.get("cwd") or "").strip()
            if not cwd:
                continue
            path = Path(cwd).expanduser().resolve()
            resolved.append(
                {
                    "id": str(item.get("id") or f"workspace-{index}"),
                    "name": str(item.get("name") or path.name or path),
                    "cwd": str(path),
                    "description": str(item.get("description") or ""),
                }
            )
        if resolved:
            return resolved

    default_cwd = str(Path.cwd().resolve())
    return [
        {
            "id": "default",
            "name": Path(default_cwd).name or default_cwd,
            "cwd": default_cwd,
            "description": "当前服务工作目录",
        }
    ]


class PersistentJsonState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except json.JSONDecodeError:
                return {}

    def save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(slots=True)
class WorkspaceSummary:
    id: str
    name: str
    cwd: str
    description: str = ""


@dataclass(slots=True)
class SessionSummary:
    id: str
    title: str
    workspace_id: str
    cwd: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class AgentSummary:
    id: str
    name: str
    system_prompt: str = ""
    transport: str = "acp"
    acp_agent_name: str = ""
    acp_command: str = ""
    stdio_command: str = ""
    status: str = "idle"
    thread_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class EventRecord:
    id: str
    kind: str
    agent_id: str
    agent_name: str
    sender: str
    text: str = ""
    run_id: str = ""
    status: str = ""
    target_agent_ids: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Delivery:
    text: str
    sender: str
    origin_event_id: str
    depth: int = 0
    trail: list[str] = field(default_factory=list)


class AgentRuntime:
    async def run(self, agent_name: str, system_prompt: str, message: str):
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        return None

    def thread_id(self) -> str:
        raise NotImplementedError


class ACPQueueClient(ACPClient):
    def __init__(self) -> None:
        self._updates: asyncio.Queue[Any] = asyncio.Queue()
        self._conn = None

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def session_update(self, session_id: str, update) -> None:
        await self._updates.put(update)

    async def request_permission(self, options, session_id: str, tool_call, **kwargs):
        raise RequestError.method_not_found("request_permission is not supported")

    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs):
        raise RequestError.method_not_found("write_text_file is not supported")

    async def read_text_file(self, path: str, session_id: str, limit: int | None = None, line: int | None = None, **kwargs):
        raise RequestError.method_not_found("read_text_file is not supported")

    async def create_terminal(self, command: str, session_id: str, args=None, cwd: str | None = None, env=None, output_byte_limit: int | None = None, **kwargs):
        raise RequestError.method_not_found("create_terminal is not supported")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs):
        raise RequestError.method_not_found("terminal_output is not supported")

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs):
        raise RequestError.method_not_found("release_terminal is not supported")

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs):
        raise RequestError.method_not_found("wait_for_terminal_exit is not supported")

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs):
        raise RequestError.method_not_found("kill_terminal is not supported")

    async def ext_method(self, method: str, params: dict[str, Any]):
        raise RequestError.method_not_found(f"Ext method {method} not found")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def pop_update(self) -> Any:
        return await self._updates.get()

    def clear_updates(self) -> None:
        while not self._updates.empty():
            try:
                self._updates.get_nowait()
            except asyncio.QueueEmpty:
                break

    def has_updates(self) -> bool:
        return not self._updates.empty()


def _render_acp_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        parts = [_render_acp_content(item) for item in content]
        return "\n".join(part for part in parts if part).strip()
    content_type = getattr(content, "type", "")
    if content_type == "text":
        return str(getattr(content, "text", "")).strip()
    inner = getattr(content, "content", None)
    if inner is not None and inner is not content:
        return _render_acp_content(inner)
    if isinstance(content, str):
        return content.strip()
    return ""


class ACPRemoteRuntime(AgentRuntime):
    """Runtime backed by a local ACP stdio process."""

    def __init__(
        self,
        command: str,
        acp_agent_name: str = "",
        timeout: float = 120.0,
        session_id: str = "",
        cwd: str | None = None,
        mcp_servers: list[Any] | None = None,
    ):
        self._command = command.strip()
        self._acp_agent_name = acp_agent_name.strip()
        self._timeout = timeout
        self._client = ACPQueueClient()
        self._conn_cm = None
        self._conn = None
        self._process: asyncio.subprocess.Process | None = None
        self._session_id = session_id.strip()
        self._agent_title = ""
        self._cwd = str(Path(cwd or Path.cwd()).resolve())
        self._mcp_servers = list(mcp_servers or [])

    async def _ensure_process(self) -> None:
        if self._conn is not None and self._process is not None and self._process.returncode is None:
            return
        if not self._command:
            raise ValueError("acp command is required")

        self._process = await asyncio.create_subprocess_exec(
            *shlex.split(self._command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        self._conn_cm = connect_to_agent(self._client, self._process.stdin, self._process.stdout)
        self._conn = await self._conn_cm.__aenter__()
        initialize_response = await self._conn.initialize(PROTOCOL_VERSION)
        agent_info = getattr(initialize_response, "agent_info", None)
        self._agent_title = str(getattr(agent_info, "title", "") or getattr(agent_info, "name", "") or "")
        detected_name = str(getattr(agent_info, "name", "") or "").strip()
        expected_name = self._acp_agent_name
        if expected_name and detected_name and detected_name != expected_name:
            await self.close()
            raise ValueError(f"ACP agent {detected_name} does not match expected {expected_name}")
        if detected_name:
            self._acp_agent_name = detected_name
        if self._session_id:
            try:
                await self._conn.load_session(cwd=self._cwd, session_id=self._session_id, mcp_servers=self._mcp_servers)
                return
            except Exception:
                self._session_id = ""
        session = await self._conn.new_session(cwd=self._cwd, mcp_servers=self._mcp_servers)
        self._session_id = str(getattr(session, "session_id", ""))

    async def run(self, agent_name: str, system_prompt: str, message: str):
        await self._ensure_process()
        prompt = _build_runtime_prompt(agent_name=agent_name, system_prompt=system_prompt, message=message)
        self._client.clear_updates()
        prompt_task = asyncio.create_task(
            self._conn.prompt(session_id=self._session_id, prompt=[text_block(prompt)])
        )

        try:
            while True:
                if prompt_task.done() and not self._client.has_updates():
                    break

                update_task = asyncio.create_task(self._client.pop_update())
                done, _ = await asyncio.wait(
                    {prompt_task, update_task},
                    timeout=self._timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    update_task.cancel()
                    raise TimeoutError("ACP prompt timed out")

                if update_task in done:
                    update = update_task.result()
                    session_update = str(getattr(update, "session_update", ""))
                    if session_update == "agent_message_chunk":
                        text = _render_acp_content(getattr(update, "content", None))
                        if text:
                            yield ("text", text)
                        continue
                    if session_update == "tool_call":
                        yield (
                            "tool_start",
                            str(getattr(update, "title", "") or getattr(update, "kind", "") or "tool"),
                            getattr(update, "raw_input", None) or {},
                            str(getattr(update, "tool_call_id", "")),
                        )
                        continue
                    if session_update == "tool_call_update" and str(getattr(update, "status", "")) in {"completed", "failed"}:
                        output = _render_acp_content(getattr(update, "content", None))
                        if not output:
                            raw_output = getattr(update, "raw_output", None)
                            output = json.dumps(raw_output, ensure_ascii=False) if raw_output else ""
                        yield (
                            "tool_end",
                            str(getattr(update, "title", "") or "tool"),
                            output,
                            str(getattr(update, "tool_call_id", "")),
                        )
                        continue
                else:
                    update_task.cancel()

            response = await prompt_task
        except Exception:
            if not prompt_task.done():
                prompt_task.cancel()
            raise

        stop_reason = str(getattr(response, "stop_reason", "") or "")
        if stop_reason == "error":
            raise RuntimeError("ACP prompt failed")

    async def clear(self) -> None:
        await self._ensure_process()
        await self._conn.ext_method("clear", {"session_id": self._session_id})

    def thread_id(self) -> str:
        return self._session_id

    async def close(self) -> None:
        if self._conn_cm is not None:
            try:
                await self._conn_cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._conn_cm = None
        self._conn = None
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()
        self._process = None

    async def stop(self) -> None:
        if self._conn is not None and self._session_id:
            try:
                await self._conn.cancel(session_id=self._session_id)
            except Exception:
                pass


class StdioRuntime(AgentRuntime):
    def __init__(self, command: str, thread_id: str = "", cwd: str | None = None):
        self._command = command.strip()
        self._process: asyncio.subprocess.Process | None = None
        self._thread_id = thread_id.strip()
        self._cwd = str(Path(cwd or Path.cwd()).resolve())

    async def _ensure_process(self) -> None:
        if self._process and self._process.returncode is None:
            return
        if not self._command:
            raise ValueError("stdio command is required")

        env = dict(os.environ)
        if self._thread_id:
            env["NOCODE_THREAD_ID"] = self._thread_id

        self._process = await asyncio.create_subprocess_exec(
            *shlex.split(self._command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=env,
        )
        hello = await self._read_event()
        if hello.get("type") == "fatal":
            raise RuntimeError(str(hello.get("message", "stdio runtime fatal error")))
        if hello.get("type") != "hello":
            raise RuntimeError(f"unexpected stdio hello event: {hello}")
        self._thread_id = str(hello.get("thread_id", ""))

    async def _send(self, payload: dict[str, Any]) -> None:
        await self._ensure_process()
        assert self._process and self._process.stdin
        self._process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_event(self) -> dict[str, Any]:
        await self._ensure_process()
        assert self._process and self._process.stdout
        line = await self._process.stdout.readline()
        if not line:
            stderr = ""
            if self._process.stderr:
                try:
                    stderr = (await asyncio.wait_for(self._process.stderr.read(), timeout=0.05)).decode("utf-8", errors="replace")
                except Exception:
                    stderr = ""
            raise RuntimeError(f"stdio process exited unexpectedly{': ' + stderr.strip() if stderr.strip() else ''}")
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid stdio json: {error}")

    async def run(self, agent_name: str, system_prompt: str, message: str):
        prompt = _build_runtime_prompt(agent_name=agent_name, system_prompt=system_prompt, message=message)
        await self._send({"type": "prompt", "text": prompt})
        while True:
            event = await self._read_event()
            event_type = str(event.get("type", ""))
            if event_type == "text":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    yield ("text", delta)
                continue
            if event_type == "tool_start":
                yield (
                    "tool_start",
                    str(event.get("name", "tool")),
                    event.get("args", {}),
                    str(event.get("tool_call_id", "")),
                )
                continue
            if event_type == "tool_end":
                yield (
                    "tool_end",
                    str(event.get("name", "tool")),
                    str(event.get("output", "")),
                    str(event.get("tool_call_id", "")),
                )
                continue
            if event_type == "done":
                return
            if event_type == "error":
                raise RuntimeError(str(event.get("message", "stdio runtime error")))
            if event_type == "fatal":
                raise RuntimeError(str(event.get("message", "stdio runtime fatal error")))
            if event_type in {"status", "hello", "cleared"}:
                if event.get("thread_id"):
                    self._thread_id = str(event["thread_id"])
                continue

    async def clear(self) -> None:
        await self._send({"type": "clear"})
        event = await self._read_event()
        if event.get("type") != "cleared":
            raise RuntimeError(f"unexpected stdio clear response: {event}")
        self._thread_id = str(event.get("thread_id", self._thread_id))

    async def stop(self) -> None:
        await self.close()
        self._process = None

    def thread_id(self) -> str:
        return self._thread_id

    async def close(self) -> None:
        if not self._process:
            return
        try:
            if self._process.returncode is None:
                await self._send({"type": "exit"})
        except Exception:
            pass
        if self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()


def _build_runtime_prompt(agent_name: str, system_prompt: str, message: str) -> str:
    sections = [
        "你正在由一个 ACP 编排层调度运行。",
        f"你的名字是：{agent_name}",
        "你必须以这个名字对应的独立 agent 身份回答。",
        "如果你需要其他 agent 协作，请先调用 MCP 工具查看当前会话里有哪些 agent，再在正文里显式使用 @agent名。",
        "如果不需要协作，不要随意 @。",
    ]
    if system_prompt.strip():
        sections.append("你的专属角色设定：")
        sections.append(system_prompt.strip())
    sections.append("当前输入：")
    sections.append(message.strip())
    return "\n\n".join(sections)


class ManagedAgent:
    def __init__(self, summary: AgentSummary, runtime: AgentRuntime):
        self.summary = summary
        self.runtime = runtime
        self.lock = asyncio.Lock()


class MultiAgentStore:
    def __init__(
        self,
        config: dict[str, Any],
        session_summary: SessionSummary,
        *,
        max_hops: int = DEFAULT_MAX_HOPS,
        persist_hook: Callable[[], None] | None = None,
        snapshot: dict[str, Any] | None = None,
    ):
        self._config = config
        self._summary = session_summary
        self._max_hops = max_hops
        self._persist_hook = persist_hook
        self._agents: dict[str, ManagedAgent] = {}
        self._events: list[EventRecord] = []
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}
        self._run_task_agents: dict[str, str] = {}
        self._lock = threading.RLock()
        self._acp_command = str(
            config.get("acp_command")
            or os.environ.get("ACP_COMMAND")
            or "python3 -m nocode_agent.acp_server"
        )
        self._acp_agent_name = str(
            config.get("acp_agent_name")
            or os.environ.get("ACP_AGENT_NAME")
            or "nocode"
        )
        self._acp_agents_cache: list[dict[str, Any]] = []
        self._last_target_ids: list[str] = []
        self._registry_path = _resolve_registry_dir(config) / f"{self._summary.id}.json"
        self._restore_snapshot(snapshot or {})

    @property
    def cwd(self) -> str:
        return self._summary.cwd

    def summary(self) -> SessionSummary:
        return SessionSummary(**asdict(self._summary))

    def _touch(self) -> None:
        self._summary.updated_at = time.time()

    def _write_registry(self) -> None:
        payload = {
            "session_id": self._summary.id,
            "title": self._summary.title,
            "workspace_id": self._summary.workspace_id,
            "cwd": self._summary.cwd,
            "agents": [asdict(agent.summary) for agent in self._agents.values()],
        }
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._registry_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _persist(self) -> None:
        self._touch()
        self._write_registry()
        if self._persist_hook is not None:
            self._persist_hook()

    def _build_mcp_servers(self, summary: AgentSummary) -> list[McpServerStdio]:
        return [
            McpServerStdio(
                name="session_registry",
                command=sys.executable,
                args=["-m", "multiagent_system.session_mcp_server"],
                env=[
                    EnvVariable(name="NOCODE_SESSION_REGISTRY_PATH", value=str(self._registry_path)),
                    EnvVariable(name="NOCODE_CURRENT_AGENT_ID", value=summary.id),
                ],
            )
        ]

    def _build_runtime_from_summary(self, summary: AgentSummary) -> AgentRuntime:
        if summary.transport == "stdio":
            return StdioRuntime(
                summary.stdio_command or "python3 -m nocode_agent.backend_stdio",
                thread_id=summary.thread_id,
                cwd=self._summary.cwd,
            )
        return ACPRemoteRuntime(
            command=summary.acp_command or self._acp_command,
            acp_agent_name=summary.acp_agent_name or self._acp_agent_name,
            session_id=summary.thread_id,
            cwd=self._summary.cwd,
            mcp_servers=self._build_mcp_servers(summary),
        )

    def _restore_snapshot(self, payload: dict[str, Any]) -> None:
        acp = payload.get("acp", {})
        if isinstance(acp, dict):
            self._acp_command = str(acp.get("command") or self._acp_command)
            self._acp_agent_name = str(acp.get("default_agent_name") or self._acp_agent_name)
            available_agents = acp.get("available_agents", [])
            if isinstance(available_agents, list):
                self._acp_agents_cache = [item for item in available_agents if isinstance(item, dict)]

        restored_agents = payload.get("agents", [])
        if isinstance(restored_agents, list):
            for item in restored_agents:
                if not isinstance(item, dict):
                    continue
                try:
                    summary = AgentSummary(**item)
                except TypeError:
                    continue
                summary.status = "idle"
                summary.updated_at = time.time()
                self._agents[summary.id] = ManagedAgent(summary=summary, runtime=self._build_runtime_from_summary(summary))

        restored_events = payload.get("events", [])
        if isinstance(restored_events, list):
            for item in restored_events:
                if not isinstance(item, dict):
                    continue
                try:
                    event = EventRecord(**item)
                except TypeError:
                    continue
                if event.status in {"queued", "dispatched", "running"}:
                    event.status = "stopped"
                    event.updated_at = time.time()
                self._events.append(event)

        self._write_registry()

    async def _fetch_acp_agents(self, command: str | None = None) -> list[dict[str, Any]]:
        runtime = ACPRemoteRuntime(command or self._acp_command, cwd=self._summary.cwd)
        try:
            await runtime._ensure_process()
            detected_name = runtime._acp_agent_name or "nocode"
            description = runtime._agent_title
            return [
                {
                    "name": detected_name,
                    "description": description,
                    "input_content_types": ["text/plain"],
                    "output_content_types": ["text/plain"],
                }
            ]
        finally:
            await runtime.close()

    async def _assert_acp_agent_available(self) -> None:
        manifests = await self._fetch_acp_agents(self._acp_command)
        self._acp_agents_cache = manifests
        if not any(item["name"] == self._acp_agent_name for item in manifests):
            raise ValueError(f"ACP agent {self._acp_agent_name} not found from command {self._acp_command}")

    def acp_state(self) -> dict[str, Any]:
        return {
            "command": self._acp_command,
            "default_agent_name": self._acp_agent_name,
            "available_agents": list(self._acp_agents_cache),
        }

    async def refresh_acp(self, command: str | None = None, default_agent_name: str | None = None) -> dict[str, Any]:
        resolved_command = (command or self._acp_command).strip()
        if not resolved_command:
            raise ValueError("acp command is required")
        manifests = await self._fetch_acp_agents(resolved_command)
        if not manifests:
            raise ValueError(f"no ACP agents found from command {resolved_command}")
        selected_name = (default_agent_name or self._acp_agent_name or manifests[0]["name"]).strip()
        if not any(item["name"] == selected_name for item in manifests):
            raise ValueError(f"ACP agent {selected_name} not found from command {resolved_command}")
        self._acp_command = resolved_command
        self._acp_agent_name = selected_name
        self._acp_agents_cache = manifests
        self._persist()
        return self.acp_state()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            agents = [asdict(agent.summary) for agent in self._agents.values()]
            events = [asdict(event) for event in self._events]
        agents.sort(key=lambda item: item["created_at"])
        events.sort(key=lambda item: item["created_at"])
        return {
            "session": asdict(self._summary),
            "agents": agents,
            "events": events,
            "acp": self.acp_state(),
        }

    def _find_agent_by_name(self, name: str) -> ManagedAgent | None:
        lowered = name.lower()
        for agent in self._agents.values():
            if agent.summary.name.lower() == lowered:
                return agent
        return None

    def _record_event(self, event: EventRecord) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > 400:
                self._events = self._events[-400:]
        self._persist()

    def _update_event(self, event_id: str, **changes: Any) -> None:
        with self._lock:
            for event in self._events:
                if event.id != event_id:
                    continue
                for key, value in changes.items():
                    setattr(event, key, value)
                event.updated_at = time.time()
                break
            else:
                return
        self._persist()

    def _register_task(self, task: asyncio.Task[Any], run_id: str, agent_id: str) -> None:
        with self._lock:
            self._run_tasks[run_id] = task
            self._run_task_agents[run_id] = agent_id

    def _unregister_task(self, run_id: str) -> None:
        with self._lock:
            self._run_tasks.pop(run_id, None)
            self._run_task_agents.pop(run_id, None)

    def _spawn_run(self, run_id: str, agent_id: str, coro) -> None:
        task = asyncio.create_task(coro)
        self._register_task(task, run_id, agent_id)
        task.add_done_callback(lambda _task, run_id=run_id: self._unregister_task(run_id))

    def _update_agent(self, agent_id: str, **changes: Any) -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return
            for key, value in changes.items():
                setattr(agent.summary, key, value)
            agent.summary.updated_at = time.time()
        self._persist()

    async def create_agent(
        self,
        name: str,
        system_prompt: str = "",
        acp_agent_name: str = "",
        acp_command: str = "",
        transport: str = "acp",
        stdio_command: str = "",
    ) -> AgentSummary:
        normalized = name.strip()
        if not normalized:
            raise ValueError("agent name is required")
        if self._find_agent_by_name(normalized):
            raise ValueError(f"agent {normalized} already exists")
        transport = (transport or "acp").strip().lower()
        runtime: AgentRuntime
        summary = AgentSummary(id=f"agent-{uuid4().hex[:10]}", name=normalized, system_prompt=system_prompt, transport=transport)

        if transport == "stdio":
            command = stdio_command.strip() or "python3 -m nocode_agent.backend_stdio"
            summary.stdio_command = command
            runtime = StdioRuntime(command, thread_id=summary.thread_id, cwd=self._summary.cwd)
            await runtime._ensure_process()
        else:
            selected_acp_command = (acp_command or self._acp_command).strip()
            selected_acp_agent_name = (acp_agent_name or self._acp_agent_name).strip()
            if not selected_acp_command:
                raise ValueError("acp command is required")
            if not selected_acp_agent_name:
                raise ValueError("acp agent name is required")
            previous_command = self._acp_command
            previous_agent_name = self._acp_agent_name
            self._acp_command = selected_acp_command
            self._acp_agent_name = selected_acp_agent_name
            try:
                await self._assert_acp_agent_available()
            finally:
                self._acp_command = previous_command
                self._acp_agent_name = previous_agent_name
            summary.acp_command = selected_acp_command
            summary.acp_agent_name = selected_acp_agent_name
            runtime = ACPRemoteRuntime(
                command=selected_acp_command,
                acp_agent_name=selected_acp_agent_name,
                cwd=self._summary.cwd,
                mcp_servers=self._build_mcp_servers(summary),
            )
            await runtime._ensure_process()

        summary.thread_id = runtime.thread_id()
        managed = ManagedAgent(summary=summary, runtime=runtime)
        with self._lock:
            self._agents[summary.id] = managed
        self._persist()
        return summary

    async def clear_agent(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if not agent:
            raise KeyError("agent not found")
        await agent.runtime.clear()
        self._update_agent(agent_id, thread_id=agent.runtime.thread_id())

    async def close(self) -> None:
        await self.stop_discussions()
        agents = list(self._agents.values())
        for agent in agents:
            close = getattr(agent.runtime, "close", None)
            if close is None:
                continue
            result = close()
            if asyncio.iscoroutine(result):
                await result

    async def submit_user_message(self, text: str) -> dict[str, Any]:
        prompt = text.strip()
        if not prompt:
            raise ValueError("message is empty")

        mentions = _extract_mentions(prompt)
        targets = self._resolve_targets(mentions)
        if not targets:
            raise ValueError("no target agent matched; create agents first or use @agentName")

        event_id = f"event-{uuid4().hex[:10]}"
        sender = "user"
        self._record_event(
            EventRecord(
                id=event_id,
                kind="user_message",
                agent_id="user",
                agent_name="User",
                sender=sender,
                text=prompt,
                status="queued",
                target_agent_ids=[agent.summary.id for agent in targets],
                mentions=mentions,
            )
        )

        for target in targets:
            delivery = Delivery(
                text=prompt,
                sender=sender,
                origin_event_id=event_id,
                depth=0,
                trail=[sender, target.summary.name],
            )
            run_id = f"run-{uuid4().hex[:10]}"
            self._spawn_run(run_id, target.summary.id, self._deliver(target.summary.id, delivery, run_id))

        self._update_event(event_id, status="dispatched")
        return {"event_id": event_id, "target_agent_ids": [agent.summary.id for agent in targets]}

    def _resolve_targets(self, mentions: list[str]) -> list[ManagedAgent]:
        if not mentions:
            with self._lock:
                if self._last_target_ids:
                    return [
                        self._agents[aid]
                        for aid in self._last_target_ids
                        if aid in self._agents
                    ]
                agents = list(self._agents.values())
                if agents:
                    return [agents[0]]
                return []

        resolved: list[ManagedAgent] = []
        seen: set[str] = set()
        for mention in mentions:
            agent = self._find_agent_by_name(mention)
            if not agent or agent.summary.id in seen:
                continue
            seen.add(agent.summary.id)
            resolved.append(agent)
        if resolved:
            self._last_target_ids = [a.summary.id for a in resolved]
        return resolved

    async def _deliver(self, agent_id: str, delivery: Delivery, run_id: str) -> None:
        agent = self._agents.get(agent_id)
        if not agent:
            return

        self._record_event(
            EventRecord(
                id=run_id,
                kind="agent_run",
                agent_id=agent.summary.id,
                agent_name=agent.summary.name,
                sender=delivery.sender,
                text="",
                run_id=run_id,
                status="running",
                metadata={
                    "source_event_id": delivery.origin_event_id,
                    "depth": delivery.depth,
                    "trail": delivery.trail,
                },
            )
        )
        self._update_agent(agent.summary.id, status="running")

        async with agent.lock:
            chunks: list[str] = []
            tool_events: list[dict[str, Any]] = []
            try:
                async for event_type, *data in agent.runtime.run(
                    agent_name=agent.summary.name,
                    system_prompt=agent.summary.system_prompt,
                    message=self._compose_delivery_message(agent.summary.name, delivery),
                ):
                    if event_type == "text":
                        chunks.append(data[0])
                        self._update_event(run_id, text="".join(chunks))
                    elif event_type == "tool_start":
                        tool_events.append(
                            {
                                "type": "tool_start",
                                "name": data[0],
                                "args": data[1] if len(data) > 1 else {},
                                "tool_call_id": data[2] if len(data) > 2 else "",
                            }
                        )
                        self._update_event(run_id, metadata={"source_event_id": delivery.origin_event_id, "depth": delivery.depth, "trail": delivery.trail, "tools": tool_events})
                    elif event_type == "tool_end":
                        tool_events.append(
                            {
                                "type": "tool_end",
                                "name": data[0],
                                "output": data[1] if len(data) > 1 else "",
                                "tool_call_id": data[2] if len(data) > 2 else "",
                            }
                        )
                        self._update_event(run_id, metadata={"source_event_id": delivery.origin_event_id, "depth": delivery.depth, "trail": delivery.trail, "tools": tool_events})
            except asyncio.CancelledError:
                self._update_event(run_id, status="stopped", text="已停止")
                self._update_agent(agent.summary.id, status="idle")
                raise
            except Exception as error:
                self._update_event(run_id, status="failed", text=str(error))
                self._update_agent(agent.summary.id, status="idle")
                return

        output = "".join(chunks).strip()
        mentions = _extract_mentions(output)
        self._update_event(run_id, status="done", text=output, mentions=mentions)
        self._update_agent(agent.summary.id, status="idle", thread_id=agent.runtime.thread_id())

        if delivery.depth >= self._max_hops:
            return

        for mention in mentions:
            target = self._find_agent_by_name(mention)
            if not target or target.summary.id == agent.summary.id:
                continue
            await self._relay_between_agents(source=agent.summary.name, target=target.summary.id, output=output, parent_run_id=run_id, depth=delivery.depth + 1, trail=[*delivery.trail, target.summary.name])

    def _compose_delivery_message(self, recipient_name: str, delivery: Delivery) -> str:
        return (
            f"发送者: {delivery.sender}\n"
            f"接收者: {recipient_name}\n"
            f"协作深度: {delivery.depth}\n"
            f"路由链路: {' -> '.join(delivery.trail)}\n\n"
            f"{delivery.text}"
        )

    async def _relay_between_agents(
        self,
        source: str,
        target: str,
        output: str,
        parent_run_id: str,
        depth: int,
        trail: list[str],
    ) -> None:
        agent = self._agents.get(target)
        if not agent:
            return

        event_id = f"event-{uuid4().hex[:10]}"
        message = (
            f"@{agent.summary.name}，{source} 请求你继续协作。\n\n"
            f"上一个 agent 的输出如下：\n{output}"
        )
        self._record_event(
            EventRecord(
                id=event_id,
                kind="relay",
                agent_id=agent.summary.id,
                agent_name=agent.summary.name,
                sender=source,
                text=message,
                status="queued",
                metadata={"parent_run_id": parent_run_id, "depth": depth},
            )
        )
        self._update_event(event_id, status="dispatched")
        run_id = f"run-{uuid4().hex[:10]}"
        self._spawn_run(
            run_id,
            agent.summary.id,
            self._deliver(
                agent.summary.id,
                Delivery(
                    text=message,
                    sender=source,
                    origin_event_id=event_id,
                    depth=depth,
                    trail=trail,
                ),
                run_id,
            ),
        )

    async def stop_agent(self, agent_id: str) -> dict[str, Any]:
        managed = self._agents.get(agent_id)
        if not managed:
            raise KeyError("agent not found")

        with self._lock:
            tasks = [
                (run_id, task)
                for run_id, task in self._run_tasks.items()
                if self._run_task_agents.get(run_id) == agent_id
            ]
            event_ids = [
                event.id
                for event in self._events
                if event.agent_id == agent_id and event.status in {"queued", "dispatched", "running"}
            ]

        for _, task in tasks:
            if not task.done():
                task.cancel()

        try:
            await managed.runtime.stop()
        except Exception:
            pass

        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)

        for event_id in event_ids:
            self._update_event(event_id, status="stopped")
        self._update_agent(agent_id, status="idle", thread_id=managed.runtime.thread_id())
        return {"stopped_runs": len(tasks), "agent_id": agent_id}

    async def stop_discussions(self) -> dict[str, Any]:
        with self._lock:
            tasks = list(self._run_tasks.items())
            agents = list(self._agents.values())
            event_ids = [
                event.id
                for event in self._events
                if event.kind in {"agent_run", "relay"} and event.status in {"queued", "dispatched", "running"}
            ]

        for _, task in tasks:
            if not task.done():
                task.cancel()

        for managed in agents:
            try:
                await managed.runtime.stop()
            except Exception:
                continue
            self._update_agent(managed.summary.id, status="idle", thread_id=managed.runtime.thread_id())

        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)

        for event_id in event_ids:
            self._update_event(event_id, status="stopped")

        return {"stopped_runs": len(tasks)}


def _extract_mentions(text: str) -> list[str]:
    seen: set[str] = set()
    mentions: list[str] = []
    for match in MENTION_PATTERN.findall(text):
        normalized = match.strip()
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        mentions.append(normalized)
    return mentions


class SessionManager:
    def __init__(self, config: dict[str, Any], max_hops: int = DEFAULT_MAX_HOPS):
        self._config = config
        self._max_hops = max_hops
        self._state = PersistentJsonState(_resolve_multiagent_state_path(config))
        self._lock = threading.RLock()
        self._workspaces = [WorkspaceSummary(**item) for item in _resolve_workspace_payloads(config)]
        self._sessions: dict[str, MultiAgentStore] = {}
        self._current_session_id = ""
        self._restore()

    def _workspace_map(self) -> dict[str, WorkspaceSummary]:
        return {item.id: item for item in self._workspaces}

    def _default_workspace(self) -> WorkspaceSummary:
        return self._workspaces[0]

    def _persist(self) -> None:
        payload = {
            "current_session_id": self._current_session_id,
            "sessions": [store.snapshot() for store in self._sessions.values()],
        }
        self._state.save(payload)

    def _restore(self) -> None:
        payload = self._state.load()
        workspace_map = self._workspace_map()
        restored_sessions = payload.get("sessions", [])
        if isinstance(restored_sessions, list):
            for item in restored_sessions:
                if not isinstance(item, dict):
                    continue
                session_payload = item.get("session", {})
                if not isinstance(session_payload, dict):
                    continue
                try:
                    summary = SessionSummary(**session_payload)
                except TypeError:
                    continue
                if summary.workspace_id not in workspace_map:
                    default = self._default_workspace()
                    summary.workspace_id = default.id
                    summary.cwd = default.cwd
                store = MultiAgentStore(
                    self._config,
                    summary,
                    max_hops=self._max_hops,
                    persist_hook=self._persist,
                    snapshot=item,
                )
                self._sessions[summary.id] = store

        requested_current = str(payload.get("current_session_id") or "")
        if requested_current in self._sessions:
            self._current_session_id = requested_current

        if not self._sessions:
            self.create_session()
        elif not self._current_session_id:
            self._current_session_id = next(iter(self._sessions.keys()))
            self._persist()

    def _current(self) -> MultiAgentStore:
        store = self._sessions.get(self._current_session_id)
        if store is None:
            raise RuntimeError("current session not found")
        return store

    def snapshot(self) -> dict[str, Any]:
        current = self._current().snapshot()
        current["sessions"] = [asdict(store.summary()) for store in self._sessions.values()]
        current["workspaces"] = [asdict(item) for item in self._workspaces]
        current["current_session_id"] = self._current_session_id
        return current

    def acp_state(self) -> dict[str, Any]:
        return self._current().acp_state()

    async def refresh_acp(self, command: str | None = None, default_agent_name: str | None = None) -> dict[str, Any]:
        return await self._current().refresh_acp(command, default_agent_name)

    def create_session(self, title: str = "", workspace_id: str = "") -> SessionSummary:
        workspace = self._workspace_map().get(workspace_id or "")
        if workspace is None:
            workspace = self._default_workspace()
        session_title = title.strip() or f"{workspace.name} 会话"
        summary = SessionSummary(
            id=f"session-{uuid4().hex[:10]}",
            title=session_title,
            workspace_id=workspace.id,
            cwd=workspace.cwd,
        )
        store = MultiAgentStore(
            self._config,
            summary,
            max_hops=self._max_hops,
            persist_hook=self._persist,
        )
        self._sessions[summary.id] = store
        self._current_session_id = summary.id
        self._persist()
        return summary

    def select_session(self, session_id: str) -> SessionSummary:
        store = self._sessions.get(session_id)
        if store is None:
            raise KeyError("session not found")
        self._current_session_id = session_id
        self._persist()
        return store.summary()

    async def create_agent(self, *args, **kwargs) -> AgentSummary:
        return await self._current().create_agent(*args, **kwargs)

    async def clear_agent(self, agent_id: str) -> None:
        await self._current().clear_agent(agent_id)

    async def submit_user_message(self, text: str) -> dict[str, Any]:
        return await self._current().submit_user_message(text)

    async def stop_agent(self, agent_id: str) -> dict[str, Any]:
        return await self._current().stop_agent(agent_id)

    async def stop_discussions(self) -> dict[str, Any]:
        return await self._current().stop_discussions()

    async def close(self) -> None:
        for store in self._sessions.values():
            await store.close()


class WebApplication:
    def __init__(self, config_path: str | None = None, config_overrides: dict[str, Any] | None = None):
        self.config = load_config(config_path)
        if config_overrides:
            self.config.update({key: value for key, value in config_overrides.items() if value})
        self.store = SessionManager(self.config)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def shutdown(self) -> None:
        asyncio.run_coroutine_threadsafe(self.store.close(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=1)


APP: WebApplication | None = None


class RequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._send_json(HTTPStatus.OK, APP.store.snapshot())
            return
        if parsed.path == "/api/acp":
            self._send_json(HTTPStatus.OK, APP.store.acp_state())
            return
        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path in {"/", "/index.html"}:
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json_body()
        if payload is None:
            return

        try:
            if parsed.path == "/api/acp":
                result = APP.call(
                    APP.store.refresh_acp(
                        payload.get("command"),
                        payload.get("default_agent_name"),
                    )
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if parsed.path == "/api/sessions":
                summary = APP.store.create_session(
                    str(payload.get("title", "")),
                    str(payload.get("workspace_id", "")),
                )
                self._send_json(HTTPStatus.CREATED, {"session": asdict(summary)})
                return
            if parsed.path == "/api/sessions/select":
                summary = APP.store.select_session(str(payload.get("session_id", "")))
                self._send_json(HTTPStatus.OK, {"session": asdict(summary)})
                return
            if parsed.path == "/api/stop":
                result = APP.call(APP.store.stop_discussions())
                self._send_json(HTTPStatus.OK, result)
                return
            if parsed.path == "/api/agents":
                summary = APP.call(
                    APP.store.create_agent(
                        payload.get("name", ""),
                        payload.get("system_prompt", ""),
                        payload.get("acp_agent_name", ""),
                        payload.get("acp_command", ""),
                        payload.get("transport", "acp"),
                        payload.get("stdio_command", ""),
                    )
                )
                self._send_json(HTTPStatus.CREATED, {"agent": asdict(summary)})
                return
            if parsed.path == "/api/messages":
                result = APP.call(APP.store.submit_user_message(payload.get("text", "")))
                self._send_json(HTTPStatus.ACCEPTED, result)
                return
            if parsed.path.endswith("/stop") and parsed.path.startswith("/api/agents/"):
                agent_id = parsed.path.split("/")[3]
                result = APP.call(APP.store.stop_agent(agent_id))
                self._send_json(HTTPStatus.OK, result)
                return
            if parsed.path.endswith("/clear") and parsed.path.startswith("/api/agents/"):
                agent_id = parsed.path.split("/")[3]
                APP.call(APP.store.clear_agent(agent_id))
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except KeyError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
            return
        except RuntimeError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return None

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-agent ACP web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", help="Path to YAML config file.")
    parser.add_argument("--acp-command", dest="acp_command", help="ACP agent command, e.g. python3 -m nocode_agent.acp_server")
    parser.add_argument("--acp-agent-name", dest="acp_agent_name", help="ACP agent manifest name to call.")
    return parser.parse_args()


def main() -> None:
    global APP
    args = parse_args()
    APP = WebApplication(
        config_path=args.config,
        config_overrides={
            "acp_command": args.acp_command,
            "acp_agent_name": args.acp_agent_name,
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    try:
        print(f"web ui listening on http://{args.host}:{args.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        APP.shutdown()


if __name__ == "__main__":
    main()
