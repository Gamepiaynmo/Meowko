"""Voice channel handling - streaming STT/TTS integration.

Data flow:
  Discord Voice (48kHz stereo PCM, 20ms frames)
    → per-user routing via BasicSink callback
    → AudioResampler.discord_to_stt() (48kHz stereo → 48kHz mono)
    → SonioxStreamingSTT WebSocket (client-side silence → end stream)
    → committed transcript
    → LLMClient.chat_stream() (streaming tokens)
    → _TagStripper (incremental tag removal)
    → FishAudioStreamingTTS WebSocket (44.1kHz PCM → resample → 48kHz)
    → AudioResampler.tts_to_discord() (48kHz mono → 48kHz stereo)
    → PCMStreamSource buffer → VoiceClient.play()
"""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterable

import discord
from discord.ext.voice_recv import BasicSink, VoiceRecvClient

from src.config import get_config
from src.core.context_builder import ContextBuilder
from src.core.user_state import UserState
from src.discord.handlers import format_user_message
from src.media.audio import AudioResampler, PCMStreamSource, generate_ding
from src.providers.fish_audio import FishAudioStreamingTTS
from src.providers.soniox import SonioxStreamingSTT
from src.providers.llm_client import LLMClient

logger = logging.getLogger("meowko.discord.voice")

# Tag patterns for stripping LLM output for voice (used for text channel echo)
_TTI_BLOCK_RE = re.compile(r"\[tti\].*?\[/tti\]", re.DOTALL)
_TTS_TAG_RE = re.compile(r"\[/?tts\]")

# Tags the incremental stripper recognises
_KNOWN_TAGS = {"[tti]", "[/tti]", "[tts]", "[/tts]"}
_MAX_TAG_LEN = 6  # len("[/tti]")


