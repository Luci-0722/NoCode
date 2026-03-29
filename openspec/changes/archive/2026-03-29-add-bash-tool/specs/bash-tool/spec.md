## ADDED Requirements

### Requirement: Bash 工具作为工具调用暴露给 LLM
Agent SHALL 向 LLM 暴露 `bash` 工具定义，允许其执行 shell 命令。工具定义 MUST 包含 `command` 参数（字符串，必填），并在描述中说明用于执行 shell 命令。

#### Scenario: LLM 在可用工具中看到 bash
- **WHEN** agent 向 LLM 发送消息
- **THEN** 工具列表中包含 `bash` 工具定义，且包含 `command` 参数

### Requirement: Agent 直接分发 bash 工具调用
Agent loop SHALL 将 `bash` 工具调用直接分发到 `_handle_bash()`，不经过 SkillRegistry。

#### Scenario: Agent 收到 LLM 的 bash 工具调用
- **WHEN** LLM 返回的工具调用中 `name == "bash"`
- **THEN** agent 调用 `_handle_bash(args)` 并将结果作为工具消息返回

### Requirement: 通过异步子进程执行命令
系统 SHALL 使用 `asyncio.create_subprocess_shell` 执行 shell 命令，捕获 stdout 和 stderr。

#### Scenario: 执行简单命令
- **WHEN** bash 工具以 `command: "echo hello"` 调用
- **THEN** 系统执行命令并返回 stdout 输出

#### Scenario: 命令产生 stderr 输出
- **WHEN** bash 工具调用的命令向 stderr 写入内容
- **THEN** 返回结果中包含 stderr 输出

#### Scenario: 命令返回非零退出码
- **WHEN** bash 工具调用的命令执行失败
- **THEN** 返回结果中包含错误输出并指示退出码

### Requirement: 命令具有超时保护
系统 SHALL 对命令执行强制执行可配置的超时时间。超时时 MUST 终止进程并返回错误信息。

#### Scenario: 命令超过超时时间
- **WHEN** bash 工具调用的命令运行时间超过 `bash_timeout`
- **THEN** 系统终止进程并返回超时错误信息

### Requirement: 输出被截断以防止上下文泛滥
系统 SHALL 在返回给 LLM 之前将命令输出截断至最大长度。

#### Scenario: 命令产生超大输出
- **WHEN** bash 工具调用的命令产生超过最大长度的输出
- **THEN** 返回结果被截断，并提示输出已被截断

### Requirement: 工作目录可配置
系统 SHALL 支持通过 `AgentConfig.bash_workdir` 配置命令执行的工作目录。

#### Scenario: 已配置工作目录
- **WHEN** `AgentConfig.bash_workdir` 设置为有效目录
- **THEN** 命令以该目录作为工作目录执行

#### Scenario: 未配置工作目录
- **WHEN** `AgentConfig.bash_workdir` 未设置（None）
- **THEN** 命令以 agent 当前工作目录执行
