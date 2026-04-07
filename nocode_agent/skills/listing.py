"""SkillListBuilder: generate budget-limited skill listings."""

from __future__ import annotations

from . import SkillEntry


class SkillListBuilder:
    """Build a concise skill listing for injection into the conversation."""

    BUDGET_PERCENT = 0.01       # 1% of context window
    MAX_DESC_CHARS = 250

    def __init__(self, context_window_tokens: int = 128_000):
        self.budget_chars = int(context_window_tokens * self.BUDGET_PERCENT * 3)

    def build_listing(self, skills: list[SkillEntry]) -> str | None:
        """Build skill listing text. Returns None if nothing to list."""
        if not skills:
            return None

        lines: list[str] = []
        total_chars = 0

        for skill in skills:
            desc = skill.description or skill.name
            if len(desc) > self.MAX_DESC_CHARS:
                desc = desc[:self.MAX_DESC_CHARS] + "..."

            line = f"- {skill.name}: {desc}"
            if skill.when_to_use:
                line += f" — {skill.when_to_use[:100]}"

            if total_chars + len(line) > self.budget_chars:
                break

            lines.append(line)
            total_chars += len(line)

        if not lines:
            return None

        header = (
            "The following skills are available. "
            "When a user uses /<skill-name> or when you judge a skill is relevant, "
            "use the invoke_skill tool to load and apply it."
        )
        return header + "\n\n" + "\n".join(lines)
