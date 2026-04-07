"""Auto-Compact 总结 Prompt。

两阶段结构（与 Claude Code 一致）:
  <analysis> — 模型内部草稿，最终从上下文中剥离
  <summary>  — 结构化总结，保留在上下文中

禁止工具调用：总结 turn 不允许调用任何工具，否则浪费唯一的 turn。
"""

SUMMARY_SYSTEM_PROMPT = """你是一个专门总结对话的助手。你的任务是生成一份结构化的对话摘要。

关键规则:
- 不要调用任何工具（Read、Bash、Grep 等）
- 你的整个输出必须是纯文本: 一个 <analysis> 块后跟一个 <summary> 块
- 工具调用会被拒绝，你将无法完成任务
"""

SUMMARY_USER_PROMPT = """请对以上对话进行总结。按以下结构输出:

<analysis>
内部梳理: 回顾对话的关键信息、用户意图、技术决策。
此部分不会展示给用户，仅用于组织你的思路。
</analysis>

<summary>
## Primary Request and Intent
用户的主要请求和意图（包括所有明确提出的请求）。

## Key Technical Concepts
对话中涉及的关键技术概念、框架和设计决策。

## Files and Code Sections
涉及的重要文件和代码片段。列出文件路径和关键代码（保留完整代码片段），以及每个文件的作用和重要性。

## Errors and Fixes
遇到的错误及修复方式，包含用户的反馈和纠正。

## Problem Solving
已解决的问题、正在进行的排查。

## All User Messages
所有非工具结果的用户消息（按时间顺序，保留原文）。

## Pending Tasks
未完成的任务和待办事项。

## Current Work
压缩前正在进行的具体工作（精确描述，包含文件路径和代码位置）。

## Optional Next Step
与最近工作直接相关的下一步（引用近期对话中的原文）。如果最后的任务已完成且没有明确的下一步，留空。
</summary>

再次提醒: 不要调用任何工具。只输出纯文本: <analysis> 块 + <summary> 块。"""

CONTINUATION_INSTRUCTION = """此会话从之前超出上下文长度的对话继续。以下摘要覆盖了对话的早期部分。

直接继续工作 — 不要确认摘要内容，不要复述发生了什么，不要以"我将继续"之类的开头。
从上次中断的地方直接恢复。"""


def format_summary_for_context(raw_summary: str) -> str:
    """后处理: 剥离 <analysis> 块，提取 <summary> 内容。"""
    import re

    # 剥离 <analysis>...</analysis>
    cleaned = re.sub(r"<analysis>[\s\S]*?</analysis>", "", raw_summary)

    # 提取 <summary>...</summary> 内容
    summary_match = re.search(r"<summary>([\s\S]*?)</summary>", cleaned)
    if summary_match:
        cleaned = summary_match.group(1).strip()

    # 去掉多余空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ─── Session Memory (Layer 2) ──────────────────────────────────


DEFAULT_SESSION_MEMORY_TEMPLATE = """\
# Session Title
_简短且具有辨识度的 5-10 词会话标题_

# Current State
_当前正在做什么？哪些任务尚未完成？_

# Task Specification
_用户要求构建什么？有哪些设计决策？_

# Files and Functions
_涉及的重要文件有哪些？简述各文件的作用_

# Errors & Corrections
_遇到的错误及修复方式_

# Key Results
_用户要求的特定输出或结论_

# Worklog
_逐步记录: 尝试了什么、做了什么_
"""

SESSION_MEMORY_UPDATE_PROMPT = """\
你是一个会话笔记提取器。你的任务是根据对话内容更新会话笔记文件。

## 规则

1. 你会看到当前笔记内容和最近的对话记录。
2. 请根据对话内容更新笔记的各个章节。
3. **绝对不要**修改章节标题（`# ...`）和斜体描述行（`_..._`），它们是固定模板结构。
4. 只修改斜体描述行**下方**的内容。
5. 不要新增章节。
6. 每个章节保持简洁，避免冗余。
7. 排除系统提示词、CLAUDE.md 条目和过去的会话摘要。
8. 直接输出更新后的完整笔记内容（纯 Markdown），不要用代码块包裹。

## 当前笔记内容

{current_notes}

---

请根据以上对话记录更新笔记。只输出更新后的完整 Markdown 内容。"""


SESSION_MEMORY_COMPACT_HEADER = (
    "此会话从之前超出上下文长度的对话继续。"
    "以下会话记忆覆盖了对话的早期部分。\n\n"
)
SESSION_MEMORY_EMPTY_NOTE = "（空）"
