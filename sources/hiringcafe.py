"""
sources/hiringcafe.py — hiring.cafe job search (internal Next.js API)

hiring.cafe aggregates jobs from multiple ATS boards (Workday, Greenhouse,
Lever, etc.) and exposes them via a rich internal search API. No official
public API exists, but the Next.js data endpoint accepts JSON search filters
and returns structured job data.

Strategy:
  - Fetch the dynamic Next.js buildId from the homepage on each run.
  - Run multiple filtered search queries covering the candidate's stack
    (Go/Golang, TypeScript, Backend, Software Engineer).
  - Server-side filters: seniority (entry-level/fresher), departments
    (Software Dev, Engineering, IT), locations (India cities + remote).
  - Synthesise a description from the v5_processed_job_data fields
    (requirements_summary, technical_tools, role_activities, company_tagline).
  - Deduplicate within the batch by apply_url.
  - Polite delays between paginated requests to avoid rate-limiting.

Anti-blocking measures:
  - Browser-like headers (User-Agent, Referer, Accept, x-nextjs-data).
  - 1.5s delay between paginated requests.
  - Max 3 pages per query (ceiling of ~120 jobs/query).
  - Graceful build ID re-fetch on 404 (stale deployment ID).
  - Abort on 4xx/5xx errors (don't hammer a rate-limited endpoint).

NOTE: The API does not return full HTML job descriptions. The structured
v5_processed_job_data fields provide high-signal summaries that are
typically better for pipeline scoring than raw HTML.
"""

import re
import json
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────

_HOMEPAGE = "https://hiring.cafe/"
_API_TEMPLATE = "https://hiring.cafe/_next/data/{build_id}/index.json"

_TIMEOUT = 20          # seconds per request
_PAGE_DELAY = 1.5      # seconds between paginated requests
_MAX_PAGES = 3         # max pages per search query (40 jobs/page)
_MAX_BUILD_ID_RETRIES = 1  # re-fetch build ID at most once on 404

# Browser-like headers — hiring.cafe checks for these
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://hiring.cafe/",
    "x-nextjs-data": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Connection": "keep-alive",
}


# ─────────────────────────────────────────────────────────────────
# SEARCH QUERIES — each covers a different keyword slice
# ─────────────────────────────────────────────────────────────────

