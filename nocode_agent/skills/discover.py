"""SkillDiscover: scan directories for SKILL.md files."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from . import SkillEntry, build_skill_entry

logger = logging.getLogger(__name__)


class SkillDiscover:
    """Scan directories to discover SKILL.md files."""

    SCAN_SOURCES = [
        "project",  # .nocode/skills/ (walk up from CWD)
        "user",     # ~/.nocode/skills/
        "builtin",  # nocode_agent/bundled_skills/
    ]

    def __init__(self, cwd: Path):
        self.cwd = cwd.resolve()

    def discover_all(self) -> list[SkillEntry]:
        """Scan all sources, return deduplicated skill list."""
        entries: list[SkillEntry] = []
        seen_paths: set[str] = set()

        for source in self.SCAN_SOURCES:
            for entry in self._scan_source(source):
                real = str(entry.skill_dir.resolve())
                if real not in seen_paths:
                    seen_paths.add(real)
                    entries.append(entry)

        return entries

    def _scan_source(self, source: str) -> list[SkillEntry]:
        if source == "project":
            return self._scan_project_dirs()
        elif source == "user":
            return self._scan_dir(Path.home() / ".nocode" / "skills", source="user")
        elif source == "builtin":
            return self._scan_builtin()
        return []

    def _scan_project_dirs(self) -> list[SkillEntry]:
        """Walk up from CWD, look for .nocode/skills/ directories."""
        entries: list[SkillEntry] = []
        for parent in [self.cwd, *self.cwd.parents]:
            skills_dir = parent / ".nocode" / "skills"
            if skills_dir.exists():
                entries.extend(self._scan_dir(skills_dir, source="project"))
        return entries

    def _scan_builtin(self) -> list[SkillEntry]:
        """Scan the bundled_skills directory shipped with nocode_agent."""
        # nocode_agent/bundled_skills/ is relative to this file's parent
        builtin_dir = Path(__file__).resolve().parent.parent / "bundled_skills"
        return self._scan_dir(builtin_dir, source="builtin")

    def _scan_dir(self, skills_dir: Path, source: str = "user") -> list[SkillEntry]:
        """Scan a single skills/ directory. Each subdirectory with a SKILL.md is a skill."""
        entries: list[SkillEntry] = []
        if not skills_dir.exists():
            return entries

        try:
            items = sorted(skills_dir.iterdir())
        except OSError:
            return entries

        for skill_dir in items:
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                entry = build_skill_entry(skill_md, skill_dir, source)
                if entry is not None:
                    entries.append(entry)

        return entries
