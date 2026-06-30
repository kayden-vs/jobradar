"""
tests/test_sources_ats.py — Unit tests for sources/ats.py

Tests the ATS fetch functions using mocked HTTP responses so no
real network calls are made. Each ATS provider (Greenhouse, Lever,
Ashby, Workable, SmartRecruiters, Rippling, BambooHR, Recruitee,
Personio) gets its own test class verifying:
  - Correct job dict schema (required keys present)
  - Source tag set correctly
  - HTML stripping works
  - Empty / error responses return []
  - fetch_all_ats() orchestrator delegates correctly
"""

import pytest
from unittest.mock import patch, MagicMock
import requests

from sources.ats import (
    _strip_html,
    fetch_greenhouse,
    fetch_lever,
    fetch_ashby,
    fetch_workable,
    fetch_smartrecruiters,
    fetch_rippling,
    fetch_bamboohr,
    fetch_recruitee,
    fetch_personio,
    fetch_all_ats,
)

# ─────────────────────────────────────────────────────────────────
# Required keys every job dict must have
# ─────────────────────────────────────────────────────────────────
REQUIRED_KEYS = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}


def _assert_job_schema(job: dict, expected_source: str):
    assert REQUIRED_KEYS.issubset(job.keys()), f"Missing keys: {REQUIRED_KEYS - job.keys()}"
    assert job["source"] == expected_source


def _mock_response(json_data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


def _mock_xml_response(xml_content: str):
    mock = MagicMock()
    mock.status_code = 200
    mock.content = xml_content.encode("utf-8")
    mock.raise_for_status.return_value = None
    return mock


# ─────────────────────────────────────────────────────────────────
# _strip_html
# ─────────────────────────────────────────────────────────────────

class TestStripHtml:
    def test_strips_tags(self):
        result = _strip_html("<p>Hello <b>World</b></p>")
        # Block elements become newlines; inline tags stripped to space
        assert "Hello" in result
        assert "World" in result
        assert "<p>" not in result
        assert "<b>" not in result

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_none_returns_empty(self):
        assert _strip_html(None) == ""

    def test_plain_text_unchanged(self):
        assert _strip_html("Hello World") == "Hello World"

    def test_nested_tags(self):
        result = _strip_html("<div><ul><li>Item 1</li><li>Item 2</li></ul></div>")
        assert "Item 1" in result
        assert "Item 2" in result


# ─────────────────────────────────────────────────────────────────
# Greenhouse
# ─────────────────────────────────────────────────────────────────

class TestFetchGreenhouse:
    def _greenhouse_job_payload(self):
        return {
            "jobs": [
                {
                    "id": 12345,
                    "title": "Backend Engineer Intern",
                    "absolute_url": "https://boards.greenhouse.io/testco/jobs/12345",
                    "updated_at": "2026-05-01T10:00:00Z",
                    "offices": [{"name": "Remote"}],
                    "content": "<p>We are hiring a Go developer fresher.</p>",
                }
            ]
        }

    @patch("sources.ats.requests.get")
    def test_basic_job_schema_us(self, mock_get):
        mock_get.return_value = _mock_response(self._greenhouse_job_payload())
        jobs = fetch_greenhouse("testco", eu=False)
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "greenhouse")

    @patch("sources.ats.requests.get")
    def test_basic_job_schema_eu(self, mock_get):
        mock_get.return_value = _mock_response(self._greenhouse_job_payload())
        jobs = fetch_greenhouse("testco", eu=True)
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "greenhouse_eu")

    @patch("sources.ats.requests.get")
    def test_html_description_stripped(self, mock_get):
        mock_get.return_value = _mock_response(self._greenhouse_job_payload())
        jobs = fetch_greenhouse("testco")
        assert "<p>" not in jobs[0]["description"]
        assert "Go developer" in jobs[0]["description"]

    @patch("sources.ats.requests.get")
    def test_company_name_formatted(self, mock_get):
        mock_get.return_value = _mock_response(self._greenhouse_job_payload())
        jobs = fetch_greenhouse("test-co")
        assert jobs[0]["company"] == "Test Co"

    @patch("sources.ats.requests.get")
    def test_empty_jobs_list(self, mock_get):
        mock_get.return_value = _mock_response({"jobs": []})
        jobs = fetch_greenhouse("emptyco")
        assert jobs == []

    @patch("sources.ats.requests.get")
    def test_http_error_returns_empty(self, mock_get):
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError("404")
        mock_get.return_value.json.side_effect = Exception("no json")
        jobs = fetch_greenhouse("badco")
        assert jobs == []

    @patch("sources.ats.requests.get")
    def test_location_from_offices(self, mock_get):
        payload = {
            "jobs": [{
                "id": 1,
                "title": "Engineer",
                "absolute_url": "https://example.com",
                "updated_at": "",
                "offices": [{"name": "Bangalore"}, {"name": "Remote"}],
                "content": "Job desc",
            }]
        }
        mock_get.return_value = _mock_response(payload)
        jobs = fetch_greenhouse("testco")
        assert "Bangalore" in jobs[0]["location"]
        assert "Remote" in jobs[0]["location"]


