# AGENTS.md - AI Collaboration Guide

## Project Context

This is a Python AI agent companion project ("Best Friend") using GLM models via OpenAI-compatible API.

## Workflow

1. Read `openspec/project.md` for project overview
2. Check `openspec/changes/` for current change proposals
3. Follow spec-driven development: proposal → design → tasks → specs
4. Implementation follows `tasks.md` checklist

## Code Style

- Python 3.12+ features (type hints, match-case, etc.)
- async/await throughout
- Dataclasses for data structures
- Each module has clear public API
- Chinese system prompt, English code

## Testing

```bash
source .venv/bin/activate
pytest
```

## Key Files

- `src/types.py` - All type definitions
- `src/agent/loop.py` - Agent orchestrator
- `src/agent/llm_client.py` - GLM API client
- `src/skills/registry.py` - Skill plugin system
- `src/memory/long_term.py` - SQLite memory store
