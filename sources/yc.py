"""
sources/yc.py — Y Combinator Jobs board scraper

Why this matters:
  YC portfolio companies in India (Juspay, Setu, Khatabook, etc.) often
  post ONLY on this board. Strong signal for quality startups.

Architecture (two-phase):
  Phase 1 — Listing page: fetch search results, extract card URLs + card text.
            Apply cheap title/location pre-screens. Collect surviving URLs.
  Phase 2 — Job page: fetch each surviving job URL to get the full description,
            experience requirements, location, and salary. This is necessary
            because YC listing cards show ONLY title+company+location — all
            experience/skills data is on the individual job page.

This two-phase approach means check_experience in prefilter.py actually has
real description text to work with, so "3+ years" gets correctly rejected.
"""

import re
import logging
import time
from scrapling.fetchers import Fetcher, StealthyFetcher

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# SEARCH URLS
# ─────────────────────────────────────────────────────────────────

YC_SEARCH_URLS = [
    # Backend + intern/fulltime + remote
    "https://www.ycombinator.com/jobs?q=backend&type=fulltime,intern&remote=true",
    # TypeScript / Node.js backend
    "https://www.ycombinator.com/jobs?q=typescript+backend&type=fulltime,intern",
    # Golang
    "https://www.ycombinator.com/jobs?q=golang&type=fulltime,intern",
    # India location — catches non-remote India roles
    "https://www.ycombinator.com/jobs?q=backend&location=india",
]

REQUEST_DELAY   = 2.0   # seconds between requests (polite crawl)
JOB_PAGE_DELAY  = 1.5   # seconds between individual job page fetches
MAX_JOBS_PER_SEARCH = 15  # cap per search URL to limit total fetches

# ─────────────────────────────────────────────────────────────────
# US-ONLY LOCATION SIGNALS — skip these cards immediately
# ─────────────────────────────────────────────────────────────────

_US_CITIES = re.compile(
    r'\b(san francisco|new york|new york city|nyc|seattle|boston|austin|'
    r'chicago|los angeles|la\b|denver|portland|atlanta|miami|'
    r'mountain view|palo alto|menlo park|san jose|silicon valley)\b',
    re.IGNORECASE,
)

# ─────────────────────────────────────────────────────────────────
# TITLE SIGNALS — only follow card links if title looks relevant
# ─────────────────────────────────────────────────────────────────

_KEEP_TITLE = re.compile(
    r'backend|software\s+engineer|swe|sde|full.?stack|fullstack|'
    r'golang|node\.?js|typescript|python|api|platform|infrastructure|'
    r'intern|fresher|cloud|devops|ml\s+engineer|data\s+engineer|'
    r'developer|engineering',
    re.IGNORECASE,
)

