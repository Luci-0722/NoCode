"""Memory skill: read/write long-term memory.

This skill requires the agent to inject a memory accessor via set_context()
after loading.
"""

from __future__ import annotations

from typing import Any, Callable

from src.skills.registry import Skill, SkillRegistry


class MemoryAccessor:
    """Indirection layer for memory access, set by the agent after loading."""

    def __init__(self) -> None:
        self._get_memory: Callable | None = None

    def set_getter(self, fn: Callable) -> None:
        self._get_memory = fn

    @property
    def memory(self):
        if self._get_memory is None:
            raise RuntimeError("Memory accessor not initialized. Call set_getter() first.")
        return self._get_memory()


_accessor = MemoryAccessor()


def get_memory_accessor() -> MemoryAccessor:
    """Get the memory accessor. Agent should call set_getter() on it after loading skills."""
    return _accessor


async def remember(**kwargs: Any) -> str:
    """Save a fact to long-term memory."""
    memory = _accessor.memory
    content = kwargs.get("content", "")
    category = kwargs.get("category", "general")
    importance = kwargs.get("importance", 0.5)

    if not content:
        return "Error: 'content' is required."

    fact_id = memory.add_fact(content, category, importance=importance)
    return f"OK: fact #{fact_id} saved."


async def recall(**kwargs: Any) -> str:
    """Recall facts from long-term memory."""
    memory = _accessor.memory
    category = kwargs.get("category")
    keyword = kwargs.get("keyword")
    limit = kwargs.get("limit", 10)

    if keyword:
        facts = memory.search_facts(keyword, limit=limit)
    else:
        facts = memory.get_facts(category=category, limit=limit)

    if not facts:
        return "No matching facts found."

    lines = []
    for f in facts:
        lines.append(f"[#{f['id']}] ({f['category']}) {f['content']}")
    return "\n".join(lines)


async def forget(**kwargs: Any) -> str:
    """Delete a fact from long-term memory."""
    memory = _accessor.memory
    fact_id = kwargs.get("fact_id")
    if not fact_id:
        return "Error: 'fact_id' is required."

    if memory.delete_fact(fact_id):
        return f"OK: fact #{fact_id} deleted."
    return f"Error: fact #{fact_id} not found."


async def set_preference(**kwargs: Any) -> str:
    """Save a user preference to long-term memory."""
    memory = _accessor.memory
    key = kwargs.get("key", "")
    value = kwargs.get("value", "")

    if not key or not value:
        return "Error: both 'key' and 'value' are required."

    memory.set_preference(key, value)
    return f"OK: preference '{key}' = '{value}' saved."


def register(registry: SkillRegistry) -> None:
    registry.register(Skill(
        name="remember",
        description="Save important information about the user to long-term memory.",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to remember"},
                "category": {
                    "type": "string",
                    "description": "Category for the fact (e.g. 'personal', 'work', 'hobby')",
                    "enum": ["personal", "work", "hobby", "preference", "general"],
                },
                "importance": {
                    "type": "number",
                    "description": "Importance 0.0-1.0, higher = more important. Default 0.5",
                },
            },
            "required": ["content"],
        },
        handler=remember,
    ))

    registry.register(Skill(
        name="recall",
        description="Recall facts from long-term memory. Can filter by category or keyword.",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by category"},
                "keyword": {"type": "string", "description": "Search keyword"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
        },
        handler=recall,
    ))

    registry.register(Skill(
        name="forget",
        description="Delete a specific fact from long-term memory by its ID.",
        parameters={
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer", "description": "The fact ID to delete"},
            },
            "required": ["fact_id"],
        },
        handler=forget,
    ))

    registry.register(Skill(
        name="set_preference",
        description="Save a user preference to long-term memory.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Preference key"},
                "value": {"type": "string", "description": "Preference value"},
            },
            "required": ["key", "value"],
        },
        handler=set_preference,
    ))
