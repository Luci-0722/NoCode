"""SkillRegistry: register and query skills."""

from __future__ import annotations

import logging

from . import SkillEntry

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Central registry for discovered skills."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillEntry] = {}
        self._sent_skill_names: set[str] = set()

    def register(self, entry: SkillEntry) -> None:
        self._skills[entry.name] = entry

    def register_many(self, entries: list[SkillEntry]) -> None:
        for entry in entries:
            self.register(entry)

    def get(self, name: str) -> SkillEntry | None:
        return self._skills.get(name)

    def all_skills(self) -> list[SkillEntry]:
        return list(self._skills.values())

    def get_tool_skills(self) -> list[SkillEntry]:
        """Return skills the model can invoke."""
        return [
            s for s in self._skills.values()
            if not s.disable_model_invocation and s.user_invocable
        ]

    def get_new_skills_for_listing(self) -> list[SkillEntry]:
        """Return skills not yet sent in a listing (for progressive injection)."""
        new_skills = [
            s for s in self.get_tool_skills()
            if s.name not in self._sent_skill_names
        ]
        for s in new_skills:
            self._sent_skill_names.add(s.name)
        return new_skills

    def clear_sent_flag(self) -> None:
        """Reset the sent flag so all skills will be re-listed."""
        self._sent_skill_names.clear()


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


def init_skill_registry(cwd) -> SkillRegistry:
    """Discover skills from all sources and populate the global registry."""
    from .discover import SkillDiscover
    from pathlib import Path

    global _registry
    _registry = SkillRegistry()
    discover = SkillDiscover(Path(cwd))
    entries = discover.discover_all()
    _registry.register_many(entries)
    logger.info("Skill registry initialized: %d skills discovered", len(entries))
    return _registry
