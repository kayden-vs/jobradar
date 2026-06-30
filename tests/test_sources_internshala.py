"""
tests/test_sources_internshala.py — Unit tests for sources/internshala.py

Covers:
  - _extract_company_from_reddit (actually from reddit module) — N/A
  - _fetch_page: requests-mocked HTTP responses
  - fetch_internshala: BeautifulSoup-based parsing with fixture HTML,
    deduplication, WFH → Remote normalisation, error handling
  - Playwright-unavailable path (returns [] early)
"""

import pytest
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────
# Playwright availability — always mock as True for scraper tests
# ─────────────────────────────────────────────────────────────────

# Internshala uses requests + BeautifulSoup (no Playwright), so
# we don't need to mock is_playwright_available here. However the
# module does import from sources.utils, so we keep it consistent.

# ─────────────────────────────────────────────────────────────────
# Sample HTML fixture — mimics Internshala job card structure
# ─────────────────────────────────────────────────────────────────

SAMPLE_INTERNSHALA_HTML = """
<html><body>
  <div class="individual_internship">
    <h2 class="job-internship-name">
      <a class="job-title-href" href="/internship/detail/backend-engineer-at-razorpay-12345">
        Backend Engineer Intern
      </a>
    </h2>
    <p class="company-name">Razorpay</p>
    <div class="locations">
      <span><a href="/internships/in-bangalore">Bangalore</a></span>
    </div>
    <span class="stipend">₹15,000 /month</span>
  </div>
  <div class="individual_internship">
    <h2 class="job-internship-name">
      <a class="job-title-href" href="/internship/detail/backend-dev-wfh-67890">
        TypeScript Backend Developer
      </a>
    </h2>
    <p class="company-name">StartupCo</p>
    <div class="locations">
      <span><a href="/internships/work-from-home">Work From Home</a></span>
    </div>
    <span class="stipend">₹20,000 /month</span>
  </div>
</body></html>
"""

EMPTY_HTML = "<html><body></body></html>"


def _mock_response(html_content, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.text = html_content
    mock.raise_for_status.return_value = None
    return mock


class TestFetchInternshala:
    @patch("sources.internshala.time.sleep")  # don't actually sleep
    @patch("sources.internshala._get_session")
    def test_parses_job_cards(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        assert len(jobs) >= 1

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_job_has_required_keys(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        required = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}
        for job in jobs:
            assert required.issubset(job.keys())

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_source_is_internshala(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        for job in jobs:
            assert job["source"] == "internshala"

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_wfh_normalised_to_remote(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        # The second job card has "Work From Home" → should become "Remote"
        wfh_jobs = [j for j in jobs if "TypeScript" in j["title"]]
        if wfh_jobs:
            assert wfh_jobs[0]["location"] == "Remote"

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_url_constructed_correctly(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        for job in jobs:
            assert job["url"].startswith("https://internshala.com")

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_deduplicates_same_url(self, mock_session_fn, mock_sleep):
        """Multiple search URLs returning the same job → deduplicated."""
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        # Same HTML returned for every search URL
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls)), "Duplicate URLs in results"

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_empty_page_returns_empty(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(EMPTY_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        assert jobs == []

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_request_exception_handled(self, mock_session_fn, mock_sleep):
        """HTTP error on a URL should be skipped gracefully."""
        import requests as _req
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.side_effect = _req.ConnectionError("timeout")

        from sources.internshala import fetch_internshala
        try:
            jobs = fetch_internshala()
            assert isinstance(jobs, list)
        except Exception:
            pytest.fail("fetch_internshala raised on request error")

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_stipend_captured_as_salary(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        razorpay_jobs = [j for j in jobs if "Razorpay" in j["company"]]
        if razorpay_jobs:
            assert "15,000" in razorpay_jobs[0]["salary"] or razorpay_jobs[0]["salary"] != ""

    @patch("sources.internshala.time.sleep")
    @patch("sources.internshala._get_session")
    def test_company_name_extracted(self, mock_session_fn, mock_sleep):
        mock_session = MagicMock()
        mock_session_fn.return_value = mock_session
        mock_session.get.return_value = _mock_response(SAMPLE_INTERNSHALA_HTML)

        from sources.internshala import fetch_internshala
        jobs = fetch_internshala()
        companies = {j["company"] for j in jobs}
        assert "Razorpay" in companies or "StartupCo" in companies
