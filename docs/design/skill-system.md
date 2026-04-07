# Skill 系统设计文档

> 基于 Claude Code 源码分析，为 nocode_agent 设计完整的 Skill 加载、调用、恢复体系

## 1. 背景与问题

nocode_agent 当前有一套基础的 Skill 系统（`skills.py`），但存在以下问题：

- **SKILL.md 格式过于自定义**：使用中文段标题（`## 描述`、`## 关键词`），无法与社区生态兼容
- **缺少 YAML frontmatter**：没有标准的元数据定义方式（description、allowed-tools、model 等）
- **缺少 shell 命令预执行**：没有 `!command` 语法，模型需要自己花 turn 去收集环境信息
- **Skill 内容注入方式粗糙**：通过系统提示词一次性注入所有 skill 元数据，浪费 token
- **缺少压缩后恢复**：skill 状态不参与上下文压缩的恢复机制
- **缺少动态发现**：只在启动时扫描，不会在文件操作时发现新 skill

### Claude Code 的参考架构

Claude Code 的 Skill 系统分为四个阶段：

| 阶段 | 机制 | 说明 |
|------|------|------|
| 发现 | 目录扫描 + 动态发现 | 启动时 + 文件操作时 |
| 注册 | 列表注入（system-reminder） | 只注入名字+描述，不加载全文 |
| 调用 | SkillTool → 展开 → 注入 | 读取全文、执行 shell、替换变量 |
| 恢复 | 压缩后重建 invoked_skills | 从内存注册表重新注入 |

### 设计目标

1. 采用 YAML frontmatter 的标准 SKILL.md 格式
2. 实现 Progressive Disclosure：只注入列表，按需加载全文
3. 支持 `!command` shell 命令预执行
4. 支持参数替换（`$ARGUMENTS`、位置参数、命名参数）
5. 支持 `allowed-tools` 权限控制
6. 支持压缩后 skill 状态恢复
7. 支持动态 skill 发现

---

## 2. SKILL.md 格式设计

### 2.1 标准格式

```markdown
---
name: commit
description: 分析当前变更并创建 git commit
allowed-tools:
  - Bash(git add:*)
  - Bash(git status:*)
  - Bash(git commit:*)
  - Read
  - Glob
  - Grep
argument-hint: "[commit message]"
arguments:
  - message
model: inherit
effort: high
user-invocable: true
disable-model-invocation: false
---

# Commit Skill

分析当前代码变更并创建一个 git commit。

## 当前状态

当前分支: !`git branch --show-current`
变更文件:
!`git diff --name-only`
近期提交: !`git log --oneline -10`
```

### 2.2 Frontmatter 字段说明

```python
@dataclass
class SkillFrontmatter:
    """SKILL.md YAML frontmatter 定义"""

    # 基本信息
    name: str | None = None                # 覆盖目录名作为 skill 名
    description: str = ""                  # 简短描述（用于列表展示）

    # 权限控制
    allowed_tools: list[str] = field(default_factory=list)
    # 示例: ["Bash(git add:*)", "Read", "Grep"]
    # 列出的工具自动允许（不弹权限确认），未列出的正常走权限检查

    # 参数
    argument_hint: str | None = None       # 参数提示，如 "[commit message]"
    arguments: list[str] = field(default_factory=list)
    # 命名参数，如 ["message", "scope"]
    # 调用时可通过 $message, $scope 或 $1, $2 引用

    # 模型控制
    model: str | None = None               # 模型覆盖，如 "glm-4-flash" 或 "inherit"
    effort: str | None = None              # 努力等级: low, medium, high

    # 可见性
    user_invocable: bool = True            # 用户是否可调用（/skill-name）
    disable_model_invocation: bool = False # 模型是否可自动调用

    # 执行上下文
    context: str | None = None             # "fork" = 在子 agent 中执行

    # 辅助信息
    when_to_use: str | None = None         # 模型判断何时使用的提示
    version: str | None = None             # 版本号
```

### 2.3 Markdown 正文

正文是给模型看的**指令文本**，支持三种特殊语法：

| 语法 | 作用 | 示例 |
|------|------|------|
| `!`command`` | 预执行 shell 命令，替换为输出 | `` !`git status` `` → "M src/index.ts" |
| `` ```! ... ``` `` | 块级 shell 命令预执行 | 多行命令 |
| `$ARGUMENTS` / `$1` | 用户参数替换 | `$ARGUMENTS` → 用户传入的参数文本 |
| `${SKILL_DIR}` | Skill 目录路径替换 | → `/project/.claude/skills/commit` |

