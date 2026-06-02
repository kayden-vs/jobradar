"""
sources/naukri.py — Naukri.com job aggregator source

API Architecture (two-step):
  Step 1 — Search API: GET /jobapi/v2/search
    Returns a paginated list of job cards with truncated descriptions.
    Stage-1 filters applied HERE (experience, age) to avoid unnecessary Step 2 calls.

  Step 2 — Detail API: GET /jobapi/v2/job/{jobId}
    Returns the full job description (HTML).  Always fetched for jobs that
    survive Step 1 filters.  Falls back to truncated jobDesc if this fails.

Required headers for both endpoints (without them the API returns 406):
  appid: 109
  systemid: 109
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
  Accept: application/json
  Referer: https://www.naukri.com/

Rate limiting:
  Exponential backoff on HTTP 429 (up to MAX_RETRIES attempts).
  A polite inter-request delay between job detail calls.

Configuration (per profile.yaml `naukri:` block):
  keywords  — list of search keywords
  locations — list of location strings
  pages     — number of result pages to fetch per keyword+location combo
              (each page has up to 20 results)

jobDesc handling:
  - Search API jobDesc is always a short HTML snippet — never used as final JD.
  - Detail API jobDesc is the full HTML — stripped to plain text before storage.
"""

import logging
import re
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------
# CONSTANTS
# -----------------------------------------------------------------

_SEARCH_URL = "https://www.naukri.com/jobapi/v2/search"
_DETAIL_URL = "https://www.naukri.com/jobapi/v2/job/{jobId}"

_HEADERS = {
    "appid":      "109",
    "systemid":   "109",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":     "application/json",
    "Referer":    "https://www.naukri.com/",
}

_RESULTS_PER_PAGE    = 20
_INTER_REQUEST_DELAY = 1.2   # seconds between detail API calls (rate limit politeness)
_MAX_RETRIES         = 4     # exponential backoff attempts on HTTP 429
_BACKOFF_BASE        = 2     # seconds (doubles each retry)

# Fallback experience cap used only when profile.candidate.experience.max_required
# is missing or unparseable. In practice the profile always sets this.
_DEFAULT_MAX_EXP_YEARS = 1


# -----------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------

