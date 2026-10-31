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

import json
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
import re
import logging
import time
from scrapling.fetchers import Fetcher
from sources.utils import is_playwright_available

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# SEARCH URLS
# ─────────────────────────────────────────────────────────────────

YC_SEARCH_URLS = [
    # These are real server-side routes. The old ?q=/location= parameters
    # returned the generic landing page rather than applying search filters.
    "https://www.ycombinator.com/jobs/role/software-engineer/india",
    "https://www.ycombinator.com/jobs/role/software-engineer/remote",
    "https://www.ycombinator.com/jobs/role/software-engineer",
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
        # Use the class-level shared client (avoids deprecated per-call constructor)
        page = Fetcher.get(url, timeout=timeout)
        body = page.get_all_text(ignore_tags=["script", "style"])
        if len(body) > 300:
            return page
    except Exception:
        pass

    if is_playwright_available():
        try:
            from scrapling.fetchers import StealthyFetcher
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
            return page
        except Exception as e:
            logger.debug(f"YC fetch failed for {url}: {e}")
    return None


# ─────────────────────────────────────────────────────────────────
# PHASE 1: LISTING PAGE → card URLs
# ─────────────────────────────────────────────────────────────────

def _embedded_props(page) -> dict:
    nodes = page.css('[data-page]')
    if not nodes:
        return {}
    try:
        return json.loads(nodes[0].attrib.get('data-page','{}')).get('props',{})
    except (ValueError, TypeError):
        return {}


def _eligible_card(title: str, location: str, experience: str, max_experience: int) -> bool:
    if not _KEEP_TITLE.search(title) or _SKIP_TITLE.search(title):
        return False
    minimum = re.match(r'(\d+)\s*(?:\+|years?|[-–])', experience)
    if minimum and int(minimum.group(1)) > max_experience:
        return False
    india = re.search(r'\b(india|IN|bangalore|bengaluru|mumbai|hyderabad|delhi|pune|kolkata|chennai|worldwide|anywhere)\b',location,re.I)
    foreign = re.search(r'\b(US|USA|GB|UK|CA|FR|DE|CH|EU|SG|united states|canada|london|paris|singapore)\b',location,re.I) or _US_CITIES.search(location)
    return bool(india) or not foreign


def _parse_listing_page(page, seen: set, max_experience: int = 1) -> list[tuple[str, str, dict]]:
    """Use YC's embedded records, retaining structured location and experience."""
    props = _embedded_props(page)
    records = props.get('jobPostings')
    if not isinstance(records,list):
        records = []
        for card in page.css("a[href*='/jobs/']"):
            text = card.get_all_text().strip()
            records.append({'url':card.attrib.get('href',''),'title':text.splitlines()[0] if text else ''})
    result = []
    for record in records:
        if not isinstance(record,dict):
            continue
        url = urljoin('https://www.ycombinator.com',record.get('url',''))
        if urlsplit(url).netloc != 'www.ycombinator.com' or not re.search(r'/companies/[^/]+/jobs/[A-Za-z0-9]+(?:-|$)',url):
            continue
        if url in seen:
            continue
        title = (record.get('title') or '').strip()
        if not _eligible_card(title,record.get('location') or '',str(record.get('minExperience') or ''),max_experience):
            continue
        seen.add(url)
        result.append((url,title,record))
        if len(result) >= MAX_JOBS_PER_SEARCH:
            break
    return result


# ─────────────────────────────────────────────────────────────────
# PHASE 2: INDIVIDUAL JOB PAGE → full description + structured fields
# ─────────────────────────────────────────────────────────────────

def _parse_job_page(page, job_url: str, raw_title: str, card: dict) -> dict | None:
    """Read the actual JobPosting, excluding related jobs and page navigation."""
    props = _embedded_props(page)
    metadata = props.get('job') or card
    for script in page.css('script[type="application/ld+json"]'):
        try:
            record = json.loads(script.text or '{}')
        except (ValueError,TypeError):
            continue
        if not isinstance(record,dict) or record.get('@type') != 'JobPosting':
            continue
        description = BeautifulSoup(record.get('description') or '','html.parser').get_text(' ',strip=True)
        if len(description)<100:
            continue
        organization = record.get('hiringOrganization') or {}
        company = organization.get('name') or metadata.get('companyName','')
        location = metadata.get('location') or ''
        if not location:
            places = record.get('jobLocation',[])
            if isinstance(places,dict): places=[places]
            location = ' / '.join(', '.join(str(v) for v in (place.get('address') or {}).values() if v and v != 'PostalAddress') for place in places)
        return {'title':record.get('title') or raw_title,'company':company,
                'location':location or 'Not specified','description':description[:12000],
                'description_complete':True,'url':job_url,'source':'yc',
                'salary':metadata.get('salaryRange') or '','posted_at':record.get('datePosted','')}
    logger.warning('YC detail has no complete JobPosting: %s',job_url)
    return None


# ─────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def fetch_yc(profile: dict | None = None) -> list[dict]:
    """
    Two-phase YC scraper:
      1. Fetch each search URL listing page, screen cards cheaply.
      2. Fetch individual job pages for surviving cards, extract full text.

    The full job page description is what allows prefilter.check_experience()
    to correctly reject "3+ years" and similar requirements that never appear
    in listing card text.
    """
    max_experience = (profile or {}).get("candidate",{}).get("experience",{}).get("max_required",1)
    all_jobs: list[dict] = []
    seen_urls: set[str]  = set()
    candidates: list[tuple[str, str, dict]] = []  # (url, title, structured metadata)

    # ── Phase 1: listing pages ───────────────────────────────────────────────
    for search_url in YC_SEARCH_URLS:
        time.sleep(REQUEST_DELAY)
        page = _fetch(search_url)
        if page is None:
            logger.warning(f"YC listing fetch failed: {search_url}")
            continue

        batch = _parse_listing_page(page, seen_urls, max_experience)
        logger.info(f"YC listing '{search_url.rsplit('/',1)[-1]}': {len(batch)} candidates")
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