---

## 3. 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    启动阶段                            │
│                                                       │
│  SkillDiscover.scan_dirs()                            │
│  → 扫描 ~/.nocode/skills/, .nocode/skills/ 等目录     │
│  → 读取 SKILL.md → 解析 frontmatter + markdown        │
│  → 注册到 SkillRegistry（只存元数据，不加载全文）       │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│              对话第一个 turn                           │
│                                                       │
│  SkillListBuilder.build()                             │
│  → 生成 skill 列表（名字 + 描述 + when_to_use）        │
│  → 作为系统消息的一部分注入（不是 system prompt）        │
│  → 只注入一次，后续 turn 不重复注入                     │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│           用户或模型触发 Skill (Turn N)                 │
│                                                       │
│  SkillTool.call(skill_name, args)                     │
│  → 从 Registry 查找 skill                             │
│  → 读取 SKILL.md 完整内容                              │
│  → 执行 shell 命令预展开 (!command)                    │
│  → 替换参数 ($ARGUMENTS, $1, ${SKILL_DIR})             │
│  → 应用 allowed-tools 权限                             │
│  → 展开后的文本作为用户消息注入                         │
│  → 注册到 InvokedSkillStore（为压缩恢复做准备）         │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│                  模型执行                               │
│                                                       │
│  模型看到展开后的纯文本指令 + 环境信息                   │
│  → 根据 allowed-tools 使用 Bash/Read/Edit 等工具      │
│  → 模型自己决定操作步骤                                 │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│               压缩后恢复                               │
│                                                       │
│  PostCompactRestorer.restore_skills()                 │
│  → 从 InvokedSkillStore 取出已调用的 skill             │
│  → 每个 skill 截断到 5K tokens，总共 25K tokens       │
│  → 作为附件消息重新注入                                 │
│  → 模型知道之前用过哪些 skill，继续遵循其指令            │
└─────────────────────────────────────────────────────┘
```

---

## 4. 模块设计

### 4.1 SkillDiscover — Skill 发现

```python
class SkillDiscover:
    """扫描目录发现 SKILL.md 文件"""

    # 扫描的目录来源（按优先级）
    SCAN_SOURCES = [
        "project",   # .nocode/skills/ （从 CWD 向上遍历）
        "user",      # ~/.nocode/skills/
        "builtin",   # nocode_agent/bundled_skills/
    ]

    def __init__(self, cwd: Path):
        self.cwd = cwd

    def discover_all(self) -> list[SkillEntry]:
        """扫描所有来源，返回去重后的 skill 列表"""
        entries: list[SkillEntry] = []
        seen_paths: set[str] = set()

        for source in self.SCAN_SOURCES:
            for entry in self._scan_source(source):
                real = str(entry.path.resolve())
                if real not in seen_paths:
                    seen_paths.add(real)
                    entries.append(entry)

        return entries

    def _scan_source(self, source: str) -> list[SkillEntry]:
        """扫描单个来源目录"""
        if source == "project":
            return self._scan_project_dirs()
        elif source == "user":
            return self._scan_dir(Path.home() / ".nocode" / "skills")
        elif source == "builtin":
            return self._scan_builtin()
        return []

    def _scan_project_dirs(self) -> list[SkillEntry]:
        """从 CWD 向上遍历，查找 .nocode/skills/ 目录"""
        entries = []
        for parent in [self.cwd, *self.cwd.parents]:
            skills_dir = parent / ".nocode" / "skills"
            if skills_dir.exists():
                entries.extend(self._scan_dir(skills_dir, source="project"))
        return entries

    def _scan_dir(self, skills_dir: Path, source: str = "user") -> list[SkillEntry]:
        """扫描单个 skills 目录，每个子目录含 SKILL.md"""
        entries = []
        if not skills_dir.exists():
            return entries
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                entry = self._parse_skill(skill_md, skill_dir, source)
                if entry:
                    entries.append(entry)
        return entries

    def _parse_skill(self, skill_md: Path, skill_dir: Path, source: str) -> SkillEntry | None:
        """解析 SKILL.md 文件"""
        try:
            content = skill_md.read_text(encoding="utf-8")
            frontmatter, markdown = self._split_frontmatter(content)
            fm = self._parse_frontmatter(frontmatter)

            return SkillEntry(
                name=fm.name or skill_dir.name,
                description=fm.description,
                when_to_use=fm.when_to_use,
                allowed_tools=fm.allowed_tools,
                argument_hint=fm.argument_hint,
                arguments=fm.arguments,
                user_invocable=fm.user_invocable,
                disable_model_invocation=fm.disable_model_invocation,
                context=fm.context,
                model=fm.model,
                effort=fm.effort,
                markdown_content=markdown,
                skill_dir=skill_dir,
                source=source,
            )
        except Exception as e:
            print(f"Warning: Failed to parse skill {skill_dir}: {e}", file=sys.stderr)
            return None
