"""Memory management - hierarchical markdown rollups (daily/weekly/monthly/seasonal/yearly)."""

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import get_config
from src.core.jsonl_store import JSONLStore
from src.providers.llm_client import LLMClient

logger = logging.getLogger("meowko.core.memory")

# Season start months: Jan=Q1, Apr=Q2, Jul=Q3, Oct=Q4
_SEASON_STARTS = {1, 4, 7, 10}
_MEMORY_TAG_RE = re.compile(r"\[memory\](.*?)\[/memory\]", re.DOTALL)


def estimate_tokens(text: str) -> int:
    """Estimate token count. Conservative for mixed CJK/English."""
    return int(len(text) / 2.5)


def _season_index(month: int) -> int:
    """Return 1-based season index for a month (1=winter, 2=spring, 3=summer, 4=fall)."""
    return (month - 1) // 3 + 1


class MemoryManager:
    """Manages hierarchical memory rollups per conversation scope."""

    def __init__(self, data_dir: Path | None = None) -> None:
        config = get_config()
        if data_dir is None:
            data_dir = config.data_dir
        self.data_dir = data_dir
        self.config = config
        self.store = JSONLStore(data_dir)
        self.memories_dir: Path = data_dir / config.paths["memories_dir"]

    # ── Memory file paths ──────────────────────────────────────────

    def _scope_dir(self, scope_id: str) -> Path:
        return self.memories_dir / scope_id

    def _day_path(self, scope_id: str, d: date) -> Path:
        return self._scope_dir(scope_id) / f"day-{d.isoformat()}.md"

    def _week_path(self, scope_id: str, d: date) -> Path:
        # Index = ISO week number
        return self._scope_dir(scope_id) / f"week-{d.strftime('%Y-%m')}-{d.isocalendar()[1]:02d}.md"

    def _month_path(self, scope_id: str, d: date) -> Path:
        return self._scope_dir(scope_id) / f"month-{d.strftime('%Y-%m')}.md"

    def _season_path(self, scope_id: str, d: date) -> Path:
        idx = _season_index(d.month)
        return self._scope_dir(scope_id) / f"season-{d.year}-{idx:02d}.md"

    def _year_path(self, scope_id: str, d: date) -> Path:
        return self._scope_dir(scope_id) / f"year-{d.year}.md"

    # ── LLM summarization ─────────────────────────────────────────

    async def _summarize_conversation(
        self,
        events: list[dict[str, Any]],
        existing_summary: str | None = None,
    ) -> str:
        """Summarize conversation events into bullet points."""
        lines = []
        for ev in events:
            role = ev.get("role", "")
            content = ev.get("content", "")
            if role and content:
                lines.append(f"{role}: {content}")
        conversation_text = "\n".join(lines)

        prompt = (
            "将以下对话总结为简洁的要点，使用 Markdown 列表格式。"
            "重点关注讨论的话题、做出的决定、情感时刻、透露的个人信息以及任何承诺或计划。"
            "将列表放在 [memory] ... [/memory] 标签内。"
            "只输出标签和要点，不要加标题或其他文字。"
        )
        if existing_summary:
            prompt += (
                "\n\n以下是已有的总结，请在此基础上整合新信息，去除重复内容：\n\n"
                + existing_summary
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": conversation_text},
        ]

        return await self._llm_call(messages)

    async def _merge_memories(self, contents: list[str]) -> str:
        """Merge multiple memory files into consolidated bullet points."""
        combined = "\n\n---\n\n".join(contents)

        messages = [
            {
                "role": "system",
                "content": (
                    "将以下多段记忆笔记合并为一份精炼的总结，使用 Markdown 列表格式。"
                    "去除重复内容，合并相关要点，保留最重要的信息。"
                    "将列表放在 [memory] ... [/memory] 标签内。"
                    "只输出标签和要点，不要加标题或其他文字。"
                ),
            },
            {"role": "user", "content": combined},
        ]

        return await self._llm_call(messages)

    async def _llm_call(self, messages: list[dict[str, Any]], retries: int = 3) -> str:
        """Call LLM and extract [memory] block, retrying on failure."""
        client = LLMClient()
        for attempt in range(1, retries + 1):
            try:
                response = await client.chat(messages, temperature=0.3)
                return self._extract_memory(response.content)
            except Exception as e:
                logger.warning("Memory LLM call attempt %d/%d failed: %s", attempt, retries, e)
                if attempt == retries:
                    raise
        raise RuntimeError("_llm_call exhausted retries")

    @staticmethod
    def _extract_memory(text: str) -> str:
        """Extract content from [memory]...[/memory] tags.

        Raises ValueError if the tags are missing.
        """
        m = _MEMORY_TAG_RE.search(text)
        if m:
            return m.group(1).strip()
        raise ValueError("LLM response missing [memory] block")

    # ── Read memories ──────────────────────────────────────────────

    def read_all_memories(self, scope_id: str) -> str:
        """Read all memory files for a scope, concatenated in hierarchy order.

        Order: year < season < month < week < day (chronological within tier).
        """
        scope_dir = self._scope_dir(scope_id)
        if not scope_dir.exists():
            return ""

        # Collect files by tier prefix for ordering
        tier_order = {"year": 0, "season": 1, "month": 2, "week": 3, "day": 4}
        files = sorted(
            scope_dir.glob("*.md"),
            key=lambda p: (tier_order.get(p.stem.split("-")[0], 99), p.name),
        )

        if not files:
            return ""

        parts = []
        for f in files:
            text = f.read_text(encoding="utf-8").strip()
            if text:
                parts.append(f"## {f.stem}\n{text}")
        return "\n\n".join(parts)

    # ── Daily memory creation ──────────────────────────────────────

    async def create_daily_memory(self, scope_id: str, d: date) -> Path | None:
        """Summarize a day's conversation JSONL into a daily memory file.

        Returns the memory file path, or None if there was nothing to summarize.
        """
        persona_id, user_id = self._parse_scope(scope_id)
        events = self.store.read_date(persona_id, int(user_id), datetime(d.year, d.month, d.day))

        if not events:
            return None

        # Check for existing daily memory to merge with
        day_path = self._day_path(scope_id, d)
        existing = day_path.read_text(encoding="utf-8").strip() if day_path.exists() else None

        summary = await self._summarize_conversation(events, existing)

        day_path.parent.mkdir(parents=True, exist_ok=True)
        day_path.write_text(summary + "\n", encoding="utf-8")
        logger.info("Created daily memory: %s", day_path.name)
        return day_path

    # ── Rollup hierarchy ──────────────────────────────────────────

    async def run_daily_rollup(self, scope_id: str, rollup_date: date) -> None:
        """Run full rollup hierarchy for a scope.

        1. Summarize yesterday's conversation -> daily memory, archive JSONL
        2. If Monday & >7 dailies: merge old dailies -> weekly
        3. If 1st of month & old weeklies: merge -> monthly
        4. If 1st of season & >3 monthlies: merge -> seasonal
        5. If Jan 1 & >4 seasonals: merge -> yearly
        """
        yesterday = rollup_date - timedelta(days=1)

        # 1. Daily memory + archive
        mem_path = await self.create_daily_memory(scope_id, yesterday)
        if mem_path:
            # Archive the JSONL file
            date_str = yesterday.strftime("%Y-%m-%d")
            jsonl_path = self.store.conversations_dir / scope_id / f"{date_str}.jsonl"
            if jsonl_path.exists():
                archived = self.store.archive(jsonl_path)
                logger.info("Archived conversation: %s", archived.name)

        scope_dir = self._scope_dir(scope_id)

        # 2. Weekly merge (on Monday, if enough daily files)
        if rollup_date.weekday() == 0:  # Monday
            daily_files = sorted(scope_dir.glob("day-*.md")) if scope_dir.exists() else []
            if len(daily_files) > 7:
                # Merge all but the most recent 7
                to_merge = daily_files[:-7]
                week_path = self._week_path(scope_id, yesterday)
                await self._merge_and_replace(to_merge, week_path, scope_dir)

        # 3. Monthly merge (on 1st, merge old weeklies)
        if rollup_date.day == 1:
            weekly_files = sorted(scope_dir.glob("week-*.md")) if scope_dir.exists() else []
            # Keep weeklies from this month only
            last_month = rollup_date - timedelta(days=1)
            cutoff = last_month.strftime("%Y-%m")
            old_weeklies = [f for f in weekly_files if f.stem.split("week-")[1][:7] < cutoff]
            if old_weeklies:
                month_path = self._month_path(scope_id, last_month)
                await self._merge_and_replace(old_weeklies, month_path, scope_dir)

        # 4. Seasonal merge (1st of Jan/Apr/Jul/Oct, if enough monthlies)
        if rollup_date.day == 1 and rollup_date.month in _SEASON_STARTS:
            monthly_files = sorted(scope_dir.glob("month-*.md")) if scope_dir.exists() else []
            if len(monthly_files) > 3:
                to_merge = monthly_files[:-3]
                season_path = self._season_path(scope_id, yesterday)
                await self._merge_and_replace(to_merge, season_path, scope_dir)

        # 5. Yearly merge (Jan 1, if enough seasonals)
        if rollup_date.month == 1 and rollup_date.day == 1:
            seasonal_files = sorted(scope_dir.glob("season-*.md")) if scope_dir.exists() else []
            if len(seasonal_files) > 4:
                to_merge = seasonal_files[:-4]
                year_path = self._year_path(scope_id, yesterday)
                await self._merge_and_replace(to_merge, year_path, scope_dir)

    async def _merge_and_replace(
        self,
        source_files: list[Path],
        dest_path: Path,
        scope_dir: Path,
    ) -> None:
        """Merge source files into dest, then archive sources."""
        contents = []
        for f in source_files:
            text = f.read_text(encoding="utf-8").strip()
            if text:
                contents.append(text)

        if not contents:
            return

        # Include existing dest content if present
        if dest_path.exists():
            existing = dest_path.read_text(encoding="utf-8").strip()
            if existing:
                contents.insert(0, existing)

        merged = await self._merge_memories(contents)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(merged + "\n", encoding="utf-8")

        # Archive merged source files
        archive_dir = scope_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for f in source_files:
            f.rename(archive_dir / f.name)

        logger.info("Merged %d files -> %s", len(source_files), dest_path.name)

    # ── Compaction ────────────────────────────────────────────────

    async def compact_conversation(self, scope_id: str, today: date) -> None:
        """Create daily memory for today and archive the conversation JSONL.

        Called by ContextBuilder when conversation exceeds token threshold.
        """
        mem_path = await self.create_daily_memory(scope_id, today)
        if mem_path:
            date_str = today.strftime("%Y-%m-%d")
            jsonl_path = self.store.conversations_dir / scope_id / f"{date_str}.jsonl"
            if jsonl_path.exists():
                self.store.archive(jsonl_path)
                logger.info("Compacted conversation for %s on %s", scope_id, date_str)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _parse_scope(scope_id: str) -> tuple[str, str]:
        """Parse scope_id into (persona_id, user_id).

        Handles persona IDs with hyphens by splitting from the right.
        """
        persona_id, user_id = scope_id.rsplit("-", 1)
        return persona_id, user_id
