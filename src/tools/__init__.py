"""内置工具模块。

Tool（内置工具）是 Agent 核心基础设施的一部分，始终可用，与 Skill（技能插件）是两个独立的系统。
"""

from src.tools.base import BaseTool, ToolResult
from src.tools.registry import ToolRegistry

__all__ = ["BaseTool", "ToolResult", "ToolRegistry"]