```

### 4.2 SkillRegistry — Skill 注册表

```python
@dataclass
class SkillEntry:
    """一个已发现的 skill"""
    name: str
    description: str
    when_to_use: str | None
    allowed_tools: list[str]
    argument_hint: str | None
    arguments: list[str]
    user_invocable: bool
    disable_model_invocation: bool
    context: str | None       # "fork" or None
    model: str | None
    effort: str | None
    markdown_content: str     # SKILL.md 正文（不包含 frontmatter）
    skill_dir: Path
    source: str               # "project" / "user" / "builtin"


class SkillRegistry:
    """Skill 注册表"""

    def __init__(self):
        self._skills: dict[str, SkillEntry] = {}
        self._sent_skill_names: set[str] = set()  # 已注入过的 skill（去重用）

    def register(self, entry: SkillEntry) -> None:
        self._skills[entry.name] = entry

    def get(self, name: str) -> SkillEntry | None:
        return self._skills.get(name)

    def get_tool_skills(self) -> list[SkillEntry]:
        """返回模型可调用的 skill 列表"""
        return [
            s for s in self._skills.values()
            if not s.disable_model_invocation and s.user_invocable
        ]

    def get_new_skills_for_listing(self) -> list[SkillEntry]:
        """返回尚未注入过列表的 skill（去重）"""
        new_skills = [
            s for s in self.get_tool_skills()
            if s.name not in self._sent_skill_names
        ]
        for s in new_skills:
            self._sent_skill_names.add(s.name)
        return new_skills
```

### 4.3 SkillListBuilder — 列表构建与注入

**关键设计：skill 列表不放在系统提示词中，而是作为独立消息注入。**

```python
class SkillListBuilder:
    """构建 skill 列表文本，注入到对话中"""

    BUDGET_PERCENT = 0.01     # 列表占上下文窗口的 1%
    MAX_DESC_CHARS = 250      # 每条描述最多 250 字符

    def __init__(self, context_window_tokens: int = 128000):
        self.budget_chars = int(context_window_tokens * self.BUDGET_PERCENT * 3)

    def build_listing(self, skills: list[SkillEntry]) -> str | None:
        """构建 skill 列表文本"""
        if not skills:
            return None

        lines = []
        total_chars = 0

        for skill in skills:
            desc = skill.description or skill.name
            if len(desc) > self.MAX_DESC_CHARS:
                desc = desc[:self.MAX_DESC_CHARS] + "..."

            line = f"- {skill.name}: {desc}"
            if skill.when_to_use:
                line += f" — {skill.when_to_use[:100]}"

            if total_chars + len(line) > self.budget_chars:
                break

            lines.append(line)
            total_chars += len(line)

        if not lines:
            return None

        return "Available skills:\n" + "\n".join(lines)
```

**注入方式：**

```python
# 在 MainAgent.chat() 中
async def chat(self, user_input: str):
    # 每个 turn 开始时检查是否有新 skill 需要注入
    new_skills = self._registry.get_new_skills_for_listing()
    if new_skills:
        listing = SkillListBuilder().build_listing(new_skills)
        if listing:
            # 作为用户消息的一部分注入（不是系统提示词）
            # 这样不会永久占用系统提示词空间
            yield ("skill_listing", listing)

    # 正常处理用户输入...
