"""Short-term memory: manages conversation context within a session."""

from __future__ import annotations

from collections import deque

from src.types import Message


class ShortTermMemory:
    def __init__(self, max_messages: int = 50):
        self.max_messages = max_messages
        self._messages: deque[Message] = deque(maxlen=max_messages)

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def add(self, message: Message) -> None:
        self._messages.append(message)

    def add_user(self, content: str) -> None:
        self.add(Message(role="user", content=content))

    def add_assistant(self, content: str, tool_calls: list | None = None) -> None:
        self.add(Message(role="assistant", content=content, tool_calls=tool_calls))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.add(Message(role="tool", content=content,
                         tool_call_id=tool_call_id, name=name))

    def clear(self) -> None:
        self._messages.clear()

    def format_context(self) -> list[Message]:
        """Return all messages as context for LLM."""
        return self.messages
