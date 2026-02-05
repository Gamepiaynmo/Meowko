"""Meowko Discord Bot - Main entry point."""

import asyncio
import logging


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

    # TODO: Initialize and run the bot
    # This will be implemented in subsequent milestones

    logger.info("Meowko stopped.")


if __name__ == "__main__":
    asyncio.run(main())
