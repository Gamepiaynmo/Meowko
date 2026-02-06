"""Discord event handlers for messages, attachments, and interactions."""

import logging
from datetime import datetime

import discord

from src.core.context_builder import ContextBuilder
from src.providers.llm_client import LLMClient, LLMResponse

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
        user_id = message.author.id
        user_name = message.author.display_name
        user_message_raw = message.content
        persona_id = "meowko"  # Default persona for now

        # Format message with timestamp and username
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        user_message = f"[{current_time}] {user_name}: {user_message_raw}"

        logger.info(f"Processing message from {user_name}: {user_message_raw[:50]}...")

        # Build context (includes all previous turns)
        messages = self.context_builder.build_context(
            user_id=user_id,
            persona_id=persona_id,
        )

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Get LLM response
        try:
            llm_response: LLMResponse = await self.llm_client.chat(messages)
        except Exception as e:
            logger.exception("Error getting LLM response")
            return f"Sorry, I encountered an error: {e}"

        # Save the turn (with formatted user message, token usage, and cost)
        self.context_builder.save_turn(
            user_id=user_id,
            user_message=user_message,
            assistant_message=llm_response.content,
            persona_id=persona_id,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
            total_tokens=llm_response.total_tokens,
            cached_tokens=llm_response.cached_tokens,
            cost=llm_response.cost,
        )

        logger.info(f"Response sent: {llm_response.content[:50]}...")
        return llm_response.content