class _TagStripper:
    """Incremental filter that strips [tts]/[/tts] tags and suppresses [tti]...[/tti] blocks.

    Feed LLM tokens one by one; the returned string is safe to forward to TTS.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_tti = False

    def feed(self, token: str) -> str:
        self._buf += token
        return self._drain()

    def flush(self) -> str:
        """Return any remaining buffered text (call at end of stream)."""
        result = "" if self._in_tti else self._buf
        self._buf = ""
        return result

    def _drain(self) -> str:
        out: list[str] = []
        while self._buf:
            if self._in_tti:
                idx = self._buf.find("[/tti]")
                if idx >= 0:
                    self._buf = self._buf[idx + 6:]
                    self._in_tti = False
                    continue
                # Keep last (_MAX_TAG_LEN - 1) chars for partial closing-tag match
                keep = _MAX_TAG_LEN - 1
                if len(self._buf) > keep:
                    self._buf = self._buf[-keep:]
                break

            bracket = self._buf.find("[")
            if bracket < 0:
                out.append(self._buf)
                self._buf = ""
                break

            if bracket > 0:
                out.append(self._buf[:bracket])
                self._buf = self._buf[bracket:]

            # Buffer starts with "["
            if self._buf.startswith("[tti]"):
                self._in_tti = True
                self._buf = self._buf[5:]
                continue
            if self._buf.startswith("[tts]"):
                self._buf = self._buf[5:]
                continue
            if self._buf.startswith("[/tts]"):
                self._buf = self._buf[6:]
                continue
            if self._buf.startswith("[/tti]"):
                # Orphaned closing tag — skip
                self._buf = self._buf[6:]
                continue

            # Could still be the start of a known tag — wait for more input
            if any(tag.startswith(self._buf) for tag in _KNOWN_TAGS):
                break

            # Not a recognised tag — emit the "[" and continue
            out.append("[")
            self._buf = self._buf[1:]

        return "".join(out)


class UserAudioStream:
    """Per-user audio state — routes Discord audio to streaming STT."""

    def __init__(
        self,
        user: discord.Member,
        session: "VoiceSession",
    ) -> None:
        self.user = user
        self.session = session
        self._stt: SonioxStreamingSTT | None = None
        self._silence_task: asyncio.Task[None] | None = None
        self._connecting = False
        self._connected_event = asyncio.Event()
        self._audio_buffer: list[bytes] = []
        self._audio_buffer_bytes = 0
        self._pending_tasks: set[asyncio.Task[None]] = set()

        config = get_config()
        self._endpointing_secs = config.voice.get("endpointing_ms", 500) / 1000.0
        # Cap buffer at ~20s of 48kHz mono 16-bit audio (1920000 bytes)
        self._max_buffer_bytes = 1920000

    async def ensure_connected(self) -> None:
        """Lazy-connect (or reconnect) the STT WebSocket."""
        # Check if existing connection is still alive
        if self._connected_event.is_set() and self._stt and self._stt._connected:
            return
        # Need to (re)connect
        if self._connected_event.is_set():
            # Was connected before but dropped — clean up old state
            self._connected_event.clear()
            self._connecting = False
            if self._stt:
                await self._stt.close()
                self._stt = None
            logger.info("STT connection lost for %s, reconnecting", self.user.display_name)
        if self._connecting:
            # Another coroutine is connecting — wait for it
            await self._connected_event.wait()
            return
        self._connecting = True
        try:
            self._stt = SonioxStreamingSTT(
                on_committed=self._on_committed,
            )
            await self._stt.connect()
        except Exception:
            logger.exception("STT connection failed for %s", self.user.display_name)
            if self._stt:
                await self._stt.close()
                self._stt = None
            self._connecting = False
            return
        self._connected_event.set()
        self._connecting = False
        logger.info("STT stream connected for user %s", self.user.display_name)

        # Flush any audio buffered during connection
        for chunk in self._audio_buffer:
            await self._stt.send_audio(chunk)
        self._audio_buffer.clear()
        self._audio_buffer_bytes = 0

    async def feed_audio(self, pcm_48k_stereo: bytes) -> None:
        """Resample and send audio to STT. Also check for barge-in."""
        # Barge-in: interrupt playback once, not on every frame
        if self.session.is_playing():
            self.session.interrupt_playback()

        pcm_48k_mono = AudioResampler.discord_to_stt(pcm_48k_stereo)

        # Reconnect if the STT WebSocket dropped
        if self._connected_event.is_set() and (not self._stt or not self._stt._connected):
            await self.ensure_connected()

        if not self._connected_event.is_set():
            # Buffer audio while connecting (with cap)
            if self._audio_buffer_bytes < self._max_buffer_bytes:
                self._audio_buffer.append(pcm_48k_mono)
                self._audio_buffer_bytes += len(pcm_48k_mono)
            # Kick off connection if not started
            if not self._connecting:
                asyncio.create_task(self.ensure_connected())
            self._reset_silence_timer()
            return

        assert self._stt is not None
        await self._stt.send_audio(pcm_48k_mono)
        self._reset_silence_timer()

    def _reset_silence_timer(self) -> None:
        """Reset the silence timer — end stream after endpointing_ms of no audio."""
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = asyncio.create_task(self._silence_timeout())

    async def _silence_timeout(self) -> None:
        """Wait for silence duration, then end the stream to finalise the transcript."""
        try:
            await asyncio.sleep(self._endpointing_secs)
            if self._stt and self._stt._connected:
                await self._stt.end_stream()
        except asyncio.CancelledError:
            pass

    async def _on_committed(self, text: str) -> None:
        """Forward committed transcript to the voice session.

        Launched as a task so it doesn't block the STT receive loop.
        """
        logger.info("STT committed [%s]: %s", self.user.display_name, text)
        task = asyncio.create_task(self._handle_committed(text))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _handle_committed(self, text: str) -> None:
        try:
            await self.session.handle_transcript(self.user, text)
        except Exception as e:
            logger.error("Error handling transcript: %r", e)

    async def close(self) -> None:
        """Tear down the STT WebSocket and cancel pending tasks."""
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        if self._stt:
            await self._stt.close()
            self._stt = None


class VoiceSession:
    """Per-guild voice session — orchestrates STT → LLM → TTS → playback."""

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.voice_client: VoiceRecvClient | None = None
        self._user_streams: dict[int, UserAudioStream] = {}
        self._processing_lock = asyncio.Lock()
        self._current_source: PCMStreamSource | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._last_frame_time: float = 0.0
        self._last_listener_restart: float = 0.0
        self._listener_generation: int = 0
        self._voice_ws_fingerprint: tuple[int | None, str | None, str | None] | None = None

        self._context_builder = ContextBuilder()
        self._llm_client = LLMClient()
        self._user_state = UserState()
        self._ding_pcm = generate_ding()

    async def join(self, channel: discord.VoiceChannel | discord.StageChannel) -> None:
        """Connect to a voice channel and start listening."""
        self._loop = asyncio.get_running_loop()
        self.voice_client = await channel.connect(cls=VoiceRecvClient)
        self._voice_ws_fingerprint = self._capture_voice_ws_fingerprint()

        self._frame_count = 0
        self._start_listening()
        self._health_task = asyncio.create_task(self._health_monitor())
        logger.info("Joined voice channel: %s (guild: %s)", channel.name, self.guild.name)

    def _start_listening(self) -> None:
        """(Re)start the voice receive listener with a fresh decryptor."""
        if not self.voice_client:
            return
        # Guard against stale callbacks from older readers if the library
        # leaves previous socket listeners attached during reconnect churn.
        self._listener_generation += 1
        generation = self._listener_generation
        self._last_listener_restart = time.monotonic()

        def on_audio(
            user: discord.Member | discord.User | None,
            data: discord.ext.voice_recv.VoiceData,
        ) -> None:
            if generation != self._listener_generation:
                return
            if user is None or user.bot:
                return
            self._last_frame_time = time.monotonic()
            self._frame_count += 1
            if self._frame_count % 50 == 1:
                logger.info("Voice frame %d from %s (%d bytes)", self._frame_count, user, len(data.pcm))
            # BasicSink callback runs in a thread — schedule coroutine on event loop
            assert self._loop is not None
            asyncio.run_coroutine_threadsafe(
                self._on_audio_frame(user, data.pcm), self._loop,
            )

        self.voice_client.stop_listening()
        self._drain_stale_voice_packets()
        self.voice_client.listen(BasicSink(on_audio))
        logger.info("Voice listener started (guild: %s)", self.guild.name)

    def _drain_stale_voice_packets(self, max_packets: int = 256) -> None:
        """Drop queued UDP packets that may belong to an old voice session key."""
        vc = self.voice_client
        if not vc:
            return
        conn = getattr(vc, "_connection", None)
        sock = getattr(conn, "socket", None)
        if not sock:
            return

        drained = 0
        while drained < max_packets:
            try:
                sock.recv(2048)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            drained += 1

        if drained:
            logger.info(
                "Drained %d stale voice UDP packets before listener restart (guild: %s)",
                drained,
                self.guild.name,
            )

    async def leave(self) -> None:
        """Close all user streams and disconnect from voice."""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            self._health_task = None
        for stream in self._user_streams.values():
            await stream.close()
        self._user_streams.clear()
        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.stop_listening()
            await self.voice_client.disconnect()
        self.voice_client = None
        self._voice_ws_fingerprint = None
        logger.info("Left voice channel (guild: %s)", self.guild.name)

    async def _on_audio_frame(self, user: discord.Member | discord.User, pcm_data: bytes) -> None:
        """Route incoming audio to the correct user stream."""
        user_id = user.id
        if user_id not in self._user_streams:
            member = self.guild.get_member(user_id)
            if member is None:
                return
            self._user_streams[user_id] = UserAudioStream(member, self)
        await self._user_streams[user_id].feed_audio(pcm_data)

    def is_playing(self) -> bool:
        """Check if the bot is currently playing audio."""
        return (
            self.voice_client is not None
            and self.voice_client.is_playing()
        )

    def interrupt_playback(self) -> None:
        """Barge-in — stop current playback immediately (idempotent)."""
        if not self._current_source:
            return
        self._current_source.interrupt()
        self._current_source = None
        if self.voice_client and self.voice_client.is_playing():
            # VoiceRecvClient.stop() also stops listening; use stop_playing() so
            # receive stays active while interrupting TTS playback.
            stop_playing = getattr(self.voice_client, "stop_playing", None)
            if callable(stop_playing):
                stop_playing()
            else:
                self.voice_client.stop()
        logger.debug("Playback interrupted (barge-in)")

    async def handle_transcript(self, user: discord.Member, text: str) -> None:
        """Process a committed transcript — serialized one at a time."""
        async with self._processing_lock:
            await self._process_voice_turn(user, text)

    async def _play_ding(self) -> None:
        """Play a short ding sound to acknowledge STT commit."""
        if not self.voice_client or not self.voice_client.is_connected():
            return
        if self.voice_client.is_playing():
            self.voice_client.stop()

        source = PCMStreamSource()
        source.feed(self._ding_pcm)
        source.finish()

        done = asyncio.Event()

        def after(error: Exception | None) -> None:
            if error:
                logger.error("Ding playback error: %s", error)
            if self._loop:
                self._loop.call_soon_threadsafe(done.set)

        self.voice_client.play(source, after=after)
        await done.wait()

    async def _process_voice_turn(self, user: discord.Member, text: str) -> None:
        """Build context → stream LLM → strip tags → stream TTS → play.

        LLM tokens are piped through a tag stripper directly into the Fish Audio
        WebSocket TTS, so audio generation begins as soon as the first tokens
        arrive rather than waiting for the full LLM response.
        """
        await self._play_ding()

        user_id = user.id
        user_name = user.display_name
        user_message = format_user_message(user_name, f"[Voice channel: {text}]")

        # Resolve persona per-turn so mid-session switches take effect
        persona_id = self._user_state.get_persona_id(user_id)
        persona = self._context_builder.load_persona(persona_id)
        voice_id: str | None = persona["voice_id"]

        # Build context and start streaming LLM
        context = await self._context_builder.build_context(
            user_id=user_id,
            persona_id=persona_id,
        )
        context.append({"role": "user", "content": user_message})

        try:
            llm_stream = await self._llm_client.chat_stream(context)
        except Exception:
            logger.exception("LLM error during voice turn")
            return

        # Pipe LLM tokens through tag stripper → TTS WebSocket
        stripper = _TagStripper()

        async def stripped_tokens() -> AsyncIterable[str]:
            async for token in llm_stream:
                clean = stripper.feed(token)
                if clean:
                    yield clean
            remaining = stripper.flush()
            if remaining:
                yield remaining

        # Stream TTS and play concurrently with LLM generation
        pcm_data = await self._stream_tts_and_play(stripped_tokens(), voice_id)

        # TTS may fail early; drain remaining LLM tokens so response stats are populated.
        if llm_stream.response is None:
            try:
                async for _ in llm_stream:
                    pass
            except Exception:
                logger.exception("Failed draining LLM stream after TTS error")

        llm_response = llm_stream.response
        if llm_response is None:
            # Last-resort fallback: run non-streaming completion so the turn is not lost.
            try:
                llm_response = await self._llm_client.chat(context)
                logger.warning("Recovered voice turn with non-streaming LLM fallback")
            except Exception:
                logger.exception("LLM fallback failed after TTS stream error")

        # LLM stream is now fully consumed — get response stats
        if llm_response is None:
            logger.warning("LLM stream ended without response")
            return

        raw_reply = llm_response.content
        logger.info("Voice LLM reply: %s", raw_reply[:100])

        # Send full text to the voice channel's text chat
        voice_text = self._strip_tags(raw_reply)
        if voice_text.strip() and self.voice_client and self.voice_client.channel:
            try:
                await self.voice_client.channel.send(voice_text)
            except Exception:
                logger.exception("Failed to send text to voice channel")

        # Cache audio
        assistant_attachments: list[dict[str, str]] | None = None
        if pcm_data:
            wav_data = AudioResampler.pcm_to_wav(pcm_data)
            path = self._context_builder.save_cache_file(
                persona_id, user_id, "tts.wav", wav_data,
            )
            assistant_attachments = [{"type": "tts", "path": path}]

        # Save conversation turn with attachment paths
        self._context_builder.save_turn(
            user_id=user_id,
            user_message=user_message,
            assistant_message=raw_reply,
            persona_id=persona_id,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
            total_tokens=llm_response.total_tokens,
            cached_tokens=llm_response.cached_tokens,
            cost=llm_response.cost,
            assistant_attachments=assistant_attachments,
        )

    async def _stream_tts_and_play(
        self,
        text_stream: AsyncIterable[str],
        voice_id: str | None = None,
    ) -> bytes | None:
        """Create PCMStreamSource, start playback, feed TTS audio, wait for finish.

        Returns the raw 48kHz mono PCM audio data for caching, or None on failure.
        """
        if not self.voice_client or not self.voice_client.is_connected():
            return None

        # Stop any leftover playback
        if self.voice_client.is_playing():
            self.voice_client.stop()

        source = PCMStreamSource()
        self._current_source = source

        raw_pcm_chunks: list[bytes] = []

        async def on_audio(pcm_48k: bytes) -> None:
            raw_pcm_chunks.append(pcm_48k)
            pcm_48k_stereo = AudioResampler.tts_to_discord(pcm_48k)
            source.feed(pcm_48k_stereo)

        tts = FishAudioStreamingTTS(on_audio=on_audio)

        # Start playback immediately — PCMStreamSource returns silence on underrun
        playback_done = asyncio.Event()

        def after_playback(error: Exception | None) -> None:
            if error:
                logger.error("Playback error: %s", error)
            if self._loop:
                self._loop.call_soon_threadsafe(playback_done.set)

        self.voice_client.play(source, after=after_playback)

        # Stream TTS audio into the source concurrently with playback
        try:
            await tts.synthesize_streaming(text_stream, voice_id=voice_id)
        except Exception:
            logger.exception("Streaming TTS error")
        finally:
            source.finish()
            await tts.close()

        # Wait for playback to drain the buffer
        await playback_done.wait()

        self._current_source = None

        # Refresh the reader's decryptor with the current secret key.
        # The voice WS may reconnect during playback, giving a new key
        # that the reader's PacketDecryptor doesn't know about.
        # This is non-destructive — no listener restart, no packet loss.
        self._refresh_reader_key()

        return b"".join(raw_pcm_chunks) if raw_pcm_chunks else None

    def _refresh_reader_key(self) -> None:
        """Update the voice reader's decryptor with the current secret key."""
        vc = self.voice_client
        if not vc:
            return
        reader = getattr(vc, '_reader', None)
        if reader and hasattr(reader, 'update_secret_key'):
            try:
                reader.update_secret_key(bytes(vc.secret_key))
            except Exception:
                logger.debug("Failed to refresh reader secret key", exc_info=True)

    async def _health_monitor(self) -> None:
        """Periodically check voice receive pipeline health."""
        try:
            while True:
                await asyncio.sleep(5)
                self._health_check()
        except asyncio.CancelledError:
            pass

    def _capture_voice_ws_fingerprint(self) -> tuple[int | None, str | None, str | None] | None:
        """Return identifiers that change when Discord rolls the voice WS/session."""
        vc = self.voice_client
        if not vc or not vc.is_connected():
            return None

        conn = getattr(vc, "_connection", None)
        if not conn:
            return None

        ws_obj = getattr(conn, "ws", None)
        ws_id = id(ws_obj) if ws_obj is not None else None
        endpoint = getattr(conn, "endpoint", None)
        session_id = getattr(conn, "session_id", None)
        return (ws_id, endpoint, session_id)

    def _health_check(self) -> None:
        """Single health check cycle: verify reader, refresh key, log status."""
        vc = self.voice_client
        if not vc or not vc.is_connected():
            return

        # Keep reader decrypt state aligned with the latest voice secret key.
        # This is cheap and avoids decrypt drift after transient reconnects.
        self._refresh_reader_key()

        # If Discord rolled the voice WS/session, record it and rely on key
        # refresh instead of listener restarts. In practice, repeated
        # stop/listen cycles can leave stale callbacks in some environments,
        # which then produce noisy CryptoError decrypt logs.
        current_fingerprint = self._capture_voice_ws_fingerprint()
        if self._voice_ws_fingerprint is None:
            self._voice_ws_fingerprint = current_fingerprint
        elif current_fingerprint != self._voice_ws_fingerprint:
            logger.warning(
                "Voice websocket session changed, refreshed reader key (guild: %s)",
                self.guild.name,
            )
            self._voice_ws_fingerprint = current_fingerprint
        else:
            self._voice_ws_fingerprint = current_fingerprint

        # Check if the reader is alive — if MISSING or not listening, restart
        reader = getattr(vc, '_reader', None)
        if not reader or reader == "MISSING":
            now = time.monotonic()
            if now - self._last_listener_restart > 10:
                logger.warning("Voice reader is missing, restarting listener (guild: %s)", self.guild.name)
                self._start_listening()
        else:
            is_listening = True
            if hasattr(reader, "is_listening") and callable(reader.is_listening):
                is_listening = bool(reader.is_listening())
            elif hasattr(reader, "active"):
                is_listening = bool(getattr(reader, "active"))
            if not is_listening:
                now = time.monotonic()
                if now - self._last_listener_restart > 10:
                    logger.warning("Voice reader is not listening, restarting listener (guild: %s)", self.guild.name)
                    self._start_listening()

        # Proactively refresh the decryptor's secret key
        self._refresh_reader_key()

        # Recover from stale listener after prolonged silence.
        # Discord voice gateway can silently reconnect during idle, leaving
        # the reader alive but detached from our BasicSink callback.
        now = time.monotonic()
        # If no frame has ever arrived, treat listener uptime as silence.
        silence_start = (
            self._last_frame_time
            if self._last_frame_time > 0
            else self._last_listener_restart
        )
        silence = now - silence_start if silence_start > 0 else None
        if (
            silence is not None
            and silence > 120
            and now - self._last_listener_restart > 120
        ):
            logger.warning(
                "No audio frames for %.0fs, restarting listener (guild: %s)",
                silence, self.guild.name,
            )
            self._start_listening()

        logger.debug("Health check OK (guild: %s)", self.guild.name)

    @staticmethod
    def _strip_tags(text: str) -> str:
        """Remove [tti]...[/tti] blocks entirely, strip [tts]/[/tts] tags keeping content."""
        # Remove tti blocks completely
        text = _TTI_BLOCK_RE.sub("", text)
        # Strip tts tags but keep content
        text = _TTS_TAG_RE.sub("", text)
        return text.strip()



