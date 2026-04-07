"""persistence 回归测试。"""

from __future__ import annotations

import sqlite3
import sys
import types

from nocode_agent.persistence import load_thread_messages


class FakeHumanMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeAIMessage:
    def __init__(self, text: str, tool_calls: list[dict]) -> None:
        self.text = text
        self.content = text
        self.tool_calls = tool_calls


class FakeSystemMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeToolMessage:
    def __init__(self, content: str, tool_call_id: str, name: str = "") -> None:
        self.content = content
        self.tool_call_id = tool_call_id
        self.name = name


class FakeSqliteSaver:
    def __init__(self, _db) -> None:
        pass

    def setup(self) -> None:
        return None

    def get(self, _config):
        return {
            "channel_values": {
                "messages": [
                    FakeSystemMessage("sys"),
                    FakeHumanMessage("hello"),
                    FakeAIMessage(
                        "我会调用工具",
                        [
                            {
                                "id": "call_1",
                                "name": "read_file",
                                "args": {"path": "README.md"},
                            }
                        ],
                    ),
                    FakeToolMessage("file content", "call_1", "read_file"),
                    FakeAIMessage("done", []),
                ]
            }
        }


def test_load_thread_messages_restores_tool_records(tmp_path, monkeypatch):
    # 创建空 sqlite 文件，让 Path.exists 返回 True。
    db_path = tmp_path / "cp.sqlite"
    db_path.write_text("", encoding="utf-8")

    # 注入测试桩模块，避免依赖真实 langgraph/langchain。
    fake_sqlite_mod = types.ModuleType("langgraph.checkpoint.sqlite")
    fake_sqlite_mod.SqliteSaver = FakeSqliteSaver
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.sqlite", fake_sqlite_mod)

    fake_msg_mod = types.ModuleType("langchain_core.messages")
    fake_msg_mod.AIMessage = FakeAIMessage
    fake_msg_mod.HumanMessage = FakeHumanMessage
    fake_msg_mod.SystemMessage = FakeSystemMessage
    fake_msg_mod.ToolMessage = FakeToolMessage
    monkeypatch.setitem(sys.modules, "langchain_core.messages", fake_msg_mod)

    # 使用内存连接替代真实 sqlite 文件解析。
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda _p: real_connect(":memory:"))

    events = load_thread_messages(str(db_path), thread_id="t1")

    assert events[0] == {"role": "system", "content": "sys"}
    assert events[1] == {"role": "user", "content": "hello"}
    assert events[2] == {"role": "assistant", "content": "我会调用工具"}
    assert events[3]["kind"] == "tool"
    assert events[3]["name"] == "read_file"
    assert events[3]["args"] == {"path": "README.md"}
    assert events[3]["tool_call_id"] == "call_1"
    assert events[3]["output"] == "file content"
    assert events[4] == {"role": "assistant", "content": "done"}
