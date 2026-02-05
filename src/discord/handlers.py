"""Discord event handlers for messages, attachments, and interactions."""

import logging

import discord

from src.core.context_builder import ContextBuilder
from src.providers.llm_client import LLMClient

logger = logging.getLogger("meowko")


class MessageHandler:
    """Handler for Discord text messages."""

    def __init__(self) -> None:
        """Initialize the message handler."""
        self.context_builder = ContextBuilder()
        self.llm_client = LLMClient()

    async def handle_text_message(self, message: discord.Message) -> str:
        """Handle a text message and return the assistant's response.

        Args:
            message: The Discord message to handle.

        Returns:
            The assistant's response text.
        """
        guild_id = message.guild.id if message.guild else 0
        channel_id = message.channel.id
        user_id = message.author.id
        user_name = message.author.display_name
        user_message = message.content

        logger.info(f"Processing message from {user_name}: {user_message[:50]}...")

        # Build context
        messages = self.context_builder.build_context(
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            persona_id="alice",  # Default persona for now
            recent_turns=20,
        )

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Get LLM response
        try:
            response = await self.llm_client.chat(messages)
        except Exception as e:
            logger.exception("Error getting LLM response")
            return f"Sorry, I encountered an error: {e}"

        # Save the turn
        self.context_builder.save_turn(
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            user_message=user_message,
            assistant_message=response,
        )

        logger.info(f"Response sent: {response[:50]}...")
        return response
