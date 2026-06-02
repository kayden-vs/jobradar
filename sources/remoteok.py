"""
sources/remoteok.py — RemoteOK job feed

RemoteOK provides a public JSON API at https://remoteok.com/api
The RSS endpoint (remoteok.io/remote-dev-jobs.rss) returns HTML instead
of XML when fetched server-side, so we use the JSON API instead.

Strategy:
  - Fetch the full JSON feed (returns ~200 recent jobs).
  - Filter client-side by tags for dev/engineering/golang/typescript roles.
  - No auth required. RemoteOK asks for attribution (link back).
  - Good for Go/TypeScript roles from global startups open to India timezone.

NOTE: RemoteOK rate-limits aggressive scrapers. A single fetch per daily
pipeline run is well within acceptable usage. We add a polite User-Agent
and respect their API terms.
"""

import re
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

API_BASE = "https://remoteok.com/api"

# Tag-filtered endpoints — RemoteOK supports ?tags=<tag> to pre-filter results.
# We query multiple tags to cover our target stack. Results are deduplicated by URL.
# This is far more reliable than fetching the unfiltered feed (~94 mixed jobs)
# and filtering client-side, since each tag feed returns 50-100 relevant jobs.
SEARCH_TAGS = [
    "golang",
    "typescript",
    "backend",
    "python",    # catches Go+Python shops common in fintech
    "rust",      # high-signal for systems/infra roles
    "javascript",
]

# Dev/engineering job title keywords — used as word-boundary regex patterns.
# Patterns are matched against the lowercase job title.
# Using re.search with \b ensures "java" doesn't match "javascript".
_DEV_TITLE_PATTERNS = re.compile(
    r'\b('
    # Explicit role names (always dev)
    r'engineer|developer|programmer|architect'
    r'|backend|frontend|front.end|full.?stack|fullstack'
    r'|software|devops|sre|sde|swe'
    # Language names in titles (word-boundary safe)
    r'|golang|typescript|javascript|python|rust|scala|elixir|kotlin|swift'
    r'|nodejs|node\.js|reactjs|react\.js|vue\.js'
    # Careful short names — only as whole words
    r'|(?<!\w)go(?!\w)|(?<!\w)java(?!\w)'
    # Infrastructure/platform roles
    r'|platform engineer|cloud engineer|infrastructure engineer'
    r'|data engineer|ml engineer|ai engineer|security engineer|qa engineer'
    # Web3/crypto — in titles these are always technical
    r'|blockchain|web3|defi|crypto engineer'
    # Senior engineering leadership
    r'|tech lead|engineering manager|head of engineering|vp engineering|cto'
    r')\b',
    re.IGNORECASE,
)

# Title blacklist — override the above for edge cases like "Software Sales"
_NON_DEV_TITLE_BLACKLIST = re.compile(
    r'\b('
    r'cleaner|barista|cook|chef|driver|nurse|teacher|recruiter|payroll'
    r'|accountant|marketing|customer success|customer support'
    r'|executive assistant|office manager|data entry|operations manager'
    r'|brand designer|graphic designer|sales director|sales manager'
    r')\b',
    re.IGNORECASE,
)


def _is_dev_job(title: str) -> bool:
    """
    Returns True if the job title indicates a software engineering role.

    Uses regex word-boundary matching so short keywords like 'go' and 'java'
    don't accidentally match 'good', 'javascript', 'driven', etc.
    Blacklist overrides whitelist for edge cases like 'Software Sales'.
    """
    if _NON_DEV_TITLE_BLACKLIST.search(title):
        return False
    return bool(_DEV_TITLE_PATTERNS.search(title))


_TIMEOUT = 20

# RemoteOK blocks default Python UA — must look like a browser.
# They also check for scraper patterns, so we use a standard Chrome UA.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _clean_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html or "")
    return re.sub(r'\s+', ' ', text).strip()





def _format_salary(salary_min: int, salary_max: int) -> str:
    """Format salary range into a human-readable string."""
    if salary_min and salary_max:
        return f"${salary_min:,} - ${salary_max:,}/year"
    if salary_min:
        return f"${salary_min:,}+/year"
    if salary_max:
        return f"Up to ${salary_max:,}/year"
    return ""


def fetch_remoteok() -> list[dict]:
    """
    Main entry point: fetches remote dev/engineering jobs from RemoteOK's
    tag-filtered JSON API endpoints. Queries multiple tech tags and deduplicates
    by URL. Uses title-based filtering as a final safety net.
    """
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    for tag in SEARCH_TAGS:
        try:
            r = requests.get(
                API_BASE,
                params={"tags": tag},
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()

            if not isinstance(data, list):
                logger.debug(f"RemoteOK tag={tag}: unexpected response format")
                continue

            tag_count = 0
            for item in data:
                # First element is the API legal notice / metadata — skip it
                if not isinstance(item, dict) or "position" not in item:
                    continue

                url = item.get("url", "")
                if url and url in seen_urls:
                    continue  # cross-tag duplicate

                title = item.get("position", "")

                # Title-based filter: only genuine dev/engineering roles
                if not _is_dev_job(title):
                    continue

                if url:
                    seen_urls.add(url)

                raw_desc = item.get("description", "")
                description = _clean_html(raw_desc)[:4000]

                salary_min = item.get("salary_min", 0) or 0
                salary_max = item.get("salary_max", 0) or 0
                tags = item.get("tags", [])

                all_jobs.append({
                    "title":       title,
                    "company":     item.get("company", ""),
                    "location":    item.get("location", "").strip(", ") or "Remote",
                    "description": description,
                    "url":         url,
                    "source":      "remoteok",
                    "salary":      _format_salary(salary_min, salary_max),
                    "posted_at":   item.get("date", ""),
                    "tech_stack":  ", ".join(tags),
                })
                tag_count += 1

            logger.debug(f"RemoteOK tag={tag}: {tag_count} jobs added")

        except requests.exceptions.RequestException as e:
            logger.warning(f"RemoteOK API request failed (tag={tag}): {e}")
        except (ValueError, KeyError) as e:
            logger.warning(f"RemoteOK response parsing failed (tag={tag}): {e}")

    logger.info(f"RemoteOK: {len(all_jobs)} dev/engineering jobs found")
    return all_jobs
