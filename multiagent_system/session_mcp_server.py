from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _registry_path() -> Path:
    raw = os.environ.get("NOCODE_SESSION_REGISTRY_PATH", "").strip()
    if not raw:
        raise RuntimeError("NOCODE_SESSION_REGISTRY_PATH is required")
    return Path(raw).expanduser()


def _load_registry() -> dict[str, Any]:
    path = _registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _current_agent_id() -> str:
    return os.environ.get("NOCODE_CURRENT_AGENT_ID", "").strip()


server = FastMCP(
    name="nocode-session",
    instructions="Expose the current orchestrator session state to the agent.",
)


@server.tool(description="List other registered agents in the current orchestrator session.")
def list_registered_agents(include_self: bool = False) -> dict[str, Any]:
    registry = _load_registry()
    current_agent_id = _current_agent_id()
    agents = registry.get("agents", [])
    if not isinstance(agents, list):
        agents = []

    visible_agents: list[dict[str, Any]] = []
    for item in agents:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id", ""))
        if not include_self and current_agent_id and agent_id == current_agent_id:
            continue
        visible_agents.append(
            {
                "id": agent_id,
                "name": str(item.get("name", "")),
                "system_prompt": str(item.get("system_prompt", "")),
                "status": str(item.get("status", "")),
                "thread_id": str(item.get("thread_id", "")),
                "transport": str(item.get("transport", "")),
            }
        )

    return {
        "session_id": str(registry.get("session_id", "")),
        "session_title": str(registry.get("title", "")),
        "workspace_id": str(registry.get("workspace_id", "")),
        "cwd": str(registry.get("cwd", "")),
        "current_agent_id": current_agent_id,
        "agents": visible_agents,
    }


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
