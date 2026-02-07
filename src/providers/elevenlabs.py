"""ElevenLabs STT and TTS providers."""

import asyncio
import base64
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp
import websockets

from src.config import get_config

logger = logging.getLogger("meowko")

BASE_URL = "https://api.elevenlabs.io/v1"
STT_URL = f"{BASE_URL}/speech-to-text"
TTS_URL = f"{BASE_URL}/text-to-speech"
STREAMING_STT_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"

# Callback type aliases
AsyncTextCallback = Callable[[str], Coroutine[Any, Any, None]]
AsyncAudioCallback = Callable[[bytes], Coroutine[Any, Any, None]]

def _load_config() -> dict[str, Any]:
    """Load the elevenlabs config section."""
    el = get_config().elevenlabs
    return {
        "api_key": el["api_key"],
        "timeout": el["timeout"],
        "model_id": el["model_id"],
        "default_voice_id": el["default_voice_id"],
        "stt_model": el.get("stt_model", "scribe_v2"),
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


class ElevenLabsSTT:
    """Batch speech-to-text via ElevenLabs REST API."""

    def __init__(self) -> None:
        cfg = _load_config()
        self._api_key = cfg["api_key"]
        self._timeout = cfg["timeout"]
        self._stt_model = cfg["stt_model"]
        self._language = cfg["language"]

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        content_type: str = "audio/mpeg",
    ) -> str:
        """Transcribe audio bytes to text."""
        headers = {"xi-api-key": self._api_key}

        form = aiohttp.FormData()
        form.add_field("model_id", self._stt_model)
        form.add_field(
            "file", audio_bytes,
            filename=filename,
            content_type=content_type,
        )
        if self._language:
            form.add_field("language_code", self._language)

        timeout = aiohttp.ClientTimeout(total=self._timeout)
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
    """Batch text-to-speech via ElevenLabs REST API."""

    def __init__(self) -> None:
        self._cfg = _load_config()

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

        timeout = aiohttp.ClientTimeout(total=self._cfg["timeout"])
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


class ElevenLabsStreamingSTT:
    """Persistent WebSocket for realtime speech-to-text (one per speaking user).

    Uses ElevenLabs Scribe v2 realtime with VAD-based endpointing.
    """

    def __init__(
        self,
        on_partial: AsyncTextCallback | None = None,
        on_committed: AsyncTextCallback | None = None,
    ) -> None:
        cfg = _load_config()
        self._api_key = cfg["api_key"]

        self._url = (
            f"{STREAMING_STT_URL}"
            f"?model_id=scribe_v2_realtime"
            f"&audio_format=pcm_48000"
            f"&commit_strategy=manual"
        )
        if cfg["language"]:
            self._url += f"&language_code={cfg['language']}"

        self.on_partial = on_partial
        self.on_committed = on_committed

        self._ws: websockets.ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._connected = False

    async def connect(self) -> None:
        """Open WebSocket and start the receive loop."""
        if self._connected:
            return
        self._ws = await websockets.connect(
            self._url,
            additional_headers={"xi-api-key": self._api_key},
        )
        self._connected = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.debug("Streaming STT connected")

    _send_count: int = 0

    async def send_audio(self, pcm_16k_mono: bytes) -> None:
        """Send a chunk of 16kHz mono PCM audio to the STT WebSocket."""
        if not self._ws or not self._connected:
            return
        encoded = base64.b64encode(pcm_16k_mono).decode("ascii")
        msg = json.dumps({"message_type": "input_audio_chunk", "audio_base_64": encoded})
        try:
            await self._ws.send(msg)
            self._send_count += 1
            if self._send_count % 50 == 1:
                logger.info("Streaming STT: sent %d audio chunks (%d bytes each)", self._send_count, len(pcm_16k_mono))
        except websockets.ConnectionClosed as e:
            logger.warning("Streaming STT WS closed during send: %s", e)
            self._connected = False

    async def commit(self) -> None:
        """Send a manual commit to finalize the current transcript."""
        if not self._ws or not self._connected:
            return
        # Send an empty audio chunk with commit=true
        msg = json.dumps({
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(b"\x00\x00").decode("ascii"),
            "commit": True,
        })
        try:
            await self._ws.send(msg)
            logger.debug("Streaming STT: manual commit sent")
        except websockets.ConnectionClosed as e:
            logger.warning("Streaming STT WS closed during commit: %s", e)
            self._connected = False

    async def _receive_loop(self) -> None:
        """Dispatch incoming STT events to callbacks."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                msg_type = data.get("message_type", "")
                if msg_type == "partial_transcript":
                    text = data.get("text", "").strip()
                    if text and self.on_partial:
                        await self.on_partial(text)
                elif msg_type == "committed_transcript":
                    text = data.get("text", "").strip()
                    if text and self.on_committed:
                        await self.on_committed(text)
                elif msg_type in ("session_started", "vad_event"):
                    logger.debug("Streaming STT: %s", msg_type)
                else:
                    logger.info("Streaming STT event: %s", str(raw)[:200])
        except websockets.ConnectionClosed as e:
            logger.warning("Streaming STT WS closed: %s", e)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Streaming STT receive loop error")
        finally:
            self._connected = False

    async def close(self) -> None:
        """Cancel receive task and close WebSocket."""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.debug("Streaming STT closed")


class ElevenLabsStreamingTTS:
    """Streaming text-to-speech via ElevenLabs REST API.

    POSTs text, streams back raw PCM audio via chunked response.
    """

    CHUNK_SIZE = 4096

    def __init__(self, on_audio: AsyncAudioCallback | None = None) -> None:
        self._cfg = _load_config()
        self.on_audio = on_audio

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
        timeout = aiohttp.ClientTimeout(total=cfg["timeout"])
        async with aiohttp.ClientSession(timeout=timeout) as session:
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
