"""Discord slash commands for Meowko."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("meowko")


class VoiceCommands(commands.Cog):
    """Slash commands for voice channel control."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction) -> None:
        """Join the user's current voice channel."""
        if not interaction.guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            await interaction.response.send_message("You need to be in a voice channel first.", ephemeral=True)
            return

        channel = member.voice.channel
        voice_manager = getattr(self.bot, "voice_manager", None)
        if voice_manager is None:
            await interaction.response.send_message("Voice support is not available.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await voice_manager.join(channel)
            await interaction.followup.send(f"Joined **{channel.name}**!", ephemeral=True)
        except Exception as e:
            logger.exception("Failed to join voice channel")
            await interaction.followup.send(f"Failed to join: {e}", ephemeral=True)

    @app_commands.command(name="leave", description="Leave the voice channel")
    async def leave(self, interaction: discord.Interaction) -> None:
        """Leave the current voice channel."""
        if not interaction.guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        voice_manager = getattr(self.bot, "voice_manager", None)
        if voice_manager is None:
            await interaction.response.send_message("Voice support is not available.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await voice_manager.leave(interaction.guild)
            await interaction.followup.send("Left the voice channel.", ephemeral=True)
        except Exception as e:
            logger.exception("Failed to leave voice channel")
            await interaction.followup.send(f"Failed to leave: {e}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Register the VoiceCommands cog."""
    await bot.add_cog(VoiceCommands(bot))
