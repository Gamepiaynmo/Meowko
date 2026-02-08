"""Discord slash commands for Meowko."""

import logging

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from src.config import get_config
from src.core.jsonl_store import JSONLStore
from src.core.persona_id import is_valid_persona_id
from src.core.user_state import UserState

logger = logging.getLogger("meowko.discord.commands")


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


class MemoryCommands(commands.Cog):
    """Slash commands for memory management."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.user_state = UserState()

    @app_commands.command(name="compact", description="Compact current conversation into memory")
    async def compact(self, interaction: discord.Interaction) -> None:
        """Manually compact the current conversation into a memory summary."""
        if not interaction.guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            from src.core.memory_manager import MemoryManager

            user_id = interaction.user.id
            persona_id = self.user_state.get_persona_id(user_id)
            scope_id = f"{persona_id}-{user_id}"

            mm = MemoryManager()
            await mm.compact_conversation(scope_id)
            await interaction.followup.send("Conversation compacted into memory.", ephemeral=True)
        except Exception as e:
            logger.exception("Failed to compact conversation")
            await interaction.followup.send(f"Compact failed: {e}", ephemeral=True)


class RewindCommands(commands.Cog):
    """Slash command to rewind conversation history."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.user_state = UserState()
        self.store = JSONLStore()

    @app_commands.command(name="rewind", description="Remove the last exchange from conversation history")
    async def rewind(self, interaction: discord.Interaction) -> None:
        user_id = interaction.user.id
        persona_id = self.user_state.get_persona_id(user_id)
        removed = self.store.rewind(persona_id, user_id)

        if removed:
            await interaction.response.send_message(
                f"Rewound {removed} message(s) from history.", ephemeral=True,
            )
        else:
            await interaction.response.send_message("Nothing to rewind.", ephemeral=True)


class PersonaCommands(commands.Cog):
    """Slash commands for persona selection."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.user_state = UserState()

    persona = app_commands.Group(name="persona", description="Manage active persona")

    @persona.command(name="list", description="List available personas")
    async def persona_list(self, interaction: discord.Interaction) -> None:
        """List all available personas, marking the user's current selection."""
        config = get_config()
        personas_dir = config.data_dir / config.paths["personas_dir"]

        if not personas_dir.is_dir():
            await interaction.response.send_message("No personas directory found.", ephemeral=True)
            return

        current = self.user_state.get_persona_id(interaction.user.id)
        lines: list[str] = []

        for entry in sorted(personas_dir.iterdir()):
            if not entry.is_dir():
                continue
            persona_yaml = entry / "persona.yaml"
            pid = entry.name
            nickname = pid
            if persona_yaml.exists():
                with open(persona_yaml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                nickname = data.get("nickname", pid)
            marker = " **(active)**" if pid == current else ""
            lines.append(f"- `{pid}` — {nickname}{marker}")

        if not lines:
            await interaction.response.send_message("No personas found.", ephemeral=True)
            return

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @persona.command(name="set", description="Set your active persona")
    @app_commands.describe(persona_id="Persona directory name")
    async def persona_set(self, interaction: discord.Interaction, persona_id: str) -> None:
        """Set the user's active persona."""
        config = get_config()
        if not is_valid_persona_id(persona_id):
            await interaction.response.send_message(
                "Invalid persona ID. Use letters, numbers, hyphens, and underscores only.",
                ephemeral=True,
            )
            return

        personas_dir = (config.data_dir / config.paths["personas_dir"]).resolve()
        persona_dir = (personas_dir / persona_id).resolve()
        if personas_dir not in persona_dir.parents:
            await interaction.response.send_message(
                "Invalid persona path.",
                ephemeral=True,
            )
            return

        if not persona_dir.is_dir():
            await interaction.response.send_message(
                f"Persona `{persona_id}` not found.", ephemeral=True,
            )
            return

        self.user_state.set_persona_id(interaction.user.id, persona_id)
        await interaction.response.send_message(
            f"Persona set to `{persona_id}`.", ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Register command cogs."""
    await bot.add_cog(VoiceCommands(bot))
    await bot.add_cog(MemoryCommands(bot))
    await bot.add_cog(RewindCommands(bot))
    await bot.add_cog(PersonaCommands(bot))
