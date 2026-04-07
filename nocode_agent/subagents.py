"""子代理类型定义 — 多类型 Agent 架构。

参考 Claude Code 的内置 agent 类型系统，定义 4 种专职子代理：
- general-purpose: 通用多步骤任务，可读写文件
- Explore: 只读代码搜索专家
- Plan: 软件架构规划，输出分步实施计划
- verification: 对抗性验证代理，尝试找出实现问题
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(slots=True)
class AgentDefinition:
    """内置子代理类型定义。"""

    agent_type: str
    when_to_use: str
    disallowed_tools: list[str] = field(default_factory=list)
    get_system_prompt: Callable[[], str] = field(default=lambda: "")

    @property
    def is_readonly(self) -> bool:
        """该代理是否为只读（禁止 write/edit）。"""
        return "write" in self.disallowed_tools or "edit" in self.disallowed_tools


# ── 内置定义将在 prompts.py 实现后填充 ──────────────────────────
# 延迟导入避免循环依赖，通过 get_builtin_agents() 获取

_BUILTIN_AGENTS: list[AgentDefinition] | None = None


def get_builtin_agents() -> list[AgentDefinition]:
    """获取内置子代理定义列表（延迟初始化）。"""
    global _BUILTIN_AGENTS
    if _BUILTIN_AGENTS is not None:
        return _BUILTIN_AGENTS

    # 延迟导入避免循环依赖
    from nocode_agent.prompts import (
        build_explore_subagent_prompt,
        build_plan_subagent_prompt,
        build_subagent_system_prompt,
        build_verification_subagent_prompt,
    )

    _BUILTIN_AGENTS = [
        AgentDefinition(
            agent_type="general-purpose",
            when_to_use=(
                "通用子代理，用于执行复杂的多步骤研究和编码任务。"
                "当你在搜索关键词或文件且不确定能否在最初几次尝试中找到正确匹配时使用此代理。"
                "拥有所有核心工具（含读写），适合需要修改文件的任务。"
            ),
            disallowed_tools=[],
            get_system_prompt=build_subagent_system_prompt,
        ),
        AgentDefinition(
            agent_type="Explore",
            when_to_use=(
                "快速搜索子代理，专门用于探索代码库。"
                "当需要快速通过模式查找文件（如 \"src/components/**/*.tsx\"）、"
                "搜索代码关键词（如 \"API endpoints\"）、"
                "或回答关于代码库的问题（如 \"API 端点如何工作？\"）时使用。"
                "调用时指定彻底程度：\"quick\"（基本搜索）、\"medium\"（中等探索）、"
                "\"very thorough\"（全面分析）。"
            ),
            disallowed_tools=["write", "edit", "delegate_code"],
            get_system_prompt=build_explore_subagent_prompt,
        ),
        AgentDefinition(
            agent_type="Plan",
            when_to_use=(
                "软件架构师子代理，用于设计实施方案。"
                "当需要规划任务的实施策略时使用此代理。"
                "返回分步计划，识别关键文件，考虑架构权衡。"
            ),
            disallowed_tools=["write", "edit", "delegate_code"],
            get_system_prompt=build_plan_subagent_prompt,
        ),
        AgentDefinition(
            agent_type="verification",
            when_to_use=(
                "对抗性验证子代理，用于检查实现是否正确。"
                "该代理会尝试找出 bug、遗漏的边界情况和与需求不一致的地方。"
                "不要用于简单的确认 — 它的职责是尝试打破你的实现。"
            ),
            disallowed_tools=["write", "edit", "delegate_code"],
            get_system_prompt=build_verification_subagent_prompt,
        ),
    ]
    return _BUILTIN_AGENTS


def get_agent_definition(agent_type: str) -> AgentDefinition | None:
    """按类型名查找内置子代理定义。"""
    for agent_def in get_builtin_agents():
        if agent_def.agent_type == agent_type:
            return agent_def
    return None


def build_readonly_tool_names() -> list[str]:
    """返回只读子代理可用的工具名列表（排除 write/edit/delegate_code）。"""
    return [
        "read", "glob", "list_dir", "grep", "bash",
        "web_search", "web_fetch", "todo_write", "todo_read",
    ]
