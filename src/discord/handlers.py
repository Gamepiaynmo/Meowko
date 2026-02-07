"""Discord event handlers for messages, attachments, and interactions."""

import asyncio
import base64
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

import discord

from src.core.context_builder import ContextBuilder
from src.providers.llm_client import LLMClient, LLMResponse

logger = logging.getLogger("meowko")

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


class MessageHandler:
    """Handler for Discord text and image messages."""

    def __init__(self) -> None:
        """Initialize the message handler."""
        self.context_builder = ContextBuilder()
        self.llm_client = LLMClient()
        self._scope_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    async def handle_message(self, message: discord.Message) -> str | None:
        """Handle a message with optional image attachments.

        Messages that arrive while a prior message for the same scope is
        being processed are batched into a single LLM turn, saving an API
        round-trip and preserving the prompt cache prefix.

        Returns:
            The assistant's response text, or None if this message was
            batched into another in-flight request.
        """
        user_id = message.author.id
        user_name = message.author.display_name
        user_text = message.content or ""
        persona_id = "meowko"  # Default persona for now

        # Format message with timestamp and username
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        prefix = f"[{current_time}] {user_name}:"

        # Extract images outside the lock (independent I/O)
        images = await self._extract_images(message.attachments)

        if images:
            logger.info(
                "Processing message from %s: %s (+ %d image(s))",
                user_name, user_text[:50], len(images),
            )
        else:
            logger.info("Processing message from %s: %s", user_name, user_text[:50])

        # Build text line for this message
        text_parts = [prefix]
        if user_text:
            text_parts.append(f" {user_text}")
        for img in images:
            text_parts.append(f" [Image: {img['filename']}]")
        text_line = "".join(text_parts)

        # Enqueue and let the lock holder batch us
        scope_key = f"{persona_id}-{user_id}"
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[scope_key].append({
            "text": text_line,
            "images": images,
            "future": future,
        })

        async with self._scope_locks[scope_key]:
            # If a previous lock holder already batched our message, nothing to do
            if future.done():
                return None

            # Drain all pending messages for this scope
            batch = list(self._pending[scope_key])
            self._pending[scope_key].clear()

            if len(batch) > 1:
                logger.info(
                    "Batched %d messages for scope %s",
                    len(batch), scope_key,
                )

            response = await self._process_batch(batch, user_id, persona_id)

            # Resolve all futures so followers skip sending
            for item in batch:
                if not item["future"].done():
                    item["future"].set_result(response)

        return response

    async def _process_batch(
        self,
        batch: list[dict[str, Any]],
        user_id: int,
        persona_id: str,
    ) -> str:
        """Build context, combine batch into one turn, call LLM, save."""
        # Combine text lines and images from all messages in the batch
        all_text_lines = [item["text"] for item in batch]
        all_images: list[dict[str, str]] = []
        for item in batch:
            all_images.extend(item["images"])

        combined_text = "\n".join(all_text_lines)

        # Build context (includes all previous turns)
        context = await self.context_builder.build_context(
            user_id=user_id,
            persona_id=persona_id,
        )

        # Append the (possibly combined) user turn
        if all_images:
            content_blocks: list[dict[str, Any]] = [
                {"type": "text", "text": combined_text},
            ]
            for img in all_images:
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": img["data_url"]},
                })
            context.append({"role": "user", "content": content_blocks})
        else:
            context.append({"role": "user", "content": combined_text})

        # Get LLM response
        try:
            llm_response: LLMResponse = await self.llm_client.chat(context)
        except Exception as e:
            logger.exception("Error getting LLM response")
            return f"Sorry, I encountered an error: {e}"

        # Save the turn (text-only version for storage)
        self.context_builder.save_turn(
            user_id=user_id,
            user_message=combined_text,
            assistant_message=llm_response.content,
            persona_id=persona_id,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
            total_tokens=llm_response.total_tokens,
            cached_tokens=llm_response.cached_tokens,
            cost=llm_response.cost,
        )

        logger.info("Response sent: %s...", llm_response.content[:50])
        return llm_response.content

    async def _extract_images(
        self, attachments: list[discord.Attachment]
    ) -> list[dict[str, str]]:
        """Extract supported image attachments as base64 data URLs.

        Returns:
            List of dicts with 'filename', 'media_type', and 'data_url' keys.
        """
        images = []
        for attachment in attachments:
            media_type = attachment.content_type
            if not media_type:
                continue

            # Strip charset suffix (e.g. "image/png; charset=utf-8")
            base_type = media_type.split(";")[0].strip()
            if base_type not in SUPPORTED_IMAGE_TYPES:
                continue

            try:
                image_bytes = await attachment.read()
                b64 = base64.b64encode(image_bytes).decode("ascii")
                data_url = f"data:{base_type};base64,{b64}"
                images.append({
                    "filename": attachment.filename,
                    "media_type": base_type,
                    "data_url": data_url,
                })
            except Exception:
                logger.exception("Failed to read image attachment: %s", attachment.filename)

        return images
