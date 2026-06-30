"""
tests/test_sources_hirist.py — Unit tests for sources/hirist.py

Covers the pure-Python helpers (no Playwright/network):
  - _parse_exp_range: range strings, "+" strings, bare numbers, empty/unknown
  - _exp_overlaps: overlap logic with None bounds (benefit-of-the-doubt)
  - _strip_html: tag removal, entity decoding, script stripping
  - is_playwright_available path: when False → returns [] early
"""

import pytest

from sources.hirist import (
    _parse_exp_range,
    _exp_overlaps,
    _strip_html,
)


# ─────────────────────────────────────────────────────────────────
# _parse_exp_range
# ─────────────────────────────────────────────────────────────────

class TestParseExpRange:
    def test_range_string(self):
        assert _parse_exp_range("3-5 Yrs") == (3, 5)

    def test_range_string_years(self):
        assert _parse_exp_range("2-4 years") == (2, 4)

    def test_zero_to_one(self):
        assert _parse_exp_range("0-1 yr") == (0, 1)

    def test_plus_string(self):
        min_exp, max_exp = _parse_exp_range("5+ yrs")
        assert min_exp == 5
        assert max_exp is None  # open-ended

    def test_plus_with_space(self):
        min_exp, max_exp = _parse_exp_range("5 + Yrs")
        assert min_exp == 5
        assert max_exp is None

    def test_bare_number(self):
        """'3 years' → (3, 3)"""
        min_exp, max_exp = _parse_exp_range("3 years experience required")
        assert min_exp == 3
        assert max_exp == 3

    def test_empty_string_returns_none_none(self):
        assert _parse_exp_range("") == (None, None)

    def test_none_returns_none_none(self):
        assert _parse_exp_range(None) == (None, None)

    def test_10_plus_yrs(self):
        min_exp, max_exp = _parse_exp_range("10+Yrs")
        assert min_exp == 10
        assert max_exp is None

    def test_hyphen_unicode(self):
        """Hirist sometimes uses Unicode en-dash (–) in ranges."""
        result = _parse_exp_range("3\u20135 Yrs")
        assert result[0] == 3
        assert result[1] == 5


# ─────────────────────────────────────────────────────────────────
# _exp_overlaps
# ─────────────────────────────────────────────────────────────────

class TestExpOverlaps:
    def test_direct_overlap(self):
        """Job 0-2 overlaps target 0-1 → True."""
        assert _exp_overlaps(0, 2, target_min=0, target_max=1) is True

    def test_no_overlap(self):
        """Job 3-5 does NOT overlap target 0-1 → False."""
        assert _exp_overlaps(3, 5, target_min=0, target_max=1) is False

    def test_open_ended_max(self):
        """Job 5+ (max=None) vs target 0-1 → no overlap since min=5 > target_max=1."""
        assert _exp_overlaps(5, None, target_min=0, target_max=1) is False

    def test_fresher_job_zero_one(self):
        assert _exp_overlaps(0, 1, target_min=0, target_max=1) is True

    def test_both_none_passes_through(self):
        """No experience info → benefit of the doubt → True."""
        assert _exp_overlaps(None, None, target_min=0, target_max=1) is True

    def test_min_none_max_known(self):
        """min=None, max=2 → passes (unknown lower bound)."""
        assert _exp_overlaps(None, 2, target_min=0, target_max=1) is True

    def test_exact_boundary_match(self):
        """Job exactly at target boundary → overlap."""
        assert _exp_overlaps(1, 1, target_min=0, target_max=1) is True

    def test_high_seniority_no_overlap(self):
        assert _exp_overlaps(7, 10, target_min=0, target_max=2) is False


# ─────────────────────────────────────────────────────────────────
# _strip_html
# ─────────────────────────────────────────────────────────────────

class TestHiristStripHtml:
    def test_strips_tags(self):
        result = _strip_html("<p>Backend <b>Engineer</b></p>")
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Backend" in result
        assert "Engineer" in result

    def test_strips_script_block(self):
        result = _strip_html("<script>var x=1;</script> Job description")
        assert "var x" not in result
        assert "Job description" in result

    def test_strips_style_block(self):
        result = _strip_html("<style>.a{color:red}</style> Some text")
        assert ".a{" not in result
        assert "Some text" in result

    def test_html_entities(self):
        result = _strip_html("Pay &amp; benefits &lt;great&gt;")
        assert "&amp;" not in result
        assert "& benefits" in result

    def test_empty_returns_empty(self):
        assert _strip_html("") == ""
        assert _strip_html(None) == ""

    def test_nbsp_decoded(self):
        result = _strip_html("Hello&nbsp;World")
        assert "&nbsp;" not in result
        assert "Hello" in result

    def test_block_elements_to_newlines(self):
        result = _strip_html("<p>First</p><p>Second</p>")
        # Both sections should be present
        assert "First" in result
        assert "Second" in result


# ─────────────────────────────────────────────────────────────────
# fetch_hirist — Playwright unavailable path
# ─────────────────────────────────────────────────────────────────

class TestFetchHiristPlaywrightUnavailable:
    def test_returns_empty_when_playwright_unavailable(self):
        from unittest.mock import patch
        with patch("sources.hirist.is_playwright_available", return_value=False):
            from sources.hirist import fetch_hirist
            minimal_profile = {
                "hirist": {
                    "keywords": ["golang"],
                    "min_exp": 0,
                    "max_exp": 2,
                    "pages": 1,
                    "fetch_details": False,
                }
            }
            # Re-import to pick up the patch
            import importlib
            import sources.hirist as hirist_mod
            importlib.reload(hirist_mod)
            # Patch at module level
            with patch.object(hirist_mod, "is_playwright_available", return_value=False):
                result = hirist_mod.fetch_hirist(minimal_profile)
                assert result == []
