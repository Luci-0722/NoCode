from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")


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
    raw = str(base_url or "").strip()
    if not raw:
        return False
    host = (urlparse(raw).hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0"}


def resolve_api_key(config: dict[str, Any]) -> str:
    """统一解析模型 API Key。

    优先读取更通用的环境变量；如果目标是本地模型服务且未提供 key，
    则返回占位值，满足 OpenAI 兼容客户端的参数要求。
    """
    for env_name in ("NOCODE_API_KEY", "OLLAMA_API_KEY", "OPENAI_API_KEY", "ZHIPU_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value

    config_value = str(config.get("api_key", "") or "").strip()
    if config_value:
        return config_value

    if _is_local_base_url(str(config.get("base_url", "") or "")):
        # 本地 Ollama 默认不校验真实密钥，这里给兼容客户端一个占位值。
        return "ollama"

    return ""
