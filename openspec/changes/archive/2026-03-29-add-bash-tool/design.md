## Context

Agent（`src/agent/loop.py`）当前通过两种机制支持工具调用：
1. **SkillRegistry** — 基于插件的 skill（时间、记忆、系统信息），通过 `*_skill.py` 文件注册
2. **直接处理** — `set_reminder` 在 agent loop 中直接处理

bash 工具将采用**直接处理**模式，作为核心工具集成到 agent loop 中，而非 skill 插件。这样可以对安全性和配置进行更精细的控制。

## Goals / Non-Goals

**Goals:**
- 添加 `bash` 工具调用，让 LLM 可以执行 shell 命令
- 提供超时保护，防止失控命令
- 返回捕获的 stdout/stderr 供 agent 推理
- 超时时间、工作目录可配置

**Non-Goals:**
- 命令白名单/黑名单（安全策略后续按需添加）
- 交互式 shell 会话（仅支持单条命令执行）
- 权限提升或 sudo 支持

## Decisions

### 1. 集成方式：直接在 agent loop 中处理（非 SkillRegistry）

**选择**: 在 agent loop 中直接处理 `bash` 工具调用，与 `set_reminder` 并列。

**备选方案:**
- 注册为 skill 插件 — 排除，因为 bash 执行是 agent 核心能力，不是可选插件。直接集成可以更精细地控制工具定义和配置。

**实现方式**: 定义模块级 `bash_tool_definition`（`ToolDefinition`），在 `chat()` 和 `chat_stream()` 中将其合并到工具列表，当 `tc.name == "bash"` 时分发到 `_handle_bash()`。

### 2. 执行方式：`asyncio.create_subprocess_shell`

**选择**: 使用 `asyncio.create_subprocess_shell`，配合 `asyncio.wait_for` 实现超时。

**备选方案:**
- `subprocess.run` — 排除，同步阻塞，不适合异步 agent loop。
- `os.system` — 排除，无法捕获输出。

**实现方式**: 通过管道捕获 stdout 和 stderr，用 `asyncio.wait_for` 包裹以强制执行超时。

### 3. 配置放在 AgentConfig 中

**选择**: 在 `AgentConfig` 中添加 `bash_timeout`（默认 30 秒）和 `bash_workdir`（默认 `None`，继承当前工作目录）。

**理由**: 这些是 agent 级别的配置，与 `max_tool_rounds` 等现有配置保持一致。

## Risks / Trade-offs

- **[LLM 生成恶意命令]** → 超时限制影响范围；输出截断防止 token 洪泛。后续可添加命令白名单。
- **[长时间运行的命令]** → 通过 `asyncio.wait_for` 实现可配置超时，超时后终止进程。
- **[大输出占满上下文]** → 返回前截断输出至最大长度（约 10KB）。
