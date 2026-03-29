## Why

当前 agent 没有执行 shell 命令的能力，只能使用时间、记忆和系统信息等 skill。添加 bash 工具调用可以让 agent 与文件系统交互、运行脚本、管理进程、执行系统操作——将 agent 从对话助手转变为具备实际操作能力的系统代理。

## What Changes

- 在 agent loop 中直接集成 `bash` 工具调用（不通过 SkillRegistry 插件系统）
- 定义 bash 工具的 `ToolDefinition`，并将其加入发送给 LLM 的工具列表
- 实现 `_handle_bash()` 方法，类似于现有的 `_handle_reminder()`
- 实现命令超时保护，防止命令挂起或长时间运行
- 捕获并返回 stdout/stderr 输出供 agent 推理使用
- 通过 `AgentConfig` 支持可配置的工作目录和超时时间

## Capabilities

### New Capabilities
- `bash-tool`: Shell 命令执行能力，包含超时保护和输出捕获

### Modified Capabilities
_(无)_

## Impact

- **修改**: `src/agent/loop.py` — 添加 bash 工具定义、`_handle_bash()` 方法、工具调用分发逻辑
- **修改**: `src/types.py` — 在 `AgentConfig` 中添加 bash 相关配置字段
- **修改**: `config/default.yaml` — 添加 bash 配置（超时时间、工作目录）
- **依赖**: `asyncio.subprocess`（标准库，无需新增外部依赖）
- **安全**: Shell 命令执行存在风险，需要超时保护和输出长度限制
