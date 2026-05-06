import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Settings
from ..ingestion.registry import get_ingestor
from ..pipeline.generator import ReleaseNotePipeline

logger = logging.getLogger(__name__)


def _read_last_tag(checkpoint_path: Path) -> str | None:
    if not checkpoint_path.exists():
        return None
    try:
        data = json.loads(checkpoint_path.read_text())
        return data.get("github", {}).get("last_tag")
    except (json.JSONDecodeError, OSError):
        return None


async def run_scheduler(config: Settings) -> None:
    scheduler = AsyncIOScheduler(timezone=config.schedule.timezone)

    async def job() -> None:
        if config.schedule.mode == "tag":
            await _run_tag_mode(config)
        else:
            await _run_date_mode(config)

    scheduler.add_job(job, CronTrigger.from_crontab(config.schedule.cron, timezone=config.schedule.timezone))
    scheduler.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown()


async def _run_date_mode(config: Settings) -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=config.schedule.since_hours)
    today = now.date().isoformat()
    await ReleaseNotePipeline(config).generate(since.isoformat(), today)


async def _run_tag_mode(config: Settings) -> None:
    ingestor = get_ingestor(
        "github",
        token=config.ingestion.github_token,
        repo=config.ingestion.github_repo,
        output_dir=config.output.output_dir,
    )
    latest_tag = await ingestor.get_latest_tag()
    if not latest_tag:
        logger.warning("scheduler: no tags found in repo, skipping run")
        return

    checkpoint_path = Path(config.output.output_dir) / ".releasenotes_cache.json"
    last_tag = _read_last_tag(checkpoint_path)

    if not last_tag:
        from_tag = await ingestor.get_previous_tag(latest_tag)
        if not from_tag:
            logger.warning("scheduler: only one tag exists and no prior checkpoint; skipping first run")
            return
    else:
        from_tag = last_tag

    if from_tag == latest_tag:
        logger.info("scheduler: no new release since last run (%s), skipping", latest_tag)
        return

    logger.info("scheduler: new release detected %s → %s, generating notes", from_tag, latest_tag)
    await ReleaseNotePipeline(config).generate(from_tag, latest_tag)
