"""Browser skill: 基于 Playwright 的浏览器自动化操作。

支持导航、截图、点击、填写表单、执行 JavaScript 等操作。
所有操作通过统一的 "browser" skill 入口调用，使用 action 参数区分不同操作。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.skills.registry import Skill, SkillRegistry

logger = logging.getLogger(__name__)

# 全局浏览器实例管理
_browser = None
_page = None
_context = None
_playwright = None


async def _ensure_browser(**kwargs: Any) -> None:
    """确保浏览器实例已启动，支持懒加载。"""
    global _browser, _page, _context, _playwright

    if _browser and _browser.is_connected():
        return

    headless = kwargs.get("headless", True)

    from playwright.async_api import async_playwright

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=headless,
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )
    _context = await _browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    _page = await _context.new_page()
    logger.info("浏览器实例已启动 (headless=%s)", headless)


async def _close_browser() -> None:
    """关闭浏览器实例。"""
    global _browser, _page, _context, _playwright

    try:
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
    except Exception as e:
        logger.warning("关闭浏览器时出错: %s", e)
    finally:
        _browser = _page = _context = _playwright = None


async def browser_navigate(**kwargs: Any) -> str:
    """导航到指定 URL。"""
    url = kwargs.get("url", "")
    if not url:
        return "Error: 'url' 参数必填。"

    # 自动补全协议
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        await _ensure_browser(**kwargs)
        await _page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = await _page.title()
        return f"已导航到: {url}\n页面标题: {title}"
    except Exception as e:
        return f"Error: 导航失败 - {e}"


async def browser_screenshot(**kwargs: Any) -> str:
    """截取当前页面截图。"""
    save_path = kwargs.get("path", "")
    full_page = kwargs.get("full_page", False)

    try:
        await _ensure_browser(**kwargs)

        # 如果没有指定路径，生成默认路径
        if not save_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = f"screenshots/screenshot_{timestamp}.png"

        screenshot_path = Path(save_path)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)

        await _page.screenshot(path=str(screenshot_path), full_page=bool(full_page))
        return f"截图已保存: {screenshot_path.absolute()}"
    except Exception as e:
        return f"Error: 截图失败 - {e}"


async def browser_click(**kwargs: Any) -> str:
    """点击页面元素。"""
    selector = kwargs.get("selector", "")
    if not selector:
        return "Error: 'selector' 参数必填（CSS 选择器）。"

    try:
        await _ensure_browser(**kwargs)
        await _page.click(selector, timeout=10000)
        return f"已点击元素: {selector}"
    except Exception as e:
        return f"Error: 点击失败 - {e}"


async def browser_fill(**kwargs: Any) -> str:
    """填写表单输入框。"""
    selector = kwargs.get("selector", "")
    value = kwargs.get("value", "")
    if not selector:
        return "Error: 'selector' 参数必填（CSS 选择器）。"
    if not value and value != "":
        return "Error: 'value' 参数必填。"

    try:
        await _ensure_browser(**kwargs)
        await _page.fill(selector, str(value), timeout=10000)
        return f"已在 {selector} 中填入内容。"
    except Exception as e:
        return f"Error: 填写失败 - {e}"


async def browser_evaluate(**kwargs: Any) -> str:
    """执行 JavaScript 代码。"""
    script = kwargs.get("script", "")
    if not script:
        return "Error: 'script' 参数必填（JavaScript 代码）。"

    try:
        await _ensure_browser(**kwargs)
        result = await _page.evaluate(script)
        return f"执行结果:\n{result}"
    except Exception as e:
        return f"Error: JavaScript 执行失败 - {e}"


async def browser_close(**kwargs: Any) -> str:
    """关闭浏览器实例。"""
    await _close_browser()
    return "浏览器已关闭。"


# action 到处理函数的映射
_ACTION_HANDLERS = {
    "navigate": browser_navigate,
    "screenshot": browser_screenshot,
    "click": browser_click,
    "fill": browser_fill,
    "evaluate": browser_evaluate,
    "close": browser_close,
}


async def browser_handler(**kwargs: Any) -> str:
    """统一的浏览器操作入口，通过 action 参数分发到不同的处理函数。"""
    action = kwargs.get("action", "")

    if not action:
        return (
            "Error: 'action' 参数必填。"
            f"\n可用操作: {', '.join(_ACTION_HANDLERS.keys())}"
        )

    handler = _ACTION_HANDLERS.get(action)
    if not handler:
        return (
            f"Error: 未知操作 '{action}'。"
            f"\n可用操作: {', '.join(_ACTION_HANDLERS.keys())}"
        )

    return await handler(**kwargs)


def register(registry: SkillRegistry) -> None:
    """注册浏览器自动化 skill。"""
    registry.register(Skill(
        name="browser",
        description=(
            "浏览器自动化操作。通过 action 参数指定具体操作："
            "navigate(导航到URL)、screenshot(截图)、click(点击元素)、"
            "fill(填写表单)、evaluate(执行JavaScript)、close(关闭浏览器)。"
            "所有操作基于 Playwright + Chromium 引擎，使用 CSS 选择器定位元素。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "要执行的操作类型",
                    "enum": list(_ACTION_HANDLERS.keys()),
                },
                "url": {
                    "type": "string",
                    "description": "要导航到的 URL（仅 navigate 操作需要）",
                },
                "path": {
                    "type": "string",
                    "description": "截图保存路径（仅 screenshot 操作，默认自动生成）",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "是否截取完整页面（仅 screenshot 操作，默认 false）",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS 选择器，用于定位页面元素（click/fill 操作需要）",
                },
                "value": {
                    "type": "string",
                    "description": "要填入输入框的值（仅 fill 操作需要）",
                },
                "script": {
                    "type": "string",
                    "description": "要执行的 JavaScript 代码（仅 evaluate 操作需要）",
                },
                "headless": {
                    "type": "boolean",
                    "description": "是否使用无头模式（默认 true）",
                },
            },
            "required": ["action"],
        },
        handler=browser_handler,
    ))
