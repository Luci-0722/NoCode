"""Skill system: discover, register, expand, and invoke skills."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillFrontmatter:
    """SKILL.md YAML frontmatter definition."""

    # Basic info
    name: str | None = None
    description: str = ""

    # Permission control
    allowed_tools: list[str] = field(default_factory=list)

    # Arguments
    argument_hint: str | None = None
    arguments: list[str] = field(default_factory=list)

    # Model control
    model: str | None = None
    effort: str | None = None

    # Visibility
    user_invocable: bool = True
    disable_model_invocation: bool = False

    # Execution context
    context: str | None = None  # "fork" or None

    # Auxiliary info
    when_to_use: str | None = None
    version: str | None = None


@dataclass
class SkillEntry:
    """A discovered skill."""

    name: str
    description: str
    when_to_use: str | None
    allowed_tools: list[str]
    argument_hint: str | None
    arguments: list[str]
    user_invocable: bool
    disable_model_invocation: bool
    context: str | None
    model: str | None
    effort: str | None
    markdown_content: str  # SKILL.md body (without frontmatter)
    skill_dir: Path
    source: str  # "project" / "user" / "builtin"


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter_dict, markdown_body).

    Returns ({}, full_content) if no frontmatter is found.
    """
    text = raw.strip()
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    rest = text[3:]
    close = rest.find("\n---")
    if close == -1:
        return {}, text

    fm_text = rest[:close].strip()
    body = rest[close + 4:].strip()

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}

    if not isinstance(fm, dict):
        fm = {}

    return fm, body


def build_frontmatter(fm_dict: dict) -> SkillFrontmatter:
    """Convert a raw frontmatter dict into a typed SkillFrontmatter."""
    return SkillFrontmatter(
        name=fm_dict.get("name"),
        description=fm_dict.get("description", ""),
        allowed_tools=fm_dict.get("allowed-tools") or fm_dict.get("allowed_tools") or [],
        argument_hint=fm_dict.get("argument-hint") or fm_dict.get("argument_hint"),
        arguments=fm_dict.get("arguments") or [],
        model=fm_dict.get("model"),
        effort=fm_dict.get("effort"),
        user_invocable=fm_dict.get("user-invocable", fm_dict.get("user_invocable", True)),
        disable_model_invocation=fm_dict.get("disable-model-invocation", fm_dict.get("disable_model_invocation", False)),
        context=fm_dict.get("context"),
        when_to_use=fm_dict.get("when_to_use") or fm_dict.get("when-to-use"),
        version=fm_dict.get("version"),
    )


def build_skill_entry(skill_md: Path, skill_dir: Path, source: str) -> SkillEntry | None:
    """Parse a SKILL.md and return a SkillEntry (or None on failure)."""
    try:
        content = skill_md.read_text(encoding="utf-8")
        fm_dict, markdown = parse_frontmatter(content)
        fm = build_frontmatter(fm_dict)

        return SkillEntry(
            name=fm.name or skill_dir.name,
            description=fm.description,
            when_to_use=fm.when_to_use,
            allowed_tools=fm.allowed_tools,
            argument_hint=fm.argument_hint,
            arguments=fm.arguments,
            user_invocable=fm.user_invocable,
            disable_model_invocation=fm.disable_model_invocation,
            context=fm.context,
            model=fm.model,
            effort=fm.effort,
            markdown_content=markdown,
            skill_dir=skill_dir,
            source=source,
        )
    except Exception as exc:
        logger.warning("Failed to parse skill %s: %s", skill_dir, exc)
        return None
