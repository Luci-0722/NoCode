from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


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
        return {}
