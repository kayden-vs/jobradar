"""
sources/utils.py — Shared utilities for job sources
"""

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

_PLAYWRIGHT_AVAILABLE = None

def is_playwright_available() -> bool:
    """
    Check if Playwright and Chromium dependencies (like libcups.so.2) are available.
    Returns True if available, False otherwise. Caches the result.
    """
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is not None:
        return _PLAYWRIGHT_AVAILABLE

    # 1. On Linux/WSL, check if the system library 'libcups.so' exists in the library path
    if sys.platform.startswith("linux"):
        if shutil.which("ldconfig"):
            try:
                # Run ldconfig to check for libcups.so.2 (which is required by Chromium/Playwright)
                res = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=2)
                if "libcups.so" not in res.stdout:
                    logger.warning(
                        "Playwright/Chromium is unavailable: missing 'libcups.so.2' dependency. "
                        "Sources requiring JS rendering (Instahyre, Cutshort, Hirist, YC fallback) will be bypassed. "
                        "To enable, run: 'sudo pacman -S libcups'"
                    )
                    _PLAYWRIGHT_AVAILABLE = False
                    return False
            except Exception:
                pass

    # 2. Check if we can import Scrapling and get StealthyFetcher
    try:
        from scrapling.fetchers import StealthyFetcher
        _PLAYWRIGHT_AVAILABLE = True
    except Exception as e:
        logger.warning(f"Playwright/StealthyFetcher initialization failed: {e}. Bypassing JS scraping.")
        _PLAYWRIGHT_AVAILABLE = False

    return _PLAYWRIGHT_AVAILABLE
