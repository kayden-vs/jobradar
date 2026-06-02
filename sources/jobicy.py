"""
sources/jobicy.py — Jobicy remote job API

Jobicy provides a free, public JSON API for remote job listings.
Docs: https://jobicy.com/api/v2/remote-jobs

Strategy:
  - Hit multiple tag-filtered endpoints to catch Go/TypeScript/backend roles.
  - No auth required, no rate-limit issues at a few calls per day.
  - Returns structured JSON with full job descriptions (HTML).
  - Good for catching remote-first companies with open India policies.

NOTE: Jobicy asks to be credited as source and recommends fetching
only a few times per day. Our daily pipeline run is well within that.
"""

import re
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

API_BASE = "https://jobicy.com/api/v2/remote-jobs"

# Each tuple: (tag, industry) — we query multiple combos to cover
# Go, TypeScript, and general backend/engineering roles.
SEARCH_QUERIES: list[dict] = [
    {"tag": "golang",     "industry": "engineering", "count": 50},
    {"tag": "typescript", "industry": "engineering", "count": 50},
    {"tag": "backend",    "industry": "engineering", "count": 50},
    # NOTE: tag="go" returns 400 Bad Request from Jobicy (too short/ambiguous).
    # "golang" already covers Go roles, so this is not needed.
]

# Timeout for each API call
_TIMEOUT = 15

# Browser-like UA — Jobicy's API doesn't strictly require it,
# but it avoids any edge-case bot filtering.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _clean_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html or "")
    return re.sub(r'\s+', ' ', text).strip()


def _fetch_endpoint(params: dict) -> list[dict]:
    """Fetch a single Jobicy API endpoint and return normalised job dicts."""
    jobs: list[dict] = []

    try:
        r = requests.get(
            API_BASE,
            params=params,
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        if not data.get("success", False) and not data.get("jobs"):
            logger.debug(f"Jobicy returned no jobs for params {params}")
            return jobs

        for item in data.get("jobs", []):
            # Build a clean description from jobDescription (full HTML) or excerpt
            raw_desc = item.get("jobDescription", "") or item.get("jobExcerpt", "")
            description = _clean_html(raw_desc)[:4000]

            # Salary: Jobicy doesn't always include salary in the API,
            # but jobLevel can hint at seniority
            job_level = item.get("jobLevel", "")
            job_type_list = item.get("jobType", [])
            job_type = ", ".join(job_type_list) if isinstance(job_type_list, list) else str(job_type_list)

            # Industry tags
            industry_list = item.get("jobIndustry", [])
            industry = ", ".join(industry_list) if isinstance(industry_list, list) else str(industry_list)

            jobs.append({
                "title":       item.get("jobTitle", ""),
                "company":     item.get("companyName", ""),
                "location":    item.get("jobGeo", "Remote"),
                "description": description,
                "url":         item.get("url", ""),
                "source":      "jobicy",
                "salary":      "",  # Jobicy API rarely includes salary figures
                "posted_at":   item.get("pubDate", ""),
                # Extra metadata for prefilter
                "job_level":   job_level,
                "job_type":    job_type,
                "industry":    industry,
            })

    except requests.exceptions.RequestException as e:
        logger.warning(f"Jobicy API request failed (params={params}): {e}")
    except (ValueError, KeyError) as e:
        logger.warning(f"Jobicy API response parsing failed: {e}")

    return jobs


def fetch_jobicy() -> list[dict]:
    """
    Main entry point: fetches remote jobs from Jobicy across multiple
    tag/industry combinations. Deduplicates by URL within the batch.
    """
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    for params in SEARCH_QUERIES:
        batch = _fetch_endpoint(params)
        for job in batch:
            url = job.get("url", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            all_jobs.append(job)

    logger.info(f"Jobicy: {len(all_jobs)} remote jobs found")
    return all_jobs