_SKIP_TITLE = re.compile(
    r'\b(senior|sr\.|sr\s|lead|principal|staff|architect|director|vp\s|'
    r'head\s+of|manager|president|chief|recruiter|sales|marketing|'
    r'hr\b|human\s+resource|accountant|designer|graphic|content|seo|'
    r'customer\s+success|account\s+manager|writer|editor|research\s+scientist|'
    r'data\s+scientist|analyst(?!\s+engineer))\b',
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────
# HTTP HELPERS
# ─────────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: int = 20):
    """Fetch a URL. Try plain Fetcher first, fall back to StealthyFetcher."""
    try:
        page = Fetcher().get(url, timeout=timeout)
        body = page.get_all_text(ignore_tags=["script", "style"])
        if len(body) > 300:
            return page
    except Exception:
        pass

    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        return page
    except Exception as e:
        logger.debug(f"YC fetch failed for {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# PHASE 1: LISTING PAGE → card URLs
# ─────────────────────────────────────────────────────────────────

def _parse_listing_page(page, seen: set) -> list[tuple[str, str, str]]:
    """
    Extract (job_url, raw_title, card_text) tuples from a YC jobs listing page.
    Applies cheap title + location pre-screens to avoid fetching irrelevant pages.

    Returns list of (job_url, title, card_text) for cards that survived screening.
    """
    results = []

    try:
        # YC job cards: <a href="/jobs/XXXXX-title"> links
        cards = page.css("a[href*='/jobs/']")
        if not cards:
            cards = page.css("div[class*='job'] a, li[class*='job'] a")

        for card in cards[:MAX_JOBS_PER_SEARCH * 3]:  # over-fetch then filter
            href = card.attrib.get("href", "")
            if not href:
                continue

            job_url = ("https://www.ycombinator.com" + href) if href.startswith("/") else href

            # Must be a job URL, not a company/blog/nav page
            if not re.search(r'/jobs/\d', job_url):
                continue
            if job_url in seen:
                continue

            # Extract card text
            card_text = card.get_all_text() if hasattr(card, "get_all_text") else (card.text or "")
            card_text = card_text.strip()
            if not card_text:
                continue

            lines = [l.strip() for l in card_text.splitlines() if l.strip()]

            # Title is usually line 0. Skip if it looks like a nav/aggregate page.
            raw_title = lines[0] if lines else ""
            if not raw_title or len(raw_title) < 4:
                continue

            # ── Cheap pre-screens on card text ──────────────────────────
            card_lower = card_text.lower()

            # 1. Must have a tech-relevant title
            if not _KEEP_TITLE.search(raw_title):
                logger.debug(f"YC card skip (irrelevant title): '{raw_title}'")
                continue

            # 2. Skip obviously senior/non-target titles
            if _SKIP_TITLE.search(raw_title):
                logger.debug(f"YC card skip (senior/non-target title): '{raw_title}'")
                continue

            # 3. Skip US-city-only roles visible in the card
            if _US_CITIES.search(card_text) and "india" not in card_lower and "remote" not in card_lower:
                logger.debug(f"YC card skip (US location): '{raw_title}'")
                continue

            seen.add(job_url)
            results.append((job_url, raw_title, card_text))

            if len(results) >= MAX_JOBS_PER_SEARCH:
                break

    except Exception as e:
        logger.warning(f"YC listing parse error: {e}")

    return results


# ─────────────────────────────────────────────────────────────────
# PHASE 2: INDIVIDUAL JOB PAGE → full description + structured fields
# ─────────────────────────────────────────────────────────────────

_EXP_RE = re.compile(
    r'(\d+)\+?\s*[-–to]+\s*(\d+)\s*years?|'   # "3-5 years", "3 to 5 years"
    r'(\d+)\+\s*years?|'                        # "3+ years"
    r'(\d+)\s*years?\s+(of\s+)?experience',     # "3 years of experience"
    re.IGNORECASE,
)
_SALARY_RE = re.compile(
    r'(?:\$[\d,]+(?:k|K)?(?:\s*[-–]\s*\$[\d,]+(?:k|K)?)?'  # $X–$Y or $Xk
    r'|₹[\d,]+(?:\s*[-–]\s*₹[\d,]+)?'                       # ₹X–₹Y
    r'|\d+\s*LPA|\d+\s*lpa)',                                 # Xk LPA
    re.IGNORECASE,
)


def _parse_job_page(page, job_url: str, raw_title: str, card_text: str) -> dict | None:
    """
    Parse an individual YC job page to extract full description + structured fields.
    Returns a job dict, or None if the page is unparseable.
    """
    try:
        # Full page text (stripped of nav/footer)
        full_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer", "header"])
        full_text = re.sub(r'\s+', ' ', full_text).strip()

        if len(full_text) < 100:
            return None

        # ── Company name ────────────────────────────────────────────────────
        # YC job page URL: /companies/{company-slug}/jobs/{id}-{title}
        # The slug is the most reliable source — always present.
        company = "YC Company"
        slug_m = re.search(r'/companies/([^/]+)/jobs/', job_url)
        if slug_m:
            company = slug_m.group(1).replace("-", " ").title()

        # Try to refine with page content (company name link or heading)
        for sel in ("a[href*='/companies/']", "[class*='company']", "h2"):
            try:
                els = page.css(sel)
                if els:
                    candidate = (els[0].get_all_text() if hasattr(els[0], "get_all_text")
                                 else els[0].text or "").strip()
                    if 2 < len(candidate) < 60 and "\n" not in candidate:
                        company = candidate
                        break
            except Exception:
                pass

        # ── Location ────────────────────────────────────────────────────────
        location = "remote"
        text_lower = full_text.lower()
        if "india" in text_lower:
            location = "india"
        if "remote" in text_lower:
            location = "remote" if "india" not in text_lower else "india / remote"
        if _US_CITIES.search(full_text) and "india" not in text_lower and "remote" not in text_lower:
            return None  # US-only in-office — skip entirely

        # ── Salary ──────────────────────────────────────────────────────────
        salary = ""
        m = _SALARY_RE.search(full_text[:3000])
        if m:
            salary = m.group(0).strip()

        # ── Posted at ───────────────────────────────────────────────────────
        # YC pages sometimes have "Posted X days ago" in meta or page text
        posted_at = ""
        date_m = re.search(
            r'posted\s+(\d+\s+(?:hour|day|week|month)s?\s+ago)',
            full_text, re.IGNORECASE
        )
        if date_m:
            posted_at = date_m.group(1)

        # ── Description (used by prefilter.check_experience) ────────────────
        # Use full page text — this is what makes the experience check actually work.
        # Truncate to 4000 chars to keep scorer prompt size reasonable.
        description = full_text[:4000]

        return {
            "title":       raw_title,
            "company":     company,
            "location":    location,
            "description": description,
            "url":         job_url,
            "source":      "yc",
            "salary":      salary,
            "posted_at":   posted_at,
        }

    except Exception as e:
        logger.debug(f"YC job page parse error for {job_url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def fetch_yc() -> list[dict]:
    """
    Two-phase YC scraper:
      1. Fetch each search URL listing page, screen cards cheaply.
      2. Fetch individual job pages for surviving cards, extract full text.

    The full job page description is what allows prefilter.check_experience()
    to correctly reject "3+ years" and similar requirements that never appear
    in listing card text.
    """
    all_jobs: list[dict] = []
    seen_urls: set[str]  = set()
    candidates: list[tuple[str, str, str]] = []  # (url, title, card_text)

    # ── Phase 1: listing pages ───────────────────────────────────────────────
    for search_url in YC_SEARCH_URLS:
        time.sleep(REQUEST_DELAY)
        page = _fetch(search_url)
        if page is None:
            logger.warning(f"YC listing fetch failed: {search_url}")
            continue

        batch = _parse_listing_page(page, seen_urls)
        logger.info(f"YC listing '{search_url.split('?')[1][:40]}': {len(batch)} candidates")
        candidates.extend(batch)

    logger.info(f"YC Phase 1 complete: {len(candidates)} candidate job pages to fetch")

    # ── Phase 2: individual job pages ────────────────────────────────────────
    for job_url, raw_title, card_text in candidates:
        time.sleep(JOB_PAGE_DELAY)
        page = _fetch(job_url, timeout=15)
        if page is None:
            logger.debug(f"YC job page fetch failed: {job_url}")
            continue

        job = _parse_job_page(page, job_url, raw_title, card_text)
        if job:
            all_jobs.append(job)
            logger.debug(f"YC job parsed: '{raw_title}' @ {job['company']} [{job['location']}]")

    logger.info(f"YC Jobs: {len(all_jobs)} jobs parsed from {len(candidates)} fetched pages")
    return all_jobs
