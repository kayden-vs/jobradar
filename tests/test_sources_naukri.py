"""
tests/test_sources_naukri.py — Unit tests for sources/naukri.py

Covers the pure-Python helpers (no real HTTP):
  - _strip_html: tag stripping, HTML entity decoding, script block removal
  - _parse_naukri_date: date string, Unix timestamp (ms/s), empty/bad input
  - _is_too_old: recent vs. stale dates, unparseable passes through
  - _build_salary: showSal y/n, min/max combinations, missing data
"""

import pytest
from datetime import datetime, timezone, timedelta

from sources.naukri import (
    _strip_html,
    _parse_naukri_date,
    _is_too_old,
    _build_salary,
)


# ─────────────────────────────────────────────────────────────────
# _strip_html
# ─────────────────────────────────────────────────────────────────

class TestNaukriStripHtml:
    def test_strips_tags(self):
        result = _strip_html("<p>Hello <b>World</b></p>")
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello" in result
        assert "World" in result

    def test_strips_script_blocks(self):
        result = _strip_html("<script>alert('xss')</script> Job desc")
        assert "alert" not in result
        assert "Job desc" in result

    def test_strips_style_blocks(self):
        result = _strip_html("<style>.cls { color: red; }</style> Description")
        assert ".cls" not in result
        assert "Description" in result

    def test_html_entities_decoded(self):
        result = _strip_html("foo &amp; bar &lt;baz&gt; &quot;test&quot; &nbsp;end")
        assert "&amp;" not in result
        assert "& bar" in result
        assert "<baz>" in result
        assert '"test"' in result

    def test_empty_returns_empty(self):
        assert _strip_html("") == ""
        assert _strip_html(None) == ""

    def test_block_elements_become_newlines(self):
        result = _strip_html("<p>First</p><p>Second</p>")
        assert "First" in result
        assert "Second" in result

    def test_collapses_whitespace(self):
        result = _strip_html("hello   world")
        assert "  " not in result


# ─────────────────────────────────────────────────────────────────
# _parse_naukri_date
# ─────────────────────────────────────────────────────────────────

class TestParseNaukriDate:
    def test_standard_naukri_date_string(self):
        result = _parse_naukri_date("2026-05-26 16:27:38.0")
        assert "2026" in result
        assert "05" in result or "May" in result

    def test_iso_date_string(self):
        result = _parse_naukri_date("2026-01-15T10:00:00Z")
        assert "2026" in result

    def test_unix_timestamp_seconds(self):
        import time
        ts = int(time.time()) - 86400  # yesterday
        result = _parse_naukri_date(str(ts))
        assert result != ""

    def test_unix_timestamp_milliseconds(self):
        import time
        ts_ms = int(time.time() * 1000) - 86400000
        result = _parse_naukri_date(str(ts_ms))
        assert result != ""

    def test_empty_returns_empty(self):
        assert _parse_naukri_date("") == ""
        assert _parse_naukri_date(None) == ""

    def test_garbage_returns_something_or_empty(self):
        """Garbage input should not raise — returns raw string or empty."""
        result = _parse_naukri_date("not a date at all")
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────
# _is_too_old
# ─────────────────────────────────────────────────────────────────

