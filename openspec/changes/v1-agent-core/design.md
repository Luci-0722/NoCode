# v1-agent-core: Design Document

## System Architecture

```
┌─────────────┐
│    CLI      │  Rich-based terminal UI
│   cli.py    │  Command handling (/help, /memory, etc.)
└──────┬──────┘
       │
┌──────▼──────────────────────────────────┐
│              Agent Loop                  │
│            src/agent/loop.py             │
│                                         │
│  1. Receive user input                  │
│  2. Build system prompt (+ memory ctx)  │
│  3. Call LLM                            │
│  4. If tool_call → execute skill        │
│  5. Feed result back to LLM             │
│  6. Repeat until text response          │
│  7. Update memories                     │
└──┬────────┬──────────┬──────────┬───────┘
   │        │          │          │
   ▼        ▼          ▼          ▼
┌──────┐┌────────┐┌─────────┐┌──────────┐
│ LLM  ││ Short  ││  Long   ││ Scheduler│
│Client││Memory  ││ Memory  ││          │
│      ││        ││(SQLite) ││(APScheduler)
└──────┘└────────┘└────┬────┘└──────────┘
                       │
              ┌────────▼────────┐
              │  Skill Registry │
              │                 │
              │  Builtin:       │
              │  - time_skill   │
              │  - memory_skill │
              │  - system_skill │
              │                 │
              │  Plugins:       │
              │  - skills/*.py  │
              └─────────────────┘
```

## Module Design

### 1. Types (`src/types.py`)

Core data structures used across all modules:

```python
class Role(Enum):       USER, ASSISTANT, SYSTEM, TOOL
class Message:          role, content, tool_calls?, name?, tool_call_id?
class ToolCall:         id, name, arguments (JSON string)
class ToolDefinition:   name, description, parameters (JSON Schema)
class AgentConfig:      model, api_key, base_url, temperature, etc.
```

### 2. LLM Client (`src/agent/llm_client.py`)

Wraps `AsyncOpenAI` for GLM compatibility:

- `chat(messages) → Message` — non-streaming, returns full response (may include tool_calls)
- `chat_stream(messages)` — async generator yielding text chunks or ToolCall lists
- Base URL: `https://open.bigmodel.cn/api/paas/v4`
- Model: `glm-4-flash` (configurable)

### 3. Short-term Memory (`src/memory/short_term.py`)

- `collections.deque` with configurable max length (default 50)
- Stores `Message` objects in order
- `format_context() → list[dict]` — produces OpenAI-compatible message list for API
- `clear()` — reset on `/clear` command

### 4. Long-term Memory (`src/memory/long_term.py`)

SQLite database with 3 tables:

**facts**: `id, category, key, content, importance, created_at, updated_at`
- `add_fact(category, key, content, importance)`
- `get_facts(limit)` — retrieve all facts
- `search_facts(query)` — LIKE-based search
- `delete_fact(id)`
- `build_context_block()` — generate context string for system prompt injection

**conversations**: `id, role, content, timestamp`
- `save_message(role, content)`
- `get_recent_conversations(limit)`

**user_preferences**: `key, value, updated_at`
- `set_preference(key, value)`
- `get_preference(key)`

### 5. Skill System (`src/skills/registry.py`)

**Skill interface:**
```python
class Skill:
    name: str                    # unique identifier
    description: str             # for LLM to understand when to use
    parameters: dict             # JSON Schema for arguments
    handler: Callable            # async (**kwargs) → str
```

**SkillRegistry:**
- `register(skill)` / `unregister(name)`
- `get(name) → Skill`
- `execute(name, arguments) → str`
- `list_skills() → list[str]`
- `load_builtin_skills(agent)` — loads from `src/skills/builtin/*_skill.py`
- `load_skills_from_dir(directory)` — loads external plugins

**Plugin convention:** each `*_skill.py` must export `register(registry, agent=None)`.

### 6. Scheduler (`src/scheduler/scheduler.py`)

Based on APScheduler `AsyncIOScheduler`:

- `add_task(name, handler, trigger_type, trigger_args)` — supports `cron`, `interval`, `once`
- `remove_task(id)` / `toggle_task(id)`
- `list_tasks()` — returns task metadata + next_run time
- `start()` / `stop()` — lifecycle management

### 7. Agent Loop (`src/agent/loop.py`)

Core orchestrator:

```
chat(user_input):
  1. Add user message to short-term memory
  2. Build system prompt = base_prompt + long_term_context_block
  3. Loop (max_tool_rounds):
     a. Call LLM with [system, ...short_term]
     b. If response is text → return it
     c. If response has tool_calls:
        - For each tool_call: execute via skill registry
        - Add tool results to short-term memory
        - Continue loop
  4. Return final text response
```

### 8. CLI (`src/cli.py`)

Rich-based interactive REPL:

- Commands: `/quit`, `/clear`, `/skills`, `/memory`, `/tasks`, `/help`
- Streaming output with `chat_stream()`
- Config loading from YAML + env vars

## Data Flow

```
User types message
  → CLI receives input
  → Agent.chat_stream(user_input)
    → Add to short_term memory
    → Build system prompt (base + memory context)
    → LLM API call (streaming)
    → If tool_call detected:
      → SkillRegistry.execute(skill_name, args)
      → Feed result back to LLM
    → Yield text chunks to CLI
  → CLI displays response
```

## Configuration

```yaml
# config/default.yaml
model: glm-4-flash
base_url: https://open.bigmodel.cn/api/paas/v4
max_tokens: 4096
temperature: 0.7
max_short_term_messages: 50
max_tool_rounds: 10
skills_dir: skills
data_dir: data
```

Environment variables override: `ZHIPU_API_KEY`, `BF_CONFIG`
