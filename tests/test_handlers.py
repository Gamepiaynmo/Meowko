"""Tests for MessageHandler segment parsing and format_user_message."""

import pytest

from src.discord.handlers import MessageHandler, format_user_message


class _FakeTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return b"audio"


class _FakeTTI:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> bytes:
        self.calls.append(prompt)
        return b"image"


def _make_handler():
    handler = MessageHandler.__new__(MessageHandler)
    handler.tts = _FakeTTS()
    handler._get_tti = lambda: _FakeTTI()
    return handler


class TestFormatUserMessage:
    def test_format_includes_timestamp_and_name(self):
        result = format_user_message("Alice", "hello")
        assert "Alice" in result
        assert "hello" in result
        # Check timestamp format: [YYYY-MM-DD HH:MM]
        assert result.startswith("[")
        assert "]" in result

    def test_format_with_empty_text(self):
        result = format_user_message("Bob", "")
        assert "Bob:" in result


class TestBuildSegments:
    @pytest.mark.asyncio
    async def test_plain_text_only(self):
        handler = _make_handler()
        segments = await handler._build_segments("Hello world")
        assert segments == [{"type": "text", "content": "Hello world"}]

    @pytest.mark.asyncio
    async def test_empty_string(self):
        handler = _make_handler()
        segments = await handler._build_segments("")
        assert segments == []

    @pytest.mark.asyncio
    async def test_mixed_text_tts_tti(self):
        handler = _make_handler()
        text = "Start [tts]say this[/tts] middle [tti]draw cat[/tti] end"
        segments = await handler._build_segments(text)

        assert len(segments) == 5
        assert segments[0] == {"type": "text", "content": "Start"}
        assert segments[1] == {"type": "tts", "content": "say this", "audio": b"audio"}
        assert segments[2] == {"type": "text", "content": "middle"}
        assert segments[3] == {"type": "tti", "image": b"image"}
        assert segments[4] == {"type": "text", "content": "end"}

    @pytest.mark.asyncio
    async def test_multiple_tts_blocks(self):
        handler = _make_handler()
        text = "[tts]first[/tts] gap [tts]second[/tts]"
        segments = await handler._build_segments(text)

        assert len(segments) == 3
        assert segments[0]["type"] == "tts"
        assert segments[0]["content"] == "first"
        assert segments[1] == {"type": "text", "content": "gap"}
        assert segments[2]["type"] == "tts"
        assert segments[2]["content"] == "second"

    @pytest.mark.asyncio
    async def test_tts_failure_produces_none_audio(self):
        handler = _make_handler()

        async def failing_synth(text):
            raise RuntimeError("TTS failed")

        handler.tts.synthesize = failing_synth
        segments = await handler._build_segments("[tts]hello[/tts]")
        assert len(segments) == 1
        assert segments[0]["type"] == "tts"
        assert segments[0]["audio"] is None

    @pytest.mark.asyncio
    async def test_tti_failure_drops_segment(self):
        handler = _make_handler()
        tti = _FakeTTI()

        async def failing_gen(prompt):
            raise RuntimeError("TTI failed")

        tti.generate = failing_gen
        handler._get_tti = lambda: tti

        segments = await handler._build_segments("[tti]draw something[/tti]")
        # TTI failures silently drop the segment
        assert segments == []

    @pytest.mark.asyncio
    async def test_tti_no_client_configured(self):
        handler = _make_handler()
        handler._get_tti = lambda: None
        segments = await handler._build_segments("[tti]draw[/tti]")
        # No TTI client -> segment dropped (no image key)
        assert segments == []

    @pytest.mark.asyncio
    async def test_case_insensitive_tags(self):
        handler = _make_handler()
        segments = await handler._build_segments("[TTS]loud[/TTS]")
        assert len(segments) == 1
        assert segments[0]["type"] == "tts"
        assert segments[0]["content"] == "loud"

    @pytest.mark.asyncio
    async def test_text_before_and_after_tags(self):
        handler = _make_handler()
        segments = await handler._build_segments("before [tts]middle[/tts] after")
        assert len(segments) == 3
        assert segments[0] == {"type": "text", "content": "before"}
        assert segments[1]["type"] == "tts"
        assert segments[2] == {"type": "text", "content": "after"}
