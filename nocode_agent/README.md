# nocode_agent

单独的 NoCode agent 包。

- 负责单 agent 运行时、工具、提示词、持久化
- 负责单 agent 的终端 TUI：`nocode_agent/frontend/tui.ts`
- 暴露 `nocode`、`nocode-acp` 两个入口
- 不包含多会话编排、会话注册表 MCP、Web UI
- 默认配置：`nocode_agent/config.yaml`
- 默认状态目录：`nocode_agent/.state/`
