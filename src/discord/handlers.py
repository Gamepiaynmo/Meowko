"""Discord event handlers for messages, attachments, and interactions."""

import asyncio
import base64
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import discord

from src.config import get_config
from src.core.context_builder import ContextBuilder
from src.providers.elevenlabs import ElevenLabsSTT, ElevenLabsTTS
from src.providers.llm_client import LLMClient, LLMResponse

logger = logging.getLogger("meowko")

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

SUPPORTED_AUDIO_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a",
    "audio/wav", "audio/x-wav", "audio/ogg", "audio/flac",
    "audio/webm", "audio/aac",
    "video/mp4", "video/webm",  # ElevenLabs accepts video too
}

# Matches [tts]...[/tts] and [tti]...[/tti] blocks, preserving order.
_BLOCK_RE = re.compile(r"\[(tts|tti)\](.*?)\[/\1\]", re.DOTALL)

# A segment is a dict:
#   {"type": "text",  "content": str}
#   {"type": "tts",   "content": str, "audio": bytes | None}
#   {"type": "tti",   "image":   bytes | None}
Segment = dict[str, Any]


def format_user_message(user_name: str, text: str) -> str:
    """Format a user message with timestamp prefix."""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"[{current_time}] {user_name}: {text}"


class MessageHandler:
    """Handler for Discord text, image, and audio messages."""

    def __init__(self) -> None:
        """Initialize the message handler."""
        self.context_builder = ContextBuilder()
        self.llm_client = LLMClient()
        self.stt = ElevenLabsSTT()
        self.tts = ElevenLabsTTS()
        self._tti: "ImageGenClient | None" = None
        self._tti_init = False
        self._scope_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    def _get_tti(self) -> "ImageGenClient | None":
        """Lazy-init the TTI client (returns None if not configured)."""
        if not self._tti_init:
            self._tti_init = True
            config = get_config()
            if config.tti.get("model"):
                from src.providers.image_gen import ImageGenClient
                try:
                    self._tti = ImageGenClient()
                except Exception:
                    logger.exception("Failed to initialize TTI client")
        return self._tti

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def handle_message(
        self, message: discord.Message,
    ) -> list[Segment] | None:
        """Handle a message with optional image/audio attachments.

        Returns:
            An ordered list of Segments to send, or None if this message
            was batched into another in-flight request.
        """
        user_id = message.author.id
        user_name = message.author.display_name
        user_text = message.content or ""
        persona_id = "meowko"  # Default persona for now

        # Extract media outside the lock (independent I/O)
        images = await self._extract_images(message.attachments)
        transcripts = await self._transcribe_audio(message.attachments)

        if images or transcripts:
            logger.info(
                "Processing message from %s: %s (+ %d image(s), %d audio(s))",
                user_name, user_text[:50], len(images), len(transcripts),
            )
        else:
            logger.info("Processing message from %s: %s", user_name, user_text[:50])

        # Build combined text content for this message
        content_parts = []
        if user_text:
            content_parts.append(user_text)
        for img in images:
            content_parts.append(f"[Image: {img['filename']}]")
        for tr in transcripts:
            content_parts.append(f"[Voice message: {tr['text']}]")
        text_line = format_user_message(user_name, " ".join(content_parts))

        # Enqueue and let the lock holder batch us
        scope_key = f"{persona_id}-{user_id}"
        future: asyncio.Future[list[Segment]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[scope_key].append({
            "text": text_line,
            "images": images,
            "audio_files": transcripts,
            "future": future,
        })

        async with self._scope_locks[scope_key]:
            if future.done():
                return None

            batch = list(self._pending[scope_key])
            self._pending[scope_key].clear()

            if len(batch) > 1:
                logger.info(
                    "Batched %d messages for scope %s",
                    len(batch), scope_key,
                )

            result = await self._process_batch(batch, user_id, persona_id)

            for item in batch:
                if not item["future"].done():
                    item["future"].set_result(result)

        return result

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    async def _process_batch(
        self,
        batch: list[dict[str, Any]],
        user_id: int,
        persona_id: str,
    ) -> list[Segment]:
        """Build context, call LLM, parse response into ordered segments."""
        all_text_lines = [item["text"] for item in batch]
        all_images: list[dict[str, str]] = []
        all_audio_files: list[dict[str, Any]] = []
        for item in batch:
            all_images.extend(item["images"])
            all_audio_files.extend(item.get("audio_files", []))

        combined_text = "\n".join(all_text_lines)

        # Build context
        context = await self.context_builder.build_context(
            user_id=user_id,
            persona_id=persona_id,
        )

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

        # LLM call
        try:
            llm_response: LLMResponse = await self.llm_client.chat(context)
        except Exception as e:
            logger.exception("Error getting LLM response")
            return [{"type": "text", "content": f"Sorry, I encountered an error: {e}"}]

        logger.info("Response sent: %s...", llm_response.content[:50])

        # Parse into ordered segments and generate media
        segments = await self._build_segments(llm_response.content)

        # Cache user attachments
        user_attachments: list[dict[str, str]] = []
        for img in all_images:
            path = self.context_builder.save_cache_file(
                persona_id, user_id, img["filename"], img["raw_bytes"],
            )
            user_attachments.append({
                "type": "image",
                "filename": img["filename"],
                "path": path,
            })
        for af in all_audio_files:
            path = self.context_builder.save_cache_file(
                persona_id, user_id, af["filename"], af["raw_bytes"],
            )
            user_attachments.append({
                "type": "audio",
                "filename": af["filename"],
                "path": path,
            })

        # Cache assistant-generated media
        assistant_attachments: list[dict[str, str]] = []
        for seg in segments:
            if seg["type"] == "tts" and seg.get("audio"):
                path = self.context_builder.save_cache_file(
                    persona_id, user_id, "tts.mp3", seg["audio"],
                )
                assistant_attachments.append({"type": "tts", "path": path})
            elif seg["type"] == "tti" and seg.get("image"):
                path = self.context_builder.save_cache_file(
                    persona_id, user_id, "tti.png", seg["image"],
                )
                assistant_attachments.append({"type": "tti", "path": path})

        # Save the turn (raw response with tags + attachment paths)
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
            user_attachments=user_attachments or None,
            assistant_attachments=assistant_attachments or None,
        )

        return segments

    # ------------------------------------------------------------------
    # Response parsing → ordered segments
    # ------------------------------------------------------------------

    async def _build_segments(self, text: str) -> list[Segment]:
        """Parse LLM output into ordered segments, generating media in parallel.

        Plain text between blocks becomes "text" segments.
        [tts]...[/tts] becomes a "tts" segment (text shown + audio attached).
        [tti]...[/tti] becomes a "tti" segment (prompt hidden, image attached).
        """
        # 1. Parse into raw (type, content) list
        raw: list[tuple[str, str]] = []
        last_end = 0
        for m in _BLOCK_RE.finditer(text):
            before = text[last_end:m.start()].strip()
            if before:
                raw.append(("text", before))
            raw.append((m.group(1), m.group(2).strip()))
            last_end = m.end()
        tail = text[last_end:].strip()
        if tail:
            raw.append(("text", tail))

        if not raw:
            return []

        # 2. Launch TTS / TTI tasks in parallel
        tti_client = self._get_tti()
        tasks: dict[int, asyncio.Task[bytes]] = {}
        for i, (kind, content) in enumerate(raw):
            if kind == "tts" and content:
                tasks[i] = asyncio.create_task(self.tts.synthesize(content))
            elif kind == "tti" and content:
                if tti_client:
                    tasks[i] = asyncio.create_task(tti_client.generate(content))
                else:
                    logger.warning("LLM produced [tti] block but tti.model is not configured")

        # Await all, tolerating individual failures
        results: dict[int, bytes] = {}
        if tasks:
            done = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for (idx, _), result in zip(tasks.items(), done):
                if isinstance(result, BaseException):
                    logger.exception(
                        "Media generation failed for segment %d", idx,
                        exc_info=result,
                    )
                else:
                    results[idx] = result

        # 3. Assemble final segments
        segments: list[Segment] = []
        for i, (kind, content) in enumerate(raw):
            if kind == "text":
                segments.append({"type": "text", "content": content})
            elif kind == "tts":
                segments.append({
                    "type": "tts",
                    "content": content,
                    "audio": results.get(i),
                })
            elif kind == "tti":
                image = results.get(i)
                if image:
                    segments.append({"type": "tti", "image": image})
                # If generation failed, silently drop (error already logged)

        return segments

    # ------------------------------------------------------------------
    # Attachment helpers
    # ------------------------------------------------------------------

    async def _extract_images(
        self, attachments: list[discord.Attachment]
    ) -> list[dict[str, Any]]:
        """Extract supported image attachments as base64 data URLs."""
        images = []
        for attachment in attachments:
            media_type = attachment.content_type
            if not media_type:
                continue

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
                    "raw_bytes": image_bytes,
                })
            except Exception:
                logger.exception("Failed to read image attachment: %s", attachment.filename)

        return images

    async def _transcribe_audio(
        self, attachments: list[discord.Attachment]
    ) -> list[dict[str, Any]]:
        """Transcribe supported audio attachments via ElevenLabs STT."""
        transcripts = []
        for attachment in attachments:
            media_type = attachment.content_type
            if not media_type:
                continue

            base_type = media_type.split(";")[0].strip()
            if base_type not in SUPPORTED_AUDIO_TYPES:
                continue

            try:
                audio_bytes = await attachment.read()
                text = await self.stt.transcribe(
                    audio_bytes,
                    filename=attachment.filename,
                    content_type=base_type,
                )
                if text:
                    transcripts.append({
                        "filename": attachment.filename,
                        "text": text,
                        "raw_bytes": audio_bytes,
                    })
            except Exception:
                logger.exception("Failed to transcribe audio: %s", attachment.filename)

        return transcripts
