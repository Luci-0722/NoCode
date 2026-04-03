# multiagent_system

多 agent 编排层包。

- 负责 Web UI、会话管理、工作环境切换、agent 路由
- 负责会话注册表 MCP：`multiagent_system.session_mcp_server`
- 通过 ACP/STDIO 调用 `nocode_agent`，但不承载单 agent 核心实现
- 默认配置：`multiagent_system/config.yaml`
- 默认状态目录：`multiagent_system/.state/`
