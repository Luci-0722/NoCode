# NoCode Web Multi-Agent

这个目录提供一个独立的 Web 编排层，用来连接你本地的 ACP server，并把同一个 ACP agent 以“多 session”的方式包装成可在前端管理的多 agent 系统。

## 架构

- `web/server.py`
  一个基于标准库 `http.server` 的轻量服务，负责静态页面、HTTP API、任务调度和内存态存储。
- `AgentRuntime`
  ACP 运行时抽象。它定义了 `run/clear/thread_id` 三个能力。
- `ACPRemoteRuntime`
  当前默认实现。它通过 `acp_sdk.client.Client` 连接本地 ACP server，并为每个前端 agent 维护独立的 ACP `session`。
- `MultiAgentStore`
  多 agent 编排核心，负责：
  - 创建 agent
  - 维护 agent 独立上下文
  - 解析消息里的 `@agentName`
  - 将用户消息路由到多个 agent
  - 解析 agent 输出里的 `@otherAgent`
  - 自动触发 agent 之间的 relay 协作
- `web/static/*`
  一个无构建步骤的原生前端，直接调用 `/api/*` 接口。

## 数据流

1. 用户在前端创建多个 agent，每个 agent 有自己的 `name` 和 `system_prompt`。
2. 用户在输入框发送消息。
3. 后端解析消息中的 `@名字`。
4. 命中的 agent 会各自启动一次独立运行，并沿用自己的 ACP session/context。
5. agent 运行过程中产生的文本会累计到事件流里。
6. 如果某个 agent 的输出里继续出现 `@另一个 agent`，编排层会自动把它的输出包装成 relay 消息，继续派发给下一个 agent。
7. 前端通过轮询 `/api/state` 展示 agent 状态和所有协作事件。

## ACP 接入方式

当前默认就是 ACP client 模式，不再直接构造 `MainAgent`。整体关系如下：

- `src/acp_server.py` 暴露本地 ACP server
- `web/server.py` 里的 `ACPRemoteRuntime` 通过 HTTP 调用 ACP
- 前端创建出来的每个 agent 都对应一个独立 ACP session
- `clear()` 会重置为一个新的 ACP session id
- `thread_id()` 展示的就是 ACP session id

如果后面你要切换到别的 ACP server，只需要改 `acp_base_url` 和 `acp_agent_name`。

## 启动

先确保已经配置好模型 API，例如：

```bash
export ZHIPU_API_KEY=your_key
python -m src.acp_server --host 127.0.0.1 --port 8000
python -m web.server --host 127.0.0.1 --port 8080 --acp-base-url http://127.0.0.1:8000 --acp-agent-name nocode
```

然后访问：

```text
http://127.0.0.1:8080
```

## 当前限制

- 状态保存在内存里，服务重启后会丢失。
- 前端用的是轮询，不是 SSE/WebSocket。
- agent 间 relay 目前是“文本级转发”，还没有引入结构化 ACP envelope。
- 为了避免无限互相 `@`，后端限制了 relay 深度，默认最多 4 跳。
- 当前 `web` 侧只消费 ACP 的文本流事件，还没有把工具事件单独映射到前端。
