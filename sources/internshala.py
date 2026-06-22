"""
sources/internshala.py — Internshala internship & fresher job scraper

Why requests + BeautifulSoup (not scrapling.Fetcher):
  Internshala is server-side rendered and works with plain HTTP.
  scrapling.Fetcher returns 0 cards on EC2/AWS IPs (bot detection at the
  TLS/header fingerprint level that scrapling doesn't fully mask).
  Plain requests with browser-like headers + BeautifulSoup reliably
  returns all listings from both home IPs and AWS datacenter IPs.

Coverage:
  - Backend development internships (WFH + on-site)
  - Web development internships (WFH)
  - Golang-specific internships
  - Backend fresher jobs
  - TypeScript / Node.js internships
"""

import logging
import time
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# All URLs to crawl. Internshala filters are URL-based — no JS needed.
SEARCH_URLS = [
    # Backend internships
    "https://internshala.com/internships/backend-development-internship/",
    "https://internshala.com/internships/backend-development-internship/work-from-home-internships/",
    # Web / full-stack (includes Node, TypeScript backends)
    "https://internshala.com/internships/web-development-internship/work-from-home-internships/",
    "https://internshala.com/internships/web-development-internship/",
    # Golang-specific keyword search
    "https://internshala.com/internships/keywords-golang/",
    "https://internshala.com/internships/keywords-go-developer/",
    # TypeScript / Node.js keyword search
    "https://internshala.com/internships/keywords-nodejs/",
    "https://internshala.com/internships/keywords-typescript/",
    # Fresher software jobs (not just internships)
    "https://internshala.com/jobs/backend-developer-intern-jobs/",
    "https://internshala.com/jobs/software-developer-jobs/",
]

# Internshala rate-limit: 1 request per second is safe
REQUEST_DELAY = 1.2

# Browser-like headers — Internshala checks these and returns different HTML
# for bare Python clients (especially on AWS IPs).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "DNT": "1",
}

# Reuse a session for keep-alive and automatic cookie handling.
# A persistent session looks more like a real browser session than
# one-off requests, which helps on bot-detecting servers.
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(_HEADERS)
    return _session


def _fetch_page(url: str, timeout: int = 20) -> BeautifulSoup | None:
    """Fetch a URL and return a BeautifulSoup object, or None on failure."""
    session = _get_session()
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Internshala failed for {url}: {e}")
        return None


def fetch_internshala() -> list[dict]:
    """
    Scrape Internshala job listing pages and return structured job dicts.

    Uses requests + BeautifulSoup instead of scrapling.Fetcher because:
    - Internshala is fully server-side rendered (no JS needed).
    - scrapling.Fetcher returns empty results on EC2/AWS IPs due to
      IP-based bot detection at the TLS fingerprint level.
    - Plain requests with browser-like headers and a persistent session
      reliably returns all job cards on both home and datacenter IPs.
    """
    all_jobs: list[dict] = []
    seen: set[str] = set()

    for url in SEARCH_URLS:
        try:
            time.sleep(REQUEST_DELAY)
            soup = _fetch_page(url)
            if soup is None:
                continue

            # Primary card selector — Internshala uses .individual_internship
            cards = soup.select(".individual_internship")

            if not cards:
                logger.debug(f"Internshala: no cards found at {url}")
                continue

            for card in cards:
                # Title — <h2 class="job-internship-name"><a class="job-title-href">
                title_el = card.select_one("h2.job-internship-name a, .job-internship-name a")
                title = title_el.get_text(strip=True) if title_el else ""

                # Company name — <p class="company-name">
                company_el = card.select_one("p.company-name")
                company = company_el.get_text(strip=True) if company_el else ""

                # Location — text lives inside <a> nested inside .locations span
                loc_el = card.select_one(".locations span a, .locations a")
                location = loc_el.get_text(strip=True) if loc_el else "India"
                # Normalise "Work From Home" → "Remote"
                if "home" in location.lower() or "wfh" in location.lower():
                    location = "Remote"

                # Stipend — .stipend
                stipend_el = card.select_one(".stipend")
                stipend = stipend_el.get_text(strip=True) if stipend_el else ""

                # Application URL — <a class="job-title-href"> is the canonical link
                link_el = card.select_one(
                    "a.job-title-href, "
                    "a[href*='/internship/detail/'], "
                    "a[href*='/job/detail/']"
                )
                href = link_el.get("href", "") if link_el else ""
                job_url = ("https://internshala.com" + href) if href.startswith("/") else href

                if not job_url or job_url in seen:
                    continue
                seen.add(job_url)

                if not title:
                    continue

                all_jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    location,
                    "description": f"{title} at {company}. Location: {location}. Stipend: {stipend}",
                    "url":         job_url,
                    "source":      "internshala",
                    "salary":      stipend,
                    "posted_at":   "",   # Internshala doesn't expose exact dates in listing
                })

        except Exception as e:
            logger.warning(f"Internshala failed for {url}: {e}")
            continue

    logger.info(f"Internshala: {len(all_jobs)} jobs found")
    return all_jobs
