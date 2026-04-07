"""SkillPermissionManager: manage allowed-tools for skill context."""

from __future__ import annotations


class SkillPermissionManager:
    """Manage tool permissions during skill invocation."""

    def __init__(self) -> None:
        self._skill_allowed_tools: list[str] = []

    def enter_skill(self, allowed_tools: list[str]) -> None:
        self._skill_allowed_tools = allowed_tools

    def exit_skill(self) -> None:
        self._skill_allowed_tools = []

    def is_auto_allowed(self, tool_name: str, tool_args: dict) -> bool:
        """Check if a tool call is auto-allowed by the current skill context."""
        for pattern in self._skill_allowed_tools:
            if self._match_pattern(tool_name, tool_args, pattern):
                return True
        return False

    @staticmethod
    def _match_pattern(tool_name: str, tool_args: dict, pattern: str) -> bool:
        """Match tool name and args against an allowed-tools pattern.

        Supported formats:
          "Bash"              -> match all Bash calls
          "Bash(git add:*)"   -> match Bash calls starting with "git add"
          "Read"              -> match Read tool
        """
        if "(" not in pattern:
            return tool_name.lower() == pattern.lower()

        base, args_pattern = pattern.split("(", 1)
        args_pattern = args_pattern.rstrip(")")

        if tool_name.lower() != base.lower():
            return False

        # Check command prefix
        command = str(tool_args.get("command", ""))
        prefix = args_pattern.split(":")[0]
        return command.startswith(prefix)
