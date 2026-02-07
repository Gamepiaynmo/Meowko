"""Audio processing utilities for voice streaming."""

import io
import struct
import threading
import wave

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
        samples = struct.unpack(f"<{len(pcm_48k_stereo) // 2}h", pcm_48k_stereo)
        # Average stereo pairs to mono
        mono = []
        for i in range(0, len(samples), 2):
            mono.append((samples[i] + samples[i + 1]) // 2)
        return struct.pack(f"<{len(mono)}h", *mono)

    @staticmethod
    def tts_to_discord(pcm_48k_mono: bytes) -> bytes:
        """Convert TTS audio to Discord format.

        48kHz mono (1ch, 16-bit) → 48kHz stereo (2ch, 16-bit).
        Mono→stereo by duplicating channels.
        2x expansion.
        """
        samples = struct.unpack(f"<{len(pcm_48k_mono) // 2}h", pcm_48k_mono)
        out = []
        for s in samples:
            out.extend([s, s])
        return struct.pack(f"<{len(out)}h", *out)


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
            self._finished = True

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
        """Clean up resources."""
        with self._lock:
            self._buffer.clear()
