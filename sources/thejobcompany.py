"""
sources/thejobcompany.py — TheJobCompany.co.in job source

Architecture:
  thejobcompany.co.in is a server-rendered PHP site — job listings are present
  directly in the HTML response. Plain requests + BeautifulSoup is sufficient;
  no Playwright or JavaScript execution is needed.

URL structure:
  Category listing: https://thejobcompany.co.in/job-category/{slug}
  Page 2+:         https://thejobcompany.co.in/job-category/{slug}?page=N
  Detail page:     https://thejobcompany.co.in/frontend/job_details.php?job_id={id}

Coverage (5 categories x 2 pages = 10 listing requests per run):
  - /job-category/internships
  - /job-category/batch/2027
  - /job-category/software-Engineer
  - /job-category/devops-cloud
  - /job-category/web-development

Live-validated observations (September 2026):
  - 12 job cards per listing page, consistently across all categories
  - Page ordering is date-descending (newest first), confirmed by job ID ordering
  - Within-category P1 intersect P2 overlap: 0 (clean page boundaries observed)
  - Cross-category overlap: internships intersect batch_2027 approx 18 IDs;
    batch_2027 intersect software_engineer approx 2 IDs
  - Total unique IDs from all 10 listing pages: approx 100 (from 120 raw)
  - Detail page fully server-rendered: all metadata present in raw HTML
  - Apply link on detail page points to an internal intermediate;
    canonical detail URL is used as the application URL

Rate limiting:
  - REQUEST_DELAY_LISTING: 1.0s between listing page fetches (10 requests total)
  - REQUEST_DELAY_DETAIL:  1.2s between detail page fetches
  - No aggressive concurrency (sequential listing fetches, bounded detail fetches)

Deduplication:
  - Source-local dedup: in-run set of numeric job_id strings (handles cross-category
    duplicates; prevents fetching the same detail page twice per run)
  - Global dedup: pipeline/dedup.py handles cross-run and cross-source dedup via
    make_job_id() (title+company+location hash) and make_url_id() (canonical URL hash)

Salary handling:
  - Values marked [Expected] or (Expected) are kept verbatim but prefixed with
    "[Expected] " to make the uncertain status explicit in downstream fields.
"""

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────

_BASE_URL = "https://thejobcompany.co.in"

# Five category roots to scrape (pages 1 and 2 of each)
CATEGORY_SLUGS: list[tuple[str, str]] = [
    # (human-readable label, URL path)
    ("internships",       "/job-category/internships"),
    ("batch_2027",        "/job-category/batch/2027"),
    ("software_engineer", "/job-category/software-Engineer"),
    ("devops_cloud",      "/job-category/devops-cloud"),
    ("web_development",   "/job-category/web-development"),
]

# Exactly 2 pages per category (10 listing requests per run)
PAGES_PER_CATEGORY = 2

# Conservative rate limits — the site has no documented limit.
REQUEST_DELAY_LISTING = 1.0   # seconds between listing page fetches
REQUEST_DELAY_DETAIL  = 1.2   # seconds between detail page fetches

REQUEST_TIMEOUT = 25   # seconds per HTTP request

# Maximum detail pages to fetch in one run — safety cap.
# With 10 listing pages x 12 cards = 120 raw candidates, after source-local dedup
# we expect ~80-100 unique jobs. This cap prevents runaway fetching.
_MAX_DETAIL_FETCHES = 120

# Source label used in pipeline
SOURCE_NAME = "thejobcompany"

# Browser-like headers — required to avoid bot-detection responses
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # NOTE: Do NOT set Accept-Encoding manually. When set explicitly, requests sends
    # the header to the server but does NOT auto-decompress the response, resulting
    # in garbled binary data via resp.text. requests handles decompression
    # automatically when it sets Accept-Encoding itself (the default behaviour).
    "Connection":      "keep-alive",
    "DNT":             "1",
}

# Reusable session for keep-alive connections
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(_HEADERS)
    return _session


# ─────────────────────────────────────────────────────────────────
# TEXT NORMALISATION HELPERS
# ─────────────────────────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")
_JOB_ID_RE     = re.compile(r"job_id=(\d+)")


