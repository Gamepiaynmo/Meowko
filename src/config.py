"""Configuration loader for Meowko."""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("meowko")


# Default configuration values
DEFAULTS: dict[str, Any] = {
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "context_window": 128000,
        "max_tokens": 4096,
        "timeout": 120,
        "pricing": {
            "input": 0.0,
            "cached": 0.0,
            "output": 0.0,
        },
    },
    "elevenlabs": {
        "api_key": "",
        "default_voice_id": "21m00Tcm4TlvDq8ikWAM",
        "model_id": "eleven_turbo_v2_5",
        "language": "en",
        "timeout": 120,
    },
    "brave": {
        "api_key": "",
        "search_count": 5,
    },
    "context": {
        "max_tokens": 8000,
        "compaction_threshold": 0.9,
    },
    "memory": {
        "daily_retention": 5,
        "monthly_retention": 3,
        "rollup_time": "03:00",
        "timezone": "Asia/Singapore",
    },
    "voice": {
        "endpointing_ms": 500,
        "push_to_talk": False,
        "sample_rate": 48000,
        "channels": 2,
    },
    "scheduler": {
        "tick_interval": 60,
    },
    "discord": {
        "message_delay": 0.5,  # Delay between split messages in seconds
    },
    "paths": {
        "data_dir": "~/.meowko",
        "personas_dir": "personas",
        "state_dir": "state",
        "conversations_dir": "conversations",
        "memories_dir": "memories",
        "cache_dir": "cache",
        "logs_dir": "logs",
    },
}


class Config:
    """Bot configuration loaded from config.yaml."""

    _instance: "Config | None" = None
    _data: dict[str, Any] = {}
    _config_path: Path | None = None
    _last_modified: float = 0

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, path: Path | None = None) -> None:
        """Load configuration from YAML file."""
        if path is None:
            path = Path(__file__).parent.parent / "config.yaml"

        self._config_path = path
        self._last_modified = path.stat().st_mtime

        with open(path, encoding="utf-8") as f:
            self._data = yaml.safe_load(f)
        logger.debug(f"Config loaded from {path}")

    def reload_if_changed(self) -> bool:
        """Reload config if file has been modified.

        Returns:
            True if config was reloaded, False otherwise.
        """
        if self._config_path is None or not self._config_path.exists():
            return False

        current_mtime = self._config_path.stat().st_mtime
        if current_mtime > self._last_modified:
            logger.info(f"Config file changed, reloading...")
            self.load(self._config_path)
            return True
        return False

    def _get_with_defaults(self, key: str) -> dict[str, Any]:
        """Get config section merged with defaults."""
        user_values = self._data.get(key, {}) if self._data else {}
        defaults = DEFAULTS.get(key, {})
        return {**defaults, **user_values}

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by dot-notation key (e.g., 'llm.api_key')."""
        keys = key.split(".")

        # Check user config first
        value = self._data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                value = None
                break

        if value is not None:
            return value

        # Fall back to defaults
        value = DEFAULTS
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    @property
    def llm(self) -> dict[str, Any]:
        return self._get_with_defaults("llm")

    @property
    def elevenlabs(self) -> dict[str, Any]:
        return self._get_with_defaults("elevenlabs")

    @property
    def brave(self) -> dict[str, Any]:
        return self._get_with_defaults("brave")

    @property
    def context(self) -> dict[str, Any]:
        return self._get_with_defaults("context")

    @property
    def memory(self) -> dict[str, Any]:
        return self._get_with_defaults("memory")

    @property
    def voice(self) -> dict[str, Any]:
        return self._get_with_defaults("voice")

    @property
    def scheduler(self) -> dict[str, Any]:
        return self._get_with_defaults("scheduler")

    @property
    def paths(self) -> dict[str, Any]:
        return self._get_with_defaults("paths")

    @property
    def discord(self) -> dict[str, Any]:
        return self._get_with_defaults("discord")


def get_config() -> Config:
    """Get the global config instance."""
    return Config()
