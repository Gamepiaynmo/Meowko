"""Internal async scheduler for daily rollups and maintenance tasks."""

import asyncio
import logging
from datetime import date, datetime, time

from zoneinfo import ZoneInfo

from src.config import get_config
from src.core.jsonl_store import JSONLStore
from src.core.memory_manager import MemoryManager

logger = logging.getLogger("meowko.core.scheduler")


class Scheduler:
    """Async scheduler that triggers daily memory rollups."""

    def __init__(self) -> None:
        self.config = get_config()
        self._task: asyncio.Task | None = None
        self._last_rollup_date: date | None = None

    def start(self) -> asyncio.Task:
        """Create and return the scheduler loop task."""
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started")
        return self._task

    def stop(self) -> None:
        """Cancel the scheduler task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Scheduler stopped")

    async def _loop(self) -> None:
        """Tick at configured interval."""
        tick_interval = self.config.scheduler.get("tick_interval", 60)
        while True:
            try:
                await asyncio.sleep(tick_interval)
                await self._tick()
            except Exception:
                logger.exception("Scheduler tick failed")

    async def _tick(self) -> None:
        """Check if it's time for a daily rollup and run it."""
        memory_cfg = self.config.memory
        tz = ZoneInfo(memory_cfg.get("timezone", "UTC"))
        now = datetime.now(tz)

        # Parse rollup time
        rollup_time_str = memory_cfg.get("rollup_time", "03:00")
        hour, minute = (int(x) for x in rollup_time_str.split(":"))
        rollup_time = time(hour, minute)

        # Only run once per day, after rollup_time
        today = now.date()
        if self._last_rollup_date == today:
            return
        if now.time() < rollup_time:
            return

        logger.info("Running daily rollup for %s", today)
        self._last_rollup_date = today

        # Iterate all scopes and run rollup
        store = JSONLStore()
        memory_manager = MemoryManager()

        for scope_id in store.list_scopes():
            try:
                await memory_manager.run_daily_rollup(scope_id, today)
            except Exception:
                logger.exception("Rollup failed for scope %s", scope_id)
