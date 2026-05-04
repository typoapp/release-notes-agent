"""
ADK tool: block until it is 09:45 local time (or the time set in RN_STANDUP_TIME env var).
"""
import asyncio
import os
from datetime import datetime, time as dtime
import zoneinfo

from google.adk.tools import FunctionTool


async def _wait_until_standup() -> dict:
    tz_name = os.getenv("RN_STANDUP_TZ", "UTC")
    hhmm = os.getenv("RN_STANDUP_TIME", "09:45")
    tz = zoneinfo.ZoneInfo(tz_name)
    hour, minute = map(int, hhmm.split(":"))
    target = dtime(hour, minute)

    while True:
        now = datetime.now(tz).time().replace(second=0, microsecond=0)
        if now >= target:
            return {"status": "ready", "fired_at": datetime.now(tz).isoformat()}
        await asyncio.sleep(30)


schedule_tool = FunctionTool(func=_wait_until_standup)
