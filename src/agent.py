"""Agent 构建：LangGraph StateGraph + 自定义压缩策略。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.compression import CompressionStrategy, ContextCompressor
from src.tools import bash, read

SYSTEM_PROMPT = """你是一个名叫**小智**的 AI 伙伴，性格开朗、聪明、乐于助人。

你会记住用户告诉你的事情，并主动提供帮助。
回答要简洁友好，像一个好朋友。"""


def create_bf_agent(
    api_key: str,
    model: str = "glm-4-flash",
    base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    compression: dict | None = None,
):
    """创建基于 LangGraph 的 Best Friend Agent。

    Args:
        api_key: 智谱 AI API Key。
        model: 模型名称，默认 glm-4-flash。
        base_url: API 基础 URL。
        max_tokens: 最大生成 token 数。
        temperature: 温度参数。
        compression: 压缩策略配置字典，包含：
            - trigger_tokens: 触发阈值（默认 8000）
            - keep_recent: 保留最近消息数（默认 10）
            - compressible_tools: 可压缩工具名列表
    """
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    tools = [read, bash]
    llm_with_tools = llm.bind_tools(tools)
    tool_node = ToolNode(tools)

    # 构建压缩策略
    comp_cfg = compression or {}
    strategy = CompressionStrategy(
        trigger_tokens=comp_cfg.get("trigger_tokens", 8000),
        keep_recent=comp_cfg.get("keep_recent", 10),
        compressible_tools=tuple(comp_cfg.get("compressible_tools", ("read", "bash", "glob", "grep"))),
    )
    compressor = ContextCompressor(strategy)

    async def call_model(state: MessagesState):
        """模型调用节点：压缩消息后发送给 LLM。"""
        compressed = compressor.compress(state["messages"])
        response = await llm_with_tools.ainvoke(compressed)
        return {"messages": [response]}

    def should_continue(state: MessagesState):
        """条件边：判断是否需要调用工具。"""
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    # 构建图
    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")

    return graph.compile(checkpointer=InMemorySaver())
