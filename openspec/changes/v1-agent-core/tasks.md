# v1-agent-core: Tasks

## Implementation Checklist

### Phase 1: Foundation

- [x] **T1.1** Create `pyproject.toml` with dependencies (openai, rich, apscheduler, pyyaml)
- [x] **T1.2** Define core types in `src/types.py` (Role, Message, ToolCall, ToolDefinition, AgentConfig)
- [x] **T1.3** Create `config/default.yaml` with default settings

### Phase 2: LLM Client

- [x] **T2.1** Implement `src/agent/llm_client.py` — AsyncOpenAI wrapper for GLM
- [x] **T2.2** Implement `chat()` method (non-streaming, returns Message with optional tool_calls)
- [x] **T2.3** Implement `chat_stream()` async generator (streaming text + tool call detection)

### Phase 3: Memory System

- [x] **T3.1** Implement `src/memory/short_term.py` — deque-based session context
- [x] **T3.2** Implement `src/memory/long_term.py` — SQLite with facts, conversations, preferences tables
- [x] **T3.3** Implement `build_context_block()` for system prompt memory injection

### Phase 4: Skill System

- [x] **T4.1** Implement `src/skills/registry.py` — Skill dataclass, SkillRegistry class
- [x] **T4.2** Implement `load_builtin_skills()` and `load_skills_from_dir()`
- [x] **T4.3** Create `src/skills/builtin/time_skill.py` — get_current_time, get_date_info
- [x] **T4.4** Create `src/skills/builtin/memory_skill.py` — remember, recall, forget, set_preference
- [x] **T4.5** Create `src/skills/builtin/system_skill.py` — get_system_info, list_skills

### Phase 5: Scheduler

- [x] **T5.1** Implement `src/scheduler/scheduler.py` — APScheduler wrapper with cron/interval/once support

### Phase 6: Agent Loop

- [x] **T6.1** Implement `src/agent/loop.py` — Agent class orchestrating all modules
- [x] **T6.2** Implement `_build_system_prompt()` with memory context injection
- [x] **T6.3** Implement `chat()` — agent loop with tool calling
- [x] **T6.4** Implement `chat_stream()` — streaming version
- [x] **T6.5** Implement `start()/stop()` lifecycle management

### Phase 7: CLI & Polish

- [x] **T7.1** Implement `src/cli.py` — Rich-based REPL with commands
- [x] **T7.2** Create `run.sh` — one-click launch script (Python detection, venv, deps)
- [x] **T7.3** Write `README.md` — project documentation
- [x] **T7.4** Create OpenSpec documents (proposal, design, tasks)