def _strip_html(html_text: str) -> str:
    """Strip HTML tags and collapse whitespace to get plain text."""
    if not html_text:
        return ""
    # Remove script/style blocks entirely
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", " ",
        html_text, flags=re.IGNORECASE | re.DOTALL
    )
    # Replace block-level elements with newlines for readability
    text = re.sub(r"<(br|p|div|li|h[1-6]|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common HTML entities
    text = (
        text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&nbsp;", " ")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
    )
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_naukri_date(add_date) -> str:
    """
    Convert Naukri addDate to ISO 8601 string.

    Naukri returns addDate as a date string like '2026-05-26 16:27:38.0'
    OR occasionally as a Unix timestamp (ms or s) for older listings.
    Returns "" if unparseable.
    """
    if not add_date:
        return ""
    s = str(add_date).strip()
    # Date string format: '2026-05-26 16:27:38.0'
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        pass
    # Fallback: Unix timestamp (ms or s)
    try:
        ts = int(float(s))
        if ts > 1_000_000_000_000:   # milliseconds
            ts = ts // 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.isoformat()
    except Exception:
        return s


def _is_too_old(posted_at_iso: str, max_days: int) -> bool:
    """
    Stage-1 age filter. Returns True if job is older than max_days.
    Returns False (keep) if date is unparseable — benefit of the doubt.
    """
    if not posted_at_iso:
        return False
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.parse(posted_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).days
        return age_days > max_days
    except Exception:
        return False


def _build_salary(job_card: dict) -> str:
    """
    Build a human-readable salary string from job card fields.
    showSal is 'y'/'n'; minSal/maxSal come as numeric strings (e.g. '600').
    Naukri salary unit is thousands/year (600 -> ~6 LPA).
    Returns "" if not disclosed or showSal != 'y'.
    """
    if str(job_card.get("showSal", "n")).lower() != "y":
        return ""
    try:
        min_sal = float(job_card.get("minSal", 0) or 0)
        max_sal = float(job_card.get("maxSal", 0) or 0)
    except (TypeError, ValueError):
        return ""
    if min_sal == 0 and max_sal == 0:
        return ""
    min_lpa = min_sal / 100
    max_lpa = max_sal / 100
    if max_lpa > 0:
        return f"Rs{min_lpa:.1f}-{max_lpa:.1f} LPA"
    return f"Rs{min_lpa:.1f}+ LPA"



# -----------------------------------------------------------------
# HTTP WITH EXPONENTIAL BACKOFF
# -----------------------------------------------------------------

def _get_with_backoff(url: str, params: dict = None) -> "requests.Response | None":
    """
    GET request with exponential backoff on HTTP 429 (rate limit).

    Returns the Response on success, None on persistent failure.
    Logs a clear error on 406 so misconfigured headers are easy to spot.
    """
    delay = _BACKOFF_BASE
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                logger.warning(
                    "Naukri 429 rate limit on attempt %d/%d. "
                    "Backing off %.0fs — %s",
                    attempt, _MAX_RETRIES, delay, url,
                )
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 406:
                logger.error(
                    "Naukri returned 406 Not Acceptable — verify that all required "
                    "headers (appid, systemid, User-Agent, Accept, Referer) are set "
                    "correctly in sources/naukri.py."
                )
                return None
            if resp.status_code == 404:
                logger.debug("Naukri HTTP 404 for %s (likely empty or out-of-bounds page)", url)
            else:
                logger.warning("Naukri HTTP %d for %s", resp.status_code, url)
            return None
        except requests.exceptions.RequestException as exc:
            logger.warning("Naukri request error (attempt %d/%d): %s", attempt, _MAX_RETRIES, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
    logger.error("Naukri: all %d attempts failed for %s", _MAX_RETRIES, url)
    return None


# -----------------------------------------------------------------
# STEP 2 — DETAIL API (full JD)
# -----------------------------------------------------------------

def _fetch_job_detail(job_id: str) -> str:
    """
    Fetch full job description from the Naukri detail API.

    Returns plain text (HTML stripped).
    Returns "" on any failure — caller falls back to truncated snippet.
    """
    url  = _DETAIL_URL.format(jobId=job_id)
    resp = _get_with_backoff(url)
    if resp is None:
        return ""
    try:
        data = resp.json()
        # Response may nest under "jobDetails" or "job"
        job_data = data.get("jobDetails", data.get("job", {}))
        raw_html = (
            job_data.get("jobDescription", "")
            or job_data.get("jobDesc", "")
        )
        return _strip_html(raw_html)
    except Exception as exc:
        logger.warning("Naukri: failed to parse detail response for jobId=%s: %s", job_id, exc)
        return ""


# -----------------------------------------------------------------
# STEP 1 — SEARCH API with Stage-1 filters
# -----------------------------------------------------------------

def _fetch_search_page(
    keyword: str,
    location: str,
    page_no: int,
    max_age_days: int,
    max_exp_years: int,
) -> list:
    """
    Fetch one page of Naukri search results and apply Stage-1 filters:
      1. minExp > max_exp_years  -> reject immediately (no Step 2 waste)
         max_exp_years comes from profile.candidate.experience.max_required
      2. addDate older than max_age_days -> reject immediately

    Returns a list of partial job dicts for jobs that survived both filters.
    The special key `_naukri_job_id` is included so Step 2 can fetch the full JD;
    it is stripped before jobs are returned from fetch_naukri().
    """
    params = {
        "noOfResults": _RESULTS_PER_PAGE,
        "urlType":     "search_by_keyword",
        "searchType":  "adv",
        "keyword":     keyword,
        "location":    location,
        "pageNo":      page_no,
    }

    resp = _get_with_backoff(_SEARCH_URL, params=params)
    if resp is None:
        return []

    try:
        data = resp.json()
        # Naukri response shapes seen in the wild:
        #   - top-level "list" key  (most common as of 2025)
        #   - top-level "jobDetails"
        #   - nested under "response.jobDetails" or "response.list"
        job_list = (
            data.get("list")
            or data.get("jobDetails", [])
        )
        if not job_list:
            response_obj = data.get("response", {})
            job_list = (
                response_obj.get("jobDetails", [])
                or response_obj.get("list", [])
            )
    except Exception as exc:
        logger.warning(
            "Naukri: JSON parse error for keyword=%r page=%d: %s",
            keyword, page_no, exc,
        )
        return []

    if not job_list:
        logger.debug(
            "Naukri: no results for keyword=%r location=%r page=%d",
            keyword, location, page_no,
        )
        return []

    survivors = []
    for card in job_list:
        job_id  = card.get("jobId", "")
        # 'post' is the actual title field in the API response
        title   = (card.get("post", "") or card.get("title", "")).strip()
        company = (card.get("companyName", "") or card.get("company", "")).strip()

        # Location: 'cityfield' format:
        #   ' telangana - hyderabad, maharashtra pune, karnataka bengaluru  Metropolitan...'
        # Everything before the double-space is the clean city data.
        # Entries are like 'state - city' or 'state city'; grab the part after ' - ' or last word.
        cityfield = (card.get("cityfield", "") or "").strip()
        if cityfield:
            clean_part = cityfield.split("  ")[0].strip()   # drop '  Metropolitan...' noise
            cities = []
            for chunk in clean_part.split(","):
                chunk = chunk.strip()
                # 'telangana - hyderabad' -> 'hyderabad'; 'maharashtra pune' -> 'pune'
                if " - " in chunk:
                    cities.append(chunk.split(" - ", 1)[1].strip().title())
                elif chunk:
                    # last word is usually the city
                    cities.append(chunk.split()[-1].strip().title())
            location_val = ", ".join(c for c in cities if c) or location
        else:
            location_val = location


        # URL: 'urlStr' is the canonical direct listing URL
        url_str   = card.get("urlStr", "") or card.get("jdURL", "")
        posted_at = _parse_naukri_date(card.get("addDate") or card.get("createdDate"))
        # minExp/maxExp come as strings (e.g. '0', '2') — cast to float for comparison
        min_exp   = card.get("minExp")
        max_exp   = card.get("maxExp")
        # keywords field name in actual response
        skills    = card.get("keywords", "") or card.get("tagsAndSkills", "")
        salary    = _build_salary(card)
        # Truncated HTML snippet — replaced by full JD in Step 2
        snippet   = _strip_html(
            card.get("jobDesc", "") or card.get("jobDescription", "")
        )
        employment_type = card.get("employmentType", "")
        vacancies = card.get("noOfVacancy") or card.get("vacancy", "")

        # ---- Stage-1 Filter 1: Experience --------------------------------
        # minExp is a structured numeric field — no regex/scraping needed, zero cost.
        # Threshold comes from profile.candidate.experience.max_required.
        if min_exp is not None:
            try:
                if float(min_exp) > max_exp_years:
                    logger.debug(
                        "Naukri Stage-1 REJECT (exp): %r @ %r — minExp=%s > %d",
                        title, company, min_exp, max_exp_years,
                    )
                    continue
            except (TypeError, ValueError):
                pass   # Unparseable — let it through; prefilter will catch it

        # ---- Stage-1 Filter 2: Age ---------------------------------------
        if posted_at and _is_too_old(posted_at, max_age_days):
            logger.debug(
                "Naukri Stage-1 REJECT (age): %r @ %r — posted_at=%s",
                title, company, posted_at,
            )
            continue

        # Build canonical URL
        if url_str and not url_str.startswith("http"):
            url_str = "https://www.naukri.com" + url_str

        survivors.append({
            "_naukri_job_id":  job_id,          # internal — removed before returning
            "title":           title,
            "company":         company,
            "location":        location_val.strip(),
            "description":     snippet,          # placeholder, replaced by full JD
            "url":             url_str,
            "source":          "naukri",
            "salary":          salary,
            "posted_at":       posted_at,
            # Extra Naukri metadata — useful for scorer context
            "skills":          skills,
            "employment_type": employment_type,
            "vacancies":       str(vacancies) if vacancies else "",
            "min_exp":         str(min_exp) if min_exp is not None else "",
            "max_exp":         str(max_exp) if max_exp is not None else "",
        })

    logger.debug(
        "Naukri search p%d %r / %r: %d cards -> %d after Stage-1",
        page_no, keyword, location, len(job_list), len(survivors),
    )
    return survivors


# -----------------------------------------------------------------
# MAIN ENTRY POINT
# -----------------------------------------------------------------

def fetch_naukri(profile: dict = None) -> list:
    """
    Fetch jobs from Naukri.com using the two-step search + detail API.

    Configuration is read from the profile naukri: block:
      naukri:
        keywords:   [...]   # search terms (required)
        locations:  [...]   # location strings
        pages:      2       # result pages per keyword+location combo

    If profile is None or the naukri block is absent, falls back to defaults.

    Pipeline:
      1. For each (keyword x location x page): Search API -> Stage-1 filter
         (minExp > 1yr OR age > max_job_age_days -> immediate reject)
      2. Deduplicate survivors by jobId across all combos
      3. For each surviving job: Detail API -> full JD replaces snippet
         Failure -> graceful fallback to snippet (job is NOT dropped)

    Returns:
      Standard pipeline job dicts (description is always plain text).
    """
    naukri_cfg   = (profile or {}).get("naukri", {})
    hard_reject  = (profile or {}).get("hard_reject", {})
    candidate    = (profile or {}).get("candidate", {})
    max_age_days = hard_reject.get("max_job_age_days", 45)

    # Read experience cap from profile — same field the prefilter respects.
    # Falls back to _DEFAULT_MAX_EXP_YEARS (1) if the profile key is absent.
    max_exp_years = int(
        candidate.get("experience", {}).get("max_required", _DEFAULT_MAX_EXP_YEARS)
    )

    keywords  = naukri_cfg.get("keywords") or _DEFAULT_KEYWORDS
    locations = naukri_cfg.get("locations") or _DEFAULT_LOCATIONS
    pages     = int(naukri_cfg.get("pages", 2))

    total_combos = len(keywords) * len(locations) * pages
    logger.info(
        "Naukri: %d keywords x %d locations x %d pages "
        "= up to %d search calls (%d raw listings max) "
        "| exp cap: %dyr | age cap: %dd",
        len(keywords), len(locations), pages,
        total_combos, total_combos * _RESULTS_PER_PAGE,
        max_exp_years, max_age_days,
    )

    # ---- Step 1: Search across all keyword x location x page combos ------
    stage1_jobs: list = []
    seen_job_ids: set = set()

    for keyword in keywords:
        for location in locations:
            for page_no in range(1, pages + 1):
                batch = _fetch_search_page(keyword, location, page_no, max_age_days, max_exp_years)
                for job in batch:
                    jid = job.get("_naukri_job_id", "")
                    if jid and jid in seen_job_ids:
                        continue   # Same job from multiple keyword/location combos
                    if jid:
                        seen_job_ids.add(jid)
                    stage1_jobs.append(job)
                time.sleep(0.5)   # polite inter-page delay

    logger.info(
        "Naukri Stage-1 complete: %d unique jobs survived (exp <= %dyr, age <= %dd)",
        len(stage1_jobs), max_exp_years, max_age_days,
    )

    if not stage1_jobs:
        return []

    # ---- Step 2: Fetch full JDs for surviving jobs -----------------------
    final_jobs: list = []

    for job in stage1_jobs:
        job_id = job.pop("_naukri_job_id", "")   # remove internal key

        if job_id:
            time.sleep(_INTER_REQUEST_DELAY)
            full_jd = _fetch_job_detail(job_id)
            if full_jd:
                job["description"] = full_jd
            else:
                # Graceful fallback — truncated snippet already in job["description"]
                logger.debug(
                    "Naukri: detail API failed for jobId=%s (%r) — using snippet fallback",
                    job_id, job.get("title", ""),
                )

        final_jobs.append(job)

    logger.info("Naukri: %d jobs ready for pipeline (Step 2 complete)", len(final_jobs))
    return final_jobs


# -----------------------------------------------------------------
# DEFAULTS (used when profile has no naukri: block)
# Override via profiles/your_profile.yaml -> naukri: keywords/locations/pages
# -----------------------------------------------------------------

_DEFAULT_KEYWORDS = [
    "backend developer fresher",
    "backend intern golang",
    "software engineer intern",
    "golang developer fresher",
    "go developer intern",
    "typescript backend intern",
    "node.js backend fresher",
    "junior backend developer",
    "SDE intern",
    "software developer intern",
]

_DEFAULT_LOCATIONS = [
    "india",
    "bangalore",
    "work from home",
]
