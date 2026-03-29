"""提示词管理器：从模板目录加载提示词模板，支持变量插值。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import re

logger = logging.getLogger(__name__)

# 变量插值的正则模式，匹配 {{variable}} 语法
_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")

# 支持的模板文件扩展名
_TEMPLATE_EXTENSIONS = {".md", ".txt", ".prompt"}


class PromptManager:
    """从文件系统加载和管理提示词模板。

    支持简单的 {{variable}} 语法进行变量替换，
    不依赖 Jinja2，使用手动正则替换实现。
    模板文件推荐使用 Markdown 格式（.md）。
    """

    def __init__(self, prompts_dir: str = "prompts") -> None:
        """初始化提示词管理器。

        Args:
            prompts_dir: 提示词模板目录路径。
        """
        self.prompts_dir = Path(prompts_dir)
        self._templates: dict[str, str] = {}

        if self.prompts_dir.is_dir():
            self._load_all_templates()
            logger.info("已加载 %d 个提示词模板，目录: %s", len(self._templates), self.prompts_dir)
        else:
            logger.warning("提示词目录不存在: %s", self.prompts_dir)

    def _load_all_templates(self) -> None:
        """从目录中加载所有模板文件。"""
        for file_path in sorted(self.prompts_dir.iterdir()):
            if file_path.is_file() and file_path.suffix in _TEMPLATE_EXTENSIONS:
                name = file_path.stem
                content = file_path.read_text(encoding="utf-8")
                self._templates[name] = content
                logger.debug("加载模板: %s -> %s", name, file_path)

    def get_prompt(self, name: str, **kwargs: Any) -> str:
        """获取指定名称的提示词，并执行变量替换。

        Args:
            name: 模板名称（不含扩展名）。
            **kwargs: 用于替换模板中 {{variable}} 的键值对。

        Returns:
            渲染后的提示词字符串。

        Raises:
            KeyError: 模板中引用了未提供的变量。
            FileNotFoundError: 指定名称的模板不存在。
        """
        if name not in self._templates:
            raise FileNotFoundError(f"提示词模板不存在: {name}（目录: {self.prompts_dir}）")

        template = self._templates[name]

        def _replacer(match: re.Match) -> str:
            var_name = match.group(1)
            if var_name not in kwargs:
                raise KeyError(
                    f"模板 '{name}' 中引用了未提供的变量: {{{{{var_name}}}}}"
                )
            value = kwargs[var_name]
            return str(value) if value is not None else ""

        return _VAR_PATTERN.sub(_replacer, template)

    def has_prompt(self, name: str) -> bool:
        """检查指定名称的提示词模板是否存在。"""
        return name in self._templates

    def list_prompts(self) -> list[str]:
        """列出所有已加载的提示词模板名称。"""
        return sorted(self._templates.keys())

    def reload(self) -> None:
        """重新从磁盘加载所有模板。"""
        self._templates.clear()
        if self.prompts_dir.is_dir():
            self._load_all_templates()
            logger.info("重新加载 %d 个提示词模板", len(self._templates))
