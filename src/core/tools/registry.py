"""工具注册中心，管理所有内置工具。"""

from __future__ import annotations

import logging
from typing import Any

from src.core import ToolDefinition
from src.core.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """管理内置工具的注册、查找和执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个内置工具。"""
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def get(self, name: str) -> BaseTool | None:
        """按名称查找工具。"""
        return self._tools.get(name)

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """获取所有工具的 ToolDefinition 列表。"""
        return [t.definition for t in self._tools.values()]

    async def execute(self, name: str, args: dict[str, Any], config: Any) -> str:
        """执行指定工具，返回字符串结果。"""
        tool = self.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"
        try:
            result = await tool.execute(args, config)
            return result.content
        except Exception as e:
            logger.error("Tool %s execution failed: %s", name, e)
            return f"Error: {e}"

    def list_tools(self) -> list[str]:
        """列出所有已注册的工具名称。"""
        return list(self._tools.keys())
