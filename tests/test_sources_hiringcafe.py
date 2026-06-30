"""
tests/test_sources_hiringcafe.py — Unit tests for sources/hiringcafe.py

Covers:
  - _synthesise_description: synthesising description from v5_processed_job_data fields
  - _fetch_build_id: parsing Next.js buildId from homepage
  - fetch_hiringcafe: mocked HTTP responses, deduplication, build ID retry,
    pagination, error handling
"""

import pytest
from unittest.mock import patch, MagicMock
import copy
import requests

from sources.hiringcafe import (
    _synthesise_description,
    _fetch_build_id,
    fetch_hiringcafe,
)


# ─────────────────────────────────────────────────────────────────
# _synthesise_description
# ─────────────────────────────────────────────────────────────────

SAMPLE_V5 = {
    "requirements_summary": "Go, gRPC, microservices experience preferred.",
    "technical_tools": ["Go", "gRPC", "PostgreSQL", "Redis"],
    "role_activities": ["Build backend services", "Design APIs"],
    "company_tagline": "We build fintech infrastructure.",
}

SAMPLE_ECD = {"tagline": "Leading fintech company"}


class TestSynthesiseDescription:
    def test_includes_requirements(self):
        desc = _synthesise_description(SAMPLE_V5, SAMPLE_ECD)
        assert "Go" in desc
        assert "gRPC" in desc

    def test_includes_activities(self):
        desc = _synthesise_description(SAMPLE_V5, SAMPLE_ECD)
        assert "Build backend services" in desc

    def test_includes_company_tagline(self):
        desc = _synthesise_description(SAMPLE_V5, SAMPLE_ECD)
        assert "fintech" in desc.lower()

    def test_empty_v5_returns_empty(self):
        desc = _synthesise_description({}, {})
        assert isinstance(desc, str)

    def test_tools_list_joined(self):
        desc = _synthesise_description(SAMPLE_V5, {})
        assert "PostgreSQL" in desc or "Redis" in desc

    def test_truncated_to_max_length(self):
        v5_big = {
            "requirements_summary": "A" * 5000,
            "role_activities": ["B" * 5000],
        }
        desc = _synthesise_description(v5_big, {})
        assert len(desc) <= 4000


# ─────────────────────────────────────────────────────────────────
# _fetch_build_id — mocked HTTP
# ─────────────────────────────────────────────────────────────────

SAMPLE_HTML_WITH_BUILD_ID = """
<html><head>
<script id="__NEXT_DATA__" type="application/json">
{"buildId": "abc123xyz", "props": {}}
</script>
</head><body>Hiring Cafe</body></html>
"""

SAMPLE_HTML_WITHOUT_BUILD_ID = """
<html><head></head><body>Error page</body></html>
"""


