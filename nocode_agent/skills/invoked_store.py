"""InvokedSkillStore: track invoked skills for compression recovery."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class InvokedSkill:
    """Record of a previously invoked skill."""
    name: str
    content: str
    invoked_at: float


class InvokedSkillStore:
    """Track skills invoked in this session for post-compaction restoration."""

    MAX_TOKENS_PER_SKILL = 5000
    TOTAL_TOKENS_BUDGET = 25000

    def __init__(self) -> None:
        self._skills: dict[str, InvokedSkill] = {}

    def record(self, name: str, expanded_content: str) -> None:
        """Record an invoked skill."""
        self._skills[name] = InvokedSkill(
            name=name,
            content=expanded_content,
            invoked_at=time.time(),
        )

    def get_all(self) -> list[InvokedSkill]:
        """Return all invoked skills, most recent first."""
        return sorted(self._skills.values(), key=lambda s: s.invoked_at, reverse=True)

    def build_restore_message(self) -> str | None:
        """Build a recovery message for post-compaction injection."""
        skills = self.get_all()
        if not skills:
            return None

        used_tokens = 0
        sections: list[str] = [
            "The following skills were invoked in this session. "
            "Continue to follow these guidelines:\n"
        ]

        for skill in skills:
            content = self._truncate(skill.content, self.MAX_TOKENS_PER_SKILL)
            tokens = len(content) // 3

            if used_tokens + tokens > self.TOTAL_TOKENS_BUDGET:
                break

            sections.append(f"\n### Skill: {skill.name}\n\n{content}")
            used_tokens += tokens

        if len(sections) <= 1:
            return None

        return "\n".join(sections)

    @staticmethod
    def _truncate(content: str, max_tokens: int) -> str:
        char_budget = max_tokens * 3
        if len(content) <= char_budget:
            return content
        return content[:char_budget] + "\n\n[... truncated for compaction]"


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_store: InvokedSkillStore | None = None


def get_invoked_skill_store() -> InvokedSkillStore:
    global _store
    if _store is None:
        _store = InvokedSkillStore()
    return _store
