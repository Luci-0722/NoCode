"""模型客户端选择回归测试。"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from nocode_agent.agent import _build_model


def test_build_model_uses_anthropic_client_for_anthropic_base_url() -> None:
    model = _build_model(
        api_key="test-key",
        model="claude-sonnet-4-5",
        base_url="https://coding.dashscope.aliyuncs.com/apps/anthropic",
        temperature=0.7,
        max_tokens=128,
        request_timeout=30.0,
    )

    assert isinstance(model, ChatAnthropic)
    assert model.anthropic_api_url == "https://coding.dashscope.aliyuncs.com/apps/anthropic"


def test_build_model_uses_openai_client_for_openai_compatible_base_url() -> None:
    model = _build_model(
        api_key="test-key",
        model="glm-5",
        base_url="https://coding.dashscope.aliyuncs.com/v1",
        temperature=0.7,
        max_tokens=128,
        request_timeout=30.0,
    )

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "https://coding.dashscope.aliyuncs.com/v1"


def test_build_model_normalizes_openai_endpoint_url() -> None:
    model = _build_model(
        api_key="test-key",
        model="glm-5",
        base_url="https://coding.dashscope.aliyuncs.com/v1/chat/completions",
        temperature=0.7,
        max_tokens=128,
        request_timeout=30.0,
    )

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "https://coding.dashscope.aliyuncs.com/v1"