# Shared filters applied to every query (matching the user's profile)
_BASE_FILTERS = {
    "departments": ["Software Development", "Engineering", "Information Technology"],
    "physicalLaborIntensity": ["Low"],
    "computerUsageLevels": ["High"],
    "seniorityLevel": ["No Prior Experience Required", "Entry Level"],
    "locations": [
        # India (country-level, remote)
        {
            "types": ["country"],
            "formatted_address": "India",
            "address_components": [
                {"long_name": "India", "short_name": "IN", "types": ["country"]}
            ],
            "workplace_types": ["Remote"],
            "options": {},
            "id": "Indiacountry",
        },
        # Bengaluru
        {
            "id": "sxg1yZQBoEtHp_8UbmmX",
            "types": ["locality"],
            "address_components": [
                {"long_name": "Bengaluru", "short_name": "Bengaluru", "types": ["locality"]},
                {"long_name": "Karnataka", "short_name": "19", "types": ["administrative_area_level_1"]},
                {"long_name": "India", "short_name": "IN", "types": ["country"]},
            ],
            "geometry": {"location": {"lat": 12.97194, "lon": 77.59369}},
            "formatted_address": "Bengaluru, Karnataka, IN",
            "population": 8443675,
            "workplace_types": [],
            "options": {"radius": 50, "radius_unit": "miles", "ignore_radius": False},
        },
        # Kolkata
        {
            "id": "hRg1yZQBoEtHp_8UbmiX",
            "types": ["locality"],
            "address_components": [
                {"long_name": "Kolkata", "short_name": "Kolkata", "types": ["locality"]},
                {"long_name": "West Bengal", "short_name": "28", "types": ["administrative_area_level_1"]},
                {"long_name": "India", "short_name": "IN", "types": ["country"]},
            ],
            "geometry": {"location": {"lat": 22.56263, "lon": 88.36304}},
            "formatted_address": "Kolkata, West Bengal, IN",
            "population": 4631392,
            "workplace_types": [],
            "options": {"radius": 50, "radius_unit": "miles", "ignore_radius": False},
        },
        # Gurugram
        {
            "id": "YBg1yZQBoEtHp_8UbmaX",
            "types": ["locality"],
            "address_components": [
                {"long_name": "Gurugram", "short_name": "Gurugram", "types": ["locality"]},
                {"long_name": "Haryana", "short_name": "10", "types": ["administrative_area_level_1"]},
                {"long_name": "India", "short_name": "IN", "types": ["country"]},
            ],
            "geometry": {"location": {"lat": 28.4601, "lon": 77.02635}},
            "formatted_address": "Gurugram, Haryana, IN",
            "population": 886519,
            "workplace_types": [],
            "options": {"radius": 50, "radius_unit": "miles", "ignore_radius": False},
        },
        # Haryana state (remote)
        {
            "types": ["administrative_area_level_1"],
            "formatted_address": "Haryana, India",
            "address_components": [
                {"long_name": "Haryana", "short_name": "10", "types": ["administrative_area_level_1"]},
                {"long_name": "India", "short_name": "IN", "types": ["country"]},
            ],
            "workplace_types": ["Remote"],
            "options": {},
            "id": "Haryana, Indiaadministrative_area_level_1",
        },
        # West Bengal state (remote)
        {
            "types": ["administrative_area_level_1"],
            "formatted_address": "West Bengal, India",
            "address_components": [
                {"long_name": "West Bengal", "short_name": "28", "types": ["administrative_area_level_1"]},
                {"long_name": "India", "short_name": "IN", "types": ["country"]},
            ],
            "workplace_types": ["Remote"],
            "options": {},
            "id": "West Bengal, Indiaadministrative_area_level_1",
        },
    ],
}

# Per-query keyword overrides — each targets a different slice.
# NOTE: Keep quotes around multi-word phrases and OR operators.
# Query 3 is intentionally broader (no technologyKeywordsQuery) to catch
# roles like "SDE Intern" or "Associate Software Engineer" that don't
# always list specific tech in the title/tags.
SEARCH_QUERIES: list[dict] = [
    {
        "jobTitleQuery": '"Backend"',
        "technologyKeywordsQuery": '"Go" OR "Golang"',
    },
    {
        "jobTitleQuery": '"Backend"',
        "technologyKeywordsQuery": '"TypeScript"',
    },
    {
        "jobTitleQuery": '"Software Engineer" OR "SDE" OR "Developer"',
        "technologyKeywordsQuery": '"Go" OR "Golang" OR "TypeScript"',
    },
    {
        "jobTitleQuery": '"Intern" OR "Associate"',
        "technologyKeywordsQuery": '"Go" OR "Golang" OR "TypeScript" OR "Backend"',
    },
]


# ─────────────────────────────────────────────────────────────────
# BUILD ID EXTRACTION
# ─────────────────────────────────────────────────────────────────

# Module-level cache — avoids re-fetching within the same pipeline run
_cached_build_id: str | None = None


