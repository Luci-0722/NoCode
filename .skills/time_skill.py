"""Time skill: get current time and date information."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.skills.registry import Skill, SkillRegistry


async def get_current_time(**kwargs: Any) -> str:
    """Get current date and time."""
    tz_name = kwargs.get("timezone", "local")
    now = datetime.now()

    if tz_name != "local":
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_name)
            now = datetime.now(tz)
        except (ImportError, Exception):
            return f"Unknown timezone '{tz_name}', using local time."

    return now.strftime("%Y-%m-%d %H:%M:%S %A")


async def get_date_info(**kwargs: Any) -> str:
    """Get detailed date information."""
    now = datetime.now()
    year_start = datetime(now.year, 1, 1)
    days_passed = (now - year_start).days + 1
    days_remaining = 366 if (now.year % 4 == 0 and now.year % 100 != 0) else 365
    days_remaining -= days_passed

    return (
        f"Today: {now.strftime('%Y-%m-%d %A')}\n"
        f"Day {days_passed} of {days_passed + days_remaining} in year {now.year}\n"
        f"{days_remaining} days remaining"
    )


def register(registry: SkillRegistry) -> None:
    registry.register(Skill(
        name="get_current_time",
        description="Get the current date and time. Optionally specify a timezone like 'Asia/Shanghai'.",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Timezone name (e.g. 'Asia/Shanghai', 'UTC'). Default is local.",
                },
            },
        },
        handler=get_current_time,
    ))

    registry.register(Skill(
        name="get_date_info",
        description="Get detailed information about today's date, including day of year.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=get_date_info,
    ))
