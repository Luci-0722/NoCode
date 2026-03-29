# Skill System Spec

## Modules

- `src/skills/registry.py`
- `src/skills/builtin/time_skill.py`
- `src/skills/builtin/memory_skill.py`
- `src/skills/builtin/system_skill.py`

## Skill Interface

```python
@dataclass
class Skill:
    name: str              # unique identifier
    description: str       # tells LLM when/how to use this skill
    parameters: dict       # JSON Schema for tool arguments
    handler: Callable      # async (**kwargs) -> str
```

## SkillRegistry

### API
- `register(skill: Skill)` — add skill to registry
- `unregister(name: str)` — remove skill
- `get(name: str) -> Skill | None`
- `execute(name: str, arguments: str | dict) -> str` — execute skill handler
- `list_skills() -> list[str]` — list all registered skill names
- `to_openai_tools() -> list[dict]` — convert all skills to OpenAI tool definitions

### Discovery
- `load_builtin_skills(agent)` — imports `src/skills/builtin/*_skill.py`, calls `register()`
- `load_skills_from_dir(directory)` — imports `skills/*_skill.py`, calls `register()`

### Plugin Convention
Each `*_skill.py` must export:
```python
def register(registry: SkillRegistry, agent=None) -> None:
    registry.register(Skill(name=..., description=..., parameters=..., handler=...))
```

## Builtin Skills

| Skill | Parameters | Description |
|-------|-----------|-------------|
| `get_current_time` | `timezone?` | Returns current time, optionally in specified timezone |
| `get_date_info` | — | Returns detailed date info (year, month, day, weekday, week number) |
| `remember` | `content, category?, key?` | Save info to long-term memory |
| `recall` | `query?` | Recall facts from long-term memory |
| `forget` | `content?` | Delete matching facts from memory |
| `set_preference` | `key, value` | Save user preference |
| `get_system_info` | — | Returns OS, Python version, hostname |
| `list_skills` | — | Lists all available skills |
