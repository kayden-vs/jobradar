#!/usr/bin/env python3
"""
test_hirist.py — Smoke test for the Hirist.tech job source.

Run from the project root:
    ./venv/bin/python test_hirist.py

Prerequisites (Arch Linux / WSL):
    sudo pacman -S nspr nss   # needed by Playwright's Chromium binary
    ./venv/bin/python -m patchright install chromium

Four checks:
  1. RENDER CHECK  — fetches python-jobs page 1 and asserts at least one job
                     title is present (proves JS rendered, not shell HTML).
  2. EXP FILTER    — runs _exp_overlaps / _parse_exp_range against hardcoded
                     mock jobs and asserts correct include/exclude decisions.
  3. LIVE E2E RUN  — fetches 1 page for 'golang' keyword, applies broad exp
                     filter (0-20 yrs), and prints the resulting job list.
  4. URL RESOLVE   — verifies at least one extracted job URL returns HTTP 200
                     by fetching it with StealthyFetcher.

Each check prints PASS / FAIL. Exit code = number of failures (0 = all pass).
Checks 1/3/4 require Playwright's Chromium and system libraries (nspr/nss).
Check 2 is a pure Python unit test and always runs regardless.
"""

import sys
import time
import traceback

# ── Ensure project is importable ─────────────────────────────────────────────
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from sources.hirist import (
    _parse_exp_range,
    _exp_overlaps,
    _fetch_listing_page,
    fetch_hirist,
)
from scrapling.fetchers import StealthyFetcher

# ── Pre-flight: check if Chromium is actually usable ─────────────────────────
def _chromium_available() -> bool:
    """
    Real test: run `ldd` on the actual Playwright Chromium binary and check
    for missing .so files. This catches ALL missing libraries, not just nspr.
    """
    import subprocess, glob
    # Find the Chromium binary installed by patchright/playwright
    patterns = [
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
    ]
    binary = None
    for pat in patterns:
        matches = glob.glob(pat)
        if matches:
            binary = matches[0]
            break

    if not binary or not os.path.isfile(binary):
        print("  [pre-flight] Playwright Chromium binary not found. Run:")
        print("    ./venv/bin/python -m patchright install chromium")
        return False

    try:
        result = subprocess.run(
            ["ldd", binary],
            capture_output=True, text=True, timeout=10,
        )
        missing = [line.strip() for line in result.stdout.splitlines()
                   if "not found" in line]
        if missing:
            print(f"  [pre-flight] Chromium binary has {len(missing)} missing .so file(s):")
            for m in missing[:5]:
                print(f"    {m}")
            print("  Fix on Arch Linux: sudo pacman -S nspr nss atk at-spi2-core")
            print("  Or use Ubuntu/Debian EC2 where playwright install-deps works.")
            return False
    except FileNotFoundError:
        # ldd not available — assume OK and let it fail at runtime
        pass
    except Exception as e:
        print(f"  [pre-flight] ldd check failed: {e} — assuming Chromium available")

    return True

_HAS_CHROMIUM = _chromium_available()
if not _HAS_CHROMIUM:
    print(
        "\n[WARN] Playwright Chromium is not fully functional in this environment.\n"
        "  Checks 1, 3, 4 will be SKIPPED (not counted as failures).\n"
        "  Check 2 (exp filter logic) will run normally.\n"
    )
import requests  # only used for URL-resolve check fallback


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"

failures = 0

def report(name: str, ok: bool, detail: str = ""):
    global failures
    status = _PASS if ok else _FAIL
    line = f"[{status}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line)
    if not ok:
        failures += 1


# ─────────────────────────────────────────────────────────────────
# CHECK 1 — Rendered DOM (StealthyFetcher returns actual job cards)
# ─────────────────────────────────────────────────────────────────

print("\n=== CHECK 1: JS Render (python-jobs page 1) ===")
cards = []
if not _HAS_CHROMIUM:
    print("  [SKIP] Chromium not available — install nspr/nss then rerun")
else:
    try:
        cards = _fetch_listing_page("python", 1, 0, 5)
        titles = [c.get("title", "") for c in cards if c.get("title")]
        ok = len(titles) >= 1
        detail = f"{len(titles)} job titles found" if ok else "0 job titles -- page likely unrendered"
        if titles:
            print(f"  Sample titles: {titles[:3]}")
        report("Rendered DOM returns job cards", ok, detail)
    except Exception as e:
        traceback.print_exc()
        report("Rendered DOM returns job cards", False, str(e))


# ─────────────────────────────────────────────────────────────────
# CHECK 2 — Experience filter logic (unit test against mock data)
# ─────────────────────────────────────────────────────────────────

print("\n=== CHECK 2: Experience Filter Logic ===")

