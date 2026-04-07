"""Claude Code 风格核心工具集。"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
from html import unescape
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen
from pathlib import Path
from uuid import uuid4
from typing import Annotated, Any, Callable

from enum import Flag, auto

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 12_000
_TODO_STORE: list[str] = []


# ---------------------------------------------------------------------------
# Tool safety flags — mirrors Claude Code's Tool.ts isConcurrencySafe /
# isReadOnly / isDestructive with fail-closed defaults.
#
# Source: claude-code-analysis/src/Tool.ts:748-761
#   - isConcurrencySafe → false (assume not safe)
#   - isReadOnly         → false (assume writes)
#   - isDestructive      → false
#
# Per-tool overrides (from individual tool files):
#   FileReadTool.ts:373  GlobTool.ts:76   GrepTool.ts:183
# ---------------------------------------------------------------------------

class ToolSafety(Flag):
    READ_ONLY = auto()
    CONCURRENCY_SAFE = auto()
    DESTRUCTIVE = auto()


# Unlisted tools default to "unsafe" (fail-closed), matching Tool.ts:757-761.
_TOOL_SAFETY: dict[str, ToolSafety] = {
    # Concurrency-safe + read-only
    "read":              ToolSafety.READ_ONLY | ToolSafety.CONCURRENCY_SAFE,
    "glob":              ToolSafety.READ_ONLY | ToolSafety.CONCURRENCY_SAFE,
    "list_dir":          ToolSafety.READ_ONLY | ToolSafety.CONCURRENCY_SAFE,
    "grep":              ToolSafety.READ_ONLY | ToolSafety.CONCURRENCY_SAFE,
    "web_search":        ToolSafety.READ_ONLY | ToolSafety.CONCURRENCY_SAFE,
    "web_fetch":         ToolSafety.READ_ONLY | ToolSafety.CONCURRENCY_SAFE,
    "todo_read":         ToolSafety.READ_ONLY | ToolSafety.CONCURRENCY_SAFE,
    # Concurrency-safe but not read-only (cf. TaskUpdateTool, ConfigTool)
    "ask_user_question": ToolSafety.CONCURRENCY_SAFE,
    "todo_write":        ToolSafety.CONCURRENCY_SAFE,
    # Unsafe — use default fail-closed
    "write":             ToolSafety.DESTRUCTIVE,
    "edit":              ToolSafety.DESTRUCTIVE,
    "bash":              ToolSafety(0),
}


def is_concurrency_safe(tool_name: str) -> bool:
    """Check if a tool can be safely executed concurrently.

    Source: claude-code-analysis/src/Tool.ts:402  isConcurrencySafe()
    Default is ``False`` (fail-closed).
    """
    safety = _TOOL_SAFETY.get(tool_name)
    return safety is not None and ToolSafety.CONCURRENCY_SAFE in safety


def is_read_only(tool_name: str) -> bool:
    """Check if a tool is read-only (never modifies the filesystem).

    Source: claude-code-analysis/src/Tool.ts:404  isReadOnly()
    """
    safety = _TOOL_SAFETY.get(tool_name)
    return safety is not None and ToolSafety.READ_ONLY in safety


def _workspace_root() -> Path:
    return Path.cwd().resolve()


def _resolve_path(file_path: str) -> Path:
    root = _workspace_root()
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"路径 {path} 超出当前工作区 {root}")
    return path


def _trim_output(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n... (已截断，共 {len(text)} 字符)"


def _stringify_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)


def _extract_last_ai_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _stringify_message_content(message.content).strip()
            if text:
                return text
    return "子代理已完成任务，但没有返回文本结果。"


def _render_tool_output(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _trim_output(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
                parts.append(json.dumps(item, ensure_ascii=False))
                continue
            parts.append(str(item))
        return _trim_output("\n".join(part for part in parts if part).strip())
    return _trim_output(str(content))


class ReadInput(BaseModel):
    file_path: str = Field(description="工作区内的文件路径，支持相对路径。")
    offset: int = Field(default=1, ge=1, description="起始行号，从 1 开始。")
    limit: int = Field(default=2000, ge=1, le=4000, description="最多读取多少行。")


_FILE_UNCHANGED_STUB = (
    "文件自上次读取后未变更。之前的读取内容仍然有效，无需重复读取。"
)


@tool("read", args_schema=ReadInput)
def read_file(file_path: str, offset: int = 1, limit: int = 2000) -> str:
    """读取文件内容并返回带行号的文本。"""
    from nocode_agent.file_state import get_file_state_cache

    try:
        path = _resolve_path(file_path)
    except ValueError as error:
        return f"错误：{error}"

    # ── 未变更检测：相同路径 + 相同范围 + mtime 未变 → 返回 stub ──
    cache = get_file_state_cache()
    state = cache.get(path)
    if state is not None and state.is_mtime_valid(path):
        # 如果之前读的是完整文件（offset=1, limit 覆盖全部行），且这次请求的范围相同或更小
        # 直接返回 stub 节省 token
        try:
            total_lines = len(path.read_text(encoding="utf-8").splitlines())
            start = offset - 1
            end = min(total_lines, start + limit)
            is_full_read = (start == 0 and end >= total_lines)
            if is_full_read:
                return _FILE_UNCHANGED_STUB
        except Exception:
            pass  # 读取失败就继续正常读取

    # ── 正常读取 ─────────────────────────────────────────────────
    try:
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
    except FileNotFoundError:
        return f"错误：文件不存在: {file_path}"
    except Exception as error:
        return f"错误：读取文件失败: {error}"

    start = offset - 1
    end = min(len(lines), start + limit)
    if start >= len(lines):
        return f"错误：起始行号 {offset} 超出总行数 {len(lines)}。"

    rendered = [f"{index:>6}\t{line}" for index, line in enumerate(lines[start:end], start=offset)]
    suffix = ""
    if start > 0 or end < len(lines):
        suffix = f"\n\n[显示第 {offset}-{end} 行，共 {len(lines)} 行]"

    # 写入缓存
    cache.set(path, content)

    return _trim_output("\n".join(rendered) + suffix)


class WriteInput(BaseModel):
    file_path: str = Field(description="工作区内的文件路径，支持相对路径。")
    content: str = Field(description="完整文件内容。")


@tool("write", args_schema=WriteInput)
def write_file(file_path: str, content: str) -> str:
    """覆盖写入文件。如果是已有文件，必须先使用 read 工具读取过才能写入。"""
    from nocode_agent.file_state import get_file_state_cache

    logger.info("write: %s (%d chars)", file_path, len(content))
    try:
        path = _resolve_path(file_path)
    except ValueError as error:
        return f"错误：{error}"

    # 前置验证：现有文件必须先 read
    cache = get_file_state_cache()
    if path.exists():
        if not cache.has_valid_read(path):
            return (
                "错误：必须先使用 read 工具读取此文件，然后才能写入。"
                "（此检查防止在不了解文件内容的情况下意外覆盖。）"
            )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        # 写入成功后更新缓存
        cache.set(path, content)
        return f"已写入 {path}"
    except Exception as error:
        return f"错误：写入失败: {error}"


class EditInput(BaseModel):
    file_path: str = Field(description="工作区内的文件路径。")
    old_text: str = Field(description="要替换的原始文本。")
    new_text: str = Field(description="替换后的文本。")
    replace_all: bool = Field(default=False, description="是否替换全部匹配。")


@tool("edit", args_schema=EditInput)
def edit_file(file_path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    """基于精确文本匹配编辑文件。必须先使用 read 工具读取此文件。"""
    from nocode_agent.file_state import get_file_state_cache

    logger.info("edit: %s (replace_all=%s)", file_path, replace_all)
    try:
        path = _resolve_path(file_path)
    except ValueError as error:
        return f"错误：{error}"

    # 前置验证：必须先 read，且文件自读取后未被外部修改
    cache = get_file_state_cache()
    if not cache.has_valid_read(path):
        if cache.get(path) is None:
            return (
                "错误：必须先使用 read 工具读取此文件，然后才能编辑。"
                "（此检查防止在不了解文件内容的情况下意外修改。）"
            )
        return (
            "错误：文件自上次读取后已被修改（mtime 不匹配），请重新使用 read 读取最新内容。"
        )

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as error:
        return f"错误：读取文件失败: {error}"

    occurrences = content.count(old_text)
    if occurrences == 0:
        return "错误：未找到要替换的文本。"
    if occurrences > 1 and not replace_all:
        return f"错误：命中 {occurrences} 处；如需全部替换，请设置 replace_all=true。"

    updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
    path.write_text(updated, encoding="utf-8")
    # 编辑成功后更新缓存
    cache.set(path, updated)
    return f"已更新 {path}，替换 {occurrences if replace_all else 1} 处。"


class GlobInput(BaseModel):
    pattern: str = Field(description="glob 模式，例如 `nocode_agent/**/*.py`。")


@tool("glob", args_schema=GlobInput)
def glob_search(pattern: str) -> str:
    """在工作区内执行 glob 搜索，返回按修改时间降序排列的文件路径（最近修改的排在前面）。"""
    root = _workspace_root()
    try:
        paths = [p for p in root.glob(pattern) if not p.is_dir()]
    except Exception:
        paths = []
    # 按修改时间降序排列（最近修改的文件排在前面）
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    matches = [str(p.relative_to(root)) for p in paths]
    if not matches:
        return "未找到匹配文件。"
    return _trim_output("\n".join(matches))


class ListDirInput(BaseModel):
    path: str = Field(default=".", description="要列出的目录，默认当前工作区。")
    recursive: bool = Field(default=False, description="是否递归列出子目录。")
    max_entries: int = Field(default=200, ge=1, le=5000, description="最多返回多少条。")


@tool("list_dir", args_schema=ListDirInput)
def list_dir(path: str = ".", recursive: bool = False, max_entries: int = 200) -> str:
    """列出目录内容。"""
    try:
        root = _resolve_path(path)
        iterator = root.rglob("*") if recursive else root.iterdir()
        entries: list[str] = []
        workspace = _workspace_root()
        for index, item in enumerate(sorted(iterator), start=1):
            rel = item.relative_to(workspace)
            suffix = "/" if item.is_dir() else ""
            entries.append(f"{rel}{suffix}")
            if index >= max_entries:
                entries.append(f"\n[结果已截断，最多 {max_entries} 条]")
                break
        if not entries:
            return "目录为空。"
        return _trim_output("\n".join(entries))
    except Exception as error:
        return f"错误：列目录失败: {error}"


class GrepInput(BaseModel):
    pattern: str = Field(description="正则或普通文本模式。")
    path: str = Field(default=".", description="搜索起点目录。")
    file_glob: str = Field(default="*", description="文件筛选 glob，例如 `*.py`。")
    output_mode: str = Field(
        default="content",
        description=(
            "输出模式："
            "content（默认，显示匹配行及行号）、"
            "files_with_matches（仅显示包含匹配的文件路径）、"
            "count（显示每个文件的匹配计数）。"
        ),
    )
    context_lines: int = Field(
        default=0, ge=0, le=10,
        description="上下文行数（前后各N行），仅 content 模式有效。",
    )
    max_matches: int = Field(default=200, ge=1, le=2000, description="最多返回多少条结果。")


# ── rg 二进制检测（启动时检查一次）────────────────────────────────
_rg_path: str | None = None


def _find_rg_binary() -> str | None:
    """查找可用的 rg 二进制。优先级：项目内置 > 系统 PATH。"""
    # 1. 项目内置 rg（nocode_agent/bin/rg-{platform}-{arch}）
    platform_key = f"{os.uname().sysname.lower()}-{os.uname().machine.lower()}"
    # 标准化架构名
    arch = os.uname().machine.lower()
    if arch in ("x86_64", "amd64"):
        arch = "x86_64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"
    platform_key = f"{os.uname().sysname.lower()}-{arch}"

    bundled = Path(__file__).parent / "bin" / f"rg-{platform_key}"
    if bundled.exists() and os.access(str(bundled), os.X_OK):
        return str(bundled)

    # 2. 系统 PATH 中的 rg
    system_rg = shutil.which("rg")
    if system_rg:
        return system_rg

    return None


def _get_rg_path() -> str | None:
    global _rg_path
    if _rg_path is None:
        _rg_path = _find_rg_binary()
    return _rg_path


def _grep_with_rg(
    pattern: str,
    base: Path,
    file_glob: str,
    output_mode: str,
    context_lines: int,
    max_matches: int,
) -> str | None:
    """用 ripgrep 执行搜索。返回结果字符串，或 None 表示 rg 不可用/出错。"""
    rg = _get_rg_path()
    if not rg:
        return None

    cmd = [rg, "--no-config", "--no-ignore-vcs"]
    workspace = _workspace_root()
    cwd = str(base)

    if output_mode == "files_with_matches":
        cmd += ["--files-with-matches", "--max-count", str(max_matches)]
    elif output_mode == "count":
        cmd += ["--count", "--max-count", str(max_matches)]
    else:
        # content 模式
        cmd += ["--line-number", "--max-count", str(max_matches)]
        if context_lines > 0:
            cmd += [f"--context={context_lines}"]

    # glob 过滤
    if file_glob and file_glob != "*":
        cmd += ["--glob", file_glob]

    cmd.append(pattern)
    cmd.append(cwd)

    try:
        proc = asyncio.run(_run_rg(cmd))
    except Exception:
        return None

    if not proc:
        return None

    stdout, stderr, returncode = proc
    # rg 返回 1 表示无匹配，返回 2+ 表示错误
    if returncode == 1:
        return "未找到匹配内容。"
    if returncode >= 2:
        # rg 出错，fallback 到 Python
        return None

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return "未找到匹配内容。"

    # content 模式下把绝对路径转为相对路径
    if output_mode == "content":
        lines = text.splitlines()
        converted = []
        for line in lines:
            # rg 输出格式: /abs/path/to/file:line_no:content 或 /abs/path/to/file-line_no-content (context)
            for prefix_sep in [":", "-"]:
                idx = line.find(prefix_sep)
                if idx > 0:
                    abs_path = line[:idx]
                    try:
                        rel = str(Path(abs_path).relative_to(workspace))
                    except ValueError:
                        rel = abs_path
                    converted.append(rel + line[idx:])
                    break
            else:
                converted.append(line)
        text = "\n".join(converted)
    elif output_mode in ("files_with_matches", "count"):
        # 把绝对路径转为相对路径
        lines = text.splitlines()
        converted = []
        for line in lines:
            for sep in [":", "\n"]:
                idx = line.find(sep)
                if idx > 0:
                    abs_path = line[:idx]
                    try:
                        rel = str(Path(abs_path).relative_to(workspace))
                    except ValueError:
                        rel = abs_path
                    converted.append(rel + line[idx:])
                    break
            else:
                try:
                    converted.append(str(Path(line).relative_to(workspace)))
                except ValueError:
                    converted.append(line)
        text = "\n".join(converted)

    return _trim_output(text)


async def _run_rg(cmd: list[str]) -> tuple[bytes, bytes, int] | None:
    """异步运行 rg 命令。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout, stderr, proc.returncode or 0
    except Exception:
        return None


