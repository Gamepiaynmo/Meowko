"""ElevenLabs TTS providers."""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp

from src.config import get_config

logger = logging.getLogger("meowko.providers.elevenlabs")

BASE_URL = "https://api.elevenlabs.io/v1"
TTS_URL = f"{BASE_URL}/text-to-speech"

# Callback type alias
AsyncAudioCallback = Callable[[bytes], Coroutine[Any, Any, None]]

def _load_config() -> dict[str, Any]:
    """Load the elevenlabs config section."""
    el = get_config().elevenlabs
    return {
        "api_key": el["api_key"],
        "timeout": el["timeout"],
        "model_id": el["model_id"],
        "default_voice_id": el["default_voice_id"],
        "language": el.get("language", ""),
        "voice_settings": el.get("voice_settings") or {},
    }


def _tts_payload(
    cfg: dict[str, Any], text: str,
) -> dict[str, Any]:
    """Build the shared TTS request body."""
    payload: dict[str, Any] = {
        "text": text,
        "model_id": cfg["model_id"],
    }
    if cfg["language"]:
        payload["language_code"] = cfg["language"]
    if cfg["voice_settings"]:
        payload["voice_settings"] = cfg["voice_settings"]
    return payload


class ElevenLabsTTS:
    """Batch text-to-speech via ElevenLabs REST API."""

    def __init__(self) -> None:
        self._cfg = _load_config()
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._cfg["timeout"])
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        output_format: str = "mp3_44100_128",
    ) -> bytes:
        """Synthesize text to audio bytes."""
        voice_id = voice_id or self._cfg["default_voice_id"]
        url = f"{TTS_URL}/{voice_id}"
        headers = {
            "xi-api-key": self._cfg["api_key"],
            "Content-Type": "application/json",
        }
        payload = _tts_payload(self._cfg, text)
        params = {"output_format": output_format}

        session = await self._get_session()
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


class ElevenLabsStreamingTTS:
    """Streaming text-to-speech via ElevenLabs REST API.

    POSTs text, streams back raw PCM audio via chunked response.
    """

    CHUNK_SIZE = 4096

    def __init__(self, on_audio: AsyncAudioCallback | None = None) -> None:
        self._cfg = _load_config()
        self.on_audio = on_audio
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._cfg["timeout"])
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def synthesize_streaming(
        self, text: str, voice_id: str | None = None,
    ) -> None:
        """Stream TTS for the given text. Calls on_audio with PCM chunks."""
        cfg = self._cfg
        voice_id = voice_id or cfg["default_voice_id"]
        url = f"{TTS_URL}/{voice_id}/stream"
        headers = {
            "xi-api-key": cfg["api_key"],
            "Content-Type": "application/json",
        }
        payload = _tts_payload(cfg, text)
        params = {"output_format": "pcm_48000"}

        logger.info("Streaming TTS request: model=%s voice=%s", cfg["model_id"], voice_id)

        total_bytes = 0
        # PCM 16-bit = 2 bytes per sample; keep leftover byte for alignment
        leftover = b""
        session = await self._get_session()
        async with session.post(
            url, headers=headers, json=payload, params=params,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"ElevenLabs streaming TTS failed ({resp.status}): {body[:200]}"
                )

            logger.info("Streaming TTS connected: status=%d", resp.status)

            async for chunk in resp.content.iter_chunked(self.CHUNK_SIZE):
                if not chunk or not self.on_audio:
                    continue
                chunk = leftover + chunk
                # Ensure even length for 16-bit PCM alignment
                if len(chunk) % 2:
                    leftover = chunk[-1:]
                    chunk = chunk[:-1]
                else:
                    leftover = b""
                if chunk:
                    await self.on_audio(chunk)
                    total_bytes += len(chunk)
        if leftover and self.on_audio:
            await self.on_audio(leftover + b"\x00")
            total_bytes += 2

        logger.info("Streaming TTS completed: %d chars → %d bytes", len(text), total_bytes)
