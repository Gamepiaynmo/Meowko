"""Soniox STT providers — batch (REST) and streaming (WebSocket)."""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp
import websockets

from src.config import get_config

logger = logging.getLogger("meowko.providers.soniox")

BASE_URL = "https://api.soniox.com/v1"
STREAMING_URL = "wss://stt-rt.soniox.com/transcribe-websocket"

AsyncTextCallback = Callable[[str], Coroutine[Any, Any, None]]


def _load_config() -> dict[str, Any]:
    """Load the soniox config section."""
    cfg = get_config().soniox
    return {
        "api_key": cfg["api_key"],
        "model": cfg["model"],
        "streaming_model": cfg["streaming_model"],
        "language_hints": cfg.get("language_hints") or [],
        "timeout": cfg["timeout"],
    }


class SonioxSTT:
    """Batch speech-to-text via Soniox async REST API."""

    def __init__(self) -> None:
        cfg = _load_config()
        self._api_key = cfg["api_key"]
        self._model = cfg["model"]
        self._language_hints = cfg["language_hints"]
        self._timeout = cfg["timeout"]
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def transcribe(self, audio_url: str) -> str:
        """Transcribe audio from a URL via Soniox async API.

        Flow: POST create → poll GET transcript → DELETE cleanup.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "audio_url": audio_url,
        }
        if self._language_hints:
            payload["language_hints"] = self._language_hints

        session = await self._get_session()

        # 1. Create transcription
        async with session.post(
            f"{BASE_URL}/transcriptions", headers=headers, json=payload,
        ) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                raise RuntimeError(
                    f"Soniox STT create failed ({resp.status}): {body[:200]}"
                )
            data = await resp.json()

        transcription_id = data["id"]
        logger.info("Soniox STT created transcription %s", transcription_id)

        # 2. Poll for transcript
        poll_url = f"{BASE_URL}/transcriptions/{transcription_id}/transcript"
        text = await self._poll_transcript(session, headers, poll_url)

        # 3. Delete transcription (fire-and-forget)
        asyncio.create_task(self._delete(session, headers, transcription_id))

        return text

    async def _poll_transcript(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        url: str,
    ) -> str:
        """Poll until transcript is ready, with exponential backoff."""
        delay = 0.5
        elapsed = 0.0
        while elapsed < self._timeout:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data.get("text", "").strip()
                    logger.info("Soniox STT transcribed: %s", text[:80])
                    return text
                if resp.status == 409:
                    # Transcription not yet complete
                    await asyncio.sleep(delay)
                    elapsed += delay
                    delay = min(delay * 1.5, 5.0)
                    continue
                body = await resp.text()
                raise RuntimeError(
                    f"Soniox STT poll failed ({resp.status}): {body[:200]}"
                )
        raise TimeoutError(
            f"Soniox STT transcription timed out after {self._timeout}s"
        )

    async def _delete(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        transcription_id: str,
    ) -> None:
        """Delete a completed transcription (best-effort cleanup)."""
        try:
            async with session.delete(
                f"{BASE_URL}/transcriptions/{transcription_id}",
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Soniox STT delete %s returned %d",
                        transcription_id, resp.status,
                    )
        except Exception:
            logger.debug("Soniox STT delete failed", exc_info=True)


class SonioxStreamingSTT:
    """WebSocket for realtime speech-to-text (one per speaking user).

    Uses Soniox realtime STT. The caller ends the stream by calling
    end_stream() after a silence timeout, which sends an empty frame.
    The server then finalises all remaining tokens, sends ``finished: true``,
    and closes the connection.  on_committed fires with the full transcript.
    """

    def __init__(
        self,
        on_partial: AsyncTextCallback | None = None,
        on_committed: AsyncTextCallback | None = None,
    ) -> None:
        self._cfg = _load_config()

        self.on_partial = on_partial
        self.on_committed = on_committed

        self._ws: websockets.ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._connected = False
        self._send_count = 0

        self._final_parts: list[str] = []

    async def connect(self) -> None:
        """Open WebSocket, send config, and start the receive loop."""
        if self._connected:
            return

        self._ws = await websockets.connect(STREAMING_URL)

        config_msg: dict[str, Any] = {
            "api_key": self._cfg["api_key"],
            "model": self._cfg["streaming_model"],
            "audio_format": "s16le",
            "sample_rate": 48000,
            "num_channels": 1,
        }
        if self._cfg["language_hints"]:
            config_msg["language_hints"] = self._cfg["language_hints"]

        await self._ws.send(json.dumps(config_msg))

        self._connected = True
        self._final_parts.clear()
        self._send_count = 0
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.debug("Soniox streaming STT connected")

    async def send_audio(self, pcm_48k_mono: bytes) -> None:
        """Send a chunk of 48kHz mono PCM audio as a binary WebSocket frame."""
        if not self._ws or not self._connected:
            return
        try:
            await self._ws.send(pcm_48k_mono)
            self._send_count += 1
            if self._send_count % 50 == 1:
                logger.info(
                    "Soniox streaming STT: sent %d audio chunks (%d bytes each)",
                    self._send_count, len(pcm_48k_mono),
                )
        except websockets.ConnectionClosed as e:
            logger.warning("Soniox streaming STT WS closed during send: %s", e)
            self._connected = False

    async def end_stream(self) -> None:
        """Signal end-of-audio by sending an empty frame.

        The server will finalise remaining tokens, send ``finished: true``,
        and close the connection.  The receive loop handles firing
        on_committed with the accumulated transcript.
        """
        if not self._ws or not self._connected:
            return
        try:
            await self._ws.send("")
            logger.debug("Soniox streaming STT: end-of-stream sent")
        except websockets.ConnectionClosed as e:
            logger.warning("Soniox streaming STT WS closed during end_stream: %s", e)
            self._connected = False

    async def _receive_loop(self) -> None:
        """Parse token-based responses and fire callbacks."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                data = json.loads(raw)

                # Handle error messages
                if "error_code" in data:
                    logger.error(
                        "Soniox streaming STT error: %s — %s",
                        data.get("error_code"), data.get("error_message", ""),
                    )
                    break

                # Accumulate final tokens from this response
                for token in data.get("tokens", []):
                    text = token.get("text", "")
                    if token.get("is_final"):
                        self._final_parts.append(text)
                    elif text and self.on_partial:
                        await self.on_partial(text)

                # Stream finished — fire committed callback with full transcript
                if data.get("finished"):
                    committed_text = "".join(self._final_parts).strip()
                    self._final_parts.clear()
                    if committed_text and self.on_committed:
                        await self.on_committed(committed_text)
                    logger.debug("Soniox streaming STT finished")
                    break

        except websockets.ConnectionClosed as e:
            logger.warning("Soniox streaming STT WS closed: %s", e)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Soniox streaming STT receive loop error")
        finally:
            self._connected = False
            # Close the WS here so the server's close frame is consumed
            # promptly — avoids a 10s hang in close() later.
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None

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
        self._final_parts.clear()
        logger.debug("Soniox streaming STT closed")