def _grep_with_python(
    pattern: str,
    base: Path,
    file_glob: str,
    output_mode: str,
    context_lines: int,
    max_matches: int,
) -> str:
    """纯 Python 的 grep 实现（rg 不可用时的 fallback）。"""
    try:
        regex = re.compile(pattern)
    except re.error as error:
        return f"错误：无效正则: {error}"

    workspace = _workspace_root()
    results: list[str] = []

    for file in sorted(base.rglob("*")):
        if not file.is_file():
            continue
        if not fnmatch.fnmatch(file.name, file_glob):
            continue
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, Exception):
            continue

        rel_path = str(file.relative_to(workspace))

        if output_mode == "files_with_matches":
            for line in lines:
                if regex.search(line):
                    results.append(rel_path)
                    break
            if len(results) >= max_matches:
                break
            continue

        if output_mode == "count":
            count = sum(1 for line in lines if regex.search(line))
            if count > 0:
                results.append(f"{rel_path}:{count}")
            if len(results) >= max_matches:
                break
            continue

        # content 模式
        for line_no, line in enumerate(lines, start=1):
            if regex.search(line):
                if context_lines > 0:
                    # 带上下文行
                    start = max(0, line_no - 1 - context_lines)
                    end = min(len(lines), line_no + context_lines)
                    snippet_lines = []
                    for i in range(start, end):
                        prefix = ">" if i == line_no - 1 else " "
                        snippet_lines.append(f"{prefix}{i + 1}:{lines[i]}")
                    results.append(f"{rel_path}-{line_no - context_lines}-{line_no + context_lines}:")
                    results.extend(f"  {l}" for l in snippet_lines)
                else:
                    results.append(f"{rel_path}:{line_no}: {line}")
                if len(results) >= max_matches:
                    return _trim_output("\n".join(results) + f"\n\n[结果已截断，最多 {max_matches} 条]")

    if not results:
        return "未找到匹配内容。"
    return _trim_output("\n".join(results))


