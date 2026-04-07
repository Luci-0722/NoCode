# 上下文压缩机制设计文档

> 基于 Claude Code 源码分析，为 nocode_agent 设计多层上下文管理体系

## 1. 背景与问题

nocode_agent 当前有一套简单的压缩机制（`compression.py`）：

- **单层压缩**：仅在 token 超过阈值时，从旧消息中删除可压缩工具的结果
- **缺少 LLM 总结**：当工具裁剪不够时，没有更激进的压缩手段
- **缺少压缩后恢复**：压缩后模型丢失所有文件上下文，无法继续工作
- **缺少会话记忆**：无法在压缩时保留结构化的会话信息

### Claude Code 的参考架构

Claude Code 实现了五层防御：

| 层级 | 机制 | 触发时机 |
|------|------|---------|
| Layer 1 | Microcompact（工具结果裁剪） | 每次 API 调用前 |
| Layer 2 | Session Memory（后台记忆提取） | 对话过程中持续运行 |
| Layer 3 | Auto-Compact（LLM 自动总结） | 每轮结束后检查 token 阈值 |
| Layer 4 | Reactive Compact（413 兜底） | API 返回 prompt-too-long 时 |
| Layer 5 | Context Collapse（渐进压缩） | 实验性，分段总结 |

### 设计目标

在 nocode_agent 中实现 **三层压缩体系**（去掉 Layer 5 实验性功能，Layer 4 作为简单兜底），重点关注：

1. 保持现有的工具结果裁剪（Layer 1）
2. 新增会话记忆压缩（Layer 2）
3. 新增 LLM 自动总结压缩（Layer 3）
4. 新增压缩后的上下文恢复机制

---

## 2. 整体架构

```
用户输入
  ↓
┌─────────────────────────────────────────┐
│         每轮 API 调用前（middleware）      │
│                                         │
│  ① 工具结果裁剪 (Microcompact)           │
│     └→ 删除旧工具结果，保留最近 N 条      │
│                                         │
│  ② Auto-Compact 检查                     │
│     └→ token 数 > 阈值?                  │
│         ├→ 优先: Session Memory 压缩     │
│         └→ 回退: LLM 总结压缩            │
│                                         │
│  ③ API 调用                              │
│     └→ 若返回 prompt-too-long → 裁剪重试 │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│         每轮 API 调用后（post-hook）       │
│                                         │
│  ④ Session Memory 提取                   │
│     └→ 后台异步更新会话记忆文件            │
└─────────────────────────────────────────┘
```

---

## 3. Layer 1：工具结果裁剪（Microcompact）

### 3.1 现状

当前 `ContextCompressor` 已实现基础功能，但有几个问题：

- 删除策略过于简单：只保留最近 N 条消息，不区分工具类型
- 缺少按工具结果数量的触发机制
- 缺少 token 预算概念

### 3.2 改进设计

**新增 `ToolResultBudget` 概念：**

```python
@dataclass
class MicrocompactConfig:
    """工具结果裁剪配置"""
    # 按工具结果数量触发
    trigger_tool_count: int = 30       # 可压缩工具结果超过 30 条时触发
    keep_recent_tools: int = 5         # 始终保留最近 5 条工具结果

    # 按 token 阈值触发（保留现有机制）
    trigger_tokens: int = 8000
    keep_recent_messages: int = 10

    # 可压缩工具列表
    compressible_tools: tuple[str, ...] = (
        "read", "bash", "glob", "grep", "write", "edit",
        "web_search", "web_fetch", "delegate_code",
    )
```

**裁剪策略优先级：**

```
检查触发条件:
  ① 可压缩工具结果数量 > trigger_tool_count  → 按数量裁剪
  ② 总 token 估算 > trigger_tokens           → 按 token 裁剪（现有逻辑）
  ③ 两者都不满足                               → 不裁剪

按数量裁剪:
  - 收集所有可压缩工具的 ToolMessage
  - 保留最近 keep_recent_tools 条
  - 更早的删除（ToolMessage + 对应的 AIMessage tool_calls）
```

