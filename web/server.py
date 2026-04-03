from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import threading
import time
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import uvicorn.config

if not hasattr(uvicorn.config, "LoopSetupType") and hasattr(uvicorn.config, "LoopFactoryType"):
    uvicorn.config.LoopSetupType = uvicorn.config.LoopFactoryType

import httpx
from acp_sdk.client import Client
from acp_sdk.models import Session

from src.main import load_config


MENTION_PATTERN = re.compile(r"(?<![\w-])@([A-Za-z0-9_\-\u4e00-\u9fff]+)")
STATIC_DIR = Path(__file__).with_name("static")
DEFAULT_MAX_HOPS = 4


@dataclass(slots=True)
class AgentSummary:
    id: str
    name: str
    system_prompt: str = ""
    transport: str = "http"
    acp_agent_name: str = ""
    acp_base_url: str = ""
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


class ACPRemoteRuntime(AgentRuntime):
    """Runtime backed by the local ACP server."""

    def __init__(self, base_url: str, acp_agent_name: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._acp_agent_name = acp_agent_name
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        self._client = Client(client=self._http_client, manage_client=False)
        self._session = Session()

    async def run(self, agent_name: str, system_prompt: str, message: str):
        prompt = _build_runtime_prompt(agent_name=agent_name, system_prompt=system_prompt, message=message)
        session_client = self._client.session(self._session)
        async with session_client as bound_client:
            async for event in bound_client.run_stream(
                prompt,
                agent=self._acp_agent_name,
                base_url=self._base_url,
            ):
                event_type = getattr(event, "type", "")
                if event_type == "message.part":
                    part = getattr(event, "part", None)
                    if part is None:
                        continue
                    content_type = getattr(part, "content_type", "text/plain") or "text/plain"
                    if not content_type.startswith("text/"):
                        continue
                    text = getattr(part, "content", None)
                    if text:
                        yield ("text", text)
                    continue
                if event_type == "run.failed":
                    run = getattr(event, "run", None)
                    error = getattr(run, "error", None) if run is not None else None
                    message = getattr(error, "message", None) or "ACP run failed"
                    raise RuntimeError(message)
                if event_type == "error":
                    error = getattr(event, "error", None)
                    message = getattr(error, "message", None) or "ACP error"
                    raise RuntimeError(message)

    async def clear(self) -> None:
        self._session = Session()

    def thread_id(self) -> str:
        return str(self._session.id)

    async def close(self) -> None:
        await self._http_client.aclose()


class StdioRuntime(AgentRuntime):
    def __init__(self, command: str):
        self._command = command.strip()
        self._process: asyncio.subprocess.Process | None = None
        self._thread_id = ""

    async def _ensure_process(self) -> None:
        if self._process and self._process.returncode is None:
            return
        if not self._command:
            raise ValueError("stdio command is required")

        self._process = await asyncio.create_subprocess_exec(
            *shlex.split(self._command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
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
                yield ("tool_start", str(event.get("name", "tool")), event.get("args", {}))
                continue
            if event_type == "tool_end":
                yield ("tool_end", str(event.get("name", "tool")), str(event.get("output", "")))
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
        self._thread_id = ""

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
        "如果你需要其他 agent 协作，请在正文里显式使用 @agent名。",
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
    def __init__(self, config: dict[str, Any], max_hops: int = DEFAULT_MAX_HOPS):
        self._config = config
        self._max_hops = max_hops
        self._agents: dict[str, ManagedAgent] = {}
        self._events: list[EventRecord] = []
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}
        self._run_task_agents: dict[str, str] = {}
        self._lock = threading.RLock()
        self._acp_base_url = str(
            config.get("acp_base_url")
            or os.environ.get("ACP_BASE_URL")
            or "http://127.0.0.1:8000"
        )
        self._acp_agent_name = str(
            config.get("acp_agent_name")
            or os.environ.get("ACP_AGENT_NAME")
            or "nocode"
        )
        self._acp_agents_cache: list[dict[str, Any]] = []

    async def _fetch_acp_agents(self, base_url: str | None = None) -> list[dict[str, Any]]:
        resolved_base_url = (base_url or self._acp_base_url).rstrip("/")
        async with httpx.AsyncClient(
            base_url=resolved_base_url,
            timeout=10.0,
            headers={"Content-Type": "application/json"},
        ) as http_client:
            client = Client(client=http_client, manage_client=False)
            async with client:
                manifests: list[dict[str, Any]] = []
                async for manifest in client.agents(base_url=resolved_base_url):
                    manifests.append(
                        {
                            "name": manifest.name,
                            "description": manifest.description or "",
                            "input_content_types": list(manifest.input_content_types or []),
                            "output_content_types": list(manifest.output_content_types or []),
                        }
                    )
                return manifests

    async def _assert_acp_agent_available(self) -> None:
        manifests = await self._fetch_acp_agents(self._acp_base_url)
        self._acp_agents_cache = manifests
        if not any(item["name"] == self._acp_agent_name for item in manifests):
            raise ValueError(f"ACP agent {self._acp_agent_name} not found at {self._acp_base_url}")

    def acp_state(self) -> dict[str, Any]:
        return {
            "base_url": self._acp_base_url,
            "default_agent_name": self._acp_agent_name,
            "available_agents": list(self._acp_agents_cache),
        }

    async def refresh_acp(self, base_url: str | None = None, default_agent_name: str | None = None) -> dict[str, Any]:
        resolved_base_url = (base_url or self._acp_base_url).strip().rstrip("/")
        if not resolved_base_url:
            raise ValueError("acp base url is required")
        manifests = await self._fetch_acp_agents(resolved_base_url)
        if not manifests:
            raise ValueError(f"no ACP agents found at {resolved_base_url}")
        selected_name = (default_agent_name or self._acp_agent_name or manifests[0]["name"]).strip()
        if not any(item["name"] == selected_name for item in manifests):
            raise ValueError(f"ACP agent {selected_name} not found at {resolved_base_url}")
        self._acp_base_url = resolved_base_url
        self._acp_agent_name = selected_name
        self._acp_agents_cache = manifests
        return self.acp_state()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            agents = [asdict(agent.summary) for agent in self._agents.values()]
            events = [asdict(event) for event in self._events]
        agents.sort(key=lambda item: item["created_at"])
        events.sort(key=lambda item: item["created_at"])
        return {"agents": agents, "events": events, "acp": self.acp_state()}

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

    def _update_event(self, event_id: str, **changes: Any) -> None:
        with self._lock:
            for event in self._events:
                if event.id != event_id:
                    continue
                for key, value in changes.items():
                    setattr(event, key, value)
                event.updated_at = time.time()
                return

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

    async def create_agent(
        self,
        name: str,
        system_prompt: str = "",
        acp_agent_name: str = "",
        acp_base_url: str = "",
        transport: str = "http",
        stdio_command: str = "",
    ) -> AgentSummary:
        normalized = name.strip()
        if not normalized:
            raise ValueError("agent name is required")
        if self._find_agent_by_name(normalized):
            raise ValueError(f"agent {normalized} already exists")
        transport = (transport or "http").strip().lower()
        runtime: AgentRuntime
        summary = AgentSummary(id=f"agent-{uuid4().hex[:10]}", name=normalized, system_prompt=system_prompt, transport=transport)

        if transport == "stdio":
            command = stdio_command.strip() or "python3 -m src.backend_stdio"
            summary.stdio_command = command
            runtime = StdioRuntime(command)
            await runtime._ensure_process()
        else:
            selected_acp_base_url = (acp_base_url or self._acp_base_url).strip().rstrip("/")
            selected_acp_agent_name = (acp_agent_name or self._acp_agent_name).strip()
            if not selected_acp_base_url:
                raise ValueError("acp base url is required")
            if not selected_acp_agent_name:
                raise ValueError("acp agent name is required")
            previous_base_url = self._acp_base_url
            previous_agent_name = self._acp_agent_name
            self._acp_base_url = selected_acp_base_url
            self._acp_agent_name = selected_acp_agent_name
            try:
                await self._assert_acp_agent_available()
            finally:
                self._acp_base_url = previous_base_url
                self._acp_agent_name = previous_agent_name
            summary.acp_base_url = selected_acp_base_url
            summary.acp_agent_name = selected_acp_agent_name
            runtime = ACPRemoteRuntime(base_url=selected_acp_base_url, acp_agent_name=selected_acp_agent_name)

        summary.thread_id = runtime.thread_id()
        managed = ManagedAgent(summary=summary, runtime=runtime)
        with self._lock:
            self._agents[summary.id] = managed
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
                return list(self._agents.values())

        resolved: list[ManagedAgent] = []
        seen: set[str] = set()
        for mention in mentions:
            agent = self._find_agent_by_name(mention)
            if not agent or agent.summary.id in seen:
                continue
            seen.add(agent.summary.id)
            resolved.append(agent)
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
                        tool_events.append({"type": "tool_start", "name": data[0], "args": data[1] if len(data) > 1 else {}})
                        self._update_event(run_id, metadata={"source_event_id": delivery.origin_event_id, "depth": delivery.depth, "trail": delivery.trail, "tools": tool_events})
                    elif event_type == "tool_end":
                        tool_events.append({"type": "tool_end", "name": data[0], "output": data[1] if len(data) > 1 else ""})
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


class WebApplication:
    def __init__(self, config_path: str | None = None, config_overrides: dict[str, Any] | None = None):
        self.config = load_config(config_path)
        if config_overrides:
            self.config.update({key: value for key, value in config_overrides.items() if value})
        self.store = MultiAgentStore(self.config)
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
                        payload.get("base_url"),
                        payload.get("default_agent_name"),
                    )
                )
                self._send_json(HTTPStatus.OK, result)
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
                        payload.get("acp_base_url", ""),
                        payload.get("transport", "http"),
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
    parser.add_argument("--acp-base-url", dest="acp_base_url", help="ACP server base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--acp-agent-name", dest="acp_agent_name", help="ACP agent manifest name to call.")
    return parser.parse_args()


def main() -> None:
    global APP
    args = parse_args()
    APP = WebApplication(
        config_path=args.config,
        config_overrides={
            "acp_base_url": args.acp_base_url.rstrip("/") if args.acp_base_url else None,
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
