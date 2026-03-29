# v1-agent-core: Agent Core Implementation

## Status: Done

## What

Implement the core AI agent system for "Best Friend" — a personal AI companion with:

- Multi-turn conversation via GLM (智谱AI) models
- Short-term memory (session context management)
- Long-term memory (persistent SQLite storage)
- Extensible skill/plugin system
- Scheduled task management
- Rich CLI interface

## Why

Building a personal AI agent from scratch (not using LangChain/Vercel AI SDK) gives full control over:

- Memory architecture and retrieval strategy
- Tool execution flow and error handling
- Custom scheduling and automation
- Future extensibility for desktop pet UI

## Scope

### In Scope
- Agent loop with tool calling (message → LLM → tool_call → execute → feed back)
- OpenAI-compatible GLM API integration
- SQLite-backed long-term memory with facts, conversations, preferences
- Builtin skills: time, memory management, system info
- External skill plugin loading from `skills/` directory
- APScheduler-based task scheduling (cron/interval/once)
- CLI with Rich formatting and command system

### Out of Scope
- Desktop pet UI (deferred to v2)
- Frontend configuration panel (deferred)
- Multi-user support
- Web/API interface
