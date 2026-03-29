# Best Friend - AI Agent Companion

## Overview

Personal AI agent companion with memory system, skill plugins, and scheduled tasks. CLI-first, desktop UI deferred.

## Tech Stack

- Python 3.12+ / asyncio
- GLM (智谱AI) via OpenAI-compatible API
- SQLite (long-term memory)
- APScheduler (scheduled tasks)
- Rich (CLI)

## Architecture

```
User Input → CLI → Agent Loop → LLM Client (GLM)
                          ↕
                    Skill Registry → Tool Execution
                          ↕
              Short-term Memory ↔ Long-term Memory (SQLite)
                          ↕
                    Task Scheduler
```

## Key Directories

| Path | Purpose |
|------|---------|
| `src/agent/` | Agent loop, LLM client |
| `src/memory/` | Short-term & long-term memory |
| `src/skills/` | Skill registry & builtin skills |
| `src/scheduler/` | Scheduled task management |
| `config/` | YAML configuration |
| `openspec/` | Spec-driven development docs |
