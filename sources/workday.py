"""
sources/workday.py
──────────────────
Workday ATS integration — fetches jobs from Workday career portals.

Unlike other ATS platforms (Greenhouse, Lever, Ashby, etc.) that use a shared
domain with a company slug, Workday gives each company its own subdomain:

    https://{tenant}.{wd_server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

Three independently pre-discovered parameters are required per company:
  - tenant:    subdomain slug (e.g. "proofpoint", "adobe")
  - wd_server: data centre ID (e.g. "wd1", "wd3", "wd5")
  - site:      career portal name (e.g. "proofpointcareers", "External")

These are stored in companies.yaml under the `workday:` key as dicts.

Two-phase fetch (lazy JD):
  1. LIST endpoint (POST) — returns title, location, postedOn, externalPath
     → Title filter: drop non-tech titles immediately
  2. DETAIL endpoint (GET) — returns full HTML jobDescription
     → Called lazily from scorer.py only for jobs that survive prefilter

Rate limiting:
  Workday career sites sit behind Cloudflare. Per-IP rates are tracked across
  ALL *.myworkdayjobs.com subdomains. We add 0.3s delay between list pages and
  0.5s between companies to stay well within safe limits.
"""

import re
import time
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Max jobs to paginate through per company (safety valve)
_MAX_JOBS_PER_COMPANY = 200

# Page size — Workday caps at 20
_PAGE_SIZE = 20

# Delay between paginated requests to the same tenant (seconds)
_PAGE_DELAY = 0.3

# Delay between different companies (seconds)
_COMPANY_DELAY = 0.5

# Max retries on transient errors (429, 5xx)
_MAX_RETRIES = 3

# User-Agent for all requests
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Tech-role title signals — if none present, the title is non-tech and dropped.
# This prevents fetching JDs for "Director, Sales", "Treasury Manager", etc.
# Kept in sync with prefilter._ATS_TITLE_KEEP_SIGNALS.
_TITLE_KEEP_SIGNALS = [
    # Role types
    "engineer", "developer", "dev", "programmer", "sde", "swe", "intern",
    "fresher", "graduate", "trainee", "associate engineer",
    # Specialisations
    "backend", "back-end", "back end", "full stack", "fullstack", "full-stack",
    "frontend", "front-end", "front end",
    "platform", "infrastructure", "devops", "sre", "cloud", "systems",
    "data engineer", "data engineering",
    # Languages / frameworks
    "golang", "go ", " go,", " go)", "python", "typescript", "javascript",
    "java ", "java,", "java)", "kotlin", "rust", "c++", "scala",
    "node", "node.js", "nodejs", "django", "fastapi", "spring",
    # Generic tech signals
    "api", "server", "microservice", "ml engineer", "software",
]


