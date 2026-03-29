"""内置工具基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.types import ToolDefinition


@dataclass
class ToolResult:
    """工具执行结果。"""

    content: str
    is_error: bool = False


class BaseTool(ABC):
    """所有内置工具的基类。

    子类需要实现:
    - name: 工具名称
    - description: 工具描述（LLM 可见）
    - parameters: JSON Schema 格式的参数定义
    - execute(): 异步执行方法
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称，用于 LLM function calling。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，发送给 LLM。"""

    @property
    def parameters(self) -> dict[str, Any]:
        """工具参数的 JSON Schema。子类可覆盖。"""
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    @property
    def definition(self) -> ToolDefinition:
        """生成 ToolDefinition，用于发送给 LLM。"""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    @abstractmethod
    async def execute(self, args: dict[str, Any], config: Any) -> ToolResult:
        """执行工具。

        Args:
            args: LLM 传来的参数字典。
            config: AgentConfig 实例，提供配置信息。
        """
