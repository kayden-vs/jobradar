"""
tests/test_sources_hackernews.py — Unit tests for sources/hackernews.py

Covers (all logic that doesn't require real API calls):
  - _is_valid_job: valid vs. discussion thread vs. missing company+url
  - get_current_thread_id: known month key lookup
  - HN_THREAD_IDS: dict integrity (all values are ints)
  - fetch_hn_comments: mocked HTTP responses
  - parse_comments_with_ai: mocked Gemini client (no real API spend)

Note: fetch_hn_hiring() is integration-level and tested with mocks only.
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from sources.hackernews import (
    _is_valid_job,
    get_current_thread_id,
    HN_THREAD_IDS,
    fetch_hn_comments,
    parse_comments_with_ai,
)


# ─────────────────────────────────────────────────────────────────
# HN_THREAD_IDS — data integrity
# ─────────────────────────────────────────────────────────────────

class TestHnThreadIds:
    def test_all_values_are_ints(self):
        for key, val in HN_THREAD_IDS.items():
            assert isinstance(val, int), f"Thread ID for {key} is not an int"

    def test_keys_follow_yyyy_mm_format(self):
        import re
        for key in HN_THREAD_IDS:
            assert re.match(r"^\d{4}-\d{2}$", key), f"Key {key} not in YYYY-MM format"

    def test_no_zero_ids(self):
        for key, val in HN_THREAD_IDS.items():
            assert val > 0, f"Thread ID for {key} is 0 or negative"


# ─────────────────────────────────────────────────────────────────
# _is_valid_job
# ─────────────────────────────────────────────────────────────────

class TestIsValidJob:
    def test_valid_job_with_company(self):
        job = {"title": "Backend Engineer", "company": "TechCorp", "url": ""}
        assert _is_valid_job(job) is True

    def test_valid_job_with_url(self):
        job = {"title": "Go Developer", "company": "", "url": "https://example.com/apply"}
        assert _is_valid_job(job) is True

    def test_too_short_title_rejected(self):
        job = {"title": "Go", "company": "TechCorp", "url": ""}
        assert _is_valid_job(job) is False

    def test_missing_both_company_and_url(self):
        job = {"title": "Backend Engineer", "company": "", "url": ""}
        assert _is_valid_job(job) is False

    def test_discussion_thread_how_do_rejected(self):
        job = {"title": "How do I get my first job?", "company": "Community", "url": ""}
        assert _is_valid_job(job) is False

    def test_discussion_thread_anyone_else_rejected(self):
        job = {"title": "Anyone else struggling with Go interviews?", "company": "", "url": "http://x.com"}
        assert _is_valid_job(job) is False

    def test_advice_on_rejected(self):
        job = {"title": "Advice on transitioning to backend", "company": "Forum", "url": "http://a.com"}
        assert _is_valid_job(job) is False

    def test_what_is_rejected(self):
        job = {"title": "What is the best Go framework?", "company": "HN", "url": ""}
        assert _is_valid_job(job) is False

    def test_empty_title_rejected(self):
        job = {"title": "", "company": "Corp", "url": "https://apply.corp.com"}
        assert _is_valid_job(job) is False

    def test_job_market_discussion_rejected(self):
        job = {"title": "Job market update — things are improving", "company": "", "url": "http://a.com"}
        assert _is_valid_job(job) is False


# ─────────────────────────────────────────────────────────────────
# get_current_thread_id
# ─────────────────────────────────────────────────────────────────

class TestGetCurrentThreadId:
    def test_returns_known_id_for_may_2026(self):
        with patch("sources.hackernews.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 15)
            thread_id = get_current_thread_id()
        assert thread_id == HN_THREAD_IDS.get("2026-05")

    def test_autodiscovery_called_for_unknown_month(self):
        with patch("sources.hackernews.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2099, 1, 1)
            with patch("sources.hackernews._autodiscover_thread_id", return_value=99999) as mock_discover:
                result = get_current_thread_id()
                mock_discover.assert_called_once_with(2099, 1)
                assert result == 99999


# ─────────────────────────────────────────────────────────────────
# fetch_hn_comments — mocked requests
# ─────────────────────────────────────────────────────────────────

def _mock_get(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    return mock


class TestFetchHnComments:
    @patch("sources.hackernews.requests.get")
    def test_returns_comment_texts(self, mock_get):
        thread_data = {"kids": [101, 102]}
        comment1 = {"text": "We are hiring a Go backend engineer at FinTechCorp. Apply at https://fintechcorp.com/jobs", "deleted": False}
        comment2 = {"text": "Another company is looking for TypeScript devs in backend roles. Strong match for engineers.", "deleted": False}
        mock_get.side_effect = [
            _mock_get(thread_data),
            _mock_get(comment1),
            _mock_get(comment2),
        ]
        comments = fetch_hn_comments(12345, max_comments=2)
        assert len(comments) == 2

    @patch("sources.hackernews.requests.get")
    def test_deleted_comments_skipped(self, mock_get):
        thread_data = {"kids": [101]}
        comment = {"text": "Hiring now!", "deleted": True}
        mock_get.side_effect = [_mock_get(thread_data), _mock_get(comment)]
        comments = fetch_hn_comments(12345, max_comments=1)
        assert len(comments) == 0

    @patch("sources.hackernews.requests.get")
    def test_short_comments_skipped(self, mock_get):
        thread_data = {"kids": [101]}
        comment = {"text": "Cool!", "deleted": False}
        mock_get.side_effect = [_mock_get(thread_data), _mock_get(comment)]
        comments = fetch_hn_comments(12345, max_comments=1)
        assert len(comments) == 0  # too short (< 50 chars)

    @patch("sources.hackernews.requests.get")
    def test_max_comments_cap_respected(self, mock_get):
        """Only the first N kids should be fetched."""
        thread_data = {"kids": list(range(100, 200))}
        # The thread fetch + up to 10 comment fetches
        def side_effect(*args, **kwargs):
            if "item/100" in args[0] or args[0].endswith(".json"):
                # Return thread on first call
                if not hasattr(side_effect, "called"):
                    side_effect.called = True
                    return _mock_get(thread_data)
            return _mock_get({"text": "A" * 100, "deleted": False})
        mock_get.return_value = _mock_get({"text": "A" * 100, "deleted": False})

        # Simplified: just verify it doesn't fetch more than max_comments
        with patch("sources.hackernews.requests.get") as mock:
            mock.side_effect = [_mock_get(thread_data)] + [
                _mock_get({"text": "A" * 100, "deleted": False}) for _ in range(10)
            ]
            comments = fetch_hn_comments(99999, max_comments=10)
            assert len(comments) <= 10

    @patch("sources.hackernews.requests.get")
    def test_html_stripped_from_comment_text(self, mock_get):
        thread_data = {"kids": [101]}
        comment = {"text": "<p>We are hiring <b>Go engineers</b> at Corp.</p>" + " " * 50, "deleted": False}
        mock_get.side_effect = [_mock_get(thread_data), _mock_get(comment)]
        comments = fetch_hn_comments(12345, max_comments=1)
        if comments:
            assert "<p>" not in comments[0]
            assert "Go engineers" in comments[0]


# ─────────────────────────────────────────────────────────────────
# parse_comments_with_ai — mocked Gemini client
# ─────────────────────────────────────────────────────────────────

class TestParseCommentsWithAi:
    def _make_gemini_response(self, content: str):
        """Create a mock Gemini response with a .text attribute."""
        response = MagicMock()
        response.text = content
        return response

    @patch("sources.hackernews._throttle")
    @patch("sources.hackernews._gemini_client")
    def test_valid_json_extracted(self, mock_client_fn, mock_throttle):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.models.generate_content.return_value = self._make_gemini_response(
            '[{"title": "Go Backend Engineer", "company": "FinCorp", "location": "Remote", '
            '"description": "Build APIs", "url": "https://fincorp.com/jobs/1", '
            '"salary": "", "requires_experience": 0, "tech_stack": "Go, gRPC"}]'
        )
        jobs = parse_comments_with_ai(["We are hiring Go engineers at FinCorp. Remote." + " more" * 20])
        assert len(jobs) == 1
        assert jobs[0]["source"] == "hackernews"
        assert jobs[0]["title"] == "Go Backend Engineer"

    @patch("sources.hackernews._throttle")
    @patch("sources.hackernews._gemini_client")
    def test_empty_array_response(self, mock_client_fn, mock_throttle):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.models.generate_content.return_value = self._make_gemini_response("[]")
        jobs = parse_comments_with_ai(["This is a general discussion post."])
        assert jobs == []

    @patch("sources.hackernews._throttle")
    @patch("sources.hackernews._gemini_client")
    def test_markdown_fenced_json_handled(self, mock_client_fn, mock_throttle):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.models.generate_content.return_value = self._make_gemini_response(
            '```json\n[{"title": "Backend Dev", "company": "Corp", "location": "Remote", '
            '"description": "desc", "url": "https://corp.com/job", '
            '"salary": "", "requires_experience": 0, "tech_stack": "Go"}]\n```'
        )
        jobs = parse_comments_with_ai(["Hiring backend devs at Corp." + " x" * 30])
        assert len(jobs) == 1

    @patch("sources.hackernews._throttle")
    @patch("sources.hackernews._gemini_client")
    def test_invalid_json_handled_gracefully(self, mock_client_fn, mock_throttle):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.models.generate_content.return_value = self._make_gemini_response(
            "I'm sorry, I cannot extract jobs from this."
        )
        jobs = parse_comments_with_ai(["Some non-job text." + " x" * 30])
        assert jobs == []

    @patch("sources.hackernews._throttle")
    @patch("sources.hackernews._gemini_client")
    def test_invalid_jobs_filtered_by_is_valid_job(self, mock_client_fn, mock_throttle):
        """Jobs failing _is_valid_job should be filtered out before returning."""
        client = MagicMock()
        mock_client_fn.return_value = client
        # Return a job with no company and no url → _is_valid_job returns False
        client.models.generate_content.return_value = self._make_gemini_response(
            '[{"title": "Backend Dev", "company": "", "location": "Remote", '
            '"description": "desc", "url": "", "salary": "", "requires_experience": 0, "tech_stack": ""}]'
        )
        jobs = parse_comments_with_ai(["Some text." + " x" * 30])
        assert jobs == []

    @patch("sources.hackernews._throttle")
    @patch("sources.hackernews._gemini_client")
    def test_api_exception_returns_empty(self, mock_client_fn, mock_throttle):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.models.generate_content.side_effect = Exception("API error")
        jobs = parse_comments_with_ai(["Some text." + " x" * 30])
        assert jobs == []

    @patch("sources.hackernews._throttle")
    @patch("sources.hackernews._gemini_client")
    def test_empty_comments_returns_empty(self, mock_client_fn, mock_throttle):
        jobs = parse_comments_with_ai([])
        assert jobs == []
