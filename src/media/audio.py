"""Audio processing utilities for voice streaming."""

import io
import math
import threading
import wave
from array import array

import discord


class AudioResampler:
    """Static methods for resampling PCM audio between Discord and STT/TTS formats."""

    @staticmethod
    def discord_to_stt(pcm_48k_stereo: bytes) -> bytes:
        """Convert Discord audio to STT format.

        48kHz stereo (2ch, 16-bit) → 48kHz mono (1ch, 16-bit).
        Stereo→mono by averaging channels.
        3840 bytes in → 1920 bytes out.
        """
        samples = array("h", pcm_48k_stereo)
        mono = array("h", (
            (samples[i] + samples[i + 1]) // 2
            for i in range(0, len(samples), 2)
        ))
        return mono.tobytes()

    @staticmethod
    def tts_to_discord(pcm_48k_mono: bytes) -> bytes:
        """Convert TTS audio to Discord format.

        48kHz mono (1ch, 16-bit) → 48kHz stereo (2ch, 16-bit).
        Mono→stereo by duplicating each sample for L+R.
        2x expansion.
        """
        samples = array("h", pcm_48k_mono)
        stereo = array("h", (s for sample in samples for s in (sample, sample)))
        return stereo.tobytes()


    @staticmethod
    def pcm_to_wav(
        pcm_data: bytes, sample_rate: int = 48000, channels: int = 1, sample_width: int = 2,
    ) -> bytes:
        """Wrap raw PCM bytes in a WAV header."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
        return buf.getvalue()


def generate_ding(
    frequency: float = 880.0,
    duration_ms: int = 150,
    sample_rate: int = 48000,
    volume: float = 0.3,
) -> bytes:
    """Generate a short sine-wave ding as 48kHz stereo 16-bit PCM."""
    num_samples = int(sample_rate * duration_ms / 1000)
    fade_samples = min(num_samples // 3, int(sample_rate * 0.03))
    fade_in_samples = max(fade_samples // 3, 1)

    mono = array("h", [0] * num_samples)
    for i in range(num_samples):
        t = i / sample_rate
        value = math.sin(2 * math.pi * frequency * t) * volume
        if i < fade_in_samples:
            value *= i / fade_in_samples
        elif i >= num_samples - fade_samples:
            value *= (num_samples - i) / fade_samples
        mono[i] = int(value * 32767)

    stereo = array("h", (s for sample in mono for s in (sample, sample)))
    return stereo.tobytes()


class PCMStreamSource(discord.AudioSource):
    """Thread-safe buffered PCM audio source for discord.py voice playback.

    Feed PCM data from async TTS callbacks, read by discord.py's voice thread.
    """

    FRAME_SIZE = 3840  # 20ms at 48kHz stereo 16-bit: 48000 * 2 * 2 * 0.02

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._finished = False
        self._interrupted = False

    def feed(self, pcm_data: bytes) -> None:
        """Append PCM data to the buffer. Called from async TTS callback."""
        with self._lock:
            if not self._interrupted:
                self._buffer.extend(pcm_data)

    def finish(self) -> None:
        """Signal that no more data will be fed."""
        with self._lock:
            self._apply_fade_out()
            self._finished = True

    def _apply_fade_out(self) -> None:
        """Apply a short fade-out to the buffer tail to prevent end-of-stream clicks."""
        # ~5ms at 48kHz stereo 16-bit: 240 stereo frames × 4 bytes/frame = 960 bytes
        FADE_BYTES = 960
        buf_len = len(self._buffer)
        if buf_len < 4:
            return
        fade_len = min(buf_len, FADE_BYTES)
        fade_len -= fade_len % 4  # align to stereo sample boundary
        if fade_len < 4:
            return

        start = buf_len - fade_len
        samples = array("h", bytes(self._buffer[start:]))
        n = len(samples)
        for i in range(n):
            samples[i] = int(samples[i] * (1.0 - i / n))
        self._buffer[start:] = samples.tobytes()

    def interrupt(self) -> None:
        """Immediately stop playback (barge-in)."""
        with self._lock:
            self._interrupted = True
            self._buffer.clear()

    def read(self) -> bytes:
        """Read one frame (3840 bytes) for discord.py voice thread.

        Returns silence on buffer underrun, empty bytes when finished/interrupted.
        Called every 20ms by the voice send thread.
        """
        with self._lock:
            if self._interrupted:
                return b""
            if len(self._buffer) >= self.FRAME_SIZE:
                frame = bytes(self._buffer[:self.FRAME_SIZE])
                del self._buffer[:self.FRAME_SIZE]
                return frame
            if self._finished:
                if self._buffer:
                    # Pad remaining data with silence
                    frame = bytes(self._buffer) + b"\x00" * (self.FRAME_SIZE - len(self._buffer))
                    self._buffer.clear()
                    return frame
                return b""
            # Buffer underrun — return silence to avoid gaps
            return b"\x00" * self.FRAME_SIZE

    def is_opus(self) -> bool:
        """We provide raw PCM, not Opus."""
        return False

    def cleanup(self) -> None:
        with self._lock:
            self._buffer.clear()
