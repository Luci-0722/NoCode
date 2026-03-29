"""Scheduler: cron-like scheduled task management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

TaskHandler = Callable[..., Awaitable[str]]


@dataclass
class ScheduledTask:
    id: str
    name: str
    description: str
    handler: TaskHandler
    trigger_type: str  # "cron", "interval", "once"
    trigger_args: dict[str, Any]
    enabled: bool = True
    next_run: str | None = None


class TaskScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler()
        self._tasks: dict[str, ScheduledTask] = {}

    def start(self) -> None:
        self._scheduler.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def add_task(self, task: ScheduledTask) -> None:
        if task.trigger_type == "cron":
            trigger = CronTrigger(**task.trigger_args)
        elif task.trigger_type == "interval":
            trigger = IntervalTrigger(**task.trigger_args)
        elif task.trigger_type == "once":
            trigger = DateTrigger(**task.trigger_args)
        else:
            raise ValueError(f"Unknown trigger type: {task.trigger_type}")

        self._scheduler.add_job(
            self._run_task,
            trigger=trigger,
            id=task.id,
            args=[task],
            replace_existing=True,
        )
        self._tasks[task.id] = task
        job = self._scheduler.get_job(task.id)
        if job and job.next_run_time:
            task.next_run = job.next_run_time.isoformat()
        logger.info("Scheduled task: %s (%s)", task.name, task.id)

    async def _run_task(self, task: ScheduledTask) -> None:
        if not task.enabled:
            return
        logger.info("Running scheduled task: %s", task.name)
        try:
            result = await task.handler()
            logger.info("Task %s result: %s", task.name, result)
        except Exception as e:
            logger.error("Task %s failed: %s", task.name, e)

    def remove_task(self, task_id: str) -> bool:
        if task_id not in self._tasks:
            return False
        self._scheduler.remove_job(task_id)
        del self._tasks[task_id]
        return True

    def toggle_task(self, task_id: str, enabled: bool) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.enabled = enabled
        if not enabled:
            job = self._scheduler.get_job(task_id)
            if job:
                job.pause()
        else:
            job = self._scheduler.get_job(task_id)
            if job:
                job.resume()
        return True

    def list_tasks(self) -> list[dict[str, Any]]:
        result = []
        for task in self._tasks.values():
            result.append({
                "id": task.id,
                "name": task.name,
                "description": task.description,
                "trigger_type": task.trigger_type,
                "enabled": task.enabled,
                "next_run": task.next_run,
            })
        return result

    def get_task(self, task_id: str) -> ScheduledTask | None:
        return self._tasks.get(task_id)
