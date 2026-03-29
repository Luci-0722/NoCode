# Scheduler Spec

## Module

`src/scheduler/scheduler.py`

## Description

Task scheduling system built on APScheduler's AsyncIOScheduler. Supports cron expressions, fixed intervals, and one-shot (date) triggers.

## API

### `TaskScheduler.__init__()`
- Creates AsyncIOScheduler instance
- Internal task storage dict

### `add_task(name, handler, trigger_type, trigger_args, description="") -> int`
- `trigger_type`: `"cron"`, `"interval"`, or `"once"`
- `trigger_args`: dict of trigger-specific parameters
  - cron: `{"hour": 9, "minute": 0}` etc.
  - interval: `{"hours": 1}` etc.
  - once: `{"run_date": "2024-01-01 09:00:00"}` etc.
- Returns task ID

### `remove_task(task_id)` — remove and stop scheduled task
### `toggle_task(task_id)` — enable/disable task
### `list_tasks() -> list[dict]` — list all tasks with metadata
### `start()` — start the scheduler
### `stop()` — graceful shutdown
