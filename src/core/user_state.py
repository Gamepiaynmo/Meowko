"""YAML-backed per-user state manager."""

import logging
from pathlib import Path
from typing import Any

import yaml

from src.config import get_config
from src.core.persona_id import is_valid_persona_id

logger = logging.getLogger("meowko.core.state")


class UserState:
    """Persists per-user state (e.g. active persona) to YAML files."""

    def __init__(self, data_dir: Path | None = None) -> None:
        config = get_config()
        base = data_dir or config.data_dir
        self._state_dir: Path = base / config.paths["state_dir"]
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def _user_path(self, user_id: int) -> Path:
        return self._state_dir / f"{user_id}.yaml"

    def _read(self, user_id: int) -> dict[str, Any]:
        path = self._user_path(user_id)
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write(self, user_id: int, data: dict[str, Any]) -> None:
        path = self._user_path(user_id)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)

    def get_persona_id(self, user_id: int) -> str:
        """Return the user's active persona, falling back to config default."""
        data = self._read(user_id)
        default_persona = str(get_config().get("default_persona", "meowko"))

        if "persona_id" not in data:
            return default_persona

        persona_id = str(data["persona_id"])
        if is_valid_persona_id(persona_id):
            return persona_id

        logger.warning(
            "Ignoring invalid persona_id in state for user %s: %r",
            user_id,
            persona_id,
        )
        return default_persona

    def set_persona_id(self, user_id: int, persona_id: str) -> None:
        """Persist the user's persona selection."""
        data = self._read(user_id)
        data["persona_id"] = persona_id
        self._write(user_id, data)
