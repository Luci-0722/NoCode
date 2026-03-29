"""Skill registry: discovery and execution of skills from .skills directory."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine

from src.core import ToolDefinition

logger = logging.getLogger(__name__)

SkillFunc = Callable[..., Coroutine[Any, Any, str]]


class Skill:
    """A skill is an extensible plugin the agent can invoke."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: SkillFunc,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler

    @property
    def tool_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            return await self.handler(**kwargs)
        except Exception as e:
            logger.error("Skill %s execution failed: %s", self.name, e)
            return f"Error: {e}"


class SkillRegistry:
    """Manages all available skills. Skills are loaded from .skills directory."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._modules: dict[str, Any] = {}  # loaded skill modules

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        logger.info("Registered skill: %s", skill.name)

    def unregister(self, name: str) -> None:
        self._skills.pop(name, None)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return [s.tool_definition for s in self._skills.values()]

    async def execute(self, name: str, **kwargs: Any) -> str:
        skill = self.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'"
        return await skill.execute(**kwargs)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def load_skills_from_dir(self, directory: str | Path) -> None:
        """Load skills from a directory.

        Each skill file should be a Python file with a `register(registry)` function.
        """
        skills_dir = Path(directory)
        if not skills_dir.exists():
            logger.info("Skills directory %s does not exist, skipping.", skills_dir)
            return

        for py_file in skills_dir.glob("*_skill.py"):
            if py_file.name.startswith("_"):
                continue
            module_name = f"skills_plugin.{py_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore
                if hasattr(mod, "register"):
                    mod.register(self)
                self._modules[py_file.stem] = mod
                logger.info("Loaded skill from %s", py_file)
            except Exception as e:
                logger.error("Failed to load skill %s: %s", py_file, e)
