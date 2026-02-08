"""Tests for Config singleton and configuration loading."""

import time

import yaml

from src.config import Config, DEFAULTS, get_config


class TestConfigSingleton:
    def test_singleton_returns_same_instance(self):
        a = Config()
        b = Config()
        assert a is b

    def test_get_config_returns_singleton(self):
        c = Config()
        assert get_config() is c


class TestConfigLoad:
    def test_load_sets_data(self, config_file):
        config = Config()
        assert config.get("default_persona") == "test-persona"

    def test_load_populates_llm_section(self, config_file):
        config = Config()
        assert config.llm["model"] == "testprov/test-model"
        assert config.llm["timeout"] == 30

    def test_reload_if_changed_returns_false_when_unchanged(self, config_file):
        config = Config()
        assert config.reload_if_changed() is False

    def test_reload_if_changed_returns_true_after_modification(self, config_file):
        config = Config()
        # Touch the file with a newer mtime
        time.sleep(0.05)
        data = yaml.safe_load(config_file.read_text())
        data["default_persona"] = "updated"
        config_file.write_text(yaml.dump(data))
        assert config.reload_if_changed() is True
        assert config.get("default_persona") == "updated"

    def test_reload_if_changed_no_path(self):
        config = Config()
        # Never loaded — _config_path is None
        assert config.reload_if_changed() is False


class TestConfigGet:
    def test_get_user_value(self, config_file):
        config = Config()
        assert config.get("llm.model") == "testprov/test-model"

    def test_get_falls_back_to_defaults(self, config_file):
        config = Config()
        # 'voice.endpointing_ms' is not in our test config, should fall back
        assert config.get("voice.endpointing_ms") == DEFAULTS["voice"]["endpointing_ms"]

    def test_get_returns_default_for_missing_key(self, config_file):
        config = Config()
        assert config.get("nonexistent.key", "fallback") == "fallback"

    def test_get_nested_default(self, config_file):
        config = Config()
        assert config.get("context.compaction_threshold") == DEFAULTS["context"]["compaction_threshold"]


class TestConfigProperties:
    def test_section_properties_merge_defaults(self, config_file):
        config = Config()
        # memory section: user set timezone=UTC, rollup_time=03:00
        mem = config.memory
        assert mem["timezone"] == "UTC"
        assert mem["rollup_time"] == "03:00"

    def test_data_dir_expands_user(self, config_file, tmp_path):
        config = Config()
        assert config.data_dir == tmp_path / "data"

    def test_locale_default(self):
        config = Config()
        assert config.locale == DEFAULTS["locale"]


class TestResolveProviderModel:
    def test_resolve_with_provider_name(self, config_file):
        config = Config()
        result = config.resolve_provider_model("testprov/test-model")
        assert result["base_url"] == "http://localhost:1234"
        assert result["api_key"] == "test-key"
        assert result["model"] == "test-model"
        assert result["model_config"]["context_window"] == 8000

    def test_resolve_without_provider_falls_back_to_first(self, config_file):
        config = Config()
        result = config.resolve_provider_model("test-model")
        assert result["base_url"] == "http://localhost:1234"
        assert result["model"] == "test-model"

    def test_resolve_unknown_model_returns_empty_model_config(self, config_file):
        config = Config()
        result = config.resolve_provider_model("testprov/unknown-model")
        assert result["model"] == "unknown-model"
        assert result["model_config"] == {}

    def test_resolve_no_providers_raises(self):
        config = Config()
        config._data = {"providers": []}
        import pytest
        with pytest.raises(ValueError, match="No provider found"):
            config.resolve_provider_model("any/model")


class TestGetModelConfig:
    def test_returns_full_model_config(self, config_file):
        config = Config()
        mc = config.get_model_config()
        assert mc["model"] == "test-model"
        assert mc["context_window"] == 8000
        assert mc["max_tokens"] == 512
        assert mc["timeout"] == 30
        assert mc["pricing"]["input"] == 1.0
        assert mc["pricing"]["cached"] == 0.5
        assert mc["pricing"]["output"] == 2.0

    def test_raises_if_model_not_found(self, config_file):
        config = Config()
        config._data["llm"]["model"] = "testprov/nonexistent"
        import pytest
        with pytest.raises(ValueError, match="not found in provider"):
            config.get_model_config()
