"""具体内置工具实现。

抽象基类和注册中心在 src.core.tools 中，这里只放具体工具。
"""

from src.tools.bash import BashTool

__all__ = ["BashTool"]
