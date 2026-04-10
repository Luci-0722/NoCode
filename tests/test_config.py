"""config 代理解析回归测试。"""

from __future__ import annotations

from nocode_agent.agent import _build_no_proxy_mounts
from nocode_agent.config import (
    resolve_api_key,
    resolve_no_proxy,
    resolve_proxy,
    resolve_request_timeout,
)


def test_resolve_proxy_supports_proxy_object() -> None:
    config = {
        "proxy": {
            "url": "http://127.0.0.1:7890",
            "no_proxy": ["localhost", ".internal.company.com"],
        }
    }

    assert resolve_proxy(config) == "http://127.0.0.1:7890"
    assert resolve_no_proxy(config) == ["localhost", ".internal.company.com"]


def test_resolve_no_proxy_supports_top_level_string(monkeypatch) -> None:
    monkeypatch.delenv("NOCODE_NO_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    config = {"no_proxy": "localhost,127.0.0.1,.example.com"}

    assert resolve_no_proxy(config) == ["localhost", "127.0.0.1", ".example.com"]


def test_resolve_no_proxy_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("NOCODE_NO_PROXY", "localhost,.env.example.com")

    config = {"no_proxy": ["127.0.0.1"]}

    assert resolve_no_proxy(config) == ["localhost", ".env.example.com"]


def test_build_no_proxy_mounts_maps_hosts_to_httpx_patterns() -> None:
    mounts = _build_no_proxy_mounts(
        ["localhost", "127.0.0.1", ".example.com", "10.0.0.0/8", "::1", "https://internal.local"]
    )

    assert mounts == {
        "all://localhost": None,
        "all://127.0.0.1": None,
        "all://*.example.com": None,
        "all://10.0.0.0/8": None,
        "all://[::1]": None,
        "https://internal.local": None,
    }


def test_build_no_proxy_mounts_supports_wildcard_disable_proxy() -> None:
    assert _build_no_proxy_mounts(["*"]) == {"all://": None}


def test_resolve_request_timeout_supports_positive_number() -> None:
    assert resolve_request_timeout({"request_timeout": 45}) == 45.0


def test_resolve_request_timeout_falls_back_for_invalid_value() -> None:
    assert resolve_request_timeout({"request_timeout": "invalid"}, default=90.0) == 90.0


def test_resolve_api_key_supports_dashscope_env(monkeypatch) -> None:
    monkeypatch.delenv("NOCODE_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")

    assert resolve_api_key({"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}) == "dashscope-secret"


def test_resolve_api_key_prefers_config_for_dashscope_over_unrelated_zhipu_env(monkeypatch) -> None:
    monkeypatch.delenv("NOCODE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-secret")

    assert resolve_api_key(
        {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "dashscope-config-secret",
        }
    ) == "dashscope-config-secret"


def test_resolve_api_key_supports_anthropic_env(monkeypatch) -> None:
    monkeypatch.delenv("NOCODE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")

    assert resolve_api_key({"base_url": "https://api.anthropic.com/v1"}) == "anthropic-secret"


def test_resolve_api_key_supports_dashscope_claude_proxy_env(monkeypatch) -> None:
    monkeypatch.delenv("NOCODE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")

    assert (
        resolve_api_key({"base_url": "https://dashscope.aliyuncs.com/api/v2/apps/claude-code-proxy"})
        == "dashscope-secret"
    )
