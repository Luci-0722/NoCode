from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")


def _resolve_proxy_section(config: dict[str, Any]) -> tuple[str, Any]:
    """提取代理主配置，兼容字符串和对象两种写法。"""
    proxy_value = config.get("proxy", "")
    if isinstance(proxy_value, dict):
        proxy_url = str(
            proxy_value.get("url")
            or proxy_value.get("value")
            or proxy_value.get("http")
            or ""
        ).strip()
        return proxy_url, proxy_value
    return str(proxy_value or "").strip(), {}


def _split_no_proxy_value(raw_value: Any) -> list[str]:
    """把 no_proxy 输入统一展开为字符串列表。"""
    if raw_value is None:
        return []

    if isinstance(raw_value, str):
        return [item.strip() for item in raw_value.split(",") if item.strip()]

    if isinstance(raw_value, (list, tuple, set)):
        items: list[str] = []
        for item in raw_value:
            items.extend(_split_no_proxy_value(item))
        return items

    return [str(raw_value).strip()] if str(raw_value).strip() else []


def load_config(config_path: str | None = None) -> dict[str, Any]:
    resolved = (
        config_path
        or os.environ.get("NOCODE_AGENT_CONFIG")
        or os.environ.get("NOCODE_CONFIG")
        or os.environ.get("BF_CONFIG")
        or str(DEFAULT_CONFIG_PATH)
    )
    try:
        with open(resolved, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except FileNotFoundError:
        logger.debug("Config file not found: %s", resolved)
        return {}


def _is_local_base_url(base_url: str) -> bool:
    """判断模型服务是否指向本机，便于兼容 Ollama 这类本地服务。"""
    raw = normalize_model_base_url(base_url)
    if not raw:
        return False
    host = (urlparse(raw).hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0"}


def normalize_model_base_url(base_url: str) -> str:
    """把误填的完整接口地址裁剪成客户端需要的基地址。"""
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return ""

    suffixes = (
        "/chat/completions",
        "/responses",
        "/v1/messages",
        "/messages",
    )
    lowered = raw.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix):
            return raw[: -len(suffix)] or raw
    return raw


def _provider_from_base_url(base_url: str) -> str:
    """根据 base_url 推断当前模型供应商。"""
    parsed = urlparse(normalize_model_base_url(base_url))
    host = (parsed.hostname or "").strip().lower()
    path = (parsed.path or "").strip().lower()
    if not host:
        return ""
    if "anthropic.com" in host:
        return "anthropic"
    if "dashscope.aliyuncs.com" in host and (
        "claude-code-proxy" in path or "/apps/anthropic" in path
    ):
        return "anthropic"
    if "dashscope.aliyuncs.com" in host:
        return "dashscope"
    if "bigmodel.cn" in host:
        return "zhipu"
    if "openai.com" in host:
        return "openai"
    return ""


def resolve_model_provider(config: dict[str, Any]) -> str:
    """解析当前配置对应的模型协议供应商。"""
    return _provider_from_base_url(str(config.get("base_url", "") or ""))


def resolve_api_key(config: dict[str, Any]) -> str:
    """统一解析模型 API Key。

    优先读取与当前供应商匹配的专用环境变量。显式配置的 api_key 优先于
    通用 NOCODE_API_KEY，避免启动脚本注入的旧兼容变量覆盖用户当前配置。
    如果目标是本地模型服务且未提供 key，则返回占位值，满足 OpenAI
    兼容客户端的参数要求。
    """
    provider = resolve_model_provider(config)
    primary_env_candidates: list[str] = []
    fallback_env_candidates: list[str] = ["NOCODE_API_KEY"]
    if provider == "anthropic":
        primary_env_candidates.extend(["ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "BAILIAN_API_KEY"])
    elif provider == "dashscope":
        primary_env_candidates.extend(["DASHSCOPE_API_KEY", "BAILIAN_API_KEY"])
    elif provider == "zhipu":
        primary_env_candidates.append("ZHIPU_API_KEY")
    elif provider == "openai":
        primary_env_candidates.append("OPENAI_API_KEY")
    else:
        primary_env_candidates.extend(
            [
                "DASHSCOPE_API_KEY",
                "BAILIAN_API_KEY",
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
                "ZHIPU_API_KEY",
                "OLLAMA_API_KEY",
            ]
        )

    for env_name in primary_env_candidates:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value

    config_value = str(config.get("api_key", "") or "").strip()
    if config_value:
        return config_value

    for env_name in fallback_env_candidates:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value

    if _is_local_base_url(str(config.get("base_url", "") or "")):
        # 本地 Ollama 默认不校验真实密钥，这里给兼容客户端一个占位值。
        return "ollama"

    return ""


def resolve_proxy(config: dict[str, Any]) -> str:
    """统一解析 HTTP 代理地址。

    优先级: 环境变量 NOCODE_PROXY > 配置文件 proxy 字段 > 环境变量 OPENAI_PROXY
    返回空字符串表示不使用代理。
    """
    for env_name in ("NOCODE_PROXY", "OPENAI_PROXY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value

    config_value, _ = _resolve_proxy_section(config)
    if config_value:
        return config_value

    return ""


def resolve_no_proxy(config: dict[str, Any]) -> list[str]:
    """统一解析不走代理的主机列表。

    优先级: 环境变量 NOCODE_NO_PROXY > 配置文件 no_proxy / proxy.no_proxy > 环境变量 NO_PROXY
    返回空列表表示没有显式绕过规则。
    """
    env_value = os.environ.get("NOCODE_NO_PROXY", "").strip()
    if env_value:
        return _split_no_proxy_value(env_value)

    _, proxy_section = _resolve_proxy_section(config)
    config_value = config.get("no_proxy")
    if config_value is None and isinstance(proxy_section, dict):
        config_value = proxy_section.get("no_proxy")

    resolved = _split_no_proxy_value(config_value)
    if resolved:
        return resolved

    fallback_value = os.environ.get("NO_PROXY", "").strip()
    if fallback_value:
        return _split_no_proxy_value(fallback_value)

    return []


def resolve_request_timeout(config: dict[str, Any], default: float = 90.0) -> float:
    """统一解析模型请求超时时间（秒）。"""
    raw_value = config.get("request_timeout", default)
    try:
        timeout = float(raw_value)
    except (TypeError, ValueError):
        logger.warning("Invalid request_timeout=%r, fallback to %.1f", raw_value, default)
        return default
    if timeout <= 0:
        logger.warning("Non-positive request_timeout=%r, fallback to %.1f", raw_value, default)
        return default
    return timeout
