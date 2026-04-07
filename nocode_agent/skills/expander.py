"""SkillExpander: expand SKILL.md content with shell commands and variables."""

from __future__ import annotations

import asyncio
import logging
import re

from . import SkillEntry

logger = logging.getLogger(__name__)


class SkillExpander:
    """Expand a skill's markdown body: run shell commands, substitute variables."""

    def __init__(self, shell_timeout: int = 10):
        self.shell_timeout = shell_timeout

    async def expand(
        self,
        entry: SkillEntry,
        args: str | list[str] | None = None,
    ) -> str:
        """Expand skill content and return the final text."""
        content = entry.markdown_content

        # 1. Argument substitution
        content = self._substitute_arguments(content, args, entry.arguments)

        # 2. Path substitution
        content = content.replace("${SKILL_DIR}", str(entry.skill_dir))

        # 3. Shell command pre-execution
        content = await self._execute_shell_commands(content)

        # 4. Add directory header
        return f"Base directory for this skill: {entry.skill_dir}\n\n{content}"

    # ------------------------------------------------------------------
    # Argument substitution
    # ------------------------------------------------------------------

    @staticmethod
    def _split_args(args: str) -> list[str]:
        """Split arguments by whitespace (respecting quoted strings)."""
        parts: list[str] = []
        current: list[str] = []
        in_quote = False
        quote_char = ""
        for ch in args:
            if in_quote:
                if ch == quote_char:
                    in_quote = False
                else:
                    current.append(ch)
            elif ch in ('"', "'"):
                in_quote = True
                quote_char = ch
            elif ch.isspace():
                if current:
                    parts.append("".join(current))
                    current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current))
        return parts

    def _substitute_arguments(
        self,
        content: str,
        args: str | list[str] | None,
        named_args: list[str],
    ) -> str:
        args_text, parts = self._normalize_args(args)
        if not args_text:
            return content

        had_arguments_placeholder = "$ARGUMENTS" in content

        # Positional: $1, $2, ...
        for i, part in enumerate(parts):
            # Be careful not to replace ${SKILL_DIR} etc.
            content = content.replace(f"${i + 1}", part)

        # $ARGUMENTS — full argument string
        content = content.replace("$ARGUMENTS", args_text)

        # $ARGUMENTS[N] — indexed access
        for i, part in enumerate(parts):
            content = content.replace(f"$ARGUMENTS[{i}]", part)

        # Named arguments from frontmatter
        for i, name in enumerate(named_args):
            if i < len(parts):
                content = content.replace(f"${name}", parts[i])

        # If no placeholder was found, append args at the end
        if not had_arguments_placeholder and not named_args:
            content += f"\n\nArguments: {args_text}"

        return content

    def _normalize_args(
        self,
        args: str | list[str] | None,
    ) -> tuple[str | None, list[str]]:
        if args is None:
            return None, []

        if isinstance(args, list):
            parts = [str(part) for part in args if str(part)]
            if not parts:
                return None, []
            return " ".join(parts), parts

        args_text = args.strip()
        if not args_text:
            return None, []
        return args_text, self._split_args(args_text)

    # ------------------------------------------------------------------
    # Shell command execution
    # ------------------------------------------------------------------

    async def _execute_shell_commands(self, content: str) -> str:
        """Find and execute !`command` and ```! ... ``` patterns."""
        # Block mode: ```! ... ```
        content = await self._replace_pattern(
            content,
            r"```!\s*\n?([\s\S]*?)\n?```",
        )

        # Inline mode: !`command`
        if "!`" not in content:
            return content

        content = await self._replace_pattern(
            content,
            r"(?:^|\s)!`([^`]+)`",
        )

        return content

    async def _replace_pattern(self, content: str, pattern: str) -> str:
        """Replace all matches of pattern with their shell output."""
        matches = list(re.finditer(pattern, content, re.MULTILINE))
        if not matches:
            return content

        # Run all commands concurrently
        commands = [m.group(1).strip() for m in matches]
        outputs = await asyncio.gather(
            *[self._run_shell(cmd) for cmd in commands]
        )

        # Replace in reverse order to preserve positions
        for match, output in reversed(list(zip(matches, outputs))):
            start, end = match.start(), match.end()
            content = content[:start] + output + content[end:]

        return content

    async def _run_shell(self, command: str) -> str:
        """Execute a shell command and return its stdout."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.shell_timeout
            )
            return stdout.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            logger.warning("Shell command in skill expansion failed: %s", command[:200])
            return f"[shell command failed: {exc}]"
