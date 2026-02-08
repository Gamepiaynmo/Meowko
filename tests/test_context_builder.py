"""Tests for ContextBuilder persona loading, turn saving, and cache file saving."""

from pathlib import Path

import yaml

from src.core.context_builder import ContextBuilder


class TestLoadPersona:
    def test_load_persona_with_soul_and_yaml(self, config_file):
        cb = ContextBuilder()
        persona_dir = cb.data_dir / cb.config.paths["personas_dir"] / "testcat"
        persona_dir.mkdir(parents=True, exist_ok=True)

        (persona_dir / "soul.md").write_text("You are a cat.", encoding="utf-8")
        (persona_dir / "persona.yaml").write_text(
            yaml.dump({"nickname": "Kitty", "voice_id": "voice123"}),
            encoding="utf-8",
        )

        persona = cb.load_persona("testcat")
        assert persona["prompt"] == "You are a cat."
        assert persona["nickname"] == "Kitty"
        assert persona["voice_id"] == "voice123"

    def test_load_persona_fallback_no_files(self, config_file):
        cb = ContextBuilder()
        persona = cb.load_persona("nonexistent")
        assert "nonexistent" in persona["prompt"]
        assert persona["nickname"] == "nonexistent"
        assert persona["voice_id"] is None

    def test_load_persona_soul_only(self, config_file):
        cb = ContextBuilder()
        persona_dir = cb.data_dir / cb.config.paths["personas_dir"] / "simple"
        persona_dir.mkdir(parents=True, exist_ok=True)
        (persona_dir / "soul.md").write_text("Simple bot.", encoding="utf-8")

        persona = cb.load_persona("simple")
        assert persona["prompt"] == "Simple bot."
        assert persona["nickname"] == "simple"  # Falls back to persona_id
        assert persona["voice_id"] is None


class TestSaveTurn:
    def test_save_turn_creates_user_and_assistant_events(self, config_file):
        cb = ContextBuilder()
        cb.save_turn(
            user_id=42,
            user_message="hello",
            assistant_message="hi there",
            persona_id="test",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cached_tokens=2,
            cost=0.001,
        )

        events = cb.store.read_all("test", 42)
        assert len(events) == 2
        assert events[0]["role"] == "user"
        assert events[0]["content"] == "hello"
        assert events[1]["role"] == "assistant"
        assert events[1]["content"] == "hi there"
        assert events[1]["token_usage"]["prompt_tokens"] == 10
        assert events[1]["cost"] == 0.001

    def test_save_turn_with_attachments(self, config_file):
        cb = ContextBuilder()
        cb.save_turn(
            user_id=42,
            user_message="see image",
            assistant_message="nice pic",
            persona_id="test",
            user_attachments=[{"type": "image", "filename": "cat.jpg", "path": "cache/cat.jpg"}],
            assistant_attachments=[{"type": "tts", "path": "cache/tts.mp3"}],
        )

        events = cb.store.read_all("test", 42)
        assert events[0]["attachments"][0]["filename"] == "cat.jpg"
        assert events[1]["attachments"][0]["type"] == "tts"


class TestSaveCacheFile:
    def test_save_and_verify(self, config_file):
        cb = ContextBuilder()
        data = b"fake image data"
        rel_path = cb.save_cache_file("persona", 1, "photo.jpg", data)

        assert rel_path.startswith("cache/")
        assert rel_path.endswith(".jpg")

        abs_path = cb.data_dir / rel_path
        assert abs_path.exists()
        assert abs_path.read_bytes() == data

    def test_unique_filenames(self, config_file):
        cb = ContextBuilder()
        path1 = cb.save_cache_file("p", 1, "a.png", b"1")
        path2 = cb.save_cache_file("p", 1, "a.png", b"2")
        assert path1 != path2


class TestSharedPrompts:
    def test_loads_shared_prompts(self, config_file):
        cb = ContextBuilder()
        # Write config with prompts list
        cb.config._data["prompts"] = ["rules.md"]
        prompts_dir = cb.data_dir / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        (prompts_dir / "rules.md").write_text("Be nice.", encoding="utf-8")

        prompts = cb._load_shared_prompts()
        assert prompts == ["Be nice."]

    def test_missing_prompt_file_skipped(self, config_file):
        cb = ContextBuilder()
        cb.config._data["prompts"] = ["nonexistent.md"]
        prompts = cb._load_shared_prompts()
        assert prompts == []

    def test_no_prompts_configured(self, config_file):
        cb = ContextBuilder()
        prompts = cb._load_shared_prompts()
        assert prompts == []
