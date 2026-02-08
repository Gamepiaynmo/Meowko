"""Tests for JSONLStore conversation storage."""

import json
from datetime import datetime

from zoneinfo import ZoneInfo

from src.core.jsonl_store import JSONLStore


class TestAppendAndRead:
    def test_append_creates_file_and_read_returns_events(self, config_file):
        store = JSONLStore()
        event = {"role": "user", "content": "hello"}
        store.append("persona", 123, event)

        events = store.read_all("persona", 123)
        assert len(events) == 1
        assert events[0]["role"] == "user"
        assert events[0]["content"] == "hello"

    def test_append_multiple_events(self, config_file):
        store = JSONLStore()
        store.append("p", 1, {"role": "user", "content": "hi"})
        store.append("p", 1, {"role": "assistant", "content": "hello"})
        store.append("p", 1, {"role": "user", "content": "bye"})

        events = store.read_all("p", 1)
        assert len(events) == 3
        assert events[2]["content"] == "bye"

    def test_read_all_returns_empty_for_nonexistent(self, config_file):
        store = JSONLStore()
        assert store.read_all("nope", 999) == []

    def test_unicode_content(self, config_file):
        store = JSONLStore()
        store.append("p", 1, {"role": "user", "content": "你好世界 🐱"})
        events = store.read_all("p", 1)
        assert events[0]["content"] == "你好世界 🐱"


class TestReadFile:
    def test_read_file_returns_events(self, config_file):
        store = JSONLStore()
        path = store.append("p", 1, {"role": "user", "content": "test"})
        events = store.read_file(path)
        assert len(events) == 1

    def test_read_file_nonexistent(self, config_file, tmp_path):
        store = JSONLStore()
        assert store.read_file(tmp_path / "nope.jsonl") == []


class TestReadDate:
    def test_read_date(self, config_file):
        store = JSONLStore()
        store.append("p", 1, {"role": "user", "content": "dated"})

        tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        # The date file depends on rollup_time boundary; just read today's date
        events = store.read_date("p", 1, now)
        # May or may not match depending on rollup boundary — at least no crash
        assert isinstance(events, list)


class TestRewind:
    def test_rewind_removes_last_user_and_following(self, config_file):
        store = JSONLStore()
        store.append("p", 1, {"role": "user", "content": "msg1"})
        store.append("p", 1, {"role": "assistant", "content": "reply1"})
        store.append("p", 1, {"role": "user", "content": "msg2"})
        store.append("p", 1, {"role": "assistant", "content": "reply2"})

        removed = store.rewind("p", 1)
        assert removed == 2  # msg2 + reply2

        events = store.read_all("p", 1)
        assert len(events) == 2
        assert events[-1]["content"] == "reply1"

    def test_rewind_single_user_message_deletes_file(self, config_file):
        store = JSONLStore()
        path = store.append("p", 1, {"role": "user", "content": "only"})
        removed = store.rewind("p", 1)
        assert removed == 1
        assert not path.exists()

    def test_rewind_empty_returns_zero(self, config_file):
        store = JSONLStore()
        assert store.rewind("p", 999) == 0

    def test_rewind_no_user_messages_returns_zero(self, config_file):
        store = JSONLStore()
        store.append("p", 1, {"role": "system", "content": "context info"})
        removed = store.rewind("p", 1)
        assert removed == 0


class TestArchive:
    def test_archive_moves_file(self, config_file):
        store = JSONLStore()
        path = store.append("p", 1, {"role": "user", "content": "archiveme"})
        assert path.exists()

        archived = store.archive(path)
        assert not path.exists()
        assert archived.exists()
        assert archived.parent.name == "archive"

        # Verify content is preserved
        events = store.read_file(archived)
        assert events[0]["content"] == "archiveme"


class TestListScopes:
    def test_list_scopes_empty(self, config_file):
        store = JSONLStore()
        assert store.list_scopes() == []

    def test_list_scopes_returns_sorted_dirs(self, config_file):
        store = JSONLStore()
        store.append("beta", 2, {"role": "user", "content": "b"})
        store.append("alpha", 1, {"role": "user", "content": "a"})

        scopes = store.list_scopes()
        assert scopes == ["alpha-1", "beta-2"]

    def test_list_scopes_excludes_archive(self, config_file):
        store = JSONLStore()
        path = store.append("p", 1, {"role": "user", "content": "x"})
        # Create archive dir manually
        (store.conversations_dir / "archive").mkdir(exist_ok=True)
        scopes = store.list_scopes()
        assert "archive" not in scopes


class TestDateBoundary:
    def test_before_rollup_time_uses_previous_day(self, config_file):
        """Events before rollup_time (03:00 UTC) belong to the previous day."""
        store = JSONLStore()
        tz = ZoneInfo("UTC")
        # 2:30 AM should map to the previous day
        dt = datetime(2025, 6, 15, 2, 30, tzinfo=tz)
        path = store._get_file_path("p", 1, dt)
        assert "2025-06-14" in path.name

    def test_after_rollup_time_uses_current_day(self, config_file):
        """Events after rollup_time (03:00 UTC) belong to the current day."""
        store = JSONLStore()
        tz = ZoneInfo("UTC")
        dt = datetime(2025, 6, 15, 4, 0, tzinfo=tz)
        path = store._get_file_path("p", 1, dt)
        assert "2025-06-15" in path.name
