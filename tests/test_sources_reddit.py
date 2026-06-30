"""
tests/test_sources_reddit.py — Unit tests for sources/reddit.py

Covers:
  - _extract_company_from_reddit: @ pattern, [FOR HIRE] detection
  - _extract_location_hint: remote, india, not specified
  - fetch_reddit: feedparser-mocked RSS parsing, deduplication,
    keyword filtering, error handling per feed
"""

import pytest
from unittest.mock import patch, MagicMock

from sources.reddit import (
    _extract_company_from_reddit,
    _extract_location_hint,
    fetch_reddit,
)


# ─────────────────────────────────────────────────────────────────
# _extract_company_from_reddit
# ─────────────────────────────────────────────────────────────────

class TestExtractCompanyFromReddit:
    def test_at_symbol_pattern(self):
        company = _extract_company_from_reddit("[HIRING] Backend Intern @ Razorpay")
        assert company == "Razorpay"

    def test_at_symbol_with_spaces(self):
        company = _extract_company_from_reddit("Hiring: Go Dev @ Juspay India")
        assert "Juspay" in company

    def test_for_hire_returns_candidate_post(self):
        company = _extract_company_from_reddit("[FOR HIRE] Backend Developer available")
        assert company == "CANDIDATE_POST"

    def test_for_hire_case_insensitive(self):
        company = _extract_company_from_reddit("[for hire] Go Developer")
        assert company == "CANDIDATE_POST"

    def test_no_company_returns_empty(self):
        company = _extract_company_from_reddit("Backend intern needed")
        assert company == ""

    def test_company_name_extracted_correctly(self):
        company = _extract_company_from_reddit("[HIRING] SDE Intern @ TechCorp")
        assert company == "TechCorp"


# ─────────────────────────────────────────────────────────────────
# _extract_location_hint
# ─────────────────────────────────────────────────────────────────

class TestExtractLocationHint:
    def test_remote_detected(self):
        assert _extract_location_hint("Remote-first company") == "Remote"

    def test_india_detected(self):
        assert _extract_location_hint("Hiring in India") == "India"

    def test_not_specified_fallback(self):
        assert _extract_location_hint("Hiring a backend engineer") == "Not specified"

    def test_case_insensitive_remote(self):
        assert _extract_location_hint("REMOTE opportunity") == "Remote"

    def test_remote_takes_priority_over_india(self):
        """Remote mention in text → return 'Remote'."""
        result = _extract_location_hint("Remote team, India timezone preferred")
        assert result == "Remote"


# ─────────────────────────────────────────────────────────────────
# fetch_reddit — mocked feedparser
# ─────────────────────────────────────────────────────────────────

def _make_feed_entry(title, link, summary="We are hiring", published="2026-05-01"):
    entry = MagicMock()
    entry.get = lambda key, default="": {
        "title": title,
        "link": link,
        "summary": summary,
        "published": published,
    }.get(key, default)
    return entry


def _make_feed(entries):
    feed = MagicMock()
    feed.entries = entries
    return feed


class TestFetchReddit:
    @patch("sources.reddit.feedparser.parse")
    def test_returns_jobs_matching_keywords(self, mock_parse):
        entry = _make_feed_entry(
            title="[HIRING] Backend Intern @ StartupCo",
            link="https://reddit.com/r/post/1",
            summary="We are hiring a backend developer.",
        )
        mock_parse.return_value = _make_feed([entry])
        jobs = fetch_reddit()
        assert len(jobs) >= 1

    @patch("sources.reddit.feedparser.parse")
    def test_filters_non_job_posts(self, mock_parse):
        entry = _make_feed_entry(
            title="What's your favourite programming language?",
            link="https://reddit.com/r/post/discuss",
        )
        mock_parse.return_value = _make_feed([entry])
        jobs = fetch_reddit()
        assert len(jobs) == 0

    @patch("sources.reddit.feedparser.parse")
    def test_deduplicates_same_url(self, mock_parse):
        entry = _make_feed_entry(
            title="[HIRING] Backend Intern @ Corp",
            link="https://reddit.com/r/post/dup",
        )
        # Same entry returned from multiple feeds
        mock_parse.return_value = _make_feed([entry, entry])
        jobs = fetch_reddit()
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls))

    @patch("sources.reddit.feedparser.parse")
    def test_source_is_reddit(self, mock_parse):
        entry = _make_feed_entry(
            title="[HIRING] Golang Backend Intern",
            link="https://reddit.com/r/post/2",
        )
        mock_parse.return_value = _make_feed([entry])
        jobs = fetch_reddit()
        assert all(j["source"] == "reddit" for j in jobs)

    @patch("sources.reddit.feedparser.parse")
    def test_feed_error_continues_to_next_feed(self, mock_parse):
        """One feed failing should not stop the others."""
        mock_parse.side_effect = [
            Exception("connection error"),
            _make_feed([_make_feed_entry("[HIRING] Go Backend", "https://reddit.com/r/post/3")]),
        ]
        # Should not raise; at least some feeds continue
        try:
            jobs = fetch_reddit()
        except Exception:
            pytest.fail("fetch_reddit raised an exception instead of catching per-feed errors")

    @patch("sources.reddit.feedparser.parse")
    def test_for_hire_post_gets_candidate_company(self, mock_parse):
        entry = _make_feed_entry(
            title="[FOR HIRE] Backend developer looking for work",
            link="https://reddit.com/r/post/forhire",
        )
        mock_parse.return_value = _make_feed([entry])
        jobs = fetch_reddit()
        if jobs:
            assert jobs[0]["company"] == "CANDIDATE_POST"

    @patch("sources.reddit.feedparser.parse")
    def test_all_required_keys_present(self, mock_parse):
        entry = _make_feed_entry(
            title="[HIRING] Golang Backend Fresher",
            link="https://reddit.com/r/post/keys",
        )
        mock_parse.return_value = _make_feed([entry])
        jobs = fetch_reddit()
        required = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}
        for job in jobs:
            assert required.issubset(job.keys())

    @patch("sources.reddit.feedparser.parse")
    def test_golang_keyword_triggers_include(self, mock_parse):
        entry = _make_feed_entry(
            title="Golang engineer needed for fintech startup",
            link="https://reddit.com/r/post/golang",
        )
        mock_parse.return_value = _make_feed([entry])
        jobs = fetch_reddit()
        assert len(jobs) >= 1

    @patch("sources.reddit.feedparser.parse")
    def test_intern_keyword_triggers_include(self, mock_parse):
        entry = _make_feed_entry(
            title="Software intern opening — apply now",
            link="https://reddit.com/r/post/intern",
        )
        mock_parse.return_value = _make_feed([entry])
        jobs = fetch_reddit()
        assert len(jobs) >= 1
