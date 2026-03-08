"""Tests for bookmark tracker — state loading, filtering, and cleanup."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_orchestrator.core.bookmark_tracker import (
    cleanup_old_entries,
    filter_unprocessed,
    load_bookmarks,
    load_state,
    mark_processed,
    save_state,
)


class TestLoadState:
    def test_missing_file_returns_default(self, tmp_path: Path):
        state = load_state(tmp_path / "nonexistent.json")
        assert state == {"processed": {}, "last_run": None}

    def test_valid_state_file(self, tmp_path: Path):
        f = tmp_path / "state.json"
        data = {"processed": {"https://a.com": {}}, "last_run": "2026-03-01T00:00:00Z"}
        f.write_text(json.dumps(data))
        state = load_state(f)
        assert "https://a.com" in state["processed"]

    def test_corrupt_json_returns_default(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("{bad json")
        state = load_state(f)
        assert state == {"processed": {}, "last_run": None}


class TestSaveState:
    def test_creates_file_and_updates_last_run(self, tmp_path: Path):
        f = tmp_path / "sub" / "state.json"
        state = {"processed": {}, "last_run": None}
        save_state(f, state)
        assert f.exists()
        loaded = json.loads(f.read_text())
        assert loaded["last_run"] is not None

    def test_preserves_processed_data(self, tmp_path: Path):
        f = tmp_path / "state.json"
        state = {"processed": {"https://x.com": {"summary": "test"}}, "last_run": None}
        save_state(f, state)
        loaded = json.loads(f.read_text())
        assert "https://x.com" in loaded["processed"]


class TestLoadBookmarks:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert load_bookmarks(tmp_path / "no.json") == []

    def test_valid_bookmarks(self, tmp_path: Path):
        f = tmp_path / "bm.json"
        data = [{"url": "https://a.com", "added": "2026-03-08T00:00:00Z"}]
        f.write_text(json.dumps(data))
        bms = load_bookmarks(f)
        assert len(bms) == 1
        assert bms[0]["url"] == "https://a.com"

    def test_non_list_returns_empty(self, tmp_path: Path):
        f = tmp_path / "bm.json"
        f.write_text('{"url": "not a list"}')
        assert load_bookmarks(f) == []

    def test_corrupt_json_returns_empty(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("[broken")
        assert load_bookmarks(f) == []


class TestFilterUnprocessed:
    def test_filters_already_processed(self):
        bookmarks = [
            {"url": "https://a.com", "added": datetime.now(timezone.utc).isoformat()},
            {"url": "https://b.com", "added": datetime.now(timezone.utc).isoformat()},
        ]
        state = {"processed": {"https://a.com": {}}}
        result = filter_unprocessed(bookmarks, state)
        assert len(result) == 1
        assert result[0]["url"] == "https://b.com"

    def test_filters_old_bookmarks(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        bookmarks = [{"url": "https://old.com", "added": old_date}]
        state = {"processed": {}}
        result = filter_unprocessed(bookmarks, state, lookback_days=7)
        assert len(result) == 0

    def test_includes_recent_bookmarks(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        bookmarks = [{"url": "https://new.com", "added": recent}]
        state = {"processed": {}}
        result = filter_unprocessed(bookmarks, state, lookback_days=7)
        assert len(result) == 1

    def test_skips_empty_urls(self):
        bookmarks = [{"url": "", "added": datetime.now(timezone.utc).isoformat()}]
        result = filter_unprocessed(bookmarks, {"processed": {}})
        assert len(result) == 0

    def test_includes_bookmarks_without_date(self):
        bookmarks = [{"url": "https://nodate.com"}]
        result = filter_unprocessed(bookmarks, {"processed": {}})
        assert len(result) == 1

    def test_handles_unparseable_date(self):
        bookmarks = [{"url": "https://x.com", "added": "not-a-date"}]
        result = filter_unprocessed(bookmarks, {"processed": {}})
        assert len(result) == 1


class TestMarkProcessed:
    def test_marks_url(self):
        state = {"processed": {}}
        mark_processed(state, "https://a.com", summary="test", improvements=["imp1"])
        assert "https://a.com" in state["processed"]
        assert state["processed"]["https://a.com"]["summary"] == "test"
        assert state["processed"]["https://a.com"]["improvements"] == ["imp1"]

    def test_creates_processed_dict_if_missing(self):
        state = {}
        mark_processed(state, "https://b.com")
        assert "https://b.com" in state["processed"]

    def test_default_empty_improvements(self):
        state = {"processed": {}}
        mark_processed(state, "https://c.com")
        assert state["processed"]["https://c.com"]["improvements"] == []


class TestCleanupOldEntries:
    def test_removes_old_entries(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        state = {
            "processed": {
                "https://old.com": {"processed_at": old_date},
                "https://new.com": {"processed_at": datetime.now(timezone.utc).isoformat()},
            }
        }
        removed = cleanup_old_entries(state, max_age_days=30)
        assert removed == 1
        assert "https://old.com" not in state["processed"]
        assert "https://new.com" in state["processed"]

    def test_no_entries_to_remove(self):
        state = {
            "processed": {
                "https://recent.com": {"processed_at": datetime.now(timezone.utc).isoformat()},
            }
        }
        removed = cleanup_old_entries(state, max_age_days=30)
        assert removed == 0

    def test_empty_state(self):
        state = {"processed": {}}
        removed = cleanup_old_entries(state)
        assert removed == 0