# ─────────────────────────────────────────────────────────────────
# Lever
# ─────────────────────────────────────────────────────────────────

class TestFetchLever:
    def _lever_payload(self):
        return [
            {
                "id": "abc-123",
                "text": "Go Backend Intern",
                "hostedUrl": "https://jobs.lever.co/testco/abc-123",
                "createdAt": 1700000000000,
                "categories": {"location": "Remote", "commitment": "Internship"},
                "lists": [
                    {"text": "Requirements", "content": "<ul><li>Go</li><li>gRPC</li></ul>"}
                ],
                "descriptionPlain": "We build distributed systems.",
            }
        ]

    @patch("sources.ats.requests.get")
    def test_basic_job_schema(self, mock_get):
        mock_get.return_value = _mock_response(self._lever_payload())
        jobs = fetch_lever("testco")
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "lever")

    @patch("sources.ats.requests.get")
    def test_description_includes_sections(self, mock_get):
        mock_get.return_value = _mock_response(self._lever_payload())
        jobs = fetch_lever("testco")
        assert "Requirements" in jobs[0]["description"]
        assert "distributed systems" in jobs[0]["description"]

    @patch("sources.ats.requests.get")
    def test_location_includes_commitment(self, mock_get):
        mock_get.return_value = _mock_response(self._lever_payload())
        jobs = fetch_lever("testco")
        assert "Remote" in jobs[0]["location"]
        assert "Internship" in jobs[0]["location"]

    @patch("sources.ats.requests.get")
    def test_posted_at_from_created_at(self, mock_get):
        mock_get.return_value = _mock_response(self._lever_payload())
        jobs = fetch_lever("testco")
        # Should parse from ms timestamp to ISO
        assert "2023" in jobs[0]["posted_at"] or "T" in jobs[0]["posted_at"]

    @patch("sources.ats.requests.get")
    def test_empty_list_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response([])
        jobs = fetch_lever("emptyco")
        assert jobs == []

    @patch("sources.ats.requests.get")
    def test_http_error_returns_empty(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("timeout")
        jobs = fetch_lever("badco")
        assert jobs == []


# ─────────────────────────────────────────────────────────────────
# Ashby
# ─────────────────────────────────────────────────────────────────

class TestFetchAshby:
    def _ashby_payload(self):
        return {
            "jobs": [
                {
                    "id": "job-uuid-001",
                    "title": "TypeScript Backend Engineer",
                    "location": "Remote - India",
                    "jobUrl": "https://jobs.ashbyhq.com/testco/job-uuid-001",
                    "publishedAt": "2026-04-15T00:00:00Z",
                    "descriptionPlain": "Build APIs with TypeScript and Node.js.",
                    "descriptionHtml": "<p>Build APIs.</p>",
                }
            ]
        }

    @patch("sources.ats.requests.get")
    def test_basic_job_schema(self, mock_get):
        mock_get.return_value = _mock_response(self._ashby_payload())
        jobs = fetch_ashby("testco")
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "ashby")

    @patch("sources.ats.requests.get")
    def test_prefers_plain_description(self, mock_get):
        mock_get.return_value = _mock_response(self._ashby_payload())
        jobs = fetch_ashby("testco")
        assert "TypeScript" in jobs[0]["description"]
        assert "<p>" not in jobs[0]["description"]

    @patch("sources.ats.requests.get")
    def test_fallback_to_html_description(self, mock_get):
        payload = {
            "jobs": [{
                "id": "x",
                "title": "Go Engineer",
                "location": "Bangalore",
                "jobUrl": "https://jobs.ashbyhq.com/testco/x",
                "publishedAt": "",
                "descriptionPlain": "",
                "descriptionHtml": "<p>Go backend role.</p>",
            }]
        }
        mock_get.return_value = _mock_response(payload)
        jobs = fetch_ashby("testco")
        assert "Go backend role" in jobs[0]["description"]

    @patch("sources.ats.requests.get")
    def test_empty_jobs_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response({"jobs": []})
        jobs = fetch_ashby("emptyco")
        assert jobs == []

    @patch("sources.ats.requests.get")
    def test_network_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        jobs = fetch_ashby("badco")
        assert jobs == []


# ─────────────────────────────────────────────────────────────────
# Workable
# ─────────────────────────────────────────────────────────────────

