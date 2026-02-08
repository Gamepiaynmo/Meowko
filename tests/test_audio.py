"""Tests for AudioResampler and PCMStreamSource."""

import struct
import wave
import io
from array import array

from src.media.audio import AudioResampler, PCMStreamSource


class TestAudioResamplerDiscordToStt:
    def test_stereo_to_mono_halves_size(self):
        # 4 stereo samples (8 int16 values = 16 bytes) -> 4 mono samples (8 bytes)
        stereo = array("h", [100, 200, 300, 400, -100, -200, 500, 600])
        mono = AudioResampler.discord_to_stt(stereo.tobytes())
        assert len(mono) == len(stereo.tobytes()) // 2

    def test_stereo_to_mono_averages_channels(self):
        # L=100, R=200 -> mono=(100+200)//2=150
        stereo = array("h", [100, 200])
        mono_bytes = AudioResampler.discord_to_stt(stereo.tobytes())
        mono = array("h", mono_bytes)
        assert mono[0] == 150

    def test_discord_frame_size(self):
        # Standard Discord frame: 3840 bytes (960 stereo samples) -> 1920 bytes
        stereo = b"\x00" * 3840
        mono = AudioResampler.discord_to_stt(stereo)
        assert len(mono) == 1920


class TestAudioResamplerTtsToDiscord:
    def test_mono_to_stereo_doubles_size(self):
        mono = array("h", [100, 200, 300])
        stereo = AudioResampler.tts_to_discord(mono.tobytes())
        assert len(stereo) == len(mono.tobytes()) * 2

    def test_mono_to_stereo_duplicates_channels(self):
        mono = array("h", [42])
        stereo_bytes = AudioResampler.tts_to_discord(mono.tobytes())
        stereo = array("h", stereo_bytes)
        assert stereo[0] == 42  # L
        assert stereo[1] == 42  # R

    def test_roundtrip(self):
        """Mono -> stereo -> mono should roughly preserve the signal."""
        original = array("h", [100, -200, 300, -400])
        stereo = AudioResampler.tts_to_discord(original.tobytes())
        back = AudioResampler.discord_to_stt(stereo)
        result = array("h", back)
        assert list(result) == list(original)


class TestPcmToWav:
    def test_valid_wav_header(self):
        pcm = b"\x00\x01" * 480  # 480 samples of silence-ish
        wav_bytes = AudioResampler.pcm_to_wav(pcm, sample_rate=48000, channels=1, sample_width=2)

        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 48000
            assert wf.readframes(480) == pcm

    def test_stereo_wav(self):
        pcm = b"\x00" * 3840  # 960 stereo samples
        wav_bytes = AudioResampler.pcm_to_wav(pcm, sample_rate=48000, channels=2)
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 2


class TestPCMStreamSource:
    def test_read_returns_silence_on_empty_buffer(self):
        source = PCMStreamSource()
        frame = source.read()
        assert frame == b"\x00" * PCMStreamSource.FRAME_SIZE
        assert len(frame) == 3840

    def test_feed_and_read_returns_data(self):
        source = PCMStreamSource()
        data = b"\x42" * PCMStreamSource.FRAME_SIZE
        source.feed(data)
        frame = source.read()
        assert frame == data

    def test_read_returns_exact_frame_size(self):
        source = PCMStreamSource()
        # Feed more than one frame
        source.feed(b"\x01" * (PCMStreamSource.FRAME_SIZE * 2 + 100))
        frame1 = source.read()
        assert len(frame1) == PCMStreamSource.FRAME_SIZE
        frame2 = source.read()
        assert len(frame2) == PCMStreamSource.FRAME_SIZE

    def test_finish_drains_remaining_with_padding(self):
        source = PCMStreamSource()
        partial = b"\x42" * 100
        source.feed(partial)
        source.finish()
        frame = source.read()
        assert len(frame) == PCMStreamSource.FRAME_SIZE
        assert frame[:100] == partial
        assert frame[100:] == b"\x00" * (PCMStreamSource.FRAME_SIZE - 100)

    def test_finish_empty_returns_empty(self):
        source = PCMStreamSource()
        source.finish()
        frame = source.read()
        assert frame == b""

    def test_interrupt_clears_buffer(self):
        source = PCMStreamSource()
        source.feed(b"\x42" * PCMStreamSource.FRAME_SIZE * 3)
        source.interrupt()
        frame = source.read()
        assert frame == b""

    def test_feed_after_interrupt_is_ignored(self):
        source = PCMStreamSource()
        source.interrupt()
        source.feed(b"\x42" * PCMStreamSource.FRAME_SIZE)
        frame = source.read()
        assert frame == b""

    def test_is_opus_returns_false(self):
        source = PCMStreamSource()
        assert source.is_opus() is False

    def test_cleanup_clears_buffer(self):
        source = PCMStreamSource()
        source.feed(b"\x42" * 1000)
        source.cleanup()
        # After cleanup, buffer should be empty
        # but _finished and _interrupted are not set, so it returns silence
        frame = source.read()
        assert frame == b"\x00" * PCMStreamSource.FRAME_SIZE
