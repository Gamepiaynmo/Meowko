"""Tests for MemoryManager helpers and memory reading."""

from datetime import date
from pathlib import Path

import pytest

from src.core.memory_manager import MemoryManager, estimate_tokens, _season_index, _stem_to_date_range


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_english_text(self):
        text = "Hello world, this is a test."
        tokens = estimate_tokens(text)
        assert tokens == int(len(text) / 2.5)

    def test_cjk_text(self):
        text = "你好世界"
        tokens = estimate_tokens(text)
        assert tokens > 0


class TestSeasonIndex:
    def test_winter(self):
        assert _season_index(1) == 1
        assert _season_index(2) == 1
        assert _season_index(3) == 1

    def test_spring(self):
        assert _season_index(4) == 2
        assert _season_index(5) == 2
        assert _season_index(6) == 2

    def test_summer(self):
        assert _season_index(7) == 3

    def test_fall(self):
        assert _season_index(10) == 4
        assert _season_index(12) == 4


class TestExtractMemory:
    def test_extracts_content(self):
        text = "some preamble [memory]- item 1\n- item 2[/memory] trailing"
        result = MemoryManager._extract_memory(text)
        assert result == "- item 1\n- item 2"

    def test_strips_whitespace(self):
        text = "[memory]\n  - bullet  \n[/memory]"
        result = MemoryManager._extract_memory(text)
        assert result == "- bullet"

    def test_raises_on_missing_tags(self):
        with pytest.raises(ValueError, match="missing \\[memory\\] block"):
            MemoryManager._extract_memory("no tags here")


class TestParseScope:
    def test_simple_scope(self):
        persona, user = MemoryManager._parse_scope("meowko-12345")
        assert persona == "meowko"
        assert user == "12345"

    def test_hyphenated_persona(self):
        persona, user = MemoryManager._parse_scope("my-cool-persona-67890")
        assert persona == "my-cool-persona"
        assert user == "67890"


class TestMemoryPaths:
    def test_day_path_format(self, config_file):
        mm = MemoryManager()
        p = mm._day_path("meowko-1", date(2025, 3, 15))
        assert p.name == "day-2025-03-15.md"

    def test_week_path_format(self, config_file):
        mm = MemoryManager()
        d = date(2025, 3, 15)
        p = mm._week_path("meowko-1", d)
        assert p.name.startswith("week-2025-03-")

    def test_month_path_format(self, config_file):
        mm = MemoryManager()
        p = mm._month_path("meowko-1", date(2025, 3, 15))
        assert p.name == "month-2025-03.md"

    def test_season_path_format(self, config_file):
        mm = MemoryManager()
        p = mm._season_path("meowko-1", date(2025, 7, 1))
        assert p.name == "season-2025-03.md"  # July = season 3

    def test_year_path_format(self, config_file):
        mm = MemoryManager()
        p = mm._year_path("meowko-1", date(2025, 6, 1))
        assert p.name == "year-2025.md"


class TestStemToDateRange:
    def test_day(self):
        assert _stem_to_date_range("day-2025-03-15") == "2025-03-15"

    def test_week(self):
        # ISO week 11 of 2025: Monday 2025-03-10 to Sunday 2025-03-16
        result = _stem_to_date_range("week-2025-03-11")
        assert result == "2025-03-10 to 2025-03-16"

    def test_week_cross_month(self):
        # ISO week 1 of 2025: Monday 2024-12-30 to Sunday 2025-01-05
        result = _stem_to_date_range("week-2025-01-01")
        assert result == "2024-12-30 to 2025-01-05"

    def test_month(self):
        assert _stem_to_date_range("month-2025-02") == "2025-02-01 to 2025-02-28"

    def test_month_leap_year(self):
        assert _stem_to_date_range("month-2024-02") == "2024-02-01 to 2024-02-29"

    def test_season_q1(self):
        assert _stem_to_date_range("season-2025-01") == "2025-01-01 to 2025-03-31"

    def test_season_q2(self):
        assert _stem_to_date_range("season-2025-02") == "2025-04-01 to 2025-06-30"

    def test_season_q3(self):
        assert _stem_to_date_range("season-2025-03") == "2025-07-01 to 2025-09-30"

    def test_season_q4(self):
        assert _stem_to_date_range("season-2025-04") == "2025-10-01 to 2025-12-31"

    def test_year(self):
        assert _stem_to_date_range("year-2025") == "2025-01-01 to 2025-12-31"

    def test_unknown_tier_returns_stem(self):
        assert _stem_to_date_range("unknown-thing") == "unknown-thing"


class TestReadAllMemories:
    def test_empty_scope(self, config_file):
        mm = MemoryManager()
        assert mm.read_all_memories("nonexistent-1") == ""

    def test_reads_files_in_hierarchy_order(self, config_file):
        mm = MemoryManager()
        scope_dir = mm._scope_dir("test-1")
        scope_dir.mkdir(parents=True, exist_ok=True)

        (scope_dir / "day-2025-03-15.md").write_text("daily note", encoding="utf-8")
        (scope_dir / "month-2025-03.md").write_text("monthly note", encoding="utf-8")
        (scope_dir / "year-2025.md").write_text("yearly note", encoding="utf-8")

        result = mm.read_all_memories("test-1")
        # year < month < day
        lines = result.split("\n\n")
        assert "2025-01-01 to 2025-12-31" in lines[0]
        assert "2025-03-01 to 2025-03-31" in lines[1]
        assert "2025-03-15" in lines[2]

    def test_skips_empty_files(self, config_file):
        mm = MemoryManager()
        scope_dir = mm._scope_dir("test-1")
        scope_dir.mkdir(parents=True, exist_ok=True)

        (scope_dir / "day-2025-01-01.md").write_text("", encoding="utf-8")
        (scope_dir / "day-2025-01-02.md").write_text("has content", encoding="utf-8")

        result = mm.read_all_memories("test-1")
        assert "has content" in result
        assert result.count("##") == 1  # Only one file header