# ─────────────────────────────────────────────────────────────────────────────
# HTML STRIPPING
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Strip HTML tags from a JD string."""
    if not html:
        return ""
    try:
        return BeautifulSoup(html, "lxml").get_text(separator="\n").strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()


# ─────────────────────────────────────────────────────────────────────────────
# TITLE RELEVANCE CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _is_relevant_title(title: str) -> bool:
    """
    Check if a job title contains at least one tech-role signal.

    Workday companies list thousands of jobs spanning sales, finance, legal,
    HR, marketing, etc. Fetching JDs for all of them is wasteful.
    This fast check drops ~80% of listings at the title level.
    """
    title_lower = title.lower()
    return any(signal in title_lower for signal in _TITLE_KEEP_SIGNALS)


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_headers(tenant: str, wd_server: str, site: str) -> dict:
    """Build required headers for Workday API requests."""
    base = f"https://{tenant}.{wd_server}.myworkdayjobs.com"
    return {
        "Content-Type":    "application/json",
        "Accept":          "application/json",
        "Accept-Language":  "en-US,en;q=0.9",
        "Origin":          base,
        "Referer":         f"{base}/{site}/",
        "User-Agent":      _USER_AGENT,
    }


def _build_jobs_url(tenant: str, wd_server: str, site: str) -> str:
    """Build the list endpoint URL."""
    return (
        f"https://{tenant}.{wd_server}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{site}/jobs"
    )


def _build_detail_url(tenant: str, wd_server: str, site: str, external_path: str) -> str:
    """Build the detail endpoint URL."""
    return (
        f"https://{tenant}.{wd_server}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{site}{external_path}"
    )


def _build_job_url(tenant: str, wd_server: str, site: str, external_path: str) -> str:
    """Build the public-facing job URL (for the user to open in browser)."""
    return (
        f"https://{tenant}.{wd_server}.myworkdayjobs.com"
        f"/{site}{external_path}"
    )


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response | None:
    """
    Make an HTTP request with exponential backoff on transient errors.

    Handles:
      - 429 Too Many Requests → backoff and retry
      - 5xx Server Errors → backoff and retry
      - Connection errors → backoff and retry
      - 422 (wrong wd_server) → return immediately (not transient)
      - 404 (wrong site) → return immediately (not transient)
    """
    for attempt in range(_MAX_RETRIES):
        try:
            if method == "POST":
                resp = requests.post(url, **kwargs)
            else:
                resp = requests.get(url, **kwargs)

            # Non-transient errors — don't retry
            if resp.status_code in (404, 422):
                return resp

            # Rate limited — backoff and retry
            if resp.status_code == 429:
                wait = (2 ** attempt) * 2  # 2s, 4s, 8s
                logger.warning(
                    f"Workday rate limited (429) on {url} — "
                    f"retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            # Server errors — backoff and retry
            if resp.status_code >= 500:
                wait = (2 ** attempt) * 1  # 1s, 2s, 4s
                logger.warning(
                    f"Workday server error ({resp.status_code}) on {url} — "
                    f"retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            return resp

        except requests.exceptions.ConnectionError as e:
            wait = (2 ** attempt) * 1
            logger.warning(
                f"Workday connection error on {url}: {e} — "
                f"retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})"
            )
            time.sleep(wait)
        except requests.exceptions.Timeout:
            wait = (2 ** attempt) * 1
            logger.warning(
                f"Workday timeout on {url} — "
                f"retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})"
            )
            time.sleep(wait)

    logger.error(f"Workday request failed after {_MAX_RETRIES} retries: {url}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DETAIL ENDPOINT — LAZY JD FETCH
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_workday_jd(tenant: str, wd_server: str, site: str, external_path: str) -> str:
    """
    Fetch the full job description from Workday's detail endpoint.

    GET https://{tenant}.{wd_server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}

    Returns plain text JD or empty string on failure.
    """
    url = _build_detail_url(tenant, wd_server, site, external_path)
    headers = _build_headers(tenant, wd_server, site)
    # Detail endpoint uses GET, not POST
    headers.pop("Content-Type", None)

    resp = _request_with_retry("GET", url, headers=headers, timeout=15)
    if resp is None or resp.status_code != 200:
        logger.debug(f"Workday JD fetch failed for {external_path}: status={getattr(resp, 'status_code', 'N/A')}")
        return ""

    try:
        data = resp.json()
        jpi = data.get("jobPostingInfo", {})
        html_desc = jpi.get("jobDescription", "")
        return _strip_html(html_desc)
    except Exception as e:
        logger.debug(f"Workday JD parse failed for {external_path}: {e}")
        return ""


def lazy_fetch_workday_detail(job: dict) -> str:
    """
    Public lazy-fetch API — called from scorer.py when `_workday_detail_path` is present.

    Fetches the full JD from the detail endpoint and returns the plain-text
    description. Also enriches the job dict with additional metadata from
    the detail response (timeType, startDate, country).

    Returns the plain-text JD string, or empty string on failure.
    """
    tenant    = job.get("_workday_tenant", "")
    wd_server = job.get("_workday_wd_server", "")
    site      = job.get("_workday_site", "")
    path      = job.get("_workday_detail_path", "")

    if not all([tenant, wd_server, site, path]):
        return ""

    url = _build_detail_url(tenant, wd_server, site, path)
    headers = _build_headers(tenant, wd_server, site)
    headers.pop("Content-Type", None)

    resp = _request_with_retry("GET", url, headers=headers, timeout=15)
    if resp is None or resp.status_code != 200:
        logger.debug(f"Workday lazy-fetch failed for {path}: status={getattr(resp, 'status_code', 'N/A')}")
        return ""

    try:
        data = resp.json()
        jpi = data.get("jobPostingInfo", {})

        # Extract and strip HTML description
        html_desc = jpi.get("jobDescription", "")
        plain_desc = _strip_html(html_desc)

        # Enrich job with additional metadata from detail response
        time_type = jpi.get("timeType", "")
        if time_type:
            job["time_type"] = time_type

        start_date = jpi.get("startDate", "")
        if start_date:
            job["start_date"] = start_date

        country_info = jpi.get("country", {})
        if isinstance(country_info, dict):
            country_name = country_info.get("descriptor", "")
            if country_name:
                job["country"] = country_name

        return plain_desc

    except Exception as e:
        logger.debug(f"Workday lazy-fetch parse failed for {path}: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# LIST ENDPOINT — PER-COMPANY FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_workday(
    company_name: str,
    tenant: str,
    wd_server: str,
    site: str,
) -> list[dict]:
    """
    Fetch jobs from a single Workday company's career portal.

    Paginates through the list endpoint, applies title-level tech filter,
    and returns lightweight job dicts. Full JD is fetched lazily later.

    Args:
        company_name: Display name for the company (e.g. "BrowserStack")
        tenant:       Workday tenant slug (e.g. "browserstack")
        wd_server:    Data centre ID (e.g. "wd3")
        site:         Career site name (e.g. "External")

    Returns:
        List of job dicts with source="workday" and lazy-fetch keys.
    """
    url = _build_jobs_url(tenant, wd_server, site)
    headers = _build_headers(tenant, wd_server, site)

    jobs = []
    offset = 0
    total_fetched = 0
    title_filtered = 0

    while total_fetched < _MAX_JOBS_PER_COMPANY:
        payload = {
            "appliedFacets": {},
            "limit": _PAGE_SIZE,
            "offset": offset,
            "searchText": "",
        }

        resp = _request_with_retry("POST", url, json=payload, headers=headers, timeout=15)

        if resp is None:
            logger.warning(f"Workday fetch failed for {company_name} — no response after retries")
            break

        # Handle non-transient error codes
        if resp.status_code == 422:
            logger.warning(
                f"Workday {company_name}: HTTP 422 — wrong wd_server '{wd_server}'. "
                f"This tenant may have migrated. Skipping."
            )
            break

        if resp.status_code == 404:
            logger.warning(
                f"Workday {company_name}: HTTP 404 — wrong site '{site}' on {wd_server}. "
                f"The site name may have changed. Skipping."
            )
            break

        if resp.status_code != 200:
            logger.warning(
                f"Workday {company_name}: unexpected HTTP {resp.status_code}. Skipping."
            )
            break

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"Workday {company_name}: JSON parse error: {e}. Skipping.")
            break

        postings = data.get("jobPostings", [])
        total_available = data.get("total", 0)

        if not postings:
            break

        for posting in postings:
            title = posting.get("title", "")

            # ── Title relevance filter ────────────────────────────────────
            # Drop non-tech titles immediately (Director, Sales, etc.)
            if not _is_relevant_title(title):
                title_filtered += 1
                continue

            external_path = posting.get("externalPath", "")
            location_text = posting.get("locationsText", "Not specified")
            posted_on     = posting.get("postedOn", "")
            bullet_fields = posting.get("bulletFields", [])
            job_req_id    = bullet_fields[0] if bullet_fields else ""

            jobs.append({
                "title":       title,
                "company":     company_name,
                "location":    location_text,
                "description": "",  # Lazy-fetched later in scorer.py
                "url":         _build_job_url(tenant, wd_server, site, external_path),
                "source":      "workday",
                "salary":      "",
                "posted_at":   posted_on,
                "job_req_id":  job_req_id,
                # Internal keys for lazy JD fetch — stripped in scorer.py
                "_workday_detail_path": external_path,
                "_workday_tenant":     tenant,
                "_workday_wd_server":  wd_server,
                "_workday_site":       site,
            })

        total_fetched += len(postings)
        offset += len(postings)

        # Stop if we've fetched all available jobs
        if offset >= total_available:
            break

        # Rate-limit between pages
        time.sleep(_PAGE_DELAY)

    if title_filtered > 0:
        logger.debug(
            f"Workday {company_name}: dropped {title_filtered} non-tech titles at fetch time"
        )

    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_workday(companies_config: dict, profile: dict = None) -> list[dict]:
    """
    Fetch jobs from all Workday companies listed in companies.yaml.

    Args:
        companies_config: Loaded companies.yaml dict. Expected format:
            workday:
              - name: BrowserStack
                tenant: browserstack
                wd_server: wd3
                site: External
        profile: Loaded profile.yaml dict (currently unused but passed for
                 future keyword-based searchText filtering).

    Returns:
        Combined list of job dicts from all Workday companies.
    """
    workday_companies = companies_config.get("workday") or []
    if not workday_companies:
        logger.info("Workday: no companies configured in companies.yaml")
        return []

    all_jobs = []

    for entry in workday_companies:
        # Validate entry structure
        if not isinstance(entry, dict):
            logger.warning(f"Workday: skipping invalid entry (expected dict, got {type(entry).__name__}): {entry}")
            continue

        name      = entry.get("name", "Unknown")
        tenant    = entry.get("tenant", "")
        wd_server = entry.get("wd_server", "")
        site      = entry.get("site", "")

        if not all([tenant, wd_server, site]):
            logger.warning(
                f"Workday: skipping {name} — missing required fields "
                f"(tenant={tenant!r}, wd_server={wd_server!r}, site={site!r})"
            )
            continue

        try:
            jobs = fetch_workday(name, tenant, wd_server, site)
            all_jobs.extend(jobs)
            logger.info(f"Workday {name}: {len(jobs)} relevant jobs (tenant={tenant}, {wd_server}/{site})")
        except Exception as e:
            logger.error(f"Workday {name}: unexpected error: {e}")

        # Rate-limit between companies to avoid Cloudflare issues
        time.sleep(_COMPANY_DELAY)

    logger.info(f"Workday total: {len(all_jobs)} relevant jobs from {len(workday_companies)} companies")
    return all_jobs