_FILTER_CASES = [
    # (exp_text,    target_min, target_max, expected_include, label)
    ("0-1 Yrs",     0,  2,  True,   "0-1 yrs inside 0-2 range"),
    ("1-3 Yrs",     0,  2,  True,   "1-3 yrs overlaps 0-2 range"),
    ("3-5 Yrs",     0,  2,  False,  "3-5 yrs outside 0-2 range"),
    ("5+ Yrs",      0,  2,  False,  "5+ yrs above max=2"),
    ("0-2 years",   0,  3,  True,   "0-2 yrs inside 0-3 range"),
    ("2-4 years",   0,  3,  True,   "2-4 yrs overlaps 0-3 range"),
    ("4-6 years",   0,  3,  False,  "4-6 yrs above max=3"),
    ("",            0,  2,  True,   "no exp text → pass through"),
    ("2 Yrs",       0,  3,  True,   "exact 2 yrs inside 0-3"),
    ("7+ yrs",      0,  3,  False,  "7+ yrs above max=3"),
    ("1-20 Yrs",    0, 20,  True,   "broad range 1-20 inside 0-20"),
]

all_filter_ok = True
for exp_text, t_min, t_max, expected, label in _FILTER_CASES:
    parsed_min, parsed_max = _parse_exp_range(exp_text)
    result = _exp_overlaps(parsed_min, parsed_max, t_min, t_max)
    ok = result == expected
    if not ok:
        all_filter_ok = False
    flag = "ok" if ok else f"MISMATCH: got {result}, expected {expected}"
    print(f"  {'OK' if ok else 'XX'}  {label!r:45s}  parse=({parsed_min},{parsed_max})  {flag}")

report("Experience filter logic", all_filter_ok, "all cases correct" if all_filter_ok else "some cases failed — see above")


# ─────────────────────────────────────────────────────────────────
# CHECK 3 — Live E2E run (1 keyword, 1 page, broad exp filter)
# ─────────────────────────────────────────────────────────────────

print("\n=== CHECK 3: Live E2E Run (golang, page 1, exp 0-20) ===")
if not _HAS_CHROMIUM:
    print("  [SKIP] Chromium not available")
else:
    try:
        profile = {
            "hirist": {
                "keywords": ["golang"],
                "min_exp": 0,
                "max_exp": 20,   # deliberately broad -- accept almost everything
                "pages": 1,
                "fetch_details": False,   # skip detail fetch to keep smoke test fast
            }
        }
        jobs = fetch_hirist(profile)
        ok = len(jobs) >= 1
        detail = f"{len(jobs)} jobs returned"
        if jobs:
            print("  Sample jobs:")
            for j in jobs[:3]:
                print(f"    - [{j.get('source')}] {j.get('title')} @ {j.get('company')} | {j.get('location')} | exp: {j.get('min_exp')}-{j.get('max_exp')}")
        report("Live E2E returns >= 1 job", ok, detail)
    except Exception as e:
        traceback.print_exc()
        report("Live E2E returns >= 1 job", False, str(e))


# ─────────────────────────────────────────────────────────────────
# CHECK 4 — URL resolves (HTTP 200) for at least one job URL
# ─────────────────────────────────────────────────────────────────

print("\n=== CHECK 4: Job URL Resolves (HTTP 200) ===")
if not _HAS_CHROMIUM:
    print("  [SKIP] Chromium not available")
else:
    try:
        # Use the cards fetched in Check 1 (if any) or fetch fresh
        if not cards:
            cards = _fetch_listing_page("python", 1, 0, 5)

        candidate_urls = [
            c.get("url", "") for c in cards
            if c.get("url") and "/j/" in c.get("url", "")
        ]

        if not candidate_urls:
            report("Job URL resolves HTTP 200", False, "no /j/ URLs found in listing page")
        else:
            resolved = False
            tested_url = ""
            for url in candidate_urls[:3]:   # try up to 3 to account for transient failures
                tested_url = url
                print(f"  Testing URL: {url}")
                try:
                    resp = StealthyFetcher.fetch(
                        url,
                        headless=True,
                        network_idle=True,
                        timeout=30_000,
                        disable_resources=True,
                    )
                    if resp is not None and resp.status == 200:
                        resolved = True
                        print(f"  -> HTTP {resp.status} OK")
                        break
                    else:
                        status = resp.status if resp else "None"
                        print(f"  -> HTTP {status} (skipping)")
                except Exception as url_exc:
                    print(f"  -> Exception: {url_exc}")
                time.sleep(1.5)

            report("Job URL resolves HTTP 200", resolved,
                   f"{tested_url}" if resolved else "no URL returned 200")
    except Exception as e:
        traceback.print_exc()
        report("Job URL resolves HTTP 200", False, str(e))


# ─────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────

skipped = 3 if not _HAS_CHROMIUM else 0
ran = 4 - skipped
print(f"\n{'='*50}")
print(f"Results: {ran - failures}/{ran} checks passed", end="")
if skipped:
    print(f"  ({skipped} skipped — Chromium not available)", end="")
print()
if failures == 0:
    print("All ran checks PASSED.")
else:
    print(f"{failures} check(s) FAILED.")
if skipped:
    print("\nTo run all 4 checks, install missing dependencies:")
    print("  sudo pacman -S nspr nss   # on Arch Linux")
print('='*50)
sys.exit(failures)