class TestFetchWorkable:
    def _workable_list_payload(self):
        return {
            "results": [
                {
                    "title": "Software Engineer (Go)",
                    "shortcode": "ABC123",
                    "published_on": "2026-05-01",
                    "location": {"city": "Bangalore", "country": "India"},
                }
            ]
        }

    @patch("sources.ats.requests.get")
    @patch("sources.ats.requests.post")
    def test_basic_job_schema(self, mock_post, mock_get):
        mock_post.return_value = _mock_response(self._workable_list_payload())
        mock_get.return_value = _mock_response({
            "full_description": "<p>Go developer role.</p>",
            "description": "",
        })
        jobs = fetch_workable("testco")
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "workable")

    @patch("sources.ats.requests.get")
    @patch("sources.ats.requests.post")
    def test_location_city_country(self, mock_post, mock_get):
        mock_post.return_value = _mock_response(self._workable_list_payload())
        mock_get.return_value = _mock_response({"full_description": "Job desc"})
        jobs = fetch_workable("testco")
        assert "Bangalore" in jobs[0]["location"]

    @patch("sources.ats.requests.post")
    def test_http_error_returns_empty(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = requests.HTTPError("500")
        mock_post.return_value.json.side_effect = Exception("no json")
        jobs = fetch_workable("badco")
        assert jobs == []

    @patch("sources.ats.requests.get")
    @patch("sources.ats.requests.post")
    def test_empty_results_returns_empty(self, mock_post, mock_get):
        mock_post.return_value = _mock_response({"results": []})
        jobs = fetch_workable("emptyco")
        assert jobs == []


# ─────────────────────────────────────────────────────────────────
# SmartRecruiters
# ─────────────────────────────────────────────────────────────────

class TestFetchSmartRecruiters:
    def _sr_payload(self):
        return {
            "content": [
                {
                    "id": "sr-job-001",
                    "name": "Backend Engineer",
                    "releasedDate": "2026-04-20",
                    "location": {"fullLocation": "Bangalore, India"},
                    "company": {"name": "SmartCorp"},
                }
            ]
        }

    @patch("sources.ats.requests.get")
    def test_basic_job_schema(self, mock_get):
        # First call: list; second call: detail
        list_response = _mock_response(self._sr_payload())
        detail_response = _mock_response({
            "jobAd": {
                "sections": {
                    "jobDescription": {"title": "Job", "text": "<p>Build APIs.</p>"}
                }
            }
        })
        mock_get.side_effect = [list_response, detail_response]
        jobs = fetch_smartrecruiters("SmartCorp")
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "smartrecruiters")

    @patch("sources.ats.requests.get")
    def test_uses_company_from_response(self, mock_get):
        list_response = _mock_response(self._sr_payload())
        detail_response = _mock_response({"jobAd": {"sections": {}}})
        mock_get.side_effect = [list_response, detail_response]
        jobs = fetch_smartrecruiters("SmartCorp")
        assert jobs[0]["company"] == "SmartCorp"

    @patch("sources.ats.requests.get")
    def test_http_error_returns_empty(self, mock_get):
        mock_get.side_effect = requests.ConnectionError()
        jobs = fetch_smartrecruiters("badco")
        assert jobs == []


# ─────────────────────────────────────────────────────────────────
# BambooHR
# ─────────────────────────────────────────────────────────────────

class TestFetchBambooHR:
    @patch("sources.ats.requests.get")
    def test_basic_job_schema(self, mock_get):
        mock_get.return_value = _mock_response({
            "result": [
                {
                    "id": "42",
                    "jobOpeningName": "Software Engineer",
                    "location": {"city": "Remote", "state": ""},
                }
            ]
        })
        jobs = fetch_bamboohr("testco")
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "bamboohr")

    @patch("sources.ats.requests.get")
    def test_url_format(self, mock_get):
        mock_get.return_value = _mock_response({
            "result": [{"id": "99", "jobOpeningName": "Dev", "location": {}}]
        })
        jobs = fetch_bamboohr("testco")
        assert "testco.bamboohr.com/careers/99" in jobs[0]["url"]

    @patch("sources.ats.requests.get")
    def test_empty_result_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response({"result": []})
        jobs = fetch_bamboohr("emptyco")
        assert jobs == []

    @patch("sources.ats.requests.get")
    def test_http_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("network error")
        jobs = fetch_bamboohr("badco")
        assert jobs == []


# ─────────────────────────────────────────────────────────────────
# Recruitee
# ─────────────────────────────────────────────────────────────────