def _fetch_build_id() -> str | None:
    """
    Fetch the Next.js buildId from the hiring.cafe homepage.

    The buildId is embedded in the __NEXT_DATA__ script tag and changes
    on every deployment. Returns None if extraction fails.
    """
    global _cached_build_id

    try:
        r = requests.get(
            _HOMEPAGE,
            headers={"User-Agent": _UA, "Accept": "text/html"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()

        m = re.search(r'"buildId"\s*:\s*"([^"]+)"', r.text)
        if m:
            _cached_build_id = m.group(1)
            logger.debug(f"hiring.cafe buildId: {_cached_build_id}")
            return _cached_build_id

        logger.warning("hiring.cafe: buildId not found in homepage HTML")
        return None

    except requests.exceptions.RequestException as e:
        logger.warning(f"hiring.cafe: failed to fetch homepage for buildId: {e}")
        return None


def _get_build_id(force_refresh: bool = False) -> str | None:
    """Get buildId from cache or fetch fresh."""
    global _cached_build_id
    if _cached_build_id and not force_refresh:
        return _cached_build_id
    return _fetch_build_id()


# ─────────────────────────────────────────────────────────────────
# COMPANY NAME CLEANING
# ─────────────────────────────────────────────────────────────────

# Common suffixes in hiring.cafe's company_name field that hurt dedup
# against the same company from other sources (e.g. "HackerRank Careers"
# vs "HackerRank" from Greenhouse).
_COMPANY_SUFFIXES = re.compile(
    r'\s*(?:'
    r'Careers|Jobs|Hiring|Recruitment|Talent'
    r'|Career Site|Career Page|Job Board'
    r')\s*$',
    re.IGNORECASE,
)


def _clean_company_name(name: str) -> str:
    """Strip trailing noise words that hurt cross-source deduplication."""
    if not name:
        return name
    cleaned = _COMPANY_SUFFIXES.sub('', name).strip()
    return cleaned if cleaned else name  # Don't return empty string


# ─────────────────────────────────────────────────────────────────
# DESCRIPTION SYNTHESIS
# ─────────────────────────────────────────────────────────────────

def _synthesise_description(v5: dict, ecd: dict) -> str:
    """
    Build a concise, high-signal description from structured fields.

    hiring.cafe doesn't serve full HTML job descriptions, but the
    v5_processed_job_data fields contain dense, pre-extracted information
    that's actually better for pipeline scoring:
      - requirements_summary: 1-2 sentence overview of requirements
      - technical_tools: list of tech stack items
      - role_activities: list of key responsibilities
      - company_tagline / company_activities: company context
      - degree fields: education requirements (signals seniority)

    Typical output is 300-700 chars of pure signal (no HTML noise).
    """
    parts: list[str] = []

    # Requirements summary — the core signal
    req = v5.get("requirements_summary", "")
    if req:
        parts.append(req.strip())

    # Role activities
    activities = v5.get("role_activities", [])
    if activities:
        parts.append("Responsibilities: " + "; ".join(activities))

    # Tech stack
    tools = v5.get("technical_tools", [])
    if tools:
        parts.append("Tech: " + ", ".join(tools))

    # Education requirements — useful signal for seniority/experience filtering
    degree_parts = []
    for level, label in [
        ("bachelors_degree_requirement", "BS"),
        ("masters_degree_requirement", "MS"),
        ("doctorate_degree_requirement", "PhD"),
    ]:
        req_val = v5.get(level, "Not Mentioned")
        if req_val and req_val not in ("Not Mentioned", ""):
            fields = v5.get(level.replace("_requirement", "_fields_of_study"), [])
            if fields:
                degree_parts.append(f"{label} ({', '.join(fields[:2])}) {req_val}")
            else:
                degree_parts.append(f"{label} {req_val}")
    if degree_parts:
        parts.append("Education: " + "; ".join(degree_parts))

    # Min YoE — critical for experience-based prefiltering
    min_yoe = v5.get("min_industry_and_role_yoe")
    if min_yoe is not None and min_yoe != "":
        parts.append(f"Min experience: {min_yoe} years")

    # Company context
    tagline = v5.get("company_tagline", "") or ecd.get("tagline", "")
    if tagline:
        parts.append(f"Company: {tagline.strip()}")

    # Seniority + commitment as context
    seniority = v5.get("seniority_level", "")
    commitment = v5.get("commitment", [])
    if seniority or commitment:
        meta_parts = []
        if seniority:
            meta_parts.append(seniority)
        if commitment:
            meta_parts.append(", ".join(commitment))
        parts.append("Level: " + " | ".join(meta_parts))

    return " | ".join(parts)[:4000]


# ─────────────────────────────────────────────────────────────────
# SALARY FORMATTING
# ─────────────────────────────────────────────────────────────────

def _format_salary(v5: dict) -> str:
    """Format compensation from v5 fields into a human-readable string."""
    # Try yearly first, then monthly, then hourly
    for period, suffix in [
        ("yearly", "/year"),
        ("monthly", "/month"),
        ("hourly", "/hour"),
    ]:
        min_comp = v5.get(f"{period}_min_compensation")
        max_comp = v5.get(f"{period}_max_compensation")
        currency = v5.get("listed_compensation_currency", "")

        if min_comp or max_comp:
            prefix = f"{currency} " if currency else ""
            if min_comp and max_comp:
                return f"{prefix}{min_comp:,.0f} - {max_comp:,.0f}{suffix}"
            if min_comp:
                return f"{prefix}{min_comp:,.0f}+{suffix}"
            if max_comp:
                return f"Up to {prefix}{max_comp:,.0f}{suffix}"

    return ""


# ─────────────────────────────────────────────────────────────────
# SINGLE PAGE FETCH
# ─────────────────────────────────────────────────────────────────

# Reusable session — keeps the TCP connection alive across paginated
# requests. Reduces handshake overhead and looks more like a real
# browser session (which reuses connections via HTTP keep-alive).
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """Get or create a reusable requests session."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(_HEADERS)
    return _session


def _fetch_page(build_id: str, search_state: dict, page: int = 0) -> dict | None:
    """
    Fetch a single page from the hiring.cafe search API.

    Returns the parsed JSON response, or None on error.
    On 404, returns a sentinel dict {"__stale_build_id__": True} to
    signal the caller to re-fetch the build ID.
    """
    if page > 0:
        search_state = {**search_state, "page": page}

    url = _API_TEMPLATE.format(build_id=build_id)
    params = {"searchState": json.dumps(search_state, separators=(",", ":"))}
    session = _get_session()

    try:
        r = session.get(url, params=params, timeout=_TIMEOUT)

        if r.status_code == 404:
            # Build ID is stale — signal caller to re-fetch
            return {"__stale_build_id__": True}

        if r.status_code == 429:
            logger.warning("hiring.cafe: rate limited (429) — aborting")
            return None

        if r.status_code != 200:
            logger.warning(
                f"hiring.cafe: HTTP {r.status_code} on page {page} "
                f"(aborting this query)"
            )
            return None

        return r.json()

    except requests.exceptions.RequestException as e:
        logger.warning(f"hiring.cafe: request failed (page {page}): {e}")
        return None
    except (ValueError, KeyError) as e:
        logger.warning(f"hiring.cafe: JSON parse failed (page {page}): {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# NORMALISE A SINGLE HIT → JOB DICT
# ─────────────────────────────────────────────────────────────────

def _normalise_hit(hit: dict) -> dict | None:
    """
    Convert a hiring.cafe API hit into the standard job dict format
    expected by the pipeline (dedup, prefilter, ranker, scorer).

    Returns None if the hit is missing critical fields.
    """
    ji = hit.get("job_information", {})
    v5 = hit.get("v5_processed_job_data", {})
    ecd = hit.get("enriched_company_data", {})

    title = ji.get("title", "") or v5.get("core_job_title", "")
    if not title:
        return None

    # Clean company name — strip "Careers", "Jobs" etc. for better dedup
    raw_company = v5.get("company_name", "") or ecd.get("name", "")
    company = _clean_company_name(raw_company)

    location = v5.get("formatted_workplace_location", "")
    apply_url = hit.get("apply_url", "")

    if not apply_url:
        return None  # No way to apply — skip

    # Skip expired jobs
    if hit.get("is_expired", False):
        return None

    description = _synthesise_description(v5, ecd)
    salary = _format_salary(v5)

    # posted_at: prefer ISO string, fall back to millis epoch
    posted_at = v5.get("estimated_publish_date", "")
    if not posted_at:
        millis = v5.get("estimated_publish_date_millis")
        if millis:
            try:
                dt = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
                posted_at = dt.isoformat()
            except (OSError, ValueError, TypeError):
                pass

    # Tech stack from structured tools list
    tools = v5.get("technical_tools", [])
    tech_stack = ", ".join(tools) if tools else ""

    # Extra metadata for prefilter/ranker
    seniority = v5.get("seniority_level", "")
    workplace_type = v5.get("workplace_type", "")
    commitment = v5.get("commitment", [])
    commitment_str = ", ".join(commitment) if isinstance(commitment, list) else str(commitment)

    return {
        "title":          title,
        "company":        company,
        "location":       location,
        "description":    description,
        "url":            apply_url,
        "source":         "hiringcafe",
        "salary":         salary,
        "posted_at":      posted_at,
        "tech_stack":     tech_stack,
        "seniority_level": seniority,
        "workplace_type": workplace_type,
        "commitment":     commitment_str,
    }


# ─────────────────────────────────────────────────────────────────
# FETCH A SINGLE SEARCH QUERY (all pages)
# ─────────────────────────────────────────────────────────────────

def _fetch_query(build_id: str, query_overrides: dict, seen_urls: set[str]) -> tuple[list[dict], bool]:
    """
    Run a single search query with pagination.

    Returns:
      - list of normalised job dicts (deduplicated against seen_urls)
      - bool: True if the build ID went stale (caller should re-fetch)
    """
    search_state = {**_BASE_FILTERS, **query_overrides}
    jobs: list[dict] = []

    for page in range(_MAX_PAGES):
        if page > 0:
            time.sleep(_PAGE_DELAY)  # Polite delay between pages

        data = _fetch_page(build_id, search_state, page)

        if data is None:
            break  # Error — stop paginating this query

        if data.get("__stale_build_id__"):
            return jobs, True  # Signal: re-fetch build ID

        page_props = data.get("pageProps", {})
        hits = page_props.get("ssrHits", [])
        is_last = page_props.get("ssrIsLastPage", True)

        if not hits:
            break

        for hit in hits:
            job = _normalise_hit(hit)
            if job is None:
                continue

            # Dedup by apply URL within the batch
            url = job.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            jobs.append(job)

        if is_last:
            break

    return jobs, False


# ─────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def fetch_hiringcafe() -> list[dict]:
    """
    Main entry point: fetches entry-level / fresher tech jobs from
    hiring.cafe across multiple keyword queries.

    Returns a list of normalised job dicts compatible with the pipeline.
    Handles build ID rotation, pagination, and cross-query deduplication.
    """
    build_id = _get_build_id()
    if not build_id:
        logger.warning("hiring.cafe: could not obtain buildId — skipping source")
        return []

    all_jobs: list[dict] = []
    seen_urls: set[str] = set()
    build_id_refreshed = False

    for i, query in enumerate(SEARCH_QUERIES):
        if i > 0:
            time.sleep(_PAGE_DELAY)  # Polite delay between different queries

        jobs, stale = _fetch_query(build_id, query, seen_urls)
        all_jobs.extend(jobs)

        # Handle stale build ID — re-fetch once and retry this query
        if stale and not build_id_refreshed:
            logger.info("hiring.cafe: buildId stale (404), re-fetching...")
            build_id = _get_build_id(force_refresh=True)
            build_id_refreshed = True

            if not build_id:
                logger.warning("hiring.cafe: buildId re-fetch failed — aborting")
                break

            # Retry the failed query with the new build ID
            retry_jobs, retry_stale = _fetch_query(build_id, query, seen_urls)
            all_jobs.extend(retry_jobs)

            if retry_stale:
                logger.warning("hiring.cafe: buildId still stale after refresh — aborting")
                break

        elif stale and build_id_refreshed:
            # Already retried once — give up
            logger.warning("hiring.cafe: persistent buildId issues — aborting remaining queries")
            break

    logger.info(f"hiring.cafe: {len(all_jobs)} jobs found across {len(SEARCH_QUERIES)} queries")
    return all_jobs
