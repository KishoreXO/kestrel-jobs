from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from kestrel.config import get_settings
from kestrel.scanner import run_scan, scan_in_progress

log = logging.getLogger(__name__)


class _State:
    scheduler: AsyncIOScheduler | None = None


_state = _State()
JOB_ID = "periodic-scan"


async def _tick() -> None:
    if scan_in_progress():
        return
    try:
        await run_scan()
    except Exception:  # pragma: no cover
        log.exception("Scheduled scan failed")


def apply_schedule() -> None:
    """(Re)configure periodic scanning from settings. Never more often than every 6 hours."""
    settings = get_settings()
    if _state.scheduler is None:
        _state.scheduler = AsyncIOScheduler()
        _state.scheduler.start()
    scheduler = _state.scheduler
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    if settings.auto_scan:
        hours = max(6, settings.scan_interval_hours)
        scheduler.add_job(_tick, "interval", hours=hours, id=JOB_ID, max_instances=1, coalesce=True)


def shutdown() -> None:
    if _state.scheduler is not None:
        _state.scheduler.shutdown(wait=False)
        _state.scheduler = None
