"""interactive 交互桥接回归测试。"""

from __future__ import annotations

import asyncio

from nocode_agent.interactive import InteractiveSessionBroker, PendingUserInputMiddleware
from nocode_agent.tools import make_ask_user_question_tool


def test_pending_user_input_is_injected_before_model() -> None:
    async def scenario() -> None:
        broker = InteractiveSessionBroker()
        middleware = PendingUserInputMiddleware(broker)

        await broker.enqueue_user_input("第一个补充")
        await broker.enqueue_user_input("第二个补充")

        update = await middleware.abefore_model({}, runtime=None)
        events = await broker.drain_events()

        assert update == {
            "messages": [
                {"role": "user", "content": "第一个补充"},
                {"role": "user", "content": "第二个补充"},
            ]
        }
        assert events == [
            {
                "type": "queued_prompt_injected",
                "texts": ["第一个补充", "第二个补充"],
            }
        ]
        assert await middleware.abefore_model({}, runtime=None) is None

    asyncio.run(scenario())


def test_ask_user_question_waits_for_user_answer() -> None:
    async def scenario() -> None:
        broker = InteractiveSessionBroker()
        tool = make_ask_user_question_tool(broker.ask_user_question)

        task = asyncio.create_task(
            tool.ainvoke(
                {
                    "questions": [
                        {
                            "question": "仓库可见性？",
                            "options": ["Public", "Private"],
                        }
                    ]
                }
            )
        )

        for _ in range(20):
            if broker._question_future is not None:  # noqa: SLF001
                break
            await asyncio.sleep(0)
        assert broker._question_future is not None  # noqa: SLF001
        assert not task.done()

        await broker.submit_question_answer("仓库可见性？ → Private")
        result = await task

        assert result == "仓库可见性？ → Private"

    asyncio.run(scenario())