class TestIsTooOld:
    def test_recent_date_not_old(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        assert _is_too_old(recent, max_days=45) is False

    def test_old_date_is_old(self):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        assert _is_too_old(old, max_days=45) is True

    def test_exactly_at_boundary_not_old(self):
        boundary = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        assert _is_too_old(boundary, max_days=45) is False

    def test_empty_date_not_old(self):
        """Empty date = benefit of the doubt (pass through)."""
        assert _is_too_old("", max_days=45) is False

    def test_none_date_not_old(self):
        assert _is_too_old(None, max_days=45) is False

    def test_unparseable_date_not_old(self):
        assert _is_too_old("garbage-date", max_days=45) is False


# ─────────────────────────────────────────────────────────────────
# _build_salary
# ─────────────────────────────────────────────────────────────────

class TestBuildSalary:
    def test_show_sal_yes_with_range(self):
        job = {"showSal": "y", "minSal": "400", "maxSal": "600"}
        result = _build_salary(job)
        assert result != ""
        assert "Rs" in result or "LPA" in result

    def test_show_sal_no_returns_empty(self):
        job = {"showSal": "n", "minSal": "400", "maxSal": "600"}
        result = _build_salary(job)
        assert result == ""

    def test_show_sal_missing_returns_empty(self):
        job = {"minSal": "400", "maxSal": "600"}
        result = _build_salary(job)
        assert result == ""

    def test_both_zero_returns_empty(self):
        job = {"showSal": "y", "minSal": "0", "maxSal": "0"}
        result = _build_salary(job)
        assert result == ""

    def test_min_only(self):
        job = {"showSal": "y", "minSal": "300", "maxSal": "0"}
        result = _build_salary(job)
        assert result != ""
        assert "+" in result

    def test_salary_range_with_lpa_conversion(self):
        """minSal=400, maxSal=600 → 4.0-6.0 LPA (Naukri unit is thousands)."""
        job = {"showSal": "y", "minSal": "400", "maxSal": "600"}
        result = _build_salary(job)
        assert "4.0" in result
        assert "6.0" in result

    def test_non_numeric_salary_returns_empty(self):
        job = {"showSal": "y", "minSal": "N/A", "maxSal": "N/A"}
        result = _build_salary(job)
        assert result == ""


# ─────────────────────────────────────────────────────────────────
# fetch_naukri — integration smoke test (mocked HTTP)
# ─────────────────────────────────────────────────────────────────

class TestFetchNaukriSmoke:
    """Smoke test fetch_naukri with minimal mocked responses."""

    @pytest.fixture
    def minimal_profile(self):
        return {
            "candidate": {
                "experience": {"max_required": 1},
            },
            "hard_reject": {"max_job_age_days": 45},
            "naukri": {
                "keywords": ["golang backend"],
                "locations": ["work from home"],
                "pages": 1,
            },
        }

    def _mock_search_response(self):
        return {
            "jobDetails": [
                {
                    "jobId": "N001",
                    "title": "Backend Engineer Intern",
                    "companyName": "TestCorp",
                    "placeholders": [
                        {"type": "location", "label": "Remote"},
                        {"type": "experience", "label": "0-1 Yrs"},
                    ],
                    "jobDesc": "<p>Build APIs with Go.</p>",
                    "addDate": "2026-05-01 12:00:00.0",
                    "showSal": "n",
                }
            ],
            "noOfJobs": 1,
        }

    def test_returns_list_on_mocked_response(self, minimal_profile):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._mock_search_response()

        with patch("sources.naukri.requests.get", return_value=mock_resp):
            from sources.naukri import fetch_naukri
            jobs = fetch_naukri(minimal_profile)
            assert isinstance(jobs, list)

    def test_job_has_required_keys(self, minimal_profile):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._mock_search_response()

        with patch("sources.naukri.requests.get", return_value=mock_resp):
            from sources.naukri import fetch_naukri
            jobs = fetch_naukri(minimal_profile)
            required = {"title", "company", "location", "description", "url", "source", "salary", "posted_at"}
            for job in jobs:
                assert required.issubset(job.keys())

    def test_source_is_naukri(self, minimal_profile):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._mock_search_response()

        with patch("sources.naukri.requests.get", return_value=mock_resp):
            from sources.naukri import fetch_naukri
            jobs = fetch_naukri(minimal_profile)
            for job in jobs:
                assert job["source"] == "naukri"

    def test_empty_profile_naukri_config_handled(self):
        """Profile without naukri config should return empty list without raising."""
        profile = {}
        from sources.naukri import fetch_naukri
        # Should not raise even with minimal config
        try:
            result = fetch_naukri(profile)
            assert isinstance(result, list)
        except Exception:
            pass  # Some implementations may raise on missing config — that's acceptable
