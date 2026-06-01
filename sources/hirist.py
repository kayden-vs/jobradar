"""
sources/hirist.py — Hirist.tech job source

Architecture:
  Hirist.tech is a React SPA — HTML shell arrives SSR but job listings are
  injected client-side. A plain HTTP fetch returns an empty shell.
  Solution: use Scrapling's StealthyFetcher (Playwright/Chromium) with
  network_idle=True to wait for the React hydration to settle.

URL structure:
  Listing page : https://www.hirist.tech/k/{keyword}-jobs?minexp=N&maxexp=M&page=P
  Detail page  : https://www.hirist.tech/j/{slug}-{id}

Experience filtering strategy:
  The site accepts ?minexp / ?maxexp query params which filter client-side,
  so we pass the configured experience range in the URL to reduce irrelevant
  cards. A lightweight post-scrape filter is kept as a safety net.

Configuration (per profile.yaml `hirist:` block):
  keywords      — list of keyword slugs, e.g. ["python", "backend-developer"]
  min_exp       — minimum experience years to request (default: 0)
  max_exp       — maximum experience years to request (default: 2)
  pages         — page cap per keyword (default: 3)
  fetch_details — whether to hit the /j/ detail page for full JD (default: True)

Rate limiting:
  Playwright sessions are heavy — we add a polite delay between listing pages
  and between detail fetches.
"""

import logging
import re
import threading
import time

from scrapling.fetchers import StealthyFetcher

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────

_BASE_URL           = "https://www.hirist.tech"
_LISTING_URL_TPL    = _BASE_URL + "/k/{keyword}-jobs"
_PAGE_CAP           = 5          # absolute safety cap on pages
_MAX_DETAIL_FETCHES = 20         # never fetch more than N detail pages per run
_INTER_PAGE_DELAY   = 3.0        # seconds between listing page fetches (Playwright is heavy)
_INTER_DETAIL_DELAY = 2.0        # seconds between /j/ detail fetches
_FETCH_TIMEOUT_MS   = 45_000     # 45 s Playwright page timeout (browser-level)
_HARD_TIMEOUT_SECS  = 600        # 10 min hard wall-clock cap for the entire fetch_hirist() call


# ─────────────────────────────────────────────────────────────────
# EXPERIENCE PARSING & FILTERING
# ─────────────────────────────────────────────────────────────────

# Matches: "3-5 Yrs", "2-4 years", "0-1 yr", "5+ yrs", "5 + Yrs", "10+Yrs"
_EXP_RANGE_RE = re.compile(
    r'(\d+)\s*[-\u2013]\s*(\d+)\s*(?:yrs?|years?)',
    re.IGNORECASE,
)
_EXP_PLUS_RE = re.compile(
    r'(\d+)\s*\+\s*(?:yrs?|years?)',
    re.IGNORECASE,
)


