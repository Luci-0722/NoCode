"""Claude Code 风格核心工具集。"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
from html import unescape
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen
from pathlib import Path
from uuid import uuid4

from langchain.tools import tool
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, Field

_MAX_OUTPUT = 12_000
_TODO_STORE: list[str] = []


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


class ReadInput(BaseModel):
    file_path: str = Field(description="工作区内的文件路径，支持相对路径。")
    offset: int = Field(default=1, ge=1, description="起始行号，从 1 开始。")
    limit: int = Field(default=400, ge=1, le=4000, description="最多读取多少行。")


@tool("read", args_schema=ReadInput)
def read_file(file_path: str, offset: int = 1, limit: int = 400) -> str:
    """读取文件内容并返回带行号的文本。"""
    try:
        path = _resolve_path(file_path)
        lines = path.read_text(encoding="utf-8").splitlines()
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
    return _trim_output("\n".join(rendered) + suffix)


class WriteInput(BaseModel):
    file_path: str = Field(description="工作区内的文件路径，支持相对路径。")
    content: str = Field(description="完整文件内容。")


@tool("write", args_schema=WriteInput)
def write_file(file_path: str, content: str) -> str:
    """覆盖写入文件。"""
    try:
        path = _resolve_path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
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
    """基于精确文本匹配编辑文件。"""
    try:
        path = _resolve_path(file_path)
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
    return f"已更新 {path}，替换 {occurrences if replace_all else 1} 处。"


class GlobInput(BaseModel):
    pattern: str = Field(description="glob 模式，例如 `src/**/*.py`。")


@tool("glob", args_schema=GlobInput)
def glob_search(pattern: str) -> str:
    """在工作区内执行 glob 搜索。"""
    root = _workspace_root()
    matches = sorted(str(path.relative_to(root)) for path in root.glob(pattern))
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
    max_matches: int = Field(default=200, ge=1, le=2000, description="最多返回多少条结果。")


@tool("grep", args_schema=GrepInput)
def grep_search(pattern: str, path: str = ".", file_glob: str = "*", max_matches: int = 200) -> str:
    """在工作区内搜索文本。"""
    try:
        base = _resolve_path(path)
        regex = re.compile(pattern)
    except re.error as error:
        return f"错误：无效正则: {error}"
    except Exception as error:
        return f"错误：路径无效: {error}"

    results: list[str] = []
    for file in sorted(base.rglob("*")):
        if not file.is_file():
            continue
        if not fnmatch.fnmatch(file.name, file_glob):
            continue
        try:
            for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{file.relative_to(_workspace_root())}:{line_no}: {line}")
                    if len(results) >= max_matches:
                        return _trim_output("\n".join(results) + f"\n\n[结果已截断，最多 {max_matches} 条]")
        except UnicodeDecodeError:
            continue
        except Exception:
            continue

    if not results:
        return "未找到匹配内容。"
    return _trim_output("\n".join(results))


class BashInput(BaseModel):
    command: str = Field(description="要执行的 shell 命令。")
    timeout: int = Field(default=30, ge=1, le=300, description="超时时间，单位秒。")


@tool("bash", args_schema=BashInput)
async def bash(command: str, timeout: int = 30) -> str:
    """在当前工作区执行 shell 命令并返回输出。"""
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
            "User-Agent": "codeagent/0.1 (+https://local.workspace)",
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
    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        html = _http_get(search_url)
    except Exception as error:
        return f"错误：网页搜索失败: {error}"

    pattern = re.compile(
        r'(?s)<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>|'
        r'(?s)<a[^>]*class="result__a"[^>]*href="(?P<href2>[^"]+)"[^>]*>(?P<title2>.*?)</a>.*?'
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
        todo_write,
        todo_read,
    ]


def make_subagent_tool(subagent, name: str = "delegate_code"):
    class DelegateInput(BaseModel):
        task: str = Field(description="要委派给子代理的具体任务。")
        context: str = Field(default="", description="补充上下文，可选。")

    @tool(name, args_schema=DelegateInput)
    async def delegate_code(task: str, context: str = "") -> str:
        """把复杂编码任务委派给后台子代理执行。"""
        prompt = task.strip()
        if context.strip():
            prompt = f"任务：{task.strip()}\n\n补充上下文：\n{context.strip()}"

        result = await subagent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"configurable": {"thread_id": f"subagent-{uuid4().hex}"}},
        )
        messages = result.get("messages", [])
        summary = _extract_last_ai_text(messages)
        return _trim_output(summary)

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
            "todo_write",
            "todo_read",
        ],
        "subagent_tool": "delegate_code",
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2)
