"""Discord client setup and configuration."""

import asyncio
import io
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
        self.message_delay = config.discord["message_delay"]

    async def _send_split_response(self, channel: discord.TextChannel, text: str) -> None:
        """Split text by newlines and send as separate messages with delay."""
        parts = [line.strip() for line in text.split("\n") if line.strip()]
        for i, part in enumerate(parts):
            await channel.send(part)
            if i < len(parts) - 1 and self.message_delay > 0:
                await asyncio.sleep(self.message_delay)

    async def _send_segments(
        self, channel: discord.TextChannel, segments: list[dict],
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
        """Called before the bot starts."""
        logger.info("Setting up bot...")

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        if message.author == self.user:
            return
        if message.author.bot:
            return

        await self.process_commands(message)

        # Classify attachments
        has_images = False
        has_audio = False
        for a in message.attachments:
            ct = a.content_type or ""
            base = ct.split(";")[0].strip()
            if base.startswith("image/"):
                has_images = True
            elif base.startswith("audio/") or base in ("video/mp4", "video/webm"):
                has_audio = True

        if message.guild and (message.content or has_images or has_audio):
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