def _parse_exp_range(text: str) -> tuple[int | None, int | None]:
    """
    Parse an experience range string into (min_exp, max_exp) integers.

    Handles:
      "3-5 Yrs"   -> (3, 5)
      "2-4 years" -> (2, 4)
      "5+ yrs"    -> (5, None)   None means open-ended
      "0-1 yr"    -> (0, 1)
      ""          -> (None, None)
    """
    if not text:
        return None, None

    m = _EXP_RANGE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = _EXP_PLUS_RE.search(text)
    if m:
        return int(m.group(1)), None   # open-ended upper bound

    # Try bare number: "3 years"
    m = re.search(r'(\d+)\s*(?:yrs?|years?)', text, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        return n, n

    return None, None


def _exp_overlaps(
    job_min: int | None,
    job_max: int | None,
    target_min: int,
    target_max: int,
) -> bool:
    """
    Return True if the job's experience range overlaps the target range.

    If either bound is unknown (None), we give the job the benefit of the doubt.

    Overlap rule:  job_min <= target_max  AND  job_max >= target_min
    (treating None job_max as infinity).
    """
    if job_min is None and job_max is None:
        return True   # no exp info — pass through

    effective_min = job_min if job_min is not None else 0
    effective_max = job_max if job_max is not None else 999   # open-ended

    return effective_min <= target_max and effective_max >= target_min


# ─────────────────────────────────────────────────────────────────
# HTML HELPERS
# ─────────────────────────────────────────────────────────────────

def _strip_html(html_text: str) -> str:
    """Strip HTML tags and collapse whitespace to get plain text."""
    if not html_text:
        return ""
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html_text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<(br|p|div|li|h[1-6]|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────
# HARD TIMEOUT HELPER
# ─────────────────────────────────────────────────────────────────

class _TimeoutError(Exception):
    """Raised when fetch_hirist exceeds _HARD_TIMEOUT_SECS."""


def _run_with_hard_timeout(fn, timeout_secs: int, *args, **kwargs):
    """
    Run fn(*args, **kwargs) in the current thread but enforce a hard wall-clock
    deadline using a background timer thread.

    If fn does not return within timeout_secs, the timer thread raises
    _TimeoutError in the calling thread via ctypes — which interrupts even
    blocking C extensions like Playwright.

    Falls back to returning None if the ctypes injection is unavailable.
    """
    import ctypes

    result_box = [None]
    exc_box    = [None]
    caller_tid = threading.current_thread().ident

    def _inject_timeout():
        """Timer callback: inject _TimeoutError into the calling thread."""
        logger.warning(
            f"Hirist: hard timeout ({timeout_secs}s) reached — "
            "injecting TimeoutError into fetch thread"
        )
        try:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(caller_tid),
                ctypes.py_object(_TimeoutError),
            )
        except Exception as e:
            logger.error(f"Hirist: failed to inject timeout: {e}")

    timer = threading.Timer(timeout_secs, _inject_timeout)
    timer.daemon = True
    timer.start()
    try:
        result_box[0] = fn(*args, **kwargs)
    except _TimeoutError:
        logger.error(
            f"Hirist: fetch_hirist() exceeded hard timeout of {timeout_secs}s "
            "— returning empty list. Pipeline continues normally."
        )
        result_box[0] = []
    except Exception as e:
        exc_box[0] = e
    finally:
        timer.cancel()

    if exc_box[0] is not None:
        raise exc_box[0]
    return result_box[0]


# ─────────────────────────────────────────────────────────────────
# LISTING PAGE SCRAPER
# ─────────────────────────────────────────────────────────────────

def _fetch_listing_page(
    keyword: str,
    page: int,
    min_exp: int,
    max_exp: int,
) -> list[dict]:
    """
    Fetch one listing page for the given keyword+experience range using
    StealthyFetcher (JS-rendered).

    Returns a list of partial job dicts (no full JD yet).
    Returns [] on failure or when no cards are found.
    """
    url = _LISTING_URL_TPL.format(keyword=keyword)
    params_str = f"?minexp={min_exp}&maxexp={max_exp}"
    if page > 1:
        params_str += f"&page={page}"
    full_url = url + params_str

    logger.debug(f"Hirist: fetching listing -- {full_url}")

    try:
        page_resp = StealthyFetcher.fetch(
            full_url,
            headless=True,
            network_idle=True,
            timeout=_FETCH_TIMEOUT_MS,
            disable_resources=True,   # skip images/fonts for speed
            block_ads=True,
        )
    except Exception as exc:
        logger.warning(f"Hirist: StealthyFetcher failed for {full_url}: {exc}")
        return []

    if page_resp is None:
        logger.warning(f"Hirist: got None response for {full_url}")
        return []

    # CSS selectors for job cards.
    # Hirist renders each job as a card — try multiple selectors for resilience.
    cards = (
        page_resp.css(".job-listing-card")
        or page_resp.css("[class*='jobCard']")
        or page_resp.css("[class*='job-card']")
        or page_resp.css("article.job")
        or page_resp.css(".search-result-item")
        or page_resp.css("[class*='JobList'] > div")
        or page_resp.css("ul.jobs-list > li")
    )

    if not cards:
        logger.debug(f"Hirist: no job cards found at {full_url} (page {page})")
        return []

    jobs: list[dict] = []

    for card in cards:
        # Title
        title_el = (
            card.css("h2 a, h3 a, h1 a")
            or card.css("[class*='jobTitle'] a, [class*='job-title'] a")
            or card.css("[class*='title'] a")
            or card.css("h2, h3, h1")
            or card.css("[class*='jobTitle'], [class*='job-title'], [class*='title']")
        )
        title = title_el[0].text.strip() if title_el else ""
        if not title:
            continue

        # Job URL
        link_el = (
            card.css("h2 a[href], h3 a[href], h1 a[href]")
            or card.css("a[href*='/j/']")
            or card.css("[class*='title'] a[href]")
        )
        href = link_el[0].attrib.get("href", "") if link_el else ""
        job_url = (_BASE_URL + href) if href.startswith("/") else href

        # Company
        company_el = (
            card.css("[class*='companyName'], [class*='company-name']")
            or card.css("[class*='company']")
        )
        company = company_el[0].text.strip() if company_el else ""

        # Location
        location_el = card.css("[class*='location'], [class*='Location']")
        location = location_el[0].text.strip() if location_el else "India"

        # Experience
        exp_el = (
            card.css("[class*='experience'], [class*='Experience']")
            or card.css("[class*='exp']")
        )
        exp_text = exp_el[0].text.strip() if exp_el else ""
        job_min_exp, job_max_exp = _parse_exp_range(exp_text)

        # Salary / CTC
        salary_el = card.css(
            "[class*='salary'], [class*='Salary'], [class*='ctc'], [class*='CTC']"
        )
        salary = salary_el[0].text.strip() if salary_el else ""

        # Skills / Tags
        skills_els = card.css("[class*='skill'], [class*='tag'], [class*='Tag']")
        skills = ", ".join(el.text.strip() for el in skills_els if el.text.strip())

        jobs.append({
            "title":        title,
            "company":      company,
            "location":     location or "India",
            "description":  (
                f"{title} at {company}. "
                f"Location: {location}. "
                f"Experience: {exp_text}. "
                f"Skills: {skills}"
            ),
            "url":          job_url,
            "source":       "hirist",
            "salary":       salary,
            "posted_at":    "",   # not prominently shown on listing page
            "min_exp":      str(job_min_exp) if job_min_exp is not None else "",
            "max_exp":      str(job_max_exp) if job_max_exp is not None else "",
            "skills":       skills,
            # Internal fields — stripped before returning to pipeline
            "_exp_text":    exp_text,
            "_job_min_exp": job_min_exp,
            "_job_max_exp": job_max_exp,
        })

    logger.debug(f"Hirist: {len(jobs)} cards extracted from page {page} of '{keyword}'")
    return jobs


# ─────────────────────────────────────────────────────────────────
# DETAIL PAGE SCRAPER  (/j/ pages)
# ─────────────────────────────────────────────────────────────────

def _fetch_job_detail(job_url: str) -> dict:
    """
    Fetch the individual /j/ detail page to extract:
      - Full job description
      - Required skills (enriched)
      - posted_at (if visible)

    Returns a dict of fields to merge into the job dict.
    Returns {} on failure — caller keeps the listing-page data as-is.
    """
    if not job_url or "/j/" not in job_url:
        return {}

    logger.debug(f"Hirist: fetching detail -- {job_url}")

    try:
        page_resp = StealthyFetcher.fetch(
            job_url,
            headless=True,
            network_idle=True,
            timeout=_FETCH_TIMEOUT_MS,
            disable_resources=True,
            block_ads=True,
        )
    except Exception as exc:
        logger.warning(f"Hirist: detail fetch failed for {job_url}: {exc}")
        return {}

    if page_resp is None:
        return {}

    enriched: dict = {}

    # Full job description
    jd_el = (
        page_resp.css("[class*='jobDescription'], [class*='job-description']")
        or page_resp.css("[class*='description']")
        or page_resp.css("article, main section")
    )
    if jd_el:
        raw_html = jd_el[0].html or jd_el[0].text or ""
        jd_text = _strip_html(raw_html) if "<" in raw_html else raw_html.strip()
        if jd_text:
            enriched["description"] = jd_text

    # Skills (detail page often has more)
    skills_els = page_resp.css(
        "[class*='skill'], [class*='tag'], [class*='Tag'], [class*='keySkill']"
    )
    if skills_els:
        skills_list = [el.text.strip() for el in skills_els if el.text.strip()]
        if skills_list:
            enriched["skills"] = ", ".join(skills_list)

    # Posted date (if shown on detail page)
    date_el = page_resp.css("[class*='date'], [class*='postedOn'], time")
    if date_el:
        enriched["posted_at"] = date_el[0].text.strip()

    return enriched


# ─────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def fetch_hirist(profile: dict | None = None) -> list[dict]:
    """
    Fetch jobs from Hirist.tech using JS-rendered scraping.

    Configuration is read from the profile's `hirist:` block:
      hirist:
        keywords:      [...]   # keyword slugs (e.g. "python", "backend-developer")
        min_exp:       0       # minimum experience years for URL filter
        max_exp:       2       # maximum experience years for URL filter
        pages:         3       # pages per keyword (safety cap: 5)
        fetch_details: true    # whether to fetch /j/ detail pages

    Pipeline:
      1. For each keyword x page: fetch listing with StealthyFetcher
      2. Deduplicate by URL within this run
      3. Apply lightweight experience overlap filter (safety net)
      4. Optionally fetch /j/ detail pages for full JDs
      5. Strip internal fields before returning

    Returns:
      List of job dicts conforming to the standard pipeline schema.
    """
    hirist_cfg    = (profile or {}).get("hirist", {})

    keywords      = hirist_cfg.get("keywords") or _DEFAULT_KEYWORDS
    min_exp       = int(hirist_cfg.get("min_exp", 0))
    max_exp       = int(hirist_cfg.get("max_exp", 2))
    pages         = min(int(hirist_cfg.get("pages", 3)), _PAGE_CAP)
    fetch_details = hirist_cfg.get("fetch_details", True)

    logger.info(
        f"Hirist: {len(keywords)} keywords x {pages} pages "
        f"| exp range: {min_exp}-{max_exp} yrs "
        f"| detail fetch: {fetch_details} "
        f"| hard timeout: {_HARD_TIMEOUT_SECS}s"
    )

    # Wrap the real body in a hard wall-clock timeout guard.
    # This ensures fetch_hirist() *always* returns within _HARD_TIMEOUT_SECS
    # even if Playwright hangs on browser launch or network idle.
    return _run_with_hard_timeout(
        _fetch_hirist_body,
        _HARD_TIMEOUT_SECS,
        keywords, min_exp, max_exp, pages, fetch_details,
    )


def _fetch_hirist_body(
    keywords: list[str],
    min_exp: int,
    max_exp: int,
    pages: int,
    fetch_details: bool,
) -> list[dict]:
    """Inner body of fetch_hirist — separated so it can be wrapped in a timeout."""
    raw_jobs: list[dict] = []
    seen_urls: set[str]  = set()

    for keyword in keywords:
        for page_no in range(1, pages + 1):
            batch = _fetch_listing_page(keyword, page_no, min_exp, max_exp)

            if not batch:
                # No cards on this page -> stop paginating this keyword
                logger.debug(
                    f"Hirist: stopping pagination for '{keyword}' "
                    f"at page {page_no} (empty)"
                )
                break

            added = 0
            for job in batch:
                url = job.get("url", "")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                raw_jobs.append(job)
                added += 1

            logger.debug(
                f"Hirist: keyword='{keyword}' page={page_no} -> {added} new jobs added"
            )

            if page_no < pages:
                time.sleep(_INTER_PAGE_DELAY)

    logger.info(f"Hirist: {len(raw_jobs)} raw jobs from listing pages")

    if not raw_jobs:
        return []

    # Step 2: Lightweight experience overlap filter (safety net)
    # URL params already filter most irrelevant cards; this catches edge cases.
    filtered_jobs: list[dict] = []
    rejected_count = 0

    for job in raw_jobs:
        job_min = job.get("_job_min_exp")
        job_max = job.get("_job_max_exp")

        if not _exp_overlaps(job_min, job_max, min_exp, max_exp):
            logger.debug(
                f"Hirist exp filter REJECT: '{job.get('title')}' @ '{job.get('company')}' "
                f"-- job exp: {job.get('_exp_text')} vs target {min_exp}-{max_exp}"
            )
            rejected_count += 1
            continue

        filtered_jobs.append(job)

    if rejected_count:
        logger.info(f"Hirist: post-scrape exp filter rejected {rejected_count} jobs")

    logger.info(f"Hirist: {len(filtered_jobs)} jobs passed experience filter")

    if not filtered_jobs:
        return []

    # Step 3: Optional detail-page fetching (capped at _MAX_DETAIL_FETCHES)
    final_jobs: list[dict] = []
    detail_fetches = 0

    for job in filtered_jobs:
        # Strip internal fields before we either enrich or finalize
        job.pop("_job_min_exp", None)
        job.pop("_job_max_exp", None)
        exp_text = job.pop("_exp_text", "")

        if (
            fetch_details
            and job.get("url")
            and "/j/" in job["url"]
            and detail_fetches < _MAX_DETAIL_FETCHES
        ):
            time.sleep(_INTER_DETAIL_DELAY)
            enriched = _fetch_job_detail(job["url"])
            detail_fetches += 1
            if enriched:
                job.update(enriched)
            else:
                logger.debug(
                    f"Hirist: detail failed for '{job.get('title')}' -- "
                    "keeping listing-page snippet"
                )

        # Ensure description is non-empty even if detail fetch was skipped/failed
        if not job.get("description"):
            job["description"] = (
                f"{job.get('title', '')} at {job.get('company', '')}. "
                f"Location: {job.get('location', '')}. "
                f"Experience: {exp_text}."
            )

        final_jobs.append(job)

    if detail_fetches >= _MAX_DETAIL_FETCHES and len(filtered_jobs) > detail_fetches:
        logger.info(
            f"Hirist: detail fetch cap hit ({_MAX_DETAIL_FETCHES}) -- "
            f"{len(filtered_jobs) - detail_fetches} jobs kept with listing-page description"
        )

    logger.info(f"Hirist: {len(final_jobs)} jobs ready for pipeline")
    return final_jobs


# ─────────────────────────────────────────────────────────────────
# SENSIBLE DEFAULTS (used when profile has no hirist: block)
# ─────────────────────────────────────────────────────────────────

_DEFAULT_KEYWORDS = [
    "python",
    "backend-developer",
    "golang",
    "node-js",
    "data-engineer",
]