### 3.3 涉及文件

| 文件 | 修改内容 |
|------|---------|
| `nocode_agent/compression.py` | 新增 `MicrocompactConfig`，改进 `ContextCompressor` |
| `nocode_agent/config.yaml` | 新增 `microcompact` 配置段 |

---

## 4. Layer 2：会话记忆压缩（Session Memory Compact）

### 4.1 核心思想

在对话过程中，后台持续维护一份**结构化 Markdown 笔记文件**。压缩时直接裁剪旧消息 + 注入笔记，不需要调用 LLM 做总结——**免费且快速**。

### 4.2 会话记忆文件格式

路径：`nocode_agent/.state/session-memory/{thread_id}/summary.md`

```markdown
# Session Title
_简短的会话标题_

# Current State
_当前正在做什么，待完成的任务_

# Task Specification
_用户要求完成什么，设计决策_

# Files and Functions
_操作过的关键文件及其作用_

# Errors & Fixes
_遇到的错误及修复方式_

# Worklog
_操作日志，每步简述_
```

### 4.3 后台提取机制

在每轮模型回复后，异步触发提取（fire-and-forget）：

```python
class SessionMemoryExtractor:
    """后台会话记忆提取器"""

    def __init__(self, config: SessionMemoryConfig):
        self.config = config
        self.last_extracted_message_id: str | None = None
        self.last_extracted_tokens: int = 0
        self._extracting = False

    async def maybe_extract(self, messages: list[BaseMessage]) -> None:
        """每轮模型回复后调用，满足门槛时触发提取"""
        if self._extracting:
            return

        # 门槛检查
        total_tokens = estimate_tokens(messages)
        if total_tokens < self.config.min_tokens_to_init:
            return  # 上下文太少，不值得提取

        token_growth = total_tokens - self.last_extracted_tokens
        if token_growth < self.config.min_tokens_between_updates:
            return  # 增长不够，跳过

        # 触发提取
        self._extracting = True
        try:
            await self._do_extract(messages)
            self.last_extracted_tokens = total_tokens
        finally:
            self._extracting = False

    async def _do_extract(self, messages: list[BaseMessage]) -> None:
        """使用子 agent 调用 LLM 更新记忆文件"""
        # 读取现有 summary.md
        current = self._read_memory_file()

        # 构建更新 prompt
        prompt = self._build_update_prompt(current, messages)

        # 调用 LLM 生成更新（使用轻量模型节省成本）
        # 将更新写入 summary.md
        ...
```

**触发门槛（默认值）：**

```python
@dataclass
class SessionMemoryConfig:
    min_tokens_to_init: int = 10000         # 上下文达到 10K tokens 才开始提取
    min_tokens_between_updates: int = 5000  # 每次提取间至少增长 5K tokens
    max_section_length: int = 2000          # 每个章节最多 2000 tokens
    max_total_tokens: int = 12000           # 总计最多 12000 tokens
```

### 4.4 压缩时使用 Session Memory

```python
class SessionMemoryCompactor:
    """使用会话记忆进行压缩（不调用 LLM）"""

    def compact(self, messages: list[BaseMessage],
                memory_content: str) -> CompactResult | None:
        """
        ① 读取 summary.md 内容
        ② 如果内容为空模板 → 返回 None，回退到 LLM 压缩
        ③ 计算保留消息的起始位置（保留最近 10K~40K tokens）
        ④ 确保不在 tool_use/tool_result 对中间切断
        ⑤ 返回: [边界标记, 摘要消息(含 memory), 保留的消息, 附件]
        """
        ...
```

### 4.5 涉及文件

| 文件 | 内容 |
|------|------|
| `nocode_agent/session_memory.py` | `SessionMemoryExtractor` + `SessionMemoryCompactor` |
| `nocode_agent/compression.py` | 集成 SM 压缩到 middleware 流程 |
| `nocode_agent/config.yaml` | 新增 `session_memory` 配置段 |

