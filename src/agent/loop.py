"""Agent loop: the core orchestration of message -> LLM -> tool_call -> execute -> respond."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from src.agent.llm_client import LLMClient
from src.core.context import ContextCompressor
from src.prompt import PromptManager
from src.short_memory import ShortTermMemory
from src.long_memory import LongTermMemory
from src.scheduler.scheduler import TaskScheduler
from src.skills.registry import SkillRegistry
from src.tools import ToolRegistry
from src.tools.bash import BashTool
from src.types import AgentConfig, Message

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm = LLMClient(config)
        self.short_term = ShortTermMemory(config.max_short_term_messages)
        self.long_term = LongTermMemory(config.data_dir + "/memory.db")
        self.skills = SkillRegistry()
        self.tools = ToolRegistry()
        self.scheduler = TaskScheduler()

        # Register builtin tools
        self.tools.register(BashTool())

        # 初始化提示词管理器
        self.prompt_manager = PromptManager(config.prompts_dir)

        # 初始化上下文压缩器
        self.context_compressor = ContextCompressor(
            max_context_tokens=config.max_context_tokens,
        )

        # Load skills from .skills directory
        self.skills.load_skills_from_dir(config.skills_dir)

        # Initialize memory skill accessor (if memory_skill is loaded)
        mem_mod = self.skills._modules.get("memory_skill")
        if mem_mod and hasattr(mem_mod, "get_memory_accessor"):
            mem_mod.get_memory_accessor().set_getter(lambda: self.long_term)

    def _build_system_prompt(self) -> str:
        """Build system prompt with long-term memory context.

        优先使用 PromptManager 从模板文件加载提示词，
        如果模板不存在则回退到 config 中的 system_prompt。
        """
        context = self.long_term.build_context_block()

        # 优先从模板文件加载
        if self.prompt_manager.has_prompt("system"):
            try:
                prompt = self.prompt_manager.get_prompt(
                    "system",
                    memory_context=context or "",
                )
                return prompt
            except (KeyError, FileNotFoundError) as e:
                logger.warning("模板加载失败，回退到配置中的 system_prompt: %s", e)

        # 回退：使用 config 中的 system_prompt
        prompt = self.config.system_prompt
        if prompt and context:
            prompt += f"\n\n{context}"
        return prompt

    def _build_tool_list(self) -> list:
        """Build combined tool definition list for LLM."""
        return self.tools.get_tool_definitions() + self.skills.get_tool_definitions()

    async def _dispatch_tool(self, tc: Any) -> str:
        """Dispatch a tool call to the appropriate handler."""
        args = tc.parse_arguments()

        # Try builtin tools first
        if self.tools.get(tc.name):
            return await self.tools.execute(tc.name, args, self.config)

        # Try skills
        if self.skills.get(tc.name):
            return await self.skills.execute(tc.name, **args)

        # Special: reminder (scheduler)
        if tc.name == "set_reminder":
            return await self._handle_reminder(args)

        return f"Error: Unknown tool '{tc.name}'"

    async def chat(self, user_input: str) -> str:
        """Send a user message and get the agent's response."""
        self.short_term.add_user(user_input)
        self.long_term.save_message("user", user_input)

        messages = [Message(role="system", content=self._build_system_prompt())]
        messages.extend(self.short_term.format_context())

        max_rounds = self.config.max_tool_rounds
        for round_idx in range(max_rounds):
            tools = self._build_tool_list()
            # 在发送给 LLM 之前进行上下文压缩
            messages = self.context_compressor.compress(messages)
            response = await self.llm.chat(messages, tools)

            if not response.tool_calls:
                # Final text response
                self.short_term.add(Message(role="assistant", content=response.content))
                self.long_term.save_message("assistant", response.content)
                return response.content

            # Has tool calls — process them
            self.short_term.add(response)
            messages.append(response)

            for tc in response.tool_calls:
                logger.info("Tool call: %s(%s)", tc.name, tc.arguments)
                result = await self._dispatch_tool(tc)

                tool_msg = Message(
                    role="tool",
                    content=result,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                self.short_term.add(tool_msg)
                messages.append(tool_msg)
                logger.info("Tool result: %s", result[:100])

            # Continue loop — LLM will process tool results
        return "I hit the maximum number of tool rounds. Let me summarize what I found so far."

    async def chat_stream(self, user_input: str) -> AsyncIterator[str]:
        """Stream the agent's response."""
        self.short_term.add_user(user_input)
        self.long_term.save_message("user", user_input)

        messages = [Message(role="system", content=self._build_system_prompt())]
        messages.extend(self.short_term.format_context())

        max_rounds = self.config.max_tool_rounds
        for round_idx in range(max_rounds):
            tools = self._build_tool_list()
            # 在发送给 LLM 之前进行上下文压缩
            messages = self.context_compressor.compress(messages)

            final_content = ""
            tool_calls_acc: list[Any] = []
            has_tool_calls = False

            async for chunk in self.llm.chat_stream(messages, tools):
                if isinstance(chunk, str):
                    final_content += chunk
                    yield chunk
                else:
                    has_tool_calls = True
                    tool_calls_acc.extend(chunk)

            if not has_tool_calls:
                self.short_term.add_assistant(final_content)
                self.long_term.save_message("assistant", final_content)
                return

            # Process tool calls
            response = Message(role="assistant", content=final_content, tool_calls=tool_calls_acc)
            self.short_term.add(response)
            messages.append(response)

            yield "\n"

            for tc in tool_calls_acc:
                logger.info("Tool call: %s(%s)", tc.name, tc.arguments)
                yield f"[using {tc.name}...]\n"
                result = await self._dispatch_tool(tc)
                tool_msg = Message(role="tool", content=result,
                                   tool_call_id=tc.id, name=tc.name)
                self.short_term.add(tool_msg)
                messages.append(tool_msg)

        yield "\n(Max tool rounds reached)"

    async def _handle_reminder(self, args: dict[str, Any]) -> str:
        """Handle reminder/scheduled task creation."""
        from src.scheduler.scheduler import ScheduledTask
        import uuid

        content = args.get("content", "")
        trigger_type = args.get("trigger_type", "once")

        if not content:
            return "Error: 'content' is required for a reminder."

        async def reminder_handler() -> str:
            response = await self.chat(f"[System Reminder] {content}")
            return response

        task_id = str(uuid.uuid4())[:8]
        if trigger_type == "cron":
            trigger_args = {
                k: args[k] for k in ["minute", "hour", "day", "month", "day_of_week"]
                if k in args
            }
        elif trigger_type == "interval":
            trigger_args = {
                k: args[k] for k in ["seconds", "minutes", "hours", "days"]
                if k in args
            }
        else:
            # One-shot at a specific time
            trigger_args = {"run_date": args.get("run_date", "")}

        task = ScheduledTask(
            id=task_id,
            name=f"Reminder: {content[:30]}",
            description=content,
            handler=reminder_handler,
            trigger_type=trigger_type,
            trigger_args=trigger_args,
        )
        self.scheduler.add_task(task)
        return f"Reminder set: {content} (task: {task_id})"

    async def start(self) -> None:
        """Start the agent (scheduler, etc.)."""
        self.scheduler.start()
        logger.info(
            "Agent started. Tools: %s, Skills: %s",
            self.tools.list_tools(),
            self.skills.list_skills(),
        )

    async def stop(self) -> None:
        """Stop the agent."""
        self.scheduler.stop()
        self.long_term.close()
        logger.info("Agent stopped")
