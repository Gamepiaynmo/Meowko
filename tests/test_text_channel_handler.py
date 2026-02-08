import pytest

from src.discord.handlers import MessageHandler


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


@pytest.mark.asyncio
async def test_tti_block_generates_image_not_audio() -> None:
    handler = MessageHandler.__new__(MessageHandler)
    fake_tts = _FakeTTS()
    fake_tti = _FakeTTI()
    handler.tts = fake_tts
    handler._get_tti = lambda: fake_tti

    segments = await MessageHandler._build_segments(
        handler,
        "[tti]draw a cat[/tti]",
    )

    assert segments == [{"type": "tti", "image": b"image"}]
    assert fake_tti.calls == ["draw a cat"]
    assert fake_tts.calls == []


@pytest.mark.asyncio
async def test_tts_block_generates_audio() -> None:
    handler = MessageHandler.__new__(MessageHandler)
    fake_tts = _FakeTTS()
    fake_tti = _FakeTTI()
    handler.tts = fake_tts
    handler._get_tti = lambda: fake_tti

    segments = await MessageHandler._build_segments(
        handler,
        "[tts]hello world[/tts]",
    )

    assert segments == [{"type": "tts", "content": "hello world", "audio": b"audio"}]
    assert fake_tts.calls == ["hello world"]
    assert fake_tti.calls == []
