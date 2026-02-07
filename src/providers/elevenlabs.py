"""ElevenLabs STT and TTS providers."""

import logging
from typing import Any

import aiohttp

from src.config import get_config

logger = logging.getLogger("meowko")

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"


class ElevenLabsSTT:
    """Batch speech-to-text via ElevenLabs REST API."""

    def __init__(self) -> None:
        config = get_config()
        el = config.elevenlabs
        self.api_key = el["api_key"]
        self.timeout = el["timeout"]
        self.stt_model = el.get("stt_model", "scribe_v2")
        self.language = el.get("language", "")

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        content_type: str = "audio/mpeg",
    ) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio file bytes.
            filename: Original filename (for mime-type hinting).
            content_type: MIME type of the audio.

        Returns:
            Transcribed text string.
        """
        headers = {"xi-api-key": self.api_key}

        form = aiohttp.FormData()
        form.add_field("model_id", self.stt_model)
        form.add_field(
            "file", audio_bytes,
            filename=filename,
            content_type=content_type,
        )
        if self.language:
            form.add_field("language_code", self.language)

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(STT_URL, headers=headers, data=form) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(
                        f"ElevenLabs STT failed ({resp.status}): {body[:200]}"
                    )
                data: dict[str, Any] = await resp.json()

        text = data.get("text", "").strip()
        lang = data.get("language_code", "?")
        logger.info("STT transcribed %s (%s): %s", filename, lang, text[:80])
        return text


class ElevenLabsTTS:
    """Text-to-speech via ElevenLabs REST API."""

    def __init__(self) -> None:
        config = get_config()
        el = config.elevenlabs
        self.api_key = el["api_key"]
        self.timeout = el["timeout"]
        self.model_id = el["model_id"]
        self.default_voice_id = el["default_voice_id"]

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        output_format: str = "mp3_44100_128",
    ) -> bytes:
        """Synthesize text to audio bytes.

        Args:
            text: The text to speak.
            voice_id: ElevenLabs voice ID (falls back to config default).
            output_format: Audio format string.

        Returns:
            Raw audio bytes (MP3 by default).
        """
        voice_id = voice_id or self.default_voice_id
        url = f"{TTS_URL}/{voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": self.model_id,
        }
        params = {"output_format": output_format}

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, headers=headers, json=payload, params=params,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(
                        f"ElevenLabs TTS failed ({resp.status}): {body[:200]}"
                    )
                audio_bytes = await resp.read()

        logger.info(
            "TTS synthesized %d chars → %d bytes (%s)",
            len(text), len(audio_bytes), output_format,
        )
        return audio_bytes