def _clean(text: str) -> str:
    """Collapse whitespace and strip leading/trailing space from a string."""
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _normalise_salary(raw: str) -> str:
    """
    Normalise salary string.

    If the raw value contains '[Expected]' or '(Expected)', prefix it with
    '[Expected] ' to make the uncertain status explicit. The original raw
    text is preserved — we do NOT strip or rewrite the value.
    """
    if not raw:
        return ""
    cleaned = _clean(raw)
    if re.search(r"\[Expected\]|\(Expected\)", cleaned, re.IGNORECASE):
        if not cleaned.startswith("[Expected]"):
            cleaned = "[Expected] " + cleaned
    return cleaned


def _detail_url(job_id: str) -> str:
    """Canonical detail URL for a given numeric job_id."""
    return f"{_BASE_URL}/frontend/job_details.php?job_id={job_id}"


# ─────────────────────────────────────────────────────────────────
# HTTP FETCH HELPER
# ─────────────────────────────────────────────────────────────────

def _fetch(url: str) -> BeautifulSoup | None:
    """
    Fetch a URL and return a BeautifulSoup object, or None on any failure.

    Handles:
      - Connection errors, timeouts -> logs warning, returns None
      - HTTP 4xx/5xx -> logs warning, returns None
      - Malformed HTML -> BeautifulSoup still parses what it can (lxml is lenient)
    """
    session = _get_session()
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
        # NOTE: html.parser (not lxml) is required. The site's HTML has a
        # premature </body> tag at ~position 2749; lxml discards all content
        # after it (including the job listings at ~position 10342). html.parser
        # is lenient and parses the full document regardless of tag ordering.
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        logger.warning(f"[{SOURCE_NAME}] HTTP {status} for {url}: {e}")
        return None
    except requests.exceptions.Timeout:
        logger.warning(f"[{SOURCE_NAME}] Timeout fetching {url}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"[{SOURCE_NAME}] Connection error for {url}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[{SOURCE_NAME}] Unexpected error fetching {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# LISTING PAGE PARSER
# ─────────────────────────────────────────────────────────────────

def _parse_listing_page(soup: BeautifulSoup, category_label: str) -> list[dict]:
    """
    Parse a category listing page and return a list of partial job dicts.

    Card structure (validated September 2026):
      <div class="job-listing">
        <div class="company-split">
          <a class="applyBtn" href="../frontend/job_details.php?job_id=6382">
            <p class="company-title">GE Aerospace is hiring Data Science Intern</p>
            <p><strong>Batch :</strong> 2028 | 2027</p>
            <p><strong>Location :</strong> Bengaluru, India</p>
            <p><strong>Qualification :</strong> BE/B-Tech/ME/M-Tech</p>
            <p><strong>Salary :</strong> 50,000/Month(Stipend) [Expected]</p>
          </a>
        </div>
      </div>
    """
    cards = soup.select("div.job-listing")
    if not cards:
        logger.debug(f"[{SOURCE_NAME}] No job cards found in [{category_label}] listing page")
        return []

    jobs: list[dict] = []

    for card in cards:
        # ── Job ID ────────────────────────────────────────────────────────────
        link_el = card.select_one("a.applyBtn, a[href*='job_id=']")
        if not link_el:
            logger.debug(f"[{SOURCE_NAME}] Card has no job_id link — skipping")
            continue

        href = link_el.get("href", "")
        m = _JOB_ID_RE.search(href)
        if not m:
            logger.debug(f"[{SOURCE_NAME}] Could not extract job_id from href '{href}' — skipping")
            continue

        job_id = m.group(1)
        url = _detail_url(job_id)

        # ── Title + Company from .company-title ───────────────────────────────
        # Format: "<Company> is hiring <Title>"
        title_el = card.select_one(".company-title")
        raw_title_text = _clean(title_el.get_text()) if title_el else ""
        company, title = _split_title_company(raw_title_text)

        # ── Structured metadata from labelled <p> tags ────────────────────────
        location      = _extract_labeled_field(card, "Location")
        batch         = _extract_labeled_field(card, "Batch")
        qualification = _extract_labeled_field(card, "Qualification")
        salary_raw    = _extract_labeled_field(card, "Salary")
        salary        = _normalise_salary(salary_raw)

        if not title:
            logger.debug(f"[{SOURCE_NAME}] Empty title for job_id={job_id} — skipping")
            continue

        jobs.append({
            "_tjc_job_id":   job_id,        # source-native ID; used for dedup; stripped before return
            "title":         title,
            "company":       company or title,  # fallback to title if parse failed
            "location":      location or "India",
            "salary":        salary,
            "_batch":        batch,
            "_qualification": qualification,
            "url":           url,
            "source":        SOURCE_NAME,
            "description":   "",   # populated by detail fetch
            "posted_at":     "",   # populated by detail fetch
        })

    return jobs


def _split_title_company(raw: str) -> tuple[str, str]:
    """
    Split a card's company-title string into (company, title).

    Expected format: "<Company> is hiring <Title>"
    Falls back to returning ("", raw) if the pattern does not match.
    """
    parts = raw.split(" is hiring ", 1)
    if len(parts) == 2:
        company = _clean(parts[0])
        title   = _clean(parts[1])
        return company, title
    # Fallback: treat entire text as title, company unknown
    return "", _clean(raw)


def _extract_labeled_field(card: BeautifulSoup, label: str) -> str:
    """
    Extract the value of a labeled field from a job card.

    Looks for <p><strong>{label} :</strong> VALUE</p> pattern.
    Returns "" if not found.
    """
    for p in card.select("p"):
        strong = p.select_one("strong")
        if strong and label.lower() in strong.get_text(strip=True).lower():
            full_text  = _clean(p.get_text())
            label_text = _clean(strong.get_text())
            value = full_text[len(label_text):].strip().lstrip(":").strip()
            return value
    return ""


# ─────────────────────────────────────────────────────────────────
# DETAIL PAGE PARSER
# ─────────────────────────────────────────────────────────────────

def _parse_detail_page(soup: BeautifulSoup, job_id: str) -> dict:
    """
    Parse a job detail page and return a dict of enrichment fields.

    Detail page structure (validated September 2026):
      .detail-top-in:
        <p class="position">Data Science Intern</p>
        <p class="company-name"><i...></i> GE Aerospace</p>
        <p class="upload-date"><i...></i> Updated on: 16 September 2026</p>

      .box-in (within .additional-detail):
        "Website | www.geaerospace.com"
        "Work Location | Bengaluru, India"
        "Job Type | Internship + Fte"
        "Batch | 2028 | 2027"
        "Stream Required | BE/B-Tech/ME/M-Tech"
        "Salary | 50,000/Month(Stipend) [Expected]"

      .jd-content: full job description (no "Job Description" heading)
      .small-jobs: sidebar "Jobs you might like" — EXCLUDED from JD

    Returns dict with enriched fields; callers should merge with existing partial dict.
    """
    enriched: dict = {}

    # ── Title, Company, Date from .detail-top-in ─────────────────────────────
    dti = soup.select_one(".detail-top-in")
    if dti:
        pos_el     = dti.select_one("p.position")
        company_el = dti.select_one("p.company-name")
        date_el    = dti.select_one("p.upload-date")

        if pos_el:
            enriched["title"] = _clean(pos_el.get_text())

        if company_el:
            # Company name has an <i class="fa-solid fa-building"> prefix icon — stripped by get_text
            enriched["company"] = _clean(company_el.get_text())

        if date_el:
            date_text = _clean(date_el.get_text())
            enriched["posted_at"] = _parse_updated_date(date_text)

    # ── Structured metadata from .box-in elements ─────────────────────────────
    # Each .box-in contains a .text div with p.text-head (key) and p.text-ans (value)
    # Real structure (validated live):
    #   <div class="box-in">
    #     <div class="text">
    #       <p class="text-head">Work Location</p>
    #       <p class="text-ans">Bengaluru, India</p>
    #     </div>
    #     <div class="img-icon">...</div>
    #   </div>
    boxes = soup.select(".box-in")
    for box in boxes:
        head_el = box.select_one("p.text-head")
        ans_el  = box.select_one("p.text-ans")
        if not head_el or not ans_el:
            continue
        key   = _clean(head_el.get_text()).lower()
        value = _clean(ans_el.get_text())

        if "website" in key:
            enriched["_website"] = value
        elif "location" in key:
            enriched["location"] = value
        elif "job type" in key:
            enriched["_job_type"] = value
        elif "batch" in key:
            enriched["_batch"] = value
        elif "stream" in key or "qualification" in key:
            enriched["_qualification"] = value
        elif "salary" in key:
            enriched["salary"] = _normalise_salary(value)

    # ── Full Job Description from .jd-content ────────────────────────────────
    # .jd-content contains the actual JD text.
    # .small-jobs (sidebar) and .detail-right are deliberately excluded by selector scope.
    jd_el = soup.select_one(".jd-content")
    if jd_el:
        # Defensively remove any embedded sidebar elements
        for sidebar in jd_el.select(".small-jobs, .detail-right"):
            sidebar.decompose()

        jd_text = jd_el.get_text(separator="\n", strip=True)
        jd_text = _clean_jd(jd_text)
        if jd_text:
            enriched["description"] = jd_text
    else:
        # Fallback: .job-description contains "Job Description\n<actual text>"
        jd_fallback = soup.select_one(".job-description")
        if jd_fallback:
            for sidebar in jd_fallback.select(".small-jobs, .detail-right"):
                sidebar.decompose()
            jd_text = jd_fallback.get_text(separator="\n", strip=True)
            # Strip the "Job Description" heading if present
            jd_text = re.sub(r"^Job\s+Description\s*\n?", "", jd_text, flags=re.IGNORECASE)
            jd_text = _clean_jd(jd_text)
            if jd_text:
                enriched["description"] = jd_text

    return enriched


def _parse_updated_date(text: str) -> str:
    """
    Parse 'Updated on: DD Month YYYY' to ISO date string.

    Returns ISO string if parseable, raw text otherwise.
    Example: "Updated on: 16 September 2026" -> "2026-09-16"
    """
    m = re.search(r"Updated\s+on\s*:\s*(.+)", text, re.IGNORECASE)
    if not m:
        return text

    raw_date = _clean(m.group(1))

    try:
        from datetime import datetime
        dt = datetime.strptime(raw_date, "%d %B %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    return raw_date


def _clean_jd(text: str) -> str:
    """
    Clean a raw JD text block:
    - Collapse runs of 3+ blank lines to 2 blank lines
    - Strip leading/trailing whitespace
    - Remove common boilerplate suffixes if accidentally included
    """
    if not text:
        return ""
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove trailing FAQ/disclaimer/sidebar boilerplate (defensive)
    cutoff_patterns = [
        r"\nFAQ\b",
        r"\nFrequently Asked Questions\b",
        r"\nDisclaimer\s*:",
        r"\nJobs you might like\b",
    ]
    for pattern in cutoff_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            text = text[:m.start()]
    return text.strip()


# ─────────────────────────────────────────────────────────────────
# LISTING PAGE FETCHER
# ─────────────────────────────────────────────────────────────────

def _fetch_category_pages(label: str, base_path: str) -> list[dict]:
    """
    Fetch pages 1 and 2 of a single category.

    Returns a list of partial job dicts (description/posted_at not yet populated).
    """
    partial_jobs: list[dict] = []

    for page_no in range(1, PAGES_PER_CATEGORY + 1):
        if page_no == 1:
            url = _BASE_URL + base_path
        else:
            url = _BASE_URL + base_path + f"?page={page_no}"

        logger.debug(f"[{SOURCE_NAME}] Fetching [{label}] page {page_no}: {url}")

        if page_no > 1:
            time.sleep(REQUEST_DELAY_LISTING)

        soup = _fetch(url)
        if soup is None:
            logger.warning(
                f"[{SOURCE_NAME}] Failed to fetch [{label}] page {page_no} — skipping"
            )
            continue

        batch = _parse_listing_page(soup, label)
        logger.info(
            f"[{SOURCE_NAME}] [{label}] page {page_no}: {len(batch)} cards parsed"
        )
        partial_jobs.extend(batch)

    return partial_jobs


# ─────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def fetch_thejobcompany() -> list[dict]:
    """
    Scrape thejobcompany.co.in and return structured job dicts.

    Pipeline:
      1. Fetch pages 1 and 2 for each of 5 category URLs (10 listing requests)
      2. Deduplicate by numeric job_id (source-local, in-run)
      3. For each unique job, fetch the detail page (up to _MAX_DETAIL_FETCHES)
      4. Merge listing + detail metadata into final job dicts
      5. Return list conforming to standard pipeline schema

    Deduplication strategy:
      - Source-local (in-run set by job_id): prevents fetching the same detail page
        twice when a job appears in multiple categories. Simple and sufficient.
      - Global (pipeline/dedup.py): handles cross-run and cross-source dedup via
        make_job_id() (title+company+location hash) and make_url_id() (URL hash).

    Returns:
      List of job dicts with at minimum:
        title, company, location, description, url, source, salary, posted_at
    """
    logger.info(
        f"[{SOURCE_NAME}] Starting fetch: "
        f"{len(CATEGORY_SLUGS)} categories x {PAGES_PER_CATEGORY} pages"
    )

    # ── Step 1: Collect all listing-page partial jobs ─────────────────────────
    all_partial: list[dict] = []

    for i, (label, path) in enumerate(CATEGORY_SLUGS):
        if i > 0:
            time.sleep(REQUEST_DELAY_LISTING)
        batch = _fetch_category_pages(label, path)
        all_partial.extend(batch)

    logger.info(f"[{SOURCE_NAME}] Raw listing total: {len(all_partial)} cards")

    # ── Step 2: Source-local dedup by job_id ──────────────────────────────────
    seen_ids: set[str] = set()
    unique_partials: list[dict] = []

    for job in all_partial:
        jid = job.get("_tjc_job_id", "")
        if jid and jid in seen_ids:
            continue
        if jid:
            seen_ids.add(jid)
        unique_partials.append(job)

    duplicates_removed = len(all_partial) - len(unique_partials)
    logger.info(
        f"[{SOURCE_NAME}] Source-local dedup: "
        f"{len(all_partial)} raw -> {len(unique_partials)} unique "
        f"({duplicates_removed} cross-category duplicates removed)"
    )

    # ── Step 3: Fetch detail pages ────────────────────────────────────────────
    final_jobs: list[dict] = []
    detail_fetches  = 0
    detail_failures = 0

    for job in unique_partials:
        if detail_fetches >= _MAX_DETAIL_FETCHES:
            logger.warning(
                f"[{SOURCE_NAME}] Detail fetch cap ({_MAX_DETAIL_FETCHES}) reached — "
                f"remaining jobs kept with listing-page metadata only"
            )
            final_jobs.append(_finalise_job(job))
            continue

        job_id = job.get("_tjc_job_id", "")
        url    = job.get("url", "")

        if url and job_id:
            if detail_fetches > 0:
                time.sleep(REQUEST_DELAY_DETAIL)

            detail_soup = _fetch(url)
            detail_fetches += 1

            if detail_soup is not None:
                enriched = _parse_detail_page(detail_soup, job_id)
                job.update(enriched)
            else:
                detail_failures += 1
                logger.debug(
                    f"[{SOURCE_NAME}] Detail fetch failed for job_id={job_id} — "
                    "keeping listing-page metadata"
                )

        # Ensure description is non-empty even if detail fetch was skipped/failed
        if not job.get("description"):
            job["description"] = _build_fallback_description(job)

        final_jobs.append(_finalise_job(job))

    logger.info(
        f"[{SOURCE_NAME}] Detail fetches: {detail_fetches} attempted, "
        f"{detail_failures} failed, "
        f"{detail_fetches - detail_failures} successful"
    )
    logger.info(
        f"[{SOURCE_NAME}] Final: {len(final_jobs)} jobs ready for pipeline"
    )

    return final_jobs


def _build_fallback_description(job: dict) -> str:
    """
    Build a minimal description from listing-page metadata when the detail
    page fetch fails or has no description.
    """
    parts = []
    if job.get("title"):
        parts.append(f"{job['title']} at {job.get('company', '')}")
    if job.get("location"):
        parts.append(f"Location: {job['location']}")
    if job.get("_job_type"):
        parts.append(f"Type: {job['_job_type']}")
    if job.get("_batch"):
        parts.append(f"Batch: {job['_batch']}")
    if job.get("_qualification"):
        parts.append(f"Qualification: {job['_qualification']}")
    if job.get("salary"):
        parts.append(f"Salary: {job['salary']}")
    return ". ".join(parts)


def _finalise_job(job: dict) -> dict:
    """
    Produce the final job dict conforming to the standard pipeline schema.
    Strips internal-only fields (prefixed with _) after incorporating them.
    """
    desc = job.get("description", "")

    # Incorporate metadata fields as structured context at the end of description
    extra_ctx = []
    if job.get("_batch") and "batch" not in desc.lower():
        extra_ctx.append(f"Batch: {job['_batch']}")
    if job.get("_qualification") and "qualification" not in desc.lower():
        extra_ctx.append(f"Qualification: {job['_qualification']}")
    if job.get("_job_type") and "job type" not in desc.lower():
        extra_ctx.append(f"Job Type: {job['_job_type']}")
    if job.get("_website"):
        extra_ctx.append(f"Website: {job['_website']}")

    if extra_ctx and desc:
        desc = desc + "\n\n---\n" + " | ".join(extra_ctx)
    elif extra_ctx:
        desc = " | ".join(extra_ctx)

    return {
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "location":    job.get("location", "India"),
        "description": desc,
        "url":         job.get("url", ""),
        "source":      SOURCE_NAME,
        "salary":      job.get("salary", ""),
        "posted_at":   job.get("posted_at", ""),
    }
