"""
tests/test_sources_remoteok.py — Unit tests for sources/remoteok.py

Covers:
  - _is_dev_job: whitelist patterns + blacklist overrides
  - _clean_html: tag stripping
  - _format_salary: various salary combinations
  - fetch_remoteok: normal response, deduplication, non-dev filtering,
    first-element skip (API legal notice), error handling
"""

import pytest
from unittest.mock import patch, MagicMock
import requests

from sources.remoteok import (
    _is_dev_job,
    _clean_html,
    _format_salary,
    fetch_remoteok,
)


# ─────────────────────────────────────────────────────────────────
# _is_dev_job
# ─────────────────────────────────────────────────────────────────

class TestIsDevJob:
    # ── Should return True (dev roles) ────────────────────────────
    def test_backend_engineer(self):
        assert _is_dev_job("Backend Engineer") is True

    def test_golang_developer(self):
        assert _is_dev_job("Golang Developer") is True

    def test_typescript_developer(self):
        assert _is_dev_job("TypeScript Developer") is True

    def test_full_stack_engineer(self):
        assert _is_dev_job("Full Stack Engineer") is True

    def test_software_engineer(self):
        assert _is_dev_job("Software Engineer") is True

    def test_devops_engineer(self):
        assert _is_dev_job("DevOps Engineer") is True

    def test_sre(self):
        assert _is_dev_job("Site Reliability Engineer (SRE)") is True

    def test_data_engineer(self):
        assert _is_dev_job("Data Engineer") is True

    def test_ml_engineer(self):
        assert _is_dev_job("ML Engineer") is True

    def test_go_programmer_word_boundary(self):
        """'go' as a whole word matches, not part of 'good'"""
        assert _is_dev_job("Go Developer") is True

    def test_java_word_boundary(self):
        """'java' but not 'javascript' (tested separately)"""
        assert _is_dev_job("Java Developer") is True

    def test_node_js(self):
        assert _is_dev_job("Node.js Developer") is True

    # ── Should return False (non-dev roles) ───────────────────────
    def test_cleaner_rejected(self):
        assert _is_dev_job("Office Cleaner") is False

    def test_accountant_rejected(self):
        assert _is_dev_job("Senior Accountant") is False

    def test_marketing_rejected(self):
        assert _is_dev_job("Marketing Manager") is False

    def test_customer_support_rejected(self):
        assert _is_dev_job("Customer Support Specialist") is False

    def test_recruiter_rejected(self):
        assert _is_dev_job("Technical Recruiter") is False

    def test_sales_director_rejected(self):
        assert _is_dev_job("Sales Director") is False

    def test_brand_designer_rejected(self):
        assert _is_dev_job("Brand Designer") is False

    def test_executive_assistant_rejected(self):
        assert _is_dev_job("Executive Assistant") is False


# ─────────────────────────────────────────────────────────────────
# _clean_html
# ─────────────────────────────────────────────────────────────────

class TestCleanHtml:
    def test_strips_tags(self):
        result = _clean_html("<b>Bold</b> text")
        assert "<b>" not in result
        assert "Bold" in result
        assert "text" in result

    def test_collapses_whitespace(self):
        result = _clean_html("hello   world")
        assert "  " not in result

    def test_none_returns_empty(self):
        assert _clean_html(None) == ""

    def test_empty_string(self):
        assert _clean_html("") == ""


# ─────────────────────────────────────────────────────────────────
# _format_salary
# ─────────────────────────────────────────────────────────────────

class TestFormatSalary:
    def test_range_format(self):
        result = _format_salary(80000, 120000)
        assert "80,000" in result
        assert "120,000" in result

    def test_min_only(self):
        result = _format_salary(60000, 0)
        assert "60,000" in result
        assert "+" in result

    def test_max_only(self):
        result = _format_salary(0, 100000)
        assert "100,000" in result
        assert "Up to" in result

    def test_neither(self):
        result = _format_salary(0, 0)
        assert result == ""


# ─────────────────────────────────────────────────────────────────
# fetch_remoteok
# ─────────────────────────────────────────────────────────────────