```

### 4.4 SkillExpander — Skill 内容展开

```python
class SkillExpander:
    """展开 SKILL.md 内容：执行 shell 命令、替换变量"""

    async def expand(
        self,
        entry: SkillEntry,
        args: str | None = None,
    ) -> str:
        """展开 skill 内容，返回纯文本"""
        content = entry.markdown_content

        # ① 参数替换
        content = self._substitute_arguments(content, args, entry.arguments)

        # ② 路径替换
        content = content.replace("${SKILL_DIR}", str(entry.skill_dir))

        # ③ Shell 命令预执行
        content = await self._execute_shell_commands(content)

        # ④ 添加目录头
        return f"Base directory for this skill: {entry.skill_dir}\n\n{content}"

    def _substitute_arguments(
        self,
        content: str,
        args: str | None,
        named_args: list[str],
    ) -> str:
        """替换参数占位符"""
        if not args:
            return content

        # 分割参数
        parts = self._split_args(args)

        # $1, $2, ... 位置参数
        for i, part in enumerate(parts):
            content = content.replace(f"${i + 1}", part)

        # $ARGUMENTS 全部参数
        content = content.replace("$ARGUMENTS", args)

        # $ARGUMENTS[N] 索引访问
        for i, part in enumerate(parts):
            content = content.replace(f"$ARGUMENTS[{i}]", part)

        # 命名参数（从 frontmatter arguments 字段）
        for i, name in enumerate(named_args):
            if i < len(parts):
                content = content.replace(f"${name}", parts[i])

        # 如果没有占位符但传了参数，追加到末尾
        if "$ARGUMENTS" not in content and not named_args:
            content += f"\n\nArguments: {args}"

        return content

    async def _execute_shell_commands(self, content: str) -> str:
        """查找并执行 !`command` 和 ```! ... ``` 模式"""
        import re

        # 块级模式: ```! ... ```
        async def replace_block(match):
            cmd = match.group(1).strip()
            output = await self._run_shell(cmd)
            return output

        content = await re.sub_async(
            r"```!\s*\n?([\s\S]*?)\n?```",
            replace_block,
            content,
        )

        # 行内模式: !`command`
        if "!`" not in content:
            return content

        async def replace_inline(match):
            cmd = match.group(1).strip()
            output = await self._run_shell(cmd)
            return output

        content = await re.sub_async(
            r"(?<=^|\s)!`([^`]+)`",
            replace_inline,
            content,
        )

        return content

    async def _run_shell(self, command: str) -> str:
        """执行 shell 命令并返回输出"""
        import asyncio
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            return stdout.decode("utf-8", errors="replace").strip()
        except Exception as e:
            return f"[shell command failed: {e}]"
```

### 4.5 InvokedSkillStore — 已调用 Skill 追踪

```python
@dataclass
class InvokedSkill:
    """记录一个已被调用的 skill"""
    name: str
    content: str        # 展开后的完整内容
    invoked_at: float   # 时间戳


class InvokedSkillStore:
    """追踪本会话中已调用的 skill，用于压缩后恢复"""

    MAX_TOKENS_PER_SKILL = 5000
    TOTAL_TOKENS_BUDGET = 25000

    def __init__(self):
        self._skills: dict[str, InvokedSkill] = {}

    def record(self, name: str, expanded_content: str) -> None:
        """记录一个已调用的 skill"""
        self._skills[name] = InvokedSkill(
            name=name,
            content=expanded_content,
            invoked_at=time.time(),
        )

    def get_all(self) -> list[InvokedSkill]:
        """按时间降序返回所有已调用的 skill"""
        return sorted(self._skills.values(), key=lambda s: s.invoked_at, reverse=True)

    def build_restore_message(self) -> HumanMessage | None:
        """构建压缩后的 skill 恢复消息"""
        skills = self.get_all()
        if not skills:
            return None

        used_tokens = 0
        sections = []
        sections.append(
            "The following skills were invoked in this session. "
            "Continue to follow these guidelines:\n"
        )

        for skill in skills:
            # 截断到每 skill 最多 5K tokens
            content = self._truncate(skill.content, self.MAX_TOKENS_PER_SKILL)
            tokens = len(content) // 3

            if used_tokens + tokens > self.TOTAL_TOKENS_BUDGET:
                break

            sections.append(f"\n### Skill: {skill.name}\n\n{content}")
            used_tokens += tokens

        if len(sections) <= 1:
            return None

        return HumanMessage(content="\n".join(sections))

    def _truncate(self, content: str, max_tokens: int) -> str:
        char_budget = max_tokens * 3
        if len(content) <= char_budget:
            return content
        return content[:char_budget] + "\n\n[... truncated for compaction]"
```

### 4.6 SkillTool — LangChain Tool 封装

```python
from langchain_core.tools import tool

@tool
async def invoke_skill(
    skill_name: str,
    args: str | None = None,
) -> str:
    """调用一个 skill。

    当用户使用 /skill-name 格式引用 skill，或当模型判断应该使用某个 skill 时调用。

    Args:
        skill_name: skill 名称
        args: 传给 skill 的参数
    """
    registry = get_skill_registry()
    entry = registry.get(skill_name)

    if not entry:
        return f"Error: Skill '{skill_name}' not found."

    # 展开 skill 内容
    expander = SkillExpander()
    expanded = await expander.expand(entry, args)

    # 记录到已调用 store（为压缩恢复做准备）
    get_invoked_skill_store().record(skill_name, expanded)

    return expanded