---

## 5. Layer 3：LLM 自动总结压缩（Auto-Compact）

### 5.1 触发条件

```
每轮 API 调用前检查:
  effective_window = model_context_window - reserved_output_tokens
  threshold = effective_window - buffer_tokens

  若 current_tokens >= threshold → 触发压缩
```

**默认值（以 GLM-4 的 128K 上下文为例）：**

| 参数 | 值 | 说明 |
|------|-----|------|
| model_context_window | 128,000 | 模型上下文窗口 |
| reserved_output_tokens | 4,096 | 预留输出空间 |
| effective_window | 123,904 | 可用输入空间 |
| buffer_tokens | 13,000 | 缓冲区 |
| **auto_compact_threshold** | **110,904** | ~86% 时触发 |

> 注意：当前 config.yaml 中 `trigger_tokens: 8000` 是给微压缩用的。
> Auto-compact 需要基于模型实际上下文窗口动态计算。

### 5.2 压缩流程

```python
class AutoCompactor:
    """自动总结压缩器"""

    async def compact_if_needed(
        self,
        messages: list[BaseMessage],
        model: ChatOpenAI,
        session_memory: SessionMemoryExtractor | None = None,
    ) -> CompactResult | None:
        """检查并执行压缩"""
        token_count = estimate_tokens(messages)
        threshold = self._get_threshold()

        if token_count < threshold:
            return None

        # 优先尝试 Session Memory 压缩
        if session_memory:
            result = SessionMemoryCompactor().compact(messages, session_memory.content)
            if result is not None:
                return result

        # 回退到 LLM 总结压缩
        return await self._llm_compact(messages, model)

    async def _llm_compact(self, messages, model) -> CompactResult:
        """
        ① 剥离图片/大文件等（防止总结请求本身超限）
        ② 构建总结 prompt
        ③ 调用 LLM 流式生成总结（max_output_tokens=20000）
        ④ 若总结请求本身超限 → truncateHeadForPTLRetry 裁剪头部重试
        ⑤ 生成压缩结果 + 恢复附件
        """
        ...
```

### 5.3 总结 Prompt

```python
SUMMARY_PROMPT = """你是一个专门总结对话的助手。请对以下对话进行总结。

请按照以下结构输出：

<analysis>
先在内部梳理对话的关键信息（此部分不会展示给用户）。
</analysis>

<summary>
## Primary Request and Intent
用户的主要请求和意图

## Key Technical Concepts
关键技术概念和决策

## Files and Code Sections
涉及的重要文件和代码片段（保留关键代码）

## Errors and Fixes
遇到的错误及修复方式

## Problem Solving
问题解决过程

## Pending Tasks
未完成的任务

## Current Work
当前正在进行的工作
</summary>
"""
```

### 5.4 熔断器

```python
@dataclass
class AutoCompactTracking:
    """压缩追踪状态"""
    compacted: bool = False
    turn_counter: int = 0
    consecutive_failures: int = 0

    MAX_CONSECUTIVE_FAILURES = 3  # 连续失败 3 次后停止尝试
```

---

## 6. 压缩后恢复机制

这是 Claude Code 中最精妙的部分。压缩后模型丢失了所有上下文，需要**重新注入关键信息**。

### 6.1 恢复内容与预算

| 恢复内容 | 预算 | 说明 |
|---------|------|------|
| 最近读取的文件 | 最多 5 个文件，每个 5K tokens，总计 50K tokens | 重新从磁盘读取最新内容 |
| 当前 Plan | 全量 | 从磁盘读取 plan 文件 |
| 已调用的 Skill | 每个 5K tokens，总计 25K tokens | 从注册表获取 |
| 后台 Agent 状态 | 全量 | 检查是否有正在运行的子 agent |
| CLAUDE.md 指令 | 重新执行 discover_instruction_files | 与启动时相同的发现逻辑 |

