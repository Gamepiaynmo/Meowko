"""Discord client setup and configuration."""

import logging

import discord
from discord.ext import commands

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
                await message.reply(response)
