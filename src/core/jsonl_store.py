"""JSONL storage for conversations - append-only logs per scope."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_config


class JSONLStore:
    """Append-only JSONL storage for conversation events."""

    def __init__(self, data_dir: Path | None = None) -> None:
        """Initialize the store with data directory."""
        config = get_config()
        if data_dir is None:
            data_dir = config.data_dir
        self.data_dir = data_dir
        self.conversations_dir = data_dir / config.paths["conversations_dir"]

    def _get_file_path(
        self,
        persona_id: str,
        user_id: int,
        date: datetime | None = None,
    ) -> Path:
        """Get the JSONL file path for a given persona, user, and date."""
        if date is None:
            date = datetime.now()

        date_str = date.strftime("%Y-%m-%d")
        scope_dir = f"{persona_id}-{user_id}"
        file_path = self.conversations_dir / scope_dir / f"{date_str}.jsonl"
        return file_path

    def append(
        self,
        persona_id: str,
        user_id: int,
        event: dict[str, Any],
    ) -> Path:
        """Append an event to the JSONL file."""
        file_path = self._get_file_path(persona_id, user_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "a", encoding="utf-8") as f:
            json.dump(event, f, ensure_ascii=False)
            f.write("\n")

        return file_path

    def read_all(
        self,
        persona_id: str,
        user_id: int,
    ) -> list[dict[str, Any]]:
        """Read all events for a persona-user pair."""
        file_path = self._get_file_path(persona_id, user_id)

        if not file_path.exists():
            return []

        events = []
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        return events