### 6.2 文件恢复实现

```python
class PostCompactRestorer:
    """压缩后上下文恢复器"""

    def __init__(self, config: PostCompactConfig):
        self.config = config  # max_files=5, max_tokens_per_file=5000, total_budget=50000

    async def restore(
        self,
        read_file_state: dict[str, FileState],  # 本次会话中读取过的文件记录
    ) -> list[BaseMessage]:
        """生成恢复附件消息"""
        attachments: list[BaseMessage] = []

        # ① 恢复最近读取的文件
        file_attachments = self._restore_recent_files(read_file_state)
        attachments.extend(file_attachments)

        # ② 恢复 plan（如果有）
        plan_attachment = self._restore_plan()
        if plan_attachment:
            attachments.append(plan_attachment)

        # ③ 恢复 skill（如果有已调用的）
        skill_attachment = self._restore_skills()
        if skill_attachment:
            attachments.append(skill_attachment)

        # ④ 恢复 CLAUDE.md 指令
        instructions = self._restore_instructions()
        attachments.extend(instructions)

        return attachments

    def _restore_recent_files(
        self, read_file_state: dict[str, FileState]
    ) -> list[BaseMessage]:
        """
        ① 按时间戳降序排列读取过的文件
        ② 取最近 5 个
        ③ 从磁盘重新读取每个文件（获取最新内容）
        ④ 每个文件截断到 5K tokens
        ⑤ 总计不超过 50K tokens
        """
        sorted_files = sorted(
            read_file_state.items(),
            key=lambda x: x[1].timestamp,
            reverse=True,
        )[:self.config.max_files]

        used_tokens = 0
        messages = []
        for path, state in sorted_files:
            try:
                content = Path(path).read_text(encoding="utf-8")
                file_tokens = estimate_tokens_text(content)
                truncated_tokens = min(file_tokens, self.config.max_tokens_per_file)

                if used_tokens + truncated_tokens > self.config.total_budget:
                    break

                # 截断内容
                if file_tokens > self.config.max_tokens_per_file:
                    char_budget = self.config.max_tokens_per_file * 3
                    content = content[:char_budget] + "\n\n[truncated]"

                messages.append(HumanMessage(
                    content=f"[post-compact file restore] {path}\n\n{content}"
                ))
                used_tokens += truncated_tokens
            except Exception:
                continue

        return messages
```

### 6.3 文件读取状态追踪

为了让压缩后知道哪些文件被读取过，需要在工具执行时记录：

```python
# 在 Read 工具执行后记录
class FileReadTracker:
    """追踪本会话中读取过的文件"""

    def __init__(self):
        self._state: dict[str, FileState] = {}

    def record_read(self, path: str, content: str) -> None:
        self._state[path] = FileState(
            content_hash=hash(content[:500]),
            timestamp=time.time(),
        )

    def get_state(self) -> dict[str, FileState]:
        return dict(self._state)
```

### 6.4 压缩结果结构

```python
@dataclass
class CompactResult:
    """压缩结果"""
    boundary_marker: BaseMessage          # 压缩边界标记
    summary_messages: list[BaseMessage]    # 摘要消息（LLM 总结 或 session memory）
    restored_messages: list[BaseMessage]   # 恢复附件（文件、plan、skill）
    messages_to_keep: list[BaseMessage]    # 保留的近期消息
    pre_compact_tokens: int                # 压缩前 token 数
    post_compact_tokens: int               # 压缩后 token 数

    def build_final_messages(self) -> list[BaseMessage]:
        """组装最终消息列表"""
        return [
            self.boundary_marker,
            *self.summary_messages,
            *self.messages_to_keep,
            *self.restored_messages,
        ]
```

---

## 7. 配置设计

