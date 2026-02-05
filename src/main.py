"""Meowko Discord Bot - Main entry point."""

import asyncio
import logging
from pathlib import Path

from src.config import get_config
from src.discord.client import MeowkoBot


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging for the bot."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def main() -> None:
    """Main entry point for the bot."""
    setup_logging()
    logger = logging.getLogger("meowko")
    logger.info("Starting Meowko...")

    # Load configuration
    config = get_config()
    config_path = Path(__file__).parent.parent / "config.yaml"
    config.load(config_path)

    # Get Discord token from config
    # Note: Discord token should be in config.yaml under discord.token
    # For now, we'll check if it's there, otherwise log an error
    discord_token = config.get("discord.token")
    if not discord_token:
        logger.error("Discord token not found in config.yaml. Please add 'discord.token' to your config.")
        return

    # Create and run bot
    bot = MeowkoBot()
    try:
        await bot.start(discord_token)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    finally:
        await bot.close()

    logger.info("Meowko stopped.")


if __name__ == "__main__":
    asyncio.run(main())
