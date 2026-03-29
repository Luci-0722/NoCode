"""System skill: get system info, list available skills."""

from __future__ import annotations

import platform
import os
from typing import Any

from src.core.skills import Skill, SkillRegistry


async def get_system_info(**kwargs: Any) -> str:
    """Get system information."""
    info = {
        "OS": f"{platform.system()} {platform.release()}",
        "Architecture": platform.machine(),
        "Python": platform.python_version(),
        "Hostname": platform.node(),
        "CPU cores": os.cpu_count(),
    }
    return "\n".join(f"{k}: {v}" for k, v in info.items())


def register(registry: SkillRegistry) -> None:
    async def list_skills_handler(**kw: Any) -> str:
        skills = registry.list_skills()
        if not skills:
            return "No skills available."
        return "Available skills:\n" + "\n".join(f"- {s}" for s in skills)

    registry.register(Skill(
        name="get_system_info",
        description="Get current system information (OS, CPU, Python version, etc.).",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=get_system_info,
    ))

    registry.register(Skill(
        name="list_skills",
        description="List all available skills/plugins.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=list_skills_handler,
    ))
