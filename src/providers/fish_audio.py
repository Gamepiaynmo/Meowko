"""Fish Audio TTS providers."""

import logging
from collections.abc import AsyncIterable, Callable, Coroutine
from typing import Any

from fishaudio import AsyncFishAudio  # type: ignore[import-untyped]
from fishaudio.types import TTSConfig  # type: ignore[import-untyped]

from src.config import get_config
from src.media.audio import AudioResampler

logger = logging.getLogger("meowko.providers.fish_audio")

# Callback type alias
AsyncAudioCallback = Callable[[bytes], Coroutine[Any, Any, None]]


def _load_config() -> dict[str, Any]:
    """Load the fish_audio config section."""
    cfg = get_config().fish_audio
    return {
        "api_key": cfg["api_key"],
        "timeout": cfg["timeout"],
        "default_voice_id": cfg["default_voice_id"],
        "latency": cfg.get("latency", "balanced"),
        "speed": cfg.get("speed", 1.0),
    }


class FishAudioTTS:
    """Batch text-to-speech via Fish Audio SDK."""

    def __init__(self) -> None:
        self._cfg = _load_config()
        self._client = AsyncFishAudio(api_key=self._cfg["api_key"])

    async def close(self) -> None:
        if hasattr(self._client, "close"):
            await self._client.close()

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        output_format: str = "mp3",
    ) -> bytes:
        """Synthesize text to audio bytes."""
        cfg = self._cfg
        voice = voice_id or cfg["default_voice_id"]
        speed = cfg["speed"]

        audio = await self._client.tts.convert(
            text=text,
            reference_id=voice or None,
            format=output_format,
            latency=cfg["latency"],
            speed=speed if speed != 1.0 else None,
        )

        audio_bytes = bytes(audio)
        logger.info(
            "TTS synthesized %d chars → %d bytes (%s)",
            len(text), len(audio_bytes), output_format,
        )
        return audio_bytes


class FishAudioStreamingTTS:
    """Streaming text-to-speech via Fish Audio WebSocket.

    Accepts an async stream of text tokens (e.g. from a streaming LLM) and
    streams back 48kHz mono PCM audio via the on_audio callback, enabling
    LLM → TTS pipelining for minimal time-to-first-audio.
    """

    def __init__(self, on_audio: AsyncAudioCallback | None = None) -> None:
        self._cfg = _load_config()
        self.on_audio = on_audio
        self._client = AsyncFishAudio(api_key=self._cfg["api_key"])

    async def close(self) -> None:
        if hasattr(self._client, "close"):
            await self._client.close()

    async def synthesize_streaming(
        self,
        text_stream: AsyncIterable[str],
        voice_id: str | None = None,
    ) -> None:
        """Stream TTS from an async token stream. Calls on_audio with 48kHz PCM chunks.

        Fish Audio outputs 44.1kHz PCM; each chunk is resampled to 48kHz
        before invoking the callback so the voice pipeline stays at 48kHz.
        """
        cfg = self._cfg
        voice = voice_id or cfg["default_voice_id"]
        speed = cfg["speed"]

        logger.info("Streaming TTS request (websocket): voice=%s", voice)

        total_bytes = 0
        leftover = b""

        async for chunk in self._client.tts.stream_websocket(
            text_stream,
            reference_id=voice or None,
            format="pcm",
            latency=cfg["latency"],
            speed=speed if speed != 1.0 else None,
            config=TTSConfig(sample_rate=44100),
        ):
            if not chunk or not self.on_audio:
                continue
            chunk = leftover + chunk
            if len(chunk) % 2:
                leftover = chunk[-1:]
                chunk = chunk[:-1]
            else:
                leftover = b""
            if chunk:
                resampled = AudioResampler.resample_mono(chunk, 44100, 48000)
                await self.on_audio(resampled)
                total_bytes += len(resampled)

        if leftover and self.on_audio:
            resampled = AudioResampler.resample_mono(leftover + b"\x00", 44100, 48000)
            await self.on_audio(resampled)
            total_bytes += len(resampled)

        logger.info("Streaming TTS completed: %d bytes (48kHz)", total_bytes)