@tool("grep", args_schema=GrepInput)
def grep_search(
    pattern: str,
    path: str = ".",
    file_glob: str = "*",
    output_mode: str = "content",
    context_lines: int = 0,
    max_matches: int = 200,
) -> str:
    """在工作区内搜索文本。优先使用 ripgrep (rg)，不可用时使用 Python 实现。

支持三种输出模式：
- content（默认）：显示匹配的文件名:行号: 内容
- files_with_matches：仅显示包含匹配的文件路径
- count：显示每个文件的匹配计数
"""
    try:
        base = _resolve_path(path)
    except Exception as error:
        return f"错误：路径无效: {error}"

    if output_mode not in ("content", "files_with_matches", "count"):
        return "错误：output_mode 必须是 content、files_with_matches 或 count。"

    # 优先尝试 rg
    result = _grep_with_rg(pattern, base, file_glob, output_mode, context_lines, max_matches)
    if result is not None:
        return result

    # fallback 到 Python
    return _grep_with_python(pattern, base, file_glob, output_mode, context_lines, max_matches)


class BashInput(BaseModel):
    command: str = Field(description="要执行的 shell 命令。")
    timeout: int = Field(default=30, ge=1, le=300, description="超时时间，单位秒。")


@tool("bash", args_schema=BashInput)
async def bash(command: str, timeout: int = 30) -> str:
    """在当前工作区执行 shell 命令并返回输出。"""
    logger.info("bash: %s (timeout=%ds)", command[:200], timeout)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(_workspace_root()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"错误：命令执行超时（{timeout} 秒）。"
    except Exception as error:
        return f"错误：命令执行失败: {error}"

    parts: list[str] = []
    if stdout:
        parts.append(stdout.decode("utf-8", errors="replace"))
    if stderr:
        parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")
    if not parts:
        parts.append("(无输出)")
    parts.append(f"[退出码: {proc.returncode}]")
    return _trim_output("\n".join(parts))


class WebSearchInput(BaseModel):
    query: str = Field(description="搜索关键词。")
    max_results: int = Field(default=5, ge=1, le=10, description="最多返回多少条搜索结果。")


def _http_get(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "nocode/0.1 (+https://local.workspace)",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@tool("web_search", args_schema=WebSearchInput)
def web_search(query: str, max_results: int = 5) -> str:
    """执行网页搜索并返回结果摘要。"""
    logger.info("web_search: %s (max=%d)", query, max_results)
    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        html = _http_get(search_url)
    except Exception as error:
        return f"错误：网页搜索失败: {error}"

    pattern = re.compile(
        r'(?s)<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>|'
        r'<a[^>]*class="result__a"[^>]*href="(?P<href2>[^"]+)"[^>]*>(?P<title2>.*?)</a>.*?'
        r'<div[^>]*class="result__snippet"[^>]*>(?P<snippet2>.*?)</div>'
    )

    results: list[str] = []
    for match in pattern.finditer(html):
        href = match.group("href") or match.group("href2")
        title = match.group("title") or match.group("title2")
        snippet = match.group("snippet") or match.group("snippet2") or ""
        if not href or not title:
            continue
        url = urljoin("https://duckduckgo.com", unescape(href))
        clean_title = _strip_html(title)
        clean_snippet = _strip_html(snippet)
        results.append(f"- {clean_title}\n  URL: {url}\n  摘要: {clean_snippet}")
        if len(results) >= max_results:
            break

    if not results:
        return "未解析到搜索结果。"
    return _trim_output("\n".join(results))


class WebFetchInput(BaseModel):
    url: str = Field(description="要抓取的网页 URL。")
    max_chars: int = Field(default=5000, ge=200, le=20000, description="最多返回多少字符。")


@tool("web_fetch", args_schema=WebFetchInput)
def web_fetch(url: str, max_chars: int = 5000) -> str:
    """抓取网页正文并转成纯文本。"""
    try:
        html = _http_get(url)
    except Exception as error:
        return f"错误：网页抓取失败: {error}"

    text = _strip_html(html)
    return _trim_output(text[:max_chars])


class AskUserQuestionInput(BaseModel):
    questions: list[dict] = Field(
        description=(
            "要问用户的问题列表。每个问题是一个 dict，包含："
            "question(必填，问题文本)、header(可选，短标签如 'Auth method')、"
            "options(可选，2-4个选项的list，每个选项有 label 和 description)、"
            "multiSelect(可选，bool，是否可多选)。"
        )
    )


@tool("ask_user_question", args_schema=AskUserQuestionInput)
def ask_user_question(questions: list[dict]) -> str:
    """向用户提出结构化问题以澄清需求或选择方案。

    只在需要用户输入来消除歧义时使用。不要用于：
    - 确认计划或请求许可（直接执行即可）
    - 询问"这个方案可以吗？"（直接开始做）
    - 收集本可以从代码中推断的信息（先搜索代码）
    """
    if not questions:
        return "错误：问题列表不能为空。"

    validated: list[dict] = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict) or not q.get("question"):
            return f"错误：第 {i + 1} 个问题缺少必填的 'question' 字段。"
        entry: dict = {"question": str(q["question"])}
        if q.get("header"):
            entry["header"] = str(q["header"])[:12]
        if isinstance(q.get("options"), list) and q["options"]:
            opts = []
            for opt in q["options"][:4]:
                if isinstance(opt, dict) and opt.get("label"):
                    opts.append({
                        "label": str(opt["label"]),
                        "description": str(opt.get("description", "")),
                    })
                elif isinstance(opt, str):
                    opts.append({"label": opt, "description": ""})
            if len(opts) >= 2:
                entry["options"] = opts
        if isinstance(q.get("multiSelect"), bool):
            entry["multiSelect"] = q["multiSelect"]
        validated.append(entry)

    return json.dumps({"type": "ask_user_question", "questions": validated}, ensure_ascii=False)


class TodoInput(BaseModel):
    todos: list[str] = Field(description="待办事项列表。")


@tool("todo_write", args_schema=TodoInput)
def todo_write(todos: list[str]) -> str:
    """更新当前会话的待办事项。"""
    global _TODO_STORE
    _TODO_STORE = [item.strip() for item in todos if item.strip()]
    if not _TODO_STORE:
        return "待办列表已清空。"
    return "待办列表已更新：\n" + "\n".join(f"- {item}" for item in _TODO_STORE)


@tool("todo_read")
def todo_read() -> str:
    """读取当前会话的待办事项。"""
    if not _TODO_STORE:
        return "当前没有待办事项。"
    return "\n".join(f"- {item}" for item in _TODO_STORE)


def build_core_tools() -> list:
    """返回全部 12 个核心工具（含 ask_user_question）。"""
    return [
        read_file,
        write_file,
        edit_file,
        glob_search,
        list_dir,
        grep_search,
        bash,
        web_search,
        web_fetch,
        ask_user_question,
        todo_write,
        todo_read,
    ]


def build_readonly_tools() -> list:
    """返回只读工具集（排除 write/edit/delegate_code），供 Explore/Plan/verification 子代理使用。"""
    all_tools = build_core_tools()
    blocked = {"write", "edit"}
    return [t for t in all_tools if t.name not in blocked]


# ── 子代理类型描述（注入到工具 description 中） ──────────────────
_SUBAGENT_TYPE_DESCRIPTION = (
    "子代理类型。可选值：\n"
    "- general-purpose（默认）：通用编码代理，拥有所有工具，可读写文件\n"
    "- Explore：快速搜索代理，只读，擅长文件搜索和代码分析。"
    "调用时在 prompt 中指定彻底程度：quick/medium/very thorough\n"
    "- Plan：架构规划代理，只读，擅长设计实施方案和识别关键文件\n"
    "- verification：对抗性验证代理，只读，尝试找出 bug 和遗漏"
)


def make_agent_tool(
    subagents: dict[str, Any],
    name: str = "delegate_code",
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    """创建多类型子代理委派工具。

    Args:
        subagents: {agent_type: langchain_agent_instance} 字典。
            必须包含 "general-purpose" 键。
        name: 工具名。
    """

    class AgentInput(BaseModel):
        subagent_type: str = Field(
            default="general-purpose",
            description=_SUBAGENT_TYPE_DESCRIPTION,
        )
        task: str = Field(description="要委派给子代理的具体任务。")
        context: str = Field(default="", description="补充上下文，可选。")
        thread_id: str = Field(
            default="",
            description="可选的子代理会话名；传入相同值会复用同一个子代理线程。",
        )

    @tool(name, args_schema=AgentInput)
    async def delegate_code(
        subagent_type: str = "general-purpose",
        task: str = "",
        context: str = "",
        thread_id: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        """把任务委派给子代理执行。支持多种子代理类型，默认为通用编码代理。"""
        # 查找子代理实例
        agent = subagents.get(subagent_type) or subagents.get("general-purpose")
        if agent is None:
            return "错误：没有可用的子代理。"

        prompt = task.strip()
        if not prompt:
            return "错误：task 不能为空。"
        if context.strip():
            prompt = f"任务：{task.strip()}\n\n补充上下文：\n{context.strip()}"

        resolved_thread_id = (
            f"subagent-named-{thread_id.strip()}"
            if thread_id.strip()
            else f"subagent-{uuid4().hex}"
        )

        logger.info("delegate_code: type=%s, thread=%s", subagent_type, resolved_thread_id)

        invoke_config = {"configurable": {"thread_id": resolved_thread_id}}

        if event_callback:
            event_callback(
                {
                    "type": "subagent_start",
                    "parent_tool_call_id": tool_call_id,
                    "subagent_id": resolved_thread_id,
                    "subagent_type": subagent_type,
                    "thread_id": resolved_thread_id,
                }
            )

        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": prompt}]},
            config=invoke_config,
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            chunk_type = chunk.get("type")
            if chunk_type == "messages":
                token, metadata = chunk["data"]
                if metadata.get("langgraph_node") == "model" and isinstance(token, AIMessageChunk) and token.text:
                    continue
                continue
            if chunk_type != "updates":
                continue

            for step, data in chunk["data"].items():
                if not isinstance(data, dict):
                    continue
                new_messages = data.get("messages", [])
                if not isinstance(new_messages, list):
                    continue

                if step == "model":
                    for message in new_messages:
                        if not isinstance(message, AIMessage):
                            continue
                        for call in message.tool_calls:
                            if event_callback:
                                event_callback(
                                    {
                                        "type": "subagent_tool_start",
                                        "parent_tool_call_id": tool_call_id,
                                        "subagent_id": resolved_thread_id,
                                        "subagent_type": subagent_type,
                                        "name": call.get("name", "tool"),
                                        "args": call.get("args", {}),
                                        "tool_call_id": call.get("id", ""),
                                    }
                                )
                elif step == "tools":
                    for message in new_messages:
                        if not isinstance(message, ToolMessage):
                            continue
                        if event_callback:
                            event_callback(
                                {
                                    "type": "subagent_tool_end",
                                    "parent_tool_call_id": tool_call_id,
                                    "subagent_id": resolved_thread_id,
                                    "subagent_type": subagent_type,
                                    "name": message.name or "tool",
                                    "output": _render_tool_output(message.content),
                                    "tool_call_id": getattr(message, "tool_call_id", ""),
                                }
                            )

        state = await agent.aget_state(invoke_config)
        messages = state.values.get("messages", []) if state and state.values else []
        summary = _trim_output(_extract_last_ai_text(messages))

        if event_callback:
            event_callback(
                {
                    "type": "subagent_finish",
                    "parent_tool_call_id": tool_call_id,
                    "subagent_id": resolved_thread_id,
                    "subagent_type": subagent_type,
                    "summary": summary,
                }
            )

        return summary

    return delegate_code


def dump_tools_manifest() -> str:
    manifest = {
        "core_tools": [
            "read",
            "write",
            "edit",
            "glob",
            "list_dir",
            "grep",
            "bash",
            "web_search",
            "web_fetch",
            "ask_user_question",
            "todo_write",
            "todo_read",
        ],
        "subagent_tool": "delegate_code",
        "subagent_types": ["general-purpose", "Explore", "Plan", "verification"],
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2)
