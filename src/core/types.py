"""Core type definitions for the agent system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: Role | str
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def __post_init__(self):
        if isinstance(self.role, str):
            self.role = Role(self.role)

    def to_openai(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }

    def parse_arguments(self) -> dict[str, Any]:
        import json
        return json.loads(self.arguments)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": [],
    })

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentConfig:
    model: str = "glm-4-flash"
    api_key: str = ""
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str = ""
    prompts_dir: str = "prompts"
    max_short_term_messages: int = 50
    max_tool_rounds: int = 10
    max_context_tokens: int = 8000
    bash_timeout: int = 30
    bash_workdir: str | None = None
    skills_dir: str = "skills"
    data_dir: str = "data"
