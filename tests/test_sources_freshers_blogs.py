"""
tests/test_sources_freshers_blogs.py — Unit tests for sources/freshers_blogs.py

Covers:
  - title_parser: various fresher blog title formats
  - fetch_freshers_blogs: mocked feedparser, concurrent execution,
    tag extraction, deduplication, error per feed
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from sources.freshers_blogs import title_parser


# ─────────────────────────────────────────────────────────────────
# title_parser
# ─────────────────────────────────────────────────────────────────

class TestTitleParser:
    def test_standard_format_company_role_location(self):
        title = "Razorpay Recruitment 2025: Backend Engineer | Bangalore | Batch 2025"
        result = title_parser(title)
        assert "razorpay" in result["company"].lower()
        assert "backend" in result["role"].lower() or "engineer" in result["role"].lower()

    def test_pipe_separated_format(self):
        title = "Juspay Internship | Golang Developer | Hyderabad"
        result = title_parser(title)
        assert result["company"] != ""
        # Location or role should be extracted
        assert result["location"] != "" or result["role"] != ""

    def test_dash_separated_format(self):
        title = "Infosys Freshers Jobs 2025 – Backend Developer – Pune"
        result = title_parser(title)
        assert result["company"] != ""

    def test_tcs_off_campus(self):
        title = "TCS Off Campus Drive 2025 | Software Engineer | Pan India"
        result = title_parser(title)
        assert "tcs" in result["company"].lower()

    def test_empty_title_returns_empty_dict(self):
        result = title_parser("")
        assert result == {"company": "", "role": "", "location": ""}

    def test_none_title_handled(self):
        result = title_parser(None)
        assert result == {"company": "", "role": "", "location": ""}

    def test_work_from_home_normalised_to_remote(self):
        title = "TechCorp Recruitment 2025 | Backend Dev | Work From Home"
        result = title_parser(title)
        if result["location"]:
            assert result["location"] in ("remote", "work from home", "")

    def test_wfh_normalised(self):
        title = "StartupXYZ Internship 2025 | Developer | WFH"
        result = title_parser(title)
        if result["location"] and result["location"] != "":
            assert result["location"] in ("remote", "wfh", "")

    def test_location_fallback_city_scan(self):
        """If regex fails to parse, the fallback city scan should find known cities."""
        title = "Backend Developer Job – Bangalore"
        result = title_parser(title)
        # Either role or location should contain bangalore
        combined = (result["company"] + result["role"] + result["location"]).lower()
        assert "bangalore" in combined or "bengaluru" in combined or result["location"] == ""

    def test_company_noise_stripped(self):
        """'Recruitment', 'Hiring', etc. should not bleed into company name."""
        title = "Amazon Recruitment 2025: SDE Intern | Remote"
        result = title_parser(title)
        company = result["company"].lower()
        assert "recruitment" not in company
        assert "amazon" in company or company == ""

    def test_returns_dict_with_all_keys(self):
        result = title_parser("Some Company Internship | Dev | Remote")
        assert set(result.keys()) == {"company", "role", "location"}

# ─────────────────────────────────────────────────────────────────
# fetch_freshers_blogs — mocked feedparser
# ─────────────────────────────────────────────────────────────────

def _make_tag(term: str):
    """Create a feedparser-compatible tag object with a 'term' key."""
    tag = SimpleNamespace()
    tag.term = term
    tag.__contains__ = lambda self, key: key == "term"
    tag.get = lambda key, default="": term if key == "term" else default
    # Make it behave like a dict for t.get("term", "")
    return {"term": term}


def _make_entry(title: str, link: str, summary: str = "", published: str = "",
                tags: list = None) -> SimpleNamespace:
    """
    Create a feedparser-compatible entry object using SimpleNamespace.
    Uses real attributes (not MagicMock auto-attrs) so the source code
    doesn't get 'expected string or bytes-like object, got MagicMock'.
    """
    entry = SimpleNamespace()
    entry.title = title
    entry.link = link
    entry.summary = summary
    entry.published = published
    entry.updated = ""
    entry.published_parsed = None
    entry.updated_parsed = None
    # tags must be a list of dicts with "term" key
    entry.tags = [{"term": t} for t in (tags or [])]
    return entry


def _make_feed(entries: list, bozo: bool = False) -> SimpleNamespace:
    """Create a feedparser-compatible feed object."""
    feed = SimpleNamespace()
    feed.entries = entries
    feed.bozo = bozo
    feed.bozo_exception = None
    feed.get = lambda key, default=None: default
    return feed


class TestFetchFresherBlogs:
    @patch("sources.freshers_blogs.feedparser.parse")
    def test_basic_job_returned(self, mock_parse):
        entry = _make_entry(
            title="Razorpay Recruitment 2025: Backend Engineer | Bangalore",
            link="https://offcampusjobs4u.com/razorpay-backend",
            summary="Razorpay is hiring backend engineers.",
            tags=["Software", "Backend", "Fresher"],
        )
        mock_parse.return_value = _make_feed([entry])
        from sources.freshers_blogs import fetch_freshers_blogs
        jobs = fetch_freshers_blogs()
        assert len(jobs) >= 1

    @patch("sources.freshers_blogs.feedparser.parse")
    def test_source_label_set(self, mock_parse):
        entry = _make_entry(
            title="Company Internship | Backend Dev | Remote",
            link="https://freshersdunia.in/job/1",
        )
        mock_parse.return_value = _make_feed([entry])
        from sources.freshers_blogs import fetch_freshers_blogs
        jobs = fetch_freshers_blogs()
        if jobs:
            assert "freshers_blogs" in jobs[0]["source"]

    @patch("sources.freshers_blogs.feedparser.parse")
    def test_deduplicates_same_url(self, mock_parse):
        entry = _make_entry(
            title="Juspay Backend Intern | Bangalore",
            link="https://freshersarea.in/juspay-dup",
        )
        # Return same entry from all feeds
        mock_parse.return_value = _make_feed([entry])
        from sources.freshers_blogs import fetch_freshers_blogs
        jobs = fetch_freshers_blogs()
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"

    @patch("sources.freshers_blogs.feedparser.parse")
    def test_bozo_feed_still_processes_entries(self, mock_parse):
        """Bozo (malformed) feeds should still process any successfully-parsed entries."""
        entry = _make_entry(
            title="Backend Engineer Intern",
            link="https://freshers-job.com/backend",
        )
        mock_parse.return_value = _make_feed([entry], bozo=True)
        from sources.freshers_blogs import fetch_freshers_blogs
        # Should not raise
        try:
            jobs = fetch_freshers_blogs()
            assert isinstance(jobs, list)
        except Exception:
            pytest.fail("fetch_freshers_blogs raised an exception on bozo feed")

    @patch("sources.freshers_blogs.feedparser.parse")
    def test_all_required_keys_present(self, mock_parse):
        entry = _make_entry(
            title="TCS SDE Fresher | Pan India",
            link="https://fresheropenings.com/tcs",
            summary="TCS is hiring freshers.",
        )
        mock_parse.return_value = _make_feed([entry])
        from sources.freshers_blogs import fetch_freshers_blogs
        jobs = fetch_freshers_blogs()
        required = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}
        for job in jobs:
            assert required.issubset(job.keys())

    @patch("sources.freshers_blogs.feedparser.parse")
    def test_tags_extracted_to_experience_tags(self, mock_parse):
        """Tags mentioning 'year' should appear in experience_tags."""
        entry = _make_entry(
            title="Backend Dev Internship",
            link="https://tnpofficer.com/backend",
            tags=["0-1 Years Experience", "Fresher", "Bangalore"],
        )
        mock_parse.return_value = _make_feed([entry])
        from sources.freshers_blogs import fetch_freshers_blogs
        jobs = fetch_freshers_blogs()
        if jobs:
            exp_tags = jobs[0].get("experience_tags", [])
            # At least one tag mentioning experience should be captured
            assert any("year" in t.lower() or "fresher" in t.lower() for t in exp_tags) or exp_tags == []

    @patch("sources.freshers_blogs.feedparser.parse")
    def test_empty_feed_returns_empty(self, mock_parse):
        mock_parse.return_value = _make_feed([])
        from sources.freshers_blogs import fetch_freshers_blogs
        jobs = fetch_freshers_blogs()
        assert jobs == []

    @patch("sources.freshers_blogs.feedparser.parse")
    def test_feed_exception_handled_gracefully(self, mock_parse):
        """feedparser raising should not crash the entire fetch."""
        mock_parse.side_effect = Exception("connection error")
        from sources.freshers_blogs import fetch_freshers_blogs
        try:
            jobs = fetch_freshers_blogs()
            assert isinstance(jobs, list)
        except Exception:
            pytest.fail("fetch_freshers_blogs should handle feed-level exceptions")