```yaml
# config.yaml 新增配置段

# 微压缩（Layer 1）- 改造现有的 compression 段
microcompact:
  trigger_tool_count: 30        # 工具结果数超过 30 条时裁剪
  keep_recent_tools: 5          # 保留最近 5 条工具结果
  trigger_tokens: 8000          # token 超过 8000 时裁剪
  keep_recent_messages: 10      # 保留最近 10 条消息
  compressible_tools:
    - read
    - bash
    - glob
    - grep
    - write
    - edit
    - web_search
    - web_fetch
    - delegate_code

# 会话记忆（Layer 2）
session_memory:
  enabled: true
  min_tokens_to_init: 10000
  min_tokens_between_updates: 5000
  max_section_length: 2000
  max_total_tokens: 12000
  storage_path: "nocode_agent/.state/session-memory"

# 自动总结压缩（Layer 3）
auto_compact:
  enabled: true
  context_window: 128000        # 模型上下文窗口大小
  buffer_tokens: 13000          # 缓冲区
  reserved_output_tokens: 4096  # 预留输出空间
  max_summary_tokens: 20000     # 总结最大输出 tokens
  max_consecutive_failures: 3   # 熔断器阈值

# 压缩后恢复
post_compact_restore:
  max_files: 5
  max_tokens_per_file: 5000
  total_file_budget: 50000
  max_tokens_per_skill: 5000
  total_skill_budget: 25000
```

---

## 8. 实现路线图

### Phase 1：改进 Layer 1（预计改动量：小）

- 改造现有 `ContextCompressor`，新增按工具数量触发
- 保持向后兼容，config.yaml 中 `compression` 段迁移到 `microcompact`

**涉及文件：**
- `nocode_agent/compression.py` — 改进现有代码
- `nocode_agent/config.yaml` — 迁移配置

### Phase 2：实现 Layer 3 + 恢复机制（预计改动量：大）

- 实现 `AutoCompactor` 和总结 prompt
- 实现 `PostCompactRestorer` 和 `FileReadTracker`
- 实现 `CompactResult` 消息组装
- 在 middleware 中集成 auto-compact 检查

**涉及文件：**
- `nocode_agent/auto_compact.py` — 新文件
- `nocode_agent/post_compact.py` — 新文件
- `nocode_agent/compression.py` — 集成到 middleware 流程
- `nocode_agent/agent.py` — 注入 `FileReadTracker`
- `nocode_agent/tools.py` — Read 工具调用后记录文件状态

### Phase 3：实现 Layer 2（预计改动量：大）

- 实现 `SessionMemoryExtractor`
- 实现 `SessionMemoryCompactor`
- 实现后台异步提取（post-model hook）
- 集成到 auto-compact 流程（优先 SM 压缩，回退 LLM 压缩）

**涉及文件：**
- `nocode_agent/session_memory.py` — 新文件
- `nocode_agent/auto_compact.py` — 集成 SM 压缩
- `nocode_agent/agent.py` — 注册 post-model hook

### Phase 4：实现 Layer 4 兜底（预计改动量：小）

- 在 `chat()` 方法中捕获 prompt-too-long 错误
- 实现 `truncateHeadForPTLRetry` 裁剪头部重试

**涉及文件：**
- `nocode_agent/agent.py` — 错误捕获
- `nocode_agent/auto_compact.py` — 裁剪重试逻辑

---

## 9. 关键设计决策

| 决策 | 选项 | 选择 | 原因 |
|------|------|------|------|
| SM 提取时机 | 每个 turn 后 / 定时 | 每个 turn 后（满足门槛时） | 与 Claude Code 一致，增量提取 |
| SM 压缩 vs LLM 压缩优先级 | SM 优先 / LLM 优先 | SM 优先 | SM 不消耗 API 调用，免费且快 |
| 恢复文件数量 | 3/5/10 | 5 | Claude Code 验证过的值 |
| 总结模型 | 使用当前模型 / 使用轻量模型 | 使用当前模型 | 简化实现，后续可优化 |
| 配置方式 | 全局配置 / 远程配置 | 全局 YAML 配置 | nocode_agent 不需要远程特性开关 |
