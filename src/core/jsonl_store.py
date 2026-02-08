"""JSONL storage for conversations - append-only logs per scope."""

import json
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from src.config import get_config


def _config_now() -> datetime:
    """Return the current datetime in the config timezone."""
    tz = ZoneInfo(get_config().memory["timezone"])
    return datetime.now(tz)


def _resolve_logical_date(now: datetime | None = None) -> date_type:
    """Resolve the current logical date using config timezone and rollup_time.

    Times before rollup_time are considered part of the previous day.
    """
    config = get_config()
    memory_cfg = config.memory
    tz = ZoneInfo(memory_cfg["timezone"])

    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    hour, minute = (int(x) for x in memory_cfg["rollup_time"].split(":"))
    rollup_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if now < rollup_today:
        now = now - timedelta(days=1)

    return now.date()


class JSONLStore:
    """Append-only JSONL storage for conversation events."""

    def __init__(self, data_dir: Path | None = None) -> None:
        config = get_config()
        if data_dir is None:
            data_dir = config.data_dir
        self.data_dir = data_dir
        self.conversations_dir: Path = data_dir / config.paths["conversations_dir"]

    @staticmethod
    def today() -> date_type:
        """Return the current logical date (respects rollup_time boundary)."""
        return _resolve_logical_date()

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
        logical_date = _resolve_logical_date(date)
        date_str = logical_date.isoformat()
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

    def read_file(self, file_path: Path) -> list[dict[str, Any]]:
        """Read events from a specific JSONL file."""
        if not file_path.exists():
            return []
        events: list[dict[str, Any]] = []
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def read_date(self, persona_id: str, user_id: int, date: datetime) -> list[dict[str, Any]]:
        """Read events for a specific date."""
        scope_id = f"{persona_id}-{user_id}"
        date_str = date.strftime("%Y-%m-%d")
        file_path = self.conversations_dir / scope_id / f"{date_str}.jsonl"
        return self.read_file(file_path)

    def rewind(self, persona_id: str, user_id: int) -> int:
        """Remove all events from the last user message onward.

        Returns the number of events removed, or 0 if nothing to rewind.
        """
        file_path = self._get_file_path(persona_id, user_id)
        events = self.read_file(file_path)
        if not events:
            return 0

        # Find the last user message index
        last_user_idx = None
        for i in range(len(events) - 1, -1, -1):
            if events[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return 0

        removed = len(events) - last_user_idx
        kept = events[:last_user_idx]

        if kept:
            with open(file_path, "w", encoding="utf-8") as f:
                for event in kept:
                    json.dump(event, f, ensure_ascii=False)
                    f.write("\n")
        elif file_path.exists():
            file_path.unlink()

        return removed

    def archive(self, file_path: Path) -> Path:
        """Move file to archive/ subdirectory within its parent."""
        archive_dir = file_path.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / file_path.name
        if dest.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            idx = 1
            while dest.exists():
                dest = archive_dir / f"{stem}-{idx}{suffix}"
                idx += 1
        file_path.rename(dest)
        return dest
