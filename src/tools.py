"""Agent 工具定义。"""

from __future__ import annotations

import asyncio

from langchain.tools import tool

_MAX_OUTPUT = 10240  # ~10KB


@tool
async def bash(command: str, timeout: int = 30) -> str:
    """执行 shell 命令并返回 stdout/stderr 输出。

    可用于文件操作、运行脚本、系统查询等任务。

    Args:
        command: 要执行的 shell 命令。
        timeout: 最大执行时间，单位秒（默认 30）。
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"错误：命令执行超时（{timeout}秒）。"
    except Exception as e:
        return f"错误：{e}"

    parts: list[str] = []
    if stdout:
        parts.append(stdout.decode("utf-8", errors="replace"))
    if stderr:
        parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")
    result = "\n".join(parts) if parts else "(无输出)"
    result += f"\n[退出码: {proc.returncode}]"

    if len(result) > _MAX_OUTPUT:
        result = result[:_MAX_OUTPUT] + f"\n... (已截断，共 {len(result)} 字节)"
    return result


@tool
async def read(file_path: str, offset: int = 1, limit: int = 2000) -> str:
    """读取文件内容并返回（带行号）。

    Args:
        file_path: 文件的绝对路径。
        offset: 起始行号（从 1 开始，默认 1）。
        limit: 最多返回的行数（默认 2000）。
    """
    try:
        with open(file_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"错误：文件 {file_path} 不存在。"
    except Exception as e:
        return f"错误：{e}"

    total_lines = len(lines)
    start = max(1, offset) - 1
    end = min(total_lines, start + limit)

    if start >= total_lines:
        return f"错误：起始行号 {offset} 超出文件总行数 {total_lines}。"

    selected = lines[start:end]
    result_lines = []
    for i, line in enumerate(selected, start=start + 1):
        result_lines.append(f"{i:>6}\t{line.rstrip()}")

    result = "\n".join(result_lines)

    if start > 0 or end < total_lines:
        result += f"\n\n[显示第 {start + 1}-{end} 行，共 {total_lines} 行]"

    if len(result) > _MAX_OUTPUT:
        result = result[:_MAX_OUTPUT] + f"\n... (已截断，共 {len(result)} 字节)"
    return result
