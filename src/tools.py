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
