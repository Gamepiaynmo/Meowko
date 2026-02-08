"""Tests for main module helpers."""

import asyncio
import logging

import pytest

from src import main


class _FakeConfig:
    def __init__(self) -> None:
        self.reload_calls = 0

    def reload_if_changed(self) -> bool:
        self.reload_calls += 1
        raise RuntimeError("bad yaml")


class TestConfigWatcher:
    @pytest.mark.asyncio
    async def test_config_watcher_survives_reload_errors(self, monkeypatch, caplog):
        fake = _FakeConfig()
        sleep_calls = 0

        async def _fake_sleep(_: int) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls > 1:
                raise asyncio.CancelledError

        monkeypatch.setattr(main, "get_config", lambda: fake)
        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(asyncio.CancelledError):
                await main.config_watcher(interval=0)

        assert fake.reload_calls == 1
        assert "Config reload failed" in caplog.text
