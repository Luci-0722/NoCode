"""Token 估算工具。

保守估算消息列表的 token 数量。
中文约 3 字符/token，"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """保守估算消息列表的 token 数量。

    遍历所有消息，将 content 视为字符串后按 3 字符/token 估算。
    """
    total = 0
    for msg in messages:
        total += estimate_message_tokens(msg)
    return total


def estimate_message_tokens(msg: BaseMessage) -> int:
    """估算单条消息的 token 数量。"""
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        # 多模态 content blocks
        text = "\n".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b)
            for b in content
        )
    else:
        text = str(content)

    # 中文约 3 字符/token，保守取 3
    return max(1, len(text) // 3)
