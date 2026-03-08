"""Tests for fetch_github_stars — merge logic and star parsing."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from fetch_github_stars import get_recent_stars, merge_bookmarks


class TestMergeBookmarks:
    def test_adds_new_bookmarks(self):
        existing = [{"url": "https://a.com"}]
        new = [{"url": "https://b.com"}]
        result = merge_bookmarks(existing, new)
        assert len(result) == 2

    def test_skips_duplicates(self):
        existing = [{"url": "https://a.com"}]
        new = [{"url": "https://a.com"}, {"url": "https://b.com"}]
        result = merge_bookmarks(existing, new)
        assert len(result) == 2

    def test_empty_existing(self):
        result = merge_bookmarks([], [{"url": "https://a.com"}])
        assert len(result) == 1

    def test_empty_new(self):
        existing = [{"url": "https://a.com"}]
        result = merge_bookmarks(existing, [])
        assert len(result) == 1


class TestGetRecentStars:
    def test_filters_old_stars(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        api_response = [
            {
                "starred_at": recent_date,
                "repo": {
                    "html_url": "https://github.com/owner/new-repo",
                    "full_name": "owner/new-repo",
                    "description": "A new repo",
                    "topics": [],
                    "language": "Python",
                    "stargazers_count": 10,
                },
            },
            {
                "starred_at": old_date,
                "repo": {
                    "html_url": "https://github.com/owner/old-repo",
                    "full_name": "owner/old-repo",
                    "description": "An old repo",
                    "topics": [],
                    "language": "Go",
                    "stargazers_count": 5,
                },
            },
        ]

        with patch("fetch_github_stars._api_get", side_effect=[api_response]):
            result = get_recent_stars("testuser", lookback_days=7)

        assert len(result) == 1
        assert result[0]["url"] == "https://github.com/owner/new-repo"
        assert result[0]["source"] == "github-star"

    def test_empty_response(self):
        with patch("fetch_github_stars._api_get", return_value=[]):
            result = get_recent_stars("testuser", lookback_days=7)
        assert result == []

    def test_api_error_returns_empty(self):
        with patch("fetch_github_stars._api_get", return_value=None):
            result = get_recent_stars("testuser", lookback_days=7)
        assert result == []

    def test_includes_notes_with_metadata(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        api_response = [
            {
                "starred_at": recent,
                "repo": {
                    "html_url": "https://github.com/owner/repo",
                    "full_name": "owner/repo",
                    "description": "Cool project",
                    "topics": ["ai", "llm"],
                    "language": "Python",
                    "stargazers_count": 100,
                },
            },
        ]

        # Return data on first call, empty on second to stop pagination
        with patch("fetch_github_stars._api_get", side_effect=[api_response, []]):
            result = get_recent_stars("testuser", lookback_days=7)

        assert len(result) == 1
        assert "Python" in result[0]["notes"]
        assert "ai" in result[0]["notes"]
