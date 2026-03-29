"""Bash 内置工具：执行 shell 命令。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 10240  # ~10KB


class BashTool(BaseTool):
    """执行 shell 命令并返回输出。"""

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Execute a shell command and return the output. Use for file operations, running scripts, system queries, and other shell tasks."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        }

    async def execute(self, args: dict[str, Any], config: Any) -> ToolResult:
        command = args.get("command", "")
        if not command:
            return ToolResult(content="Error: 'command' is required for bash.", is_error=True)

        timeout = getattr(config, "bash_timeout", 30)
        cwd = getattr(config, "bash_workdir", None)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                content=f"Error: command timed out after {timeout}s.",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(content=f"Error: {e}", is_error=True)

        output_parts = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")

        result = "\n".join(output_parts) if output_parts else "(no output)"
        result += f"\n[exit code: {proc.returncode}]"

        if len(result) > _MAX_OUTPUT:
            result = result[:_MAX_OUTPUT] + f"\n... (output truncated, total {len(result)} bytes)"

        return ToolResult(content=result)