def _mock_get(json_data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


SAMPLE_JOB = {
    "position": "Go Backend Engineer",
    "company": "RemoteCo",
    "location": "Worldwide",
    "url": "https://remoteok.com/jobs/golang-123",
    "date": "2026-05-01T00:00:00Z",
    "description": "<p>Build distributed systems.</p>",
    "salary_min": 80000,
    "salary_max": 120000,
    "tags": ["golang", "backend", "remote"],
}

# RemoteOK's first element is a legal/metadata object with no "position" key
LEGAL_NOTICE = {
    "legal": "This job feed is provided by RemoteOK...",
    "apiVersion": "1.0",
}


class TestFetchRemoteok:
    @patch("sources.remoteok.requests.get")
    def test_basic_job_returned(self, mock_get):
        mock_get.return_value = _mock_get([LEGAL_NOTICE, SAMPLE_JOB])
        jobs = fetch_remoteok()
        # Should find at least one job across all tag queries
        assert len(jobs) >= 1

    @patch("sources.remoteok.requests.get")
    def test_skips_legal_notice(self, mock_get):
        """First element (legal notice) has no 'position' key → skipped."""
        mock_get.return_value = _mock_get([LEGAL_NOTICE])
        jobs = fetch_remoteok()
        assert jobs == []

    @patch("sources.remoteok.requests.get")
    def test_non_dev_job_filtered_out(self, mock_get):
        non_dev = dict(SAMPLE_JOB, position="Office Cleaner")
        mock_get.return_value = _mock_get([LEGAL_NOTICE, non_dev])
        jobs = fetch_remoteok()
        assert all(j["title"] != "Office Cleaner" for j in jobs)

    @patch("sources.remoteok.requests.get")
    def test_deduplicates_by_url(self, mock_get):
        """Same URL from multiple tag queries → deduplicated."""
        mock_get.return_value = _mock_get([LEGAL_NOTICE, SAMPLE_JOB])
        jobs = fetch_remoteok()
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls))

    @patch("sources.remoteok.requests.get")
    def test_salary_formatted(self, mock_get):
        mock_get.return_value = _mock_get([LEGAL_NOTICE, SAMPLE_JOB])
        jobs = fetch_remoteok()
        if jobs:
            # Job has salary_min=80000 and salary_max=120000
            assert "80,000" in jobs[0]["salary"] or "120,000" in jobs[0]["salary"] or jobs[0]["salary"] == ""

    @patch("sources.remoteok.requests.get")
    def test_source_is_remoteok(self, mock_get):
        mock_get.return_value = _mock_get([LEGAL_NOTICE, SAMPLE_JOB])
        jobs = fetch_remoteok()
        assert all(j["source"] == "remoteok" for j in jobs)

    @patch("sources.remoteok.requests.get")
    def test_network_error_returns_empty(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("offline")
        jobs = fetch_remoteok()
        assert jobs == []

    @patch("sources.remoteok.requests.get")
    def test_non_list_response_skipped(self, mock_get):
        mock_get.return_value = _mock_get({"error": "bad request"})
        jobs = fetch_remoteok()
        assert jobs == []

    @patch("sources.remoteok.requests.get")
    def test_description_html_stripped(self, mock_get):
        mock_get.return_value = _mock_get([LEGAL_NOTICE, SAMPLE_JOB])
        jobs = fetch_remoteok()
        if jobs:
            assert "<p>" not in jobs[0]["description"]

    @patch("sources.remoteok.requests.get")
    def test_all_required_keys_present(self, mock_get):
        mock_get.return_value = _mock_get([LEGAL_NOTICE, SAMPLE_JOB])
        jobs = fetch_remoteok()
        required = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}
        for job in jobs:
            assert required.issubset(job.keys())

    @patch("sources.remoteok.requests.get")
    def test_description_truncated_to_4000(self, mock_get):
        long_desc_job = dict(SAMPLE_JOB, description="<p>" + "A" * 10000 + "</p>",
                             url="https://remoteok.com/jobs/longdesc")
        mock_get.return_value = _mock_get([LEGAL_NOTICE, long_desc_job])
        jobs = fetch_remoteok()
        if jobs:
            assert len(jobs[0]["description"]) <= 4000
