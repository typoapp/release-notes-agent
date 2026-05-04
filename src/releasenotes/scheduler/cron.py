import asyncio
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Settings
from ..pipeline.generator import ReleaseNotePipeline


async def run_scheduler(config: Settings) -> None:
    scheduler = AsyncIOScheduler(timezone=config.schedule.timezone)

    async def job() -> None:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=config.schedule.since_hours)
        today = now.date().isoformat()
        await ReleaseNotePipeline(config).generate(since.isoformat(), today)

    scheduler.add_job(job, CronTrigger.from_crontab(config.schedule.cron, timezone=config.schedule.timezone))
    scheduler.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown()
