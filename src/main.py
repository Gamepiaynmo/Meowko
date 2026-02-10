"""Meowko Discord Bot - Main entry point."""

import asyncio
import locale
import logging
from datetime import datetime
from pathlib import Path
from shutil import move

from src.config import get_config
from src.core.scheduler import Scheduler
from src.discord.client import MeowkoBot

logger = logging.getLogger("meowko.main")


class SingleLineFormatter(logging.Formatter):
    """Custom formatter that only outputs the first line of each log message."""

    def format(self, record: logging.LogRecord) -> str:
        # Keep the main message on one line
        original_msg = record.msg
        if isinstance(record.msg, str):
            record.msg = record.msg.replace("\n", " | ")
        formatted = super().format(record)
        record.msg = original_msg
        return formatted


def setup_logging(log_file: Path | None = None, log_level: str = "INFO") -> None:
    """Configure logging for the bot.

    If a previous log file exists, it will be backed up with a timestamp
    before starting a new empty log file.
    """
    formatter = SingleLineFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers[0].setFormatter(formatter)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing log file if it exists
        if log_file.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{log_file.stem}_{timestamp}{log_file.suffix}"
            backup_path = log_file.parent / backup_name
            move(log_file, backup_path)

        # Keep only the 5 most recent log files (backups + new)
        # Reserve 1 slot for the new log file about to be created
        backups = sorted(log_file.parent.glob(f"{log_file.stem}_*{log_file.suffix}"))
        for old in backups[: max(0, len(backups) - 4)]:
            old.unlink()

        # Create new empty log file
        file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        handlers=handlers,
    )

    # Silence noisy voice_recv loggers (opus decoder flushes, etc.)
    logging.getLogger("discord.ext.voice_recv").setLevel(logging.ERROR)


async def config_watcher(interval: int = 5) -> None:
    """Watch for config file changes and reload if modified.

    Args:
        interval: Seconds between checks.
    """
    config = get_config()
    while True:
        await asyncio.sleep(interval)
        try:
            config.reload_if_changed()
        except Exception:
            logger.exception("Config reload failed")


async def _main() -> None:
    """Main entry point for the bot."""
    # Load configuration first to get log path
    config = get_config()
    config_path = Path(__file__).parent.parent / "config.yaml"
    config.load(config_path)

    # Setup logging with file
    data_dir = config.data_dir
    logs_dir = data_dir / config.paths["logs_dir"]
    log_file = logs_dir / "meowko.log"
    setup_logging(log_file=log_file, log_level="INFO")

    # Set locale (cloud images often lack zh_CN.UTF-8 by default)
    locale_candidates = [config.locale, "C.UTF-8", "C"]
    locale_set = False
    for loc in locale_candidates:
        try:
            locale.setlocale(locale.LC_ALL, loc)
            logger.info("Locale set to: %s", loc)
            locale_set = True
            break
        except locale.Error:
            continue
    if not locale_set:
        logger.warning("Failed to set locale from candidates: %s", locale_candidates)

    logger.info("Starting Meowko...")

    discord_token = config.get("discord.token")
    if not discord_token:
        logger.error("Discord token not found in config.yaml. Please add 'discord.token' to your config.")
        return

    # Create bot, config watcher, and scheduler tasks
    bot = MeowkoBot()
    watcher_task = asyncio.create_task(config_watcher())
    scheduler = Scheduler()
    scheduler_task = scheduler.start()

    try:
        await bot.start(discord_token)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    finally:
        scheduler.stop()
        watcher_task.cancel()
        for task in (watcher_task, scheduler_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await bot.close()

    logger.info("Meowko stopped.")


def main() -> None:
    """Synchronous entry point for console script."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
