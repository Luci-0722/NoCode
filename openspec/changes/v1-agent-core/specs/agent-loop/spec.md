# Agent Loop Spec

## Module

`src/agent/loop.py`

## Description

Core agent orchestrator. Receives user input, manages the LLM interaction loop with tool calling, coordinates memory and skills.

## API

### `Agent.__init__(config: AgentConfig)`
- Creates LLMClient, ShortTermMemory, LongTermMemory, SkillRegistry, TaskScheduler
- Loads builtin skills and external plugins from `config.skills_dir`

### `Agent.chat(user_input: str) -> str`
Non-streaming chat:
1. Add user message to short-term memory
2. Build system prompt = base + long-term memory context
3. Loop up to `max_tool_rounds`:
   - Call LLM with system prompt + short-term history
   - If text response → return it
   - If tool_calls → execute each via SkillRegistry, add results, continue loop

### `Agent.chat_stream(user_input: str) -> AsyncGenerator[str | list[ToolCall]]`
Streaming version. Yields text chunks. Handles tool calls internally, only yields final text.

### `Agent.start() / Agent.stop()`
Lifecycle: opens DB connections, starts scheduler / closes DB, stops scheduler.

## Behavior

- System prompt includes long-term memory context on every call
- Tool call results are added to short-term memory as tool-role messages
- Loop terminates on text response or max_tool_rounds exceeded
- Short-term memory is pruned to max_length automatically
