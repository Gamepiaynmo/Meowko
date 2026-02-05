"""Discord client setup and configuration."""

import discord
from discord.ext import commands


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

    async def setup_hook(self) -> None:
        """Called before the bot starts."""
        # TODO: Load extensions and setup
        pass

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        print(f"Logged in as {self.user} (ID: {self.user.id})")