```

---

## 5. 权限控制：allowed-tools

### 5.1 双重机制

与 Claude Code 一致，`allowed-tools` 同时通过两种方式生效：

**程序化强制（真正的门控）：**

```python
class SkillPermissionManager:
    """管理 skill 调用期间的工具权限"""

    def __init__(self):
        self._skill_allowed_tools: list[str] = []

    def enter_skill(self, allowed_tools: list[str]) -> None:
        """进入 skill 上下文，设置允许的工具"""
        self._skill_allowed_tools = allowed_tools

    def exit_skill(self) -> None:
        """退出 skill 上下文，清除允许的工具"""
        self._skill_allowed_tools = []

    def is_auto_allowed(self, tool_name: str, tool_args: dict) -> bool:
        """检查工具调用是否在 skill 的 allowed-tools 范围内"""
        for pattern in self._skill_allowed_tools:
            if self._match_pattern(tool_name, tool_args, pattern):
                return True
        return False

    def _match_pattern(self, tool_name: str, tool_args: dict, pattern: str) -> bool:
        """匹配工具名和参数模式

        支持的格式:
          "Bash"              → 匹配所有 Bash 调用
          "Bash(git add:*)"   → 匹配以 "git add" 开头的 Bash 调用
          "Read"              → 匹配 Read 工具
        """
        if "(" not in pattern:
            return tool_name.lower() == pattern.lower()

        base, args_pattern = pattern.split("(", 1)
        args_pattern = args_pattern.rstrip(")")

        if tool_name.lower() != base.lower():
            return False

        # 检查命令参数是否匹配
        command = str(tool_args.get("command", ""))
        return command.startswith(args_pattern.split(":")[0])
```

**提示词提示（告知模型）：**

在 skill 展开后的消息中附带权限信息：

```
[skill: commit]
Base directory: /project/.claude/skills/commit

Analyze the changes and create a commit.

Current branch: main
Changed files: src/index.ts

[Allowed tools: Bash(git add:*), Bash(git status:*), Bash(git commit:*), Read, Glob, Grep]
```

### 5.2 在 Agent 中的集成

```python
# agent.py 中
class MainAgent:
    def __init__(self, ...):
        self._skill_perms = SkillPermissionManager()

    # 在工具执行前检查权限
    async def _execute_tool(self, tool_name, tool_args):
        if self._skill_perms.is_auto_allowed(tool_name, tool_args):
            # 自动允许，不弹权限确认
            return await self._do_execute(tool_name, tool_args)
        else:
            # 正常权限流程
            return await self._do_execute_with_permission(tool_name, tool_args)
```

---

## 6. 与上下文压缩的集成

### 6.1 压缩前的处理

在 LLM 总结压缩时，剥离 skill 列表消息（避免污染总结内容）：

```python
# 在 AutoCompactor._llm_compact() 中
def _strip_skill_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
    """剥离 skill 列表和 skill_discovery 消息"""
    return [
        m for m in messages
        if not self._is_skill_listing_message(m)
    ]
```

### 6.2 压缩后的恢复

```python
# 在 PostCompactRestorer 中
def _restore_skills(self) -> list[BaseMessage]:
    """恢复已调用的 skill 状态"""
    store = get_invoked_skill_store()
    msg = store.build_restore_message()
    if msg:
        return [msg]
    return []
```

### 6.3 恢复消息格式

```
The following skills were invoked in this session. Continue to follow these guidelines:

### Skill: commit

Base directory for this skill: /project/.claude/skills/commit

Analyze the changes and create a commit.
Current branch: main
Changed files: src/index.ts
Recent commits: a1b2c3 fix login  d4e5f6 add auth

### Skill: security-review

