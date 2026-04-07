"""invoke_skill: LangChain @tool wrapper for the skill system."""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from .registry import get_skill_registry
from .expander import SkillExpander
from .invoked_store import get_invoked_skill_store

logger = logging.getLogger(__name__)


@tool
async def invoke_skill(
    skill_name: str,
    skill_args: str | None = None,
) -> str:
    """Invoke a skill by name.

    Use this when the user references a /<skill-name> or when you judge a
    skill is relevant to the current task.

    Args:
        skill_name: The skill to invoke.
        skill_args: Optional arguments to pass to the skill.
    """
    registry = get_skill_registry()
    entry = registry.get(skill_name)

    if not entry:
        available = [s.name for s in registry.get_tool_skills()]
        logger.warning("Skill not found: %s", skill_name)
        return (
            f"Error: Skill '{skill_name}' not found.\n"
            f"Available skills: {', '.join(available) or '(none)'}"
        )

    # Expand skill content
    expander = SkillExpander()
    # LangChain + Pydantic 对参数名 `args` 有特殊处理，这里显式使用 `skill_args` 避免映射为 `v__args`。
    expanded = await expander.expand(entry, skill_args)
    logger.info("Skill invoked: %s", skill_name)

    # Record for compression recovery
    get_invoked_skill_store().record(skill_name, expanded)

    # Add permission hint if allowed-tools is set
    header = f"[skill: {skill_name}]"
    if entry.allowed_tools:
        tools_str = ", ".join(entry.allowed_tools)
        header += f"\n[Allowed tools: {tools_str}]"

    return f"{header}\n\n{expanded}"
