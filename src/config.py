"""Configuration loader for Meowko."""

from pathlib import Path
from typing import Any

import yaml


class Config:
    """Bot configuration loaded from config.yaml."""

    _instance: "Config | None" = None
    _data: dict[str, Any] = {}

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, path: Path | None = None) -> None:
        """Load configuration from YAML file."""
        if path is None:
            path = Path(__file__).parent.parent / "config.yaml"

        with open(path, encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by dot-notation key (e.g., 'llm.api_key')."""
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    @property
    def llm(self) -> dict[str, Any]:
        return self._data.get("llm", {})

    @property
    def elevenlabs(self) -> dict[str, Any]:
        return self._data.get("elevenlabs", {})

    @property
    def brave(self) -> dict[str, Any]:
        return self._data.get("brave", {})

    @property
    def context(self) -> dict[str, Any]:
        return self._data.get("context", {})

    @property
    def memory(self) -> dict[str, Any]:
        return self._data.get("memory", {})

    @property
    def voice(self) -> dict[str, Any]:
        return self._data.get("voice", {})

    @property
    def scheduler(self) -> dict[str, Any]:
        return self._data.get("scheduler", {})

    @property
    def paths(self) -> dict[str, Any]:
        return self._data.get("paths", {})


def get_config() -> Config:
    """Get the global config instance."""
    return Config()