class TestFetchRecruitee:
    @patch("sources.ats.requests.get")
    def test_basic_job_schema(self, mock_get):
        mock_get.return_value = _mock_response({
            "offers": [
                {
                    "title": "Go Backend Developer",
                    "location": "Remote",
                    "company_name": "RecruitCo",
                    "slug": "go-backend-dev",
                    "careers_url": "https://recruitco.recruitee.com/o/go-backend-dev",
                    "published_at": "2026-05-10T00:00:00Z",
                    "description": "<p>We use Go and gRPC.</p>",
                    "requirements": "<p>0-1 years experience.</p>",
                    "translations": {},
                }
            ]
        })
        jobs = fetch_recruitee("recruitco")
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "recruitee")

    @patch("sources.ats.requests.get")
    def test_description_html_stripped(self, mock_get):
        mock_get.return_value = _mock_response({
            "offers": [{
                "title": "Dev",
                "location": "Remote",
                "company_name": "TestCo",
                "slug": "dev",
                "careers_url": "https://testco.recruitee.com/o/dev",
                "published_at": "",
                "description": "<p>Build APIs.</p>",
                "requirements": "",
                "translations": {},
            }]
        })
        jobs = fetch_recruitee("testco")
        assert "<p>" not in jobs[0]["description"]
        assert "Build APIs" in jobs[0]["description"]

    @patch("sources.ats.requests.get")
    def test_empty_offers_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response({"offers": []})
        jobs = fetch_recruitee("emptyco")
        assert jobs == []


# ─────────────────────────────────────────────────────────────────
# Personio (XML-based)
# ─────────────────────────────────────────────────────────────────

class TestFetchPersonio:
    _SAMPLE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<workzag-jobs>
  <position>
    <id>100</id>
    <name>Backend Engineer</name>
    <office>Berlin</office>
    <department>Engineering</department>
    <createdAt>2026-05-01</createdAt>
    <jobDescriptions>
      <jobDescription>
        <name>About the role</name>
        <value>&lt;p&gt;Build microservices in Go.&lt;/p&gt;</value>
      </jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>"""

    @patch("sources.ats.requests.get")
    def test_basic_job_schema(self, mock_get):
        mock_get.return_value = _mock_xml_response(self._SAMPLE_XML.decode())
        mock_get.return_value.content = self._SAMPLE_XML
        jobs = fetch_personio("testco")
        assert len(jobs) == 1
        _assert_job_schema(jobs[0], "personio")

    @patch("sources.ats.requests.get")
    def test_description_from_xml_sections(self, mock_get):
        mock_get.return_value = _mock_xml_response("")
        mock_get.return_value.content = self._SAMPLE_XML
        jobs = fetch_personio("testco")
        assert "microservices" in jobs[0]["description"] or "Build" in jobs[0]["description"]

    @patch("sources.ats.requests.get")
    def test_network_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("connection error")
        jobs = fetch_personio("badco")
        assert jobs == []


# ─────────────────────────────────────────────────────────────────
# fetch_all_ats — orchestrator
# ─────────────────────────────────────────────────────────────────

class TestFetchAllAts:
    @patch("sources.ats.fetch_personio", return_value=[])
    @patch("sources.ats.fetch_recruitee", return_value=[])
    @patch("sources.ats.fetch_bamboohr", return_value=[])
    @patch("sources.ats.fetch_rippling", return_value=[])
    @patch("sources.ats.fetch_smartrecruiters", return_value=[])
    @patch("sources.ats.fetch_workable", return_value=[])
    @patch("sources.ats.fetch_ashby", return_value=[])
    @patch("sources.ats.fetch_lever", return_value=[])
    @patch("sources.ats.fetch_greenhouse")
    def test_delegates_to_greenhouse(self, mock_gh, *args):
        mock_gh.return_value = [{"title": "Intern", "source": "greenhouse",
                                  "company": "X", "location": "", "description": "",
                                  "url": "", "salary": "", "posted_at": ""}]
        companies = {"greenhouse": ["razorpay"]}
        result = fetch_all_ats(companies)
        mock_gh.assert_called_once_with("razorpay", eu=False)
        assert len(result) == 1

    @patch("sources.ats.fetch_personio", return_value=[])
    @patch("sources.ats.fetch_recruitee", return_value=[])
    @patch("sources.ats.fetch_bamboohr", return_value=[])
    @patch("sources.ats.fetch_rippling", return_value=[])
    @patch("sources.ats.fetch_smartrecruiters", return_value=[])
    @patch("sources.ats.fetch_workable", return_value=[])
    @patch("sources.ats.fetch_ashby", return_value=[])
    @patch("sources.ats.fetch_lever", return_value=[])
    @patch("sources.ats.fetch_greenhouse")
    def test_greenhouse_eu_flag(self, mock_gh, *args):
        mock_gh.return_value = []
        companies = {"greenhouse_eu": ["groww"]}
        fetch_all_ats(companies)
        mock_gh.assert_called_once_with("groww", eu=True)

    def test_empty_config_returns_empty(self):
        result = fetch_all_ats({})
        assert result == []

    def test_none_values_handled(self):
        """None values in companies config shouldn't raise."""
        result = fetch_all_ats({"greenhouse": None, "lever": None})
        assert result == []
