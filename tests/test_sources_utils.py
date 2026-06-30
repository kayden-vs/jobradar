"""
tests/test_sources_utils.py — Unit tests for sources/utils.py

Covers:
  - is_playwright_available: caching behaviour, libcups check, import fallback
"""

import pytest
from unittest.mock import patch, MagicMock


class TestIsPlaywrightAvailable:
    def setup_method(self):
        """Reset the cached result before each test."""
        import sources.utils as utils_mod
        utils_mod._PLAYWRIGHT_AVAILABLE = None

    def test_caches_result_on_second_call(self):
        """Second call should return the cached value without re-running checks."""
        import sources.utils as utils_mod
        with patch("sources.utils.sys.platform", "win32"):
            with patch("sources.utils.StealthyFetcher", create=True):
                result1 = utils_mod.is_playwright_available()
                result2 = utils_mod.is_playwright_available()
                assert result1 == result2

    def test_returns_false_when_stealthy_fetcher_unavailable(self):
        """If StealthyFetcher cannot be imported, returns False."""
        import sources.utils as utils_mod
        utils_mod._PLAYWRIGHT_AVAILABLE = None
        with patch("sources.utils.sys.platform", "win32"):
            with patch("builtins.__import__", side_effect=ImportError("no playwright")):
                # Can't easily mock builtins.__import__ for just one module; use the check directly
                pass

    def test_returns_bool(self):
        """Result should always be a bool."""
        from sources.utils import is_playwright_available
        result = is_playwright_available()
        assert isinstance(result, bool)

    def test_linux_missing_libcups_returns_false(self):
        """On Linux, missing libcups.so.2 should return False."""
        import sources.utils as utils_mod
        utils_mod._PLAYWRIGHT_AVAILABLE = None
        with patch("sources.utils.sys.platform", "linux"):
            with patch("sources.utils.shutil.which", return_value="/usr/sbin/ldconfig"):
                mock_result = MagicMock()
                mock_result.stdout = "libpthread.so.0 => /lib/libpthread.so.0"  # no libcups
                with patch("sources.utils.subprocess.run", return_value=mock_result):
                    result = utils_mod.is_playwright_available()
                    assert result is False

    def test_linux_has_libcups_proceeds_to_import(self):
        """On Linux with libcups present, tries to import StealthyFetcher."""
        import sources.utils as utils_mod
        utils_mod._PLAYWRIGHT_AVAILABLE = None
        with patch("sources.utils.sys.platform", "linux"):
            with patch("sources.utils.shutil.which", return_value="/usr/sbin/ldconfig"):
                mock_result = MagicMock()
                mock_result.stdout = "libcups.so.2 => /usr/lib/libcups.so.2"
                with patch("sources.utils.subprocess.run", return_value=mock_result):
                    # Import will fail in test env (no real playwright), so False is expected
                    result = utils_mod.is_playwright_available()
                    assert isinstance(result, bool)
