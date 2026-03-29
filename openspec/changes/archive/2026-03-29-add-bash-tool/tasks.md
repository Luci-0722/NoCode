## 1. 配置

- [x] 1.1 在 `src/types.py` 的 `AgentConfig` 中添加 `bash_timeout`（int，默认 30）和 `bash_workdir`（str | None，默认 None）字段
- [x] 1.2 在 `config/default.yaml` 中添加 `bash_timeout` 和 `bash_workdir` 配置

## 2. 核心实现

- [x] 2.1 在 `src/agent/loop.py` 中定义模块级 `bash_tool_definition`（ToolDefinition），包含 `command` 参数
- [x] 2.2 在 Agent 类中实现 `_handle_bash()` 方法，使用 `asyncio.create_subprocess_shell` 并通过 `asyncio.wait_for` 实现超时
- [x] 2.3 在 `_handle_bash()` 中添加输出截断逻辑（最大约 10KB）

## 3. 集成到 Agent Loop

- [x] 3.1 在 `chat()` 方法中将 `bash_tool_definition` 合并到工具列表（与 skill 工具定义并列）
- [x] 3.2 在 `chat_stream()` 方法中将 `bash_tool_definition` 合并到工具列表
- [x] 3.3 在 `chat()` 的工具调用处理中添加 `bash` 分发——当 `tc.name == "bash"` 时路由到 `_handle_bash()`
- [x] 3.4 在 `chat_stream()` 的工具调用处理中添加同样的 `bash` 分发

## 4. 测试

- [x] 4.1 验证 agent 启动时 bash 工具出现在工具列表中
- [x] 4.2 测试基本命令执行（如 `echo hello`）
- [x] 4.3 测试超时保护——使用长时间运行的命令（如 `sleep 60`）
- [x] 4.4 测试输出截断——使用产生大量输出的命令
