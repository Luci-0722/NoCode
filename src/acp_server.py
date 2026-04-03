from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncGenerator
from threading import Lock
from typing import Any

import uvicorn
import uvicorn.config

# acp-sdk 1.0.3 still references LoopSetupType, but uvicorn 0.42 renamed it.
if not hasattr(uvicorn.config, "LoopSetupType") and hasattr(uvicorn.config, "LoopFactoryType"):
    uvicorn.config.LoopSetupType = uvicorn.config.LoopFactoryType

from acp_sdk.server.app import create_app
from acp_sdk.server.logging import configure_logger as configure_logger_func
from acp_sdk.models import Capability, Dependency, DependencyType, Message, Metadata
from acp_sdk.server import Context, RunYield, RunYieldResume, Server

from src.agent import MainAgent, create_mainagent
from src.main import load_config


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nocode-acp", description="Run NoCode as an ACP server.")
    parser.add_argument("--config", help="Path to YAML config file.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address.")
    parser.add_argument("--port", type=int, default=8000, help="Bind port.")
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


def _build_metadata(config: dict[str, Any]) -> Metadata:
    model_name = str(config.get("model", "glm-4-flash"))
    return Metadata(
        framework="langchain",
        programming_language="python",
        natural_languages=["zh-CN", "en"],
        tags=["nocode", "langchain", "langgraph", "acp"],
        capabilities=[
            Capability(name="chat", description="General conversational assistance."),
            Capability(name="tool-use", description="Can read files, search the workspace, and execute shell commands."),
            Capability(name="subagent", description="Can delegate work to a dedicated coding subagent."),
        ],
        dependencies=[
            Dependency(type=DependencyType.TOOL, name="langchain"),
            Dependency(type=DependencyType.TOOL, name="langgraph"),
            Dependency(type=DependencyType.MODEL, name=model_name),
        ],
        recommended_models=[model_name],
    )


def _extract_text(message: Message) -> str:
    parts: list[str] = []
    for part in message.parts:
        if part.content is None:
            continue
        content_type = part.content_type or "text/plain"
        if not content_type.startswith("text/"):
            continue
        text = part.content.strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _render_input(messages: list[Message]) -> str:
    rendered: list[str] = []
    multiple = len(messages) > 1
    for message in messages:
        text = _extract_text(message)
        if not text:
            continue
        if multiple:
            rendered.append(f"{message.role}: {text}")
        else:
            rendered.append(text)
    return "\n\n".join(rendered).strip()


class ACPAgentPool:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._agents: dict[str, MainAgent] = {}
        self._lock = Lock()

        api_key = os.environ.get("ZHIPU_API_KEY", config.get("api_key", ""))
        if not api_key:
            raise RuntimeError("missing API key: set ZHIPU_API_KEY first")
        self._api_key = api_key

    def get(self, session_id: str) -> MainAgent:
        with self._lock:
            agent = self._agents.get(session_id)
            if agent is None:
                agent = create_mainagent(
                    api_key=self._api_key,
                    model=self._config.get("model", "glm-4-flash"),
                    base_url=self._config.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
                    max_tokens=self._config.get("max_tokens", 4096),
                    temperature=self._config.get("temperature", 0.7),
                    compression=self._config.get("compression"),
                    subagent_model=self._config.get("subagent_model"),
                    subagent_temperature=self._config.get("subagent_temperature", 0.1),
                )
                self._agents[session_id] = agent
            return agent


def create_server(config: dict[str, Any]) -> Server:
    pool = ACPAgentPool(config)
    server = Server()

    @server.agent(
        name="nocode",
        description="NoCode AI agent companion exposed through the official ACP protocol.",
        input_content_types=["text/plain"],
        output_content_types=["text/plain"],
        metadata=_build_metadata(config),
    )
    async def nocode_agent(
        input: list[Message], context: Context
    ) -> AsyncGenerator[RunYield, RunYieldResume]:
        prompt = _render_input(input)
        if not prompt:
            yield "No text input provided."
            return

        agent = pool.get(str(context.session.id))
        async for event_type, *data in agent.chat(prompt):
            if event_type == "text":
                chunk = data[0]
                if chunk:
                    yield chunk

    return server


async def _serve_with_uvicorn(server: Server, host: str, port: int) -> None:
    app = create_app(*server.agents, lifespan=server.lifespan)
    configure_logger_func()
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        loop="auto",
        http="auto",
        ws="auto",
        server_header=True,
        date_header=True,
        headers=[("server", "acp")],
    )
    server.server = uvicorn.Server(config)
    await server._serve(self_registration=False)


async def main_async() -> int:
    args = _parse_args()
    config = _build_runtime_config(args.config, args)
    server = create_server(config)
    await _serve_with_uvicorn(server, host=args.host, port=args.port)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
