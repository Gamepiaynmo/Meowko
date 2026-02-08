"""Discord client setup and configuration."""

import asyncio
import io
import logging
from typing import Any

import discord
from discord.ext import commands

from src.config import get_config
from src.discord.handlers import MessageHandler
from src.discord.voice import VoiceSessionManager

logger = logging.getLogger("meowko.discord.client")


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
        self.voice_manager = VoiceSessionManager()
        config = get_config()
        self.message_delay = config.discord["message_delay"]

    async def _send_split_response(self, channel: discord.abc.Messageable, text: str) -> None:
        """Split text by newlines and send as separate messages with delay."""
        parts = [line.strip() for line in text.split("\n") if line.strip()]
        for i, part in enumerate(parts):
            await channel.send(part)
            if i < len(parts) - 1 and self.message_delay > 0:
                await asyncio.sleep(self.message_delay)

    async def _send_segments(
        self, channel: discord.abc.Messageable, segments: list[dict[str, Any]],
    ) -> None:
        """Send an ordered list of segments (text / tts / tti) to a channel."""
        for seg in segments:
            kind = seg["type"]

            if kind == "text":
                await self._send_split_response(channel, seg["content"])

            elif kind == "tts":
                await self._send_split_response(channel, seg["content"])
                audio = seg.get("audio")
                if audio:
                    await channel.send(
                        file=discord.File(io.BytesIO(audio), filename="voice.mp3"),
                    )

            elif kind == "tti":
                image = seg.get("image")
                if image:
                    await channel.send(
                        file=discord.File(io.BytesIO(image), filename="image.png"),
                    )

    async def setup_hook(self) -> None:
        logger.info("Setting up bot...")
        from src.discord.commands import setup as commands_setup
        await commands_setup(self)
        await self.tree.sync()
        logger.info("Slash commands synced")

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        await self._auto_join_occupied_channels()

    async def _auto_join_occupied_channels(self) -> None:
        """Join voice channels that already have users at startup."""
        for guild in self.guilds:
            for channel in guild.voice_channels:
                non_bot_members = [m for m in channel.members if not m.bot]
                if non_bot_members:
                    logger.info(
                        "Auto-joining occupied voice channel: %s (guild: %s, %d user(s))",
                        channel.name, guild.name, len(non_bot_members),
                    )
                    try:
                        await self.voice_manager.join(channel)
                    except Exception:
                        logger.exception("Auto-join failed for channel %s", channel.name)
                    break  # One channel per guild

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await self.voice_manager.on_voice_state_update(member, before, after)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        if message.author.bot:
            return

        ctx = await self.get_context(message)
        if ctx.valid:
            await self.invoke(ctx)
            return

        if message.guild and (message.content or message.attachments):
            try:
                async with message.channel.typing():
                    segments = await self.message_handler.handle_message(message)
                    if segments:
                        await self._send_segments(message.channel, segments)
            except Exception:
                logger.exception(
                    "Error handling message from %s in #%s",
                    message.author, message.channel,
                )