class VoiceSessionManager:
    """Registry of per-guild VoiceSession instances."""

    def __init__(self) -> None:
        self._sessions: dict[int, VoiceSession] = {}

    async def join(self, channel: discord.VoiceChannel | discord.StageChannel) -> VoiceSession:
        """Create or reuse a voice session for the channel's guild."""
        guild_id = channel.guild.id
        if guild_id in self._sessions:
            session = self._sessions[guild_id]
            if session.voice_client and session.voice_client.is_connected():
                # Already connected — move to new channel if different
                if session.voice_client.channel != channel:
                    await session.leave()
                else:
                    return session
        session = VoiceSession(channel.guild)
        await session.join(channel)
        self._sessions[guild_id] = session
        return session

    async def leave(self, guild: discord.Guild) -> None:
        """Tear down the session for a guild."""
        guild_id = guild.id
        session = self._sessions.pop(guild_id, None)
        if session:
            await session.leave()

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Auto-join when a user joins a voice channel, auto-leave when bot is alone."""
        if member.bot:
            return

        guild = member.guild
        guild_id = guild.id

        # Auto-join: user joined a voice channel and bot is not in a session
        if after.channel and (before.channel is None or before.channel != after.channel):
            session = self._sessions.get(guild_id)
            if session is None or session.voice_client is None or not session.voice_client.is_connected():
                logger.info(
                    "Auto-joining %s's voice channel: %s (guild: %s)",
                    member.display_name, after.channel.name, guild.name,
                )
                try:
                    await self.join(after.channel)
                except Exception:
                    logger.exception("Auto-join failed for channel %s", after.channel.name)
                return

        # Auto-leave: check if bot is now alone
        session = self._sessions.get(guild_id)
        if session is None or session.voice_client is None:
            return

        vc = session.voice_client
        if not vc.is_connected() or vc.channel is None:
            return

        non_bot_members = [m for m in vc.channel.members if not m.bot]
        if len(non_bot_members) == 0:
            logger.info("Bot is alone in voice channel, auto-leaving (guild: %s)", guild.name)
            await self.leave(guild)