Review the code for security vulnerabilities...
```

---

## 7. 目录结构

```
nocode_agent/
├── skills/
│   ├── __init__.py
│   ├── discover.py          # SkillDiscover: 目录扫描和解析
│   ├── registry.py          # SkillRegistry: 注册表和查询
│   ├── expander.py          # SkillExpander: shell 预执行 + 变量替换
│   ├── listing.py           # SkillListBuilder: 列表构建
│   ├── permissions.py       # SkillPermissionManager: allowed-tools 控制
│   ├── invoked_store.py     # InvokedSkillStore: 已调用追踪和恢复
│   └── tool.py              # invoke_skill: LangChain @tool 封装
├── bundled_skills/          # 内置 skill
│   ├── commit/
│   │   └── SKILL.md
│   └── review-pr/
│       └── SKILL.md
├── skills.py                # [废弃] 旧版 skill 系统
└── skill_tools.py           # [废弃] 旧版 skill 工具函数
```

---

## 8. 配置设计

```yaml
# config.yaml 新增配置段

skills:
  enabled: true
  # 扫描目录（按优先级，后面的覆盖前面的）
  scan_dirs:
    - "builtin"           # nocode_agent/bundled_skills/
    - "user"              # ~/.nocode/skills/
    - "project"           # .nocode/skills/ (从 CWD 向上遍历)
  # 列表注入预算
  listing_budget_percent: 0.01    # 列表占上下文窗口的 1%
  listing_max_desc_chars: 250     # 每条描述最大字符数
  # 压缩恢复预算
  restore_max_per_skill: 5000     # 每个 skill 最多 5K tokens
  restore_total_budget: 25000     # 总共最多 25K tokens
  # shell 预执行
  shell_timeout: 10               # shell 命令超时（秒）
```

---

## 9. 实现路线图

### Phase 1：核心框架（预计改动量：中）

- 实现 `SkillEntry` 数据模型和 YAML frontmatter 解析
- 实现 `SkillDiscover` 目录扫描
- 实现 `SkillRegistry` 注册表
- 实现 `SkillListBuilder` 列表构建
- 重构 `prompts.py` 中的 skill 注入方式

**涉及文件：**
- `nocode_agent/skills/` — 新目录，新文件
- `nocode_agent/prompts.py` — 修改 skill 注入方式
- `nocode_agent/agent.py` — 集成新 skill 系统
- `nocode_agent/config.yaml` — 新增 skills 配置

### Phase 2：Skill 调用（预计改动量：中）

- 实现 `SkillExpander`（shell 预执行 + 变量替换）
- 实现 `invoke_skill` LangChain tool
- 实现 `SkillPermissionManager`（allowed-tools 权限控制）
- 废弃旧版 `skills.py`、`skill_tools.py`、`skill_tool_registry.py`

**涉及文件：**
- `nocode_agent/skills/expander.py` — 新文件
- `nocode_agent/skills/permissions.py` — 新文件
- `nocode_agent/skills/tool.py` — 新文件
- `nocode_agent/agent.py` — 替换 skill tools

### Phase 3：压缩恢复 + 动态发现（预计改动量：小）

- 实现 `InvokedSkillStore` 和压缩后恢复
- 实现动态 skill 发现（文件操作时扫描）
- 与上下文压缩系统集成

**涉及文件：**
- `nocode_agent/skills/invoked_store.py` — 新文件
- `nocode_agent/post_compact.py` — 集成 skill 恢复
- `nocode_agent/tools.py` — 文件操作后触发动态发现

### Phase 4：内置 Skill + 迁移（预计改动量：中）

- 编写内置 skill（commit、review-pr 等）
- 编写迁移文档
- 清理旧版 skill 代码

**涉及文件：**
- `nocode_agent/bundled_skills/` — 新目录
- `nocode_agent/skills.py` — 删除
- `nocode_agent/skill_tools.py` — 删除
- `nocode_agent/skill_tool_registry.py` — 删除

---

## 10. 关键设计决策

| 决策 | 选项 | 选择 | 原因 |
|------|------|------|------|
| SKILL.md 格式 | 自定义中文格式 / YAML frontmatter | YAML frontmatter | 标准化，兼容社区生态 |
| 列表注入方式 | 系统提示词 / 独立消息 | 独立消息 | 不永久占用系统提示词空间，压缩时可剥离 |
| Shell 命令安全 | 无限制 / 白名单 / 沙箱 | 权限继承 | 继承调用者的 Bash 权限，不过度设计 |
| Skill 调用方式 | 模型调用 / 用户调用 / 两者 | 两者 | 与 Claude Code 一致，`disable-model-invocation` 可选关闭 |
| 动态发现时机 | 只启动时 / 文件操作时 / 定时 | 启动时 + 文件操作时 | 兼顾简洁和灵活性 |
| Fork 执行 | 支持 / 不支持 | 暂不支持 | Phase 1 简化实现，后续按需添加 |