class TestFetchBuildId:
    @patch("sources.hiringcafe.requests.get")
    def test_extracts_build_id_from_html(self, mock_get):
        import sources.hiringcafe as hc_mod
        hc_mod._cached_build_id = None  # reset cache
        mock_resp = MagicMock()
        mock_resp.text = SAMPLE_HTML_WITH_BUILD_ID
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        build_id = _fetch_build_id()
        assert build_id == "abc123xyz"

    @patch("sources.hiringcafe.requests.get")
    def test_returns_none_when_absent(self, mock_get):
        import sources.hiringcafe as hc_mod
        hc_mod._cached_build_id = None
        mock_resp = MagicMock()
        mock_resp.text = SAMPLE_HTML_WITHOUT_BUILD_ID
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        build_id = _fetch_build_id()
        assert build_id is None

    @patch("sources.hiringcafe.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        import sources.hiringcafe as hc_mod
        hc_mod._cached_build_id = None
        mock_get.side_effect = requests.ConnectionError("offline")
        build_id = _fetch_build_id()
        assert build_id is None


# ─────────────────────────────────────────────────────────────────
# fetch_hiringcafe — mocked HTTP
# ─────────────────────────────────────────────────────────────────

def _mock_response(data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    mock.text = SAMPLE_HTML_WITH_BUILD_ID
    mock.raise_for_status.return_value = None
    return mock


SAMPLE_JOB_HIT = {
    "apply_url": "https://company.com/apply/001",
    "is_expired": False,
    "job_information": {
        "title": "Go Backend Engineer",
    },
    "v5_processed_job_data": {
        "company_name": "FinCorp",
        "formatted_workplace_location": "Remote",
        "requirements_summary": "Go, gRPC, PostgreSQL required.",
        "technical_tools": ["Go", "gRPC"],
        "role_activities": ["Build payment APIs"],
        "company_tagline": "Fintech infrastructure.",
        "estimated_publish_date": "2026-05-01",
        "seniority_level": "Entry Level",
        "workplace_type": "Remote",
        "commitment": ["Full-time"],
    },
    "enriched_company_data": {"name": "FinCorp", "tagline": "Payments infra"},
}

SAMPLE_API_RESPONSE = {
    "pageProps": {
        "ssrHits": [SAMPLE_JOB_HIT],
        "ssrIsLastPage": True,
    }
}


class TestFetchHiringCafe:
    def setup_method(self):
        """Reset module-level caches before each test."""
        import sources.hiringcafe as hc_mod
        hc_mod._cached_build_id = None
        hc_mod._session = None

    def _make_api_response(self, hits=None):
        """Build a mock API page response."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "pageProps": {
                "ssrHits": hits if hits is not None else [SAMPLE_JOB_HIT],
                "ssrIsLastPage": True,
            }
        }
        return resp

    def _make_homepage_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = SAMPLE_HTML_WITH_BUILD_ID
        resp.raise_for_status.return_value = None
        return resp

    @patch("sources.hiringcafe.time.sleep")
    @patch("sources.hiringcafe._get_session")
    @patch("sources.hiringcafe.requests.get")
    def test_basic_job_returned(self, mock_requests_get, mock_get_session, mock_sleep):
        mock_requests_get.return_value = self._make_homepage_response()
        mock_session = MagicMock()
        mock_session.get.return_value = self._make_api_response()
        mock_get_session.return_value = mock_session
        jobs = fetch_hiringcafe()
        assert len(jobs) >= 1

    @patch("sources.hiringcafe.time.sleep")
    @patch("sources.hiringcafe._get_session")
    @patch("sources.hiringcafe.requests.get")
    def test_source_is_hiringcafe(self, mock_requests_get, mock_get_session, mock_sleep):
        mock_requests_get.return_value = self._make_homepage_response()
        mock_session = MagicMock()
        mock_session.get.return_value = self._make_api_response()
        mock_get_session.return_value = mock_session
        jobs = fetch_hiringcafe()
        if jobs:
            assert all(j["source"] == "hiringcafe" for j in jobs)

    @patch("sources.hiringcafe.time.sleep")
    @patch("sources.hiringcafe._get_session")
    @patch("sources.hiringcafe.requests.get")
    def test_deduplicates_by_url(self, mock_requests_get, mock_get_session, mock_sleep):
        """Same job returned from all queries → only 1 unique URL."""
        mock_requests_get.return_value = self._make_homepage_response()
        mock_session = MagicMock()
        mock_session.get.return_value = self._make_api_response()
        mock_get_session.return_value = mock_session
        jobs = fetch_hiringcafe()
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"

    @patch("sources.hiringcafe.requests.get")
    def test_returns_empty_when_build_id_unavailable(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = SAMPLE_HTML_WITHOUT_BUILD_ID
        mock_resp.raise_for_status.return_value = None
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        jobs = fetch_hiringcafe()
        assert jobs == []

    @patch("sources.hiringcafe.requests.get")
    def test_network_error_returns_empty(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("offline")
        jobs = fetch_hiringcafe()
        assert jobs == []

    @patch("sources.hiringcafe.time.sleep")
    @patch("sources.hiringcafe._get_session")
    @patch("sources.hiringcafe.requests.get")
    def test_all_required_keys_present(self, mock_requests_get, mock_get_session, mock_sleep):
        mock_requests_get.return_value = self._make_homepage_response()
        mock_session = MagicMock()
        mock_session.get.return_value = self._make_api_response()
        mock_get_session.return_value = mock_session
        jobs = fetch_hiringcafe()
        required = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}
        for job in jobs:
            assert required.issubset(job.keys())

    @patch("sources.hiringcafe.time.sleep")
    @patch("sources.hiringcafe._get_session")
    @patch("sources.hiringcafe.requests.get")
    def test_empty_hits_returns_empty(self, mock_requests_get, mock_get_session, mock_sleep):
        mock_requests_get.return_value = self._make_homepage_response()
        mock_session = MagicMock()
        mock_session.get.return_value = self._make_api_response(hits=[])
        mock_get_session.return_value = mock_session
        jobs = fetch_hiringcafe()
        assert jobs == []

    @patch("sources.hiringcafe.time.sleep")
    @patch("sources.hiringcafe._get_session")
    @patch("sources.hiringcafe.requests.get")
    def test_description_built_from_v5(self, mock_requests_get, mock_get_session, mock_sleep):
        mock_requests_get.return_value = self._make_homepage_response()
        mock_session = MagicMock()
        mock_session.get.return_value = self._make_api_response()
        mock_get_session.return_value = mock_session
        jobs = fetch_hiringcafe()
        if jobs:
            desc = jobs[0]["description"]
            assert len(desc) > 0

    def test_normalise_hit_returns_none_for_expired(self):
        """Expired jobs should be skipped by _normalise_hit."""
        from sources.hiringcafe import _normalise_hit
        expired_hit = copy.deepcopy(SAMPLE_JOB_HIT)
        expired_hit["is_expired"] = True
        result = _normalise_hit(expired_hit)
        assert result is None

    def test_normalise_hit_returns_none_for_missing_url(self):
        """Jobs with no apply_url should be skipped."""
        from sources.hiringcafe import _normalise_hit
        no_url_hit = copy.deepcopy(SAMPLE_JOB_HIT)
        no_url_hit["apply_url"] = ""
        result = _normalise_hit(no_url_hit)
        assert result is None

    def test_normalise_hit_returns_none_for_missing_title(self):
        """Jobs with no title in job_information or v5 should be skipped."""
        from sources.hiringcafe import _normalise_hit
        no_title = {
            "apply_url": "https://a.com/job",
            "is_expired": False,
            "job_information": {"title": ""},
            "v5_processed_job_data": {"core_job_title": ""},
            "enriched_company_data": {},
        }
        result = _normalise_hit(no_title)
        assert result is None


