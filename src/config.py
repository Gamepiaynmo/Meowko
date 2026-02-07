"""Configuration loader for Meowko."""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("meowko")


def _get_local_timezone() -> str:
    """Get local system timezone."""
    try:
        import tzlocal
        return str(tzlocal.get_localzone())
    except Exception:
        return "UTC"


# Default configuration values
DEFAULTS: dict[str, Any] = {
    "locale": "zh_CN.UTF-8",
    "prompts": [],
    "providers": [],
    "llm": {
        "model": "openai/gpt-4o-mini",  # format: provider/model
        "timeout": 120,
    },
    "tti": {
        "model": "",  # format: provider/model (empty = disabled)
        "api": "images",  # "images" = /v1/images/generations, "chat" = /v1/chat/completions
        "size": "",  # e.g. "1024x1024" (omitted if empty — not all providers support it)
        "quality": "",  # e.g. "auto", "hd" (omitted if empty)
        "timeout": 120,
    },
    "elevenlabs": {
        "api_key": "",
        "default_voice_id": "21m00Tcm4TlvDq8ikWAM",
        "model_id": "eleven_turbo_v2_5",
        "stt_model": "scribe_v2",
        "language": "",
        "timeout": 120,
    },
    "brave": {
        "api_key": "",
        "search_count": 5,
    },
    "context": {
        "max_tokens": 8000,
        "compaction_threshold": 0.9,
        "info_template": "Today is {date}. Weather: {weather}.",
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
    "weather": {
        "latitude": 39.9906,  # Default: Beijing
        "longitude": 116.2887,
        "timezone": _get_local_timezone(),  # Auto-detect local timezone
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

    def resolve_provider_model(self, model_ref: str) -> dict[str, Any]:
        """Resolve a 'provider/model' reference to provider + model config.

        Returns:
            Dict with base_url, api_key, model (name), and the raw
            model config dict from the provider's models list.

        Raises:
            ValueError: If provider or model cannot be found.
        """
        # Parse provider/model format
        if "/" in model_ref:
            provider_name, model_name = model_ref.split("/", 1)
        else:
            provider_name = None
            model_name = model_ref

        # Find the provider
        providers = self._data.get("providers", []) if self._data else []
        provider = None

        if provider_name:
            for p in providers:
                if p.get("name") == provider_name:
                    provider = p
                    break

        if provider is None and providers:
            provider = providers[0]

        if provider is None:
            raise ValueError(f"No provider found for model: {model_ref}")

        # Find the model in provider's models list (optional for non-LLM uses)
        models = provider.get("models", [])
        model_config: dict[str, Any] = {}
        for m in models:
            if m.get("name") == model_name:
                model_config = m
                break

        return {
            "base_url": provider.get("base_url", ""),
            "api_key": provider.get("api_key", ""),
            "model": model_name,
            "model_config": model_config,
        }

    def get_model_config(self) -> dict[str, Any]:
        """Get the full configuration for the current LLM model.

        Returns:
            Dict with base_url, api_key, model, context_window,
            max_tokens, timeout, and pricing.
        """
        llm_config = self.llm
        model_ref = llm_config.get("model", "")
        resolved = self.resolve_provider_model(model_ref)
        mc = resolved["model_config"]

        if not mc:
            raise ValueError(f"Model '{resolved['model']}' not found in provider")

        pricing = mc.get("pricing", {})

        return {
            "base_url": resolved["base_url"],
            "api_key": resolved["api_key"],
            "model": resolved["model"],
            "context_window": mc.get("context_window", 128000),
            "max_tokens": mc.get("max_tokens", 4096),
            "timeout": llm_config.get("timeout", 120),
            "pricing": {
                "input": pricing.get("input", 0.0),
                "cached": pricing.get("cached", 0.0),
                "output": pricing.get("output", 0.0),
            },
        }

    @property
    def llm(self) -> dict[str, Any]:
        return self._get_with_defaults("llm")

    @property
    def tti(self) -> dict[str, Any]:
        return self._get_with_defaults("tti")

    @property
    def providers(self) -> list[dict[str, Any]]:
        return self._data.get("providers", []) if self._data else []

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

    @property
    def weather(self) -> dict[str, Any]:
        return self._get_with_defaults("weather")

    @property
    def locale(self) -> str:
        """Get the configured locale."""
        return self._data.get("locale", DEFAULTS["locale"]) if self._data else DEFAULTS["locale"]


def get_config() -> Config:
    """Get the global config instance."""
    return Config()
