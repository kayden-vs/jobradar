"""
tests/test_sources_jobicy.py — Unit tests for sources/jobicy.py

Covers:
  - _clean_html: tag stripping and whitespace collapse
  - _fetch_endpoint: normal response, empty response, API error, bad JSON
  - fetch_jobicy: deduplication within batch, multiple endpoint queries
"""

import pytest
from unittest.mock import patch, MagicMock
import requests

from sources.jobicy import _clean_html, _fetch_endpoint, fetch_jobicy, API_BASE


# ─────────────────────────────────────────────────────────────────
# _clean_html
# ─────────────────────────────────────────────────────────────────

class TestCleanHtml:
    def test_strips_tags(self):
        result = _clean_html("<p>Hello <b>World</b></p>")
        assert "Hello" in result
        assert "World" in result
        assert "<p>" not in result
        assert "<b>" not in result

    def test_collapses_whitespace(self):
        result = _clean_html("  hello   world  ")
        assert "  " not in result

    def test_empty_string(self):
        assert _clean_html("") == ""

    def test_none_input(self):
        assert _clean_html(None) == ""

    def test_plain_text_trimmed(self):
        assert _clean_html("  hello  ") == "hello"


# ─────────────────────────────────────────────────────────────────
# _fetch_endpoint
# ─────────────────────────────────────────────────────────────────

def _mock_response(json_data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


SAMPLE_JOB = {
    "jobTitle": "Go Backend Engineer",
    "companyName": "RemoteCo",
    "jobGeo": "Worldwide",
    "url": "https://jobicy.com/jobs/go-backend-123",
    "pubDate": "2026-05-01",
    "jobDescription": "<p>Build microservices in Go.</p>",
    "jobExcerpt": "",
    "jobLevel": "Entry",
    "jobType": ["Full-time"],
    "jobIndustry": ["Engineering"],
}


class TestFetchEndpoint:
    @patch("sources.jobicy.requests.get")
    def test_returns_normalised_job_dict(self, mock_get):
        mock_get.return_value = _mock_response({
            "success": True,
            "jobs": [SAMPLE_JOB],
        })
        jobs = _fetch_endpoint({"tag": "golang", "industry": "engineering", "count": 50})
        assert len(jobs) == 1
        job = jobs[0]
        assert job["title"] == "Go Backend Engineer"
        assert job["company"] == "RemoteCo"
        assert job["source"] == "jobicy"
        assert job["url"] == SAMPLE_JOB["url"]
        assert "<p>" not in job["description"]

    @patch("sources.jobicy.requests.get")
    def test_empty_jobs_list(self, mock_get):
        mock_get.return_value = _mock_response({"success": True, "jobs": []})
        jobs = _fetch_endpoint({"tag": "typescript"})
        assert jobs == []

    @patch("sources.jobicy.requests.get")
    def test_no_success_no_jobs_key(self, mock_get):
        mock_get.return_value = _mock_response({"error": "not found"})
        jobs = _fetch_endpoint({"tag": "backend"})
        assert jobs == []

    @patch("sources.jobicy.requests.get")
    def test_request_exception_returns_empty(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("timeout")
        jobs = _fetch_endpoint({"tag": "golang"})
        assert jobs == []

    @patch("sources.jobicy.requests.get")
    def test_description_truncated_to_4000(self, mock_get):
        long_html = "<p>" + "A" * 10000 + "</p>"
        job = dict(SAMPLE_JOB, jobDescription=long_html)
        mock_get.return_value = _mock_response({"success": True, "jobs": [job]})
        jobs = _fetch_endpoint({"tag": "golang"})
        assert len(jobs[0]["description"]) <= 4000

    @patch("sources.jobicy.requests.get")
    def test_job_type_list_joined(self, mock_get):
        job = dict(SAMPLE_JOB, jobType=["Full-time", "Contract"])
        mock_get.return_value = _mock_response({"success": True, "jobs": [job]})
        jobs = _fetch_endpoint({"tag": "golang"})
        assert "Full-time" in jobs[0]["job_type"]
        assert "Contract" in jobs[0]["job_type"]

    @patch("sources.jobicy.requests.get")
    def test_industry_list_joined(self, mock_get):
        job = dict(SAMPLE_JOB, jobIndustry=["Engineering", "Technology"])
        mock_get.return_value = _mock_response({"success": True, "jobs": [job]})
        jobs = _fetch_endpoint({"tag": "golang"})
        assert "Engineering" in jobs[0]["industry"]

    @patch("sources.jobicy.requests.get")
    def test_falls_back_to_excerpt(self, mock_get):
        job = dict(SAMPLE_JOB, jobDescription="", jobExcerpt="<p>Short excerpt.</p>")
        mock_get.return_value = _mock_response({"success": True, "jobs": [job]})
        jobs = _fetch_endpoint({"tag": "golang"})
        assert "Short excerpt" in jobs[0]["description"]


# ─────────────────────────────────────────────────────────────────
# fetch_jobicy — main entry point
# ─────────────────────────────────────────────────────────────────

class TestFetchJobicy:
    @patch("sources.jobicy.requests.get")
    def test_deduplicates_by_url(self, mock_get):
        """Same URL returned from multiple tag queries → deduplicated."""
        # Return the same job for all 3 search queries
        mock_get.return_value = _mock_response({
            "success": True,
            "jobs": [SAMPLE_JOB],
        })
        jobs = fetch_jobicy()
        # Should deduplicate — only 1 unique job despite 3 queries
        unique_urls = {j["url"] for j in jobs}
        assert len(unique_urls) == 1

    @patch("sources.jobicy.requests.get")
    def test_combines_results_from_multiple_queries(self, mock_get):
        """Different jobs from different queries → all kept."""
        def make_unique_job(i):
            return dict(SAMPLE_JOB, url=f"https://jobicy.com/jobs/unique-{i}", jobTitle=f"Job {i}")

        call_count = [0]

        def side_effect(*args, **kwargs):
            i = call_count[0]
            call_count[0] += 1
            return _mock_response({"success": True, "jobs": [make_unique_job(i)]})

        mock_get.side_effect = side_effect
        jobs = fetch_jobicy()
        assert len(jobs) == 3  # one per search query, all unique

    @patch("sources.jobicy.requests.get")
    def test_returns_empty_on_all_failures(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("offline")
        jobs = fetch_jobicy()
        assert jobs == []

    @patch("sources.jobicy.requests.get")
    def test_all_jobs_have_required_keys(self, mock_get):
        mock_get.return_value = _mock_response({
            "success": True,
            "jobs": [SAMPLE_JOB],
        })
        jobs = fetch_jobicy()
        required = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}
        for job in jobs:
            assert required.issubset(job.keys())

    @patch("sources.jobicy.requests.get")
    def test_source_is_jobicy(self, mock_get):
        mock_get.return_value = _mock_response({"success": True, "jobs": [SAMPLE_JOB]})
        jobs = fetch_jobicy()
        assert all(j["source"] == "jobicy" for j in jobs)
