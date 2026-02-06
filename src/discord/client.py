"""Discord client setup and configuration."""

import asyncio
import logging

import discord
from discord.ext import commands

from src.config import get_config
from src.discord.handlers import MessageHandler

logger = logging.getLogger("meowko")


class MeowkoBot(commands.Bot):
    """Main Discord bot client for Meowko."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        self.message_handler = MessageHandler()
        config = get_config()
        self.message_delay = config.discord.get("message_delay", 0.5)

    async def _send_split_response(self, channel: discord.TextChannel, response: str) -> None:
        """Split response by newlines and send as separate messages with delay."""
        # Split by newlines and filter out empty lines
        parts = [line.strip() for line in response.split("\n") if line.strip()]

        for i, part in enumerate(parts):
            await channel.send(part)
            # Add delay between messages (but not after the last one)
            if i < len(parts) - 1 and self.message_delay > 0:
                await asyncio.sleep(self.message_delay)

    async def setup_hook(self) -> None:
        """Called before the bot starts."""
        logger.info("Setting up bot...")

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        # Ignore own messages
        if message.author == self.user:
            return

        # Ignore other bots
        if message.author.bot:
            return

        # Process commands first
        await self.process_commands(message)

        # Handle text messages in guild channels
        if message.guild and message.content:
            async with message.channel.typing():
                response = await self.message_handler.handle_text_message(message)
                await self._send_split_response(message.channel, response)
