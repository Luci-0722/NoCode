# CLI Spec

## Module

`src/cli.py`

## Description

Rich-based interactive REPL for the Best Friend agent. Provides multi-turn chat with streaming output and slash commands.

## Commands

| Command | Action |
|---------|--------|
| `/quit` | Exit the application |
| `/exit` | Alias for /quit |
| `/clear` | Clear short-term memory |
| `/skills` | List available skills in a panel |
| `/memory` | Show long-term memory facts (last 20) |
| `/tasks` | List scheduled tasks with status |
| `/help` | Show available commands |

## Config Loading

1. Load from `config/default.yaml` (or `BF_CONFIG` env var path)
2. Override `api_key` with `ZHIPU_API_KEY` env var
3. Required: `ZHIPU_API_KEY` must be set

## Behavior

- Streaming output: yields text chunks from `agent.chat_stream()`
- Errors displayed in red without crashing
- `Ctrl+C` / `Ctrl+D` exits gracefully
- Banner displayed on startup
