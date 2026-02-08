"""JSONL storage for conversations - append-only logs per scope."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from src.config import get_config


class JSONLStore:
    """Append-only JSONL storage for conversation events."""

    def __init__(self, data_dir: Path | None = None) -> None:
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
        """Get the JSONL file path for a given persona, user, and date.

        Uses memory.rollup_time as the day boundary. Times before rollup_time
        are considered part of the previous day.
        """
        config = get_config()
        memory_cfg = config.memory
        tz = ZoneInfo(memory_cfg["timezone"])

        if date is None:
            date = datetime.now(tz)
        elif date.tzinfo is None:
            date = date.replace(tzinfo=tz)

        hour, minute = (int(x) for x in memory_cfg["rollup_time"].split(":"))
        rollup_today = date.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if date < rollup_today:
            date = date - timedelta(days=1)

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

    def list_scopes(self) -> list[str]:
        """List all scope directories under conversations_dir."""
        if not self.conversations_dir.exists():
            return []
        return sorted(
            d.name
            for d in self.conversations_dir.iterdir()
            if d.is_dir() and d.name != "archive"
        )

    def read_file(self, file_path: Path) -> list[dict]:
        """Read events from a specific JSONL file."""
        if not file_path.exists():
            return []
        events = []
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def read_date(self, persona_id: str, user_id: int, date: datetime) -> list[dict]:
        """Read events for a specific date."""
        scope_id = f"{persona_id}-{user_id}"
        date_str = date.strftime("%Y-%m-%d")
        file_path = self.conversations_dir / scope_id / f"{date_str}.jsonl"
        return self.read_file(file_path)

    def archive(self, file_path: Path) -> Path:
        """Move file to archive/ subdirectory within its parent."""
        archive_dir = file_path.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / file_path.name
        file_path.rename(dest)
        return dest
